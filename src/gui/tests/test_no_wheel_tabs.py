"""The main tab strip: no wheel switching, and a crossfade between pages."""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QAbstractAnimation
from PyQt6.QtWidgets import QApplication, QLabel

from src.gui.widgets.no_wheel_tabs import NoWheelTabWidget

_app = QApplication.instance() or QApplication([])


class TestNoWheelTabWidget(unittest.TestCase):
    """Clicking a tab dissolves a photograph of the old page over the new."""

    def setUp(self) -> None:
        self.widget = NoWheelTabWidget()
        for name in ("First", "Second", "Third"):
            self.widget.addTab(QLabel(name), name)
        self.widget.show()

    def tearDown(self) -> None:
        self.widget.deleteLater()

    def _click(self, index: int) -> None:
        """A click photographs the old page, then Qt switches the tab."""
        self.widget._photograph_current(index)
        self.widget.setCurrentIndex(index)

    def _overlays(self) -> list[QLabel]:
        return [child for child in self.widget.children()
                if isinstance(child, QLabel)]

    def _running(self) -> bool:
        animation = self.widget._animation
        return (animation is not None
                and animation.state() == QAbstractAnimation.State.Running)

    def test_the_crossfade_is_a_single_dissolve(self) -> None:
        """One animation covers the whole switch end to end."""
        self.assertEqual(self.widget.fade_ms, 200)

    def test_clicking_starts_the_dissolve(self) -> None:
        self._click(1)
        self.assertTrue(self._running())
        self.assertEqual(self.widget._animation.duration(), 200)
        self.assertTrue(self.widget._overlay.isVisible())

    def test_a_strip_can_opt_out(self) -> None:
        """Nested strips do not fade: the page holding them already did."""
        plain = NoWheelTabWidget(fade_ms=0)
        for name in ("First", "Second"):
            plain.addTab(QLabel(name), name)
        plain._photograph_current(1)
        plain.setCurrentIndex(1)
        self.assertIsNone(plain._animation)
        plain.deleteLater()

    def test_a_switch_in_code_swaps_instantly(self) -> None:
        """No photograph, since nothing was clicked, so nothing animates."""
        self.widget.setCurrentIndex(1)
        self.assertFalse(self._running())

    def test_switching_faster_than_the_fade_leaks_nothing(self) -> None:
        """The bug this guards against: stopping an animation does not fire
        ``finished``, so a per switch label was never cleaned up and a half
        transparent page stayed on screen, one per interrupted switch."""
        for index in (1, 2, 0, 1, 2):
            self._click(index)
            self.assertLessEqual(len(self._overlays()), 1)
        self.assertEqual(len(self._overlays()), 1)

    def test_an_interrupted_fade_restarts_from_opaque(self) -> None:
        """Otherwise the next switch begins part way through a dissolve."""
        self._click(1)
        self.widget._animation.setCurrentTime(150)
        self._click(2)
        self.assertEqual(self.widget._overlay.graphicsEffect().opacity(), 1.0)

    def test_the_overlay_is_hidden_when_nothing_is_dissolving(self) -> None:
        """The invariant: visible only while the animation runs."""
        self._click(1)
        self.assertTrue(self.widget._overlay.isVisible())
        # A tab changed in code takes no photograph, so it must put it away.
        self.widget.setCurrentIndex(2)
        self.assertFalse(self.widget._overlay.isVisible())

    def test_a_photograph_not_followed_by_a_change_is_not_reused(self) -> None:
        """Clicking the tab already showing photographs nothing, and must not
        dissolve a stale one on the next real switch."""
        self._click(1)
        self.widget._animation.stop()
        self.widget._overlay.hide()
        self.widget._photograph_current(self.widget.currentIndex())
        self.widget.setCurrentIndex(2)
        self.assertFalse(self.widget._overlay.isVisible())


if __name__ == "__main__":
    unittest.main()
