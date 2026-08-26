"""Tab widgets that don't switch tabs on mouse-wheel, and crossfade pages.

By default a QTabBar changes the current tab when the wheel scrolls over the
tab strip, which causes accidental tab switches. These ignore the wheel so the
event falls through to whatever is underneath (e.g. the scrollable content).

Switching also crossfades: the new page is shown at full opacity right away
and a photograph of the page being left dissolves on top of it. Fading only
the photograph keeps the backdrop from bleeding through mid-switch (the flash
a dual fade produces) and never puts an opacity effect on a live, heavy page.
The page being left is already hidden by the time Qt tells us the current tab
changed, so it is photographed on the way out, on the click.

There is exactly one overlay and one animation, reused for every switch, and
one invariant to keep: **the overlay is visible only while the animation is
running**. An earlier version created a label per switch and deleted it from
the animation's ``finished`` signal, which does not fire when an animation is
stopped early. Switching faster than the fade therefore stranded a half
transparent photograph over the page, one per interrupted switch, and they
accumulated. Reusing one overlay makes that impossible rather than tidied up
afterwards.
"""
from __future__ import annotations

from PyQt6.QtWidgets import QGraphicsOpacityEffect, QLabel, QTabBar, QTabWidget
from PyQt6.QtCore import Qt, QPoint, QPropertyAnimation, QEasingCurve, QRect


class NoWheelTabBar(QTabBar):
    """A QTabBar that ignores mouse-wheel events (no tab switching on scroll)."""

    def wheelEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        event.ignore()


class NoWheelTabWidget(QTabWidget):
    """A QTabWidget whose tab strip ignores the mouse wheel, and crossfades.

    Set ``fade_ms`` to 0 for a strip that should switch instantly, such as one
    nested inside a page that is itself already fading.
    """

    # One dissolve, so this is the whole switch end to end.
    FADE_MS = 200

    def __init__(self, parent=None, fade_ms: int = FADE_MS) -> None:
        super().__init__(parent)
        self.setTabBar(NoWheelTabBar(self))
        self.fade_ms = fade_ms

        self._overlay: QLabel | None = None
        self._animation: QPropertyAnimation | None = None
        # Set on the click, consumed by the change that follows it.
        self._pending = False

        self.tabBarClicked.connect(self._photograph_current)
        self.currentChanged.connect(self._fade_to_current)

    # ------------------------------------------------------------------
    def _build_overlay(self) -> QLabel:
        """The one label every switch reuses, and the effect it fades with."""
        overlay = QLabel(self)
        overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        overlay.hide()
        overlay.setGraphicsEffect(QGraphicsOpacityEffect(overlay))

        animation = QPropertyAnimation(overlay.graphicsEffect(), b"opacity", self)
        animation.setStartValue(1.0)
        animation.setEndValue(0.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        # Hidden rather than deleted, so the next switch has it ready and there
        # is never a stray widget to account for.
        animation.finished.connect(overlay.hide)

        self._overlay, self._animation = overlay, animation
        return overlay

    def _photograph_current(self, index: int) -> None:
        """Copy the page about to be left, while it is still on screen.

        Qt hides it before announcing the change, and a hidden widget does not
        render, so the copy has to be taken here. Only a click gets one; a tab
        changed in code switches instantly.
        """
        current = self.currentWidget()
        if not self.fade_ms or index == self.currentIndex() or current is None:
            return

        overlay = self._overlay or self._build_overlay()
        overlay.setPixmap(current.grab())
        # The page lives in the internal stack, so map its corner to us. Taken
        # fresh every time, so a resize since the last switch is accounted for.
        overlay.setGeometry(QRect(current.mapTo(self, QPoint(0, 0)),
                                  current.size()))
        self._pending = True

    def _fade_to_current(self, _index: int) -> None:
        """Dissolve the photograph of the old page over the fully opaque new
        one. The new page never animates, so nothing bleeds through."""
        pending, self._pending = self._pending, False
        if self._animation is None or self._overlay is None:
            return

        # Restarting resets the effect to its start value, so an interrupted
        # fade cannot leave the overlay part transparent for the next switch.
        self._animation.stop()
        if not pending or not self.fade_ms or self.currentWidget() is None:
            # Nothing to dissolve. The invariant says hide it.
            self._overlay.hide()
            return

        self._overlay.raise_()
        self._overlay.show()
        self._animation.setDuration(self.fade_ms)
        self._animation.start()
