"""The startup splash, and the startup order around it.

Most of these guard things that break silently: a splash that imports the GUI
still works, it just stops being early; a faded window still shows, it just
looks grey. Nothing here would be noticed without a test.
"""

import ast
import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.splash import FADE_MIN_TICKS, MIN_OPACITY, PULSE_PERIOD, StartupSplash

_app = QApplication.instance() or QApplication([])

ROOT = Path(__file__).resolve().parents[3]


class TestSplashStaysLight(unittest.TestCase):
    """It has to be on screen before the GUI package is imported."""

    def test_does_not_import_the_gui_package(self) -> None:
        source = (ROOT / "src" / "splash.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        modules = {node.module for node in ast.walk(tree)
                   if isinstance(node, ast.ImportFrom) and node.module}
        modules |= {alias.name for node in ast.walk(tree)
                    if isinstance(node, ast.Import) for alias in node.names}
        self.assertEqual([name for name in modules
                          if name.startswith("src.gui")], [])


class TestPulse(unittest.TestCase):
    """A triangle wave between MIN_OPACITY and full, on the pixmap only."""

    def setUp(self) -> None:
        self.splash = StartupSplash()

    def tearDown(self) -> None:
        self.splash.deleteLater()

    def test_dim_at_the_ends_and_bright_in_the_middle(self) -> None:
        self.assertAlmostEqual(self.splash._pulse_opacity(0), MIN_OPACITY)
        self.assertAlmostEqual(self.splash._pulse_opacity(PULSE_PERIOD // 2), 1.0)
        self.assertGreater(self.splash._pulse_opacity(PULSE_PERIOD // 4),
                           MIN_OPACITY)

    def test_window_opacity_never_moves(self) -> None:
        """The pixmap fades, not the window.

        setWindowOpacity on a translucent frameless window composites badly on
        Windows and puts a grey panel behind the artwork.
        """
        for frame in range(PULSE_PERIOD):
            self.splash._frame = frame
            self.splash._tick()
            self.assertEqual(self.splash.windowOpacity(), 1.0)
        self.splash.finish()
        for _ in range(PULSE_PERIOD + 2):
            self.splash._tick()
            self.assertEqual(self.splash.windowOpacity(), 1.0)

    def test_the_fade_is_never_abrupt(self) -> None:
        """Closing right at the end of a breath must not cut off."""
        self.splash._frame = PULSE_PERIOD - 1
        self.splash.finish()
        self.assertGreaterEqual(self.splash._fade_ticks, FADE_MIN_TICKS)

    def test_finishing_twice_does_not_restart_the_fade(self) -> None:
        self.splash.finish()
        self.splash._tick()
        ticks = self.splash._closing_ticks
        self.splash.finish()
        self.assertEqual(self.splash._closing_ticks, ticks)

    def test_it_closes_itself(self) -> None:
        self.splash.show()
        self.splash.finish()
        for _ in range(PULSE_PERIOD + 2):
            self.splash._tick()
        self.assertFalse(self.splash.isVisible())

    def test_dismiss_goes_at_once(self) -> None:
        """The failure path: an error dialog must not open behind it."""
        self.splash.show()
        self.splash.dismiss()
        self.assertFalse(self.splash.isVisible())


class TestStartupOrder(unittest.TestCase):
    """main.py reads as prose; these assert the order it depends on."""

    def setUp(self) -> None:
        self.source = (ROOT / "main.py").read_text(encoding="utf-8")

    def _position(self, needle: str) -> int:
        where = self.source.find(needle)
        self.assertNotEqual(where, -1, f"{needle} is gone from main.py")
        return where

    def test_identity_is_claimed_before_the_application(self) -> None:
        """Windows binds the taskbar button when the first window appears."""
        body = self.source[self._position("def main("):]
        self.assertLess(body.find("claim_taskbar_identity()"),
                        body.find("QApplication"))

    def test_the_gui_is_imported_off_the_main_thread(self) -> None:
        """A QTimer cannot fire while the main thread sits in an import."""
        self.assertIn("threading.Thread", self.source)
        self.assertIn("from src.gui.gui import run_app",
                      self.source[self._position("def _import_gui("):
                                  self._position("def main(")])

    def test_the_splash_is_up_before_that_import_starts(self) -> None:
        body = self.source[self._position("def main("):]
        self.assertLess(body.find("show_splash()"), body.find("threading.Thread"))

    def test_the_colour_scheme_is_pinned_before_the_first_widget(self) -> None:
        """Qt hands out the palette when a widget is built, not later.

        The splash is the first widget there is, so the call has to come above
        it or a dark mode machine gets a dark palette after all.
        """
        body = self.source[self._position("def main("):]
        self.assertLess(body.find("force_light_theme()"), body.find("show_splash()"))
        self.assertLess(body.find("QApplication("),
                        body.find("force_light_theme()"))


class TestTheColourScheme(unittest.TestCase):
    """Every machine renders like one with Windows in light mode.

    Only half testable here. The offscreen platform these tests run on applies
    no colour scheme at all and reports Unknown whatever it is asked for, so
    the result can only be checked on a real desktop. What is checked is that
    the call is made, that it is made through the API that exists, and that it
    is harmless where that API is not there.
    """

    def test_it_is_harmless_whatever_qt_this_is(self) -> None:
        import main as entry

        entry.force_light_theme()
        entry.force_light_theme()

    def test_it_goes_through_set_colour_scheme(self) -> None:
        """Not a hand built palette: that would restyle a light machine too."""
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        body = source[source.index("def force_light_theme("):]
        body = body[:body.index("def _import_gui(")]
        self.assertIn("setColorScheme", body)
        self.assertIn("Qt.ColorScheme.Light", body)

    def test_an_older_qt_is_skipped_rather_than_guessed_at(self) -> None:
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        body = source[source.index("def force_light_theme("):]
        self.assertIn('hasattr(hints, "setColorScheme")', body)


class FakeSplash:
    """Counts what the window asks of it."""

    def __init__(self) -> None:
        self.finished = 0

    def finish(self, window=None) -> None:
        self.finished += 1


class TestHandover(unittest.TestCase):
    """The window owns the splash once it is up, and drops it when ready."""

    def setUp(self) -> None:
        from src.gui.gui import MainWindow

        self.window = MainWindow()          # cheap: start() is what is slow
        self.splash = FakeSplash()

    def tearDown(self) -> None:
        self.window.deleteLater()

    def test_held_until_released(self) -> None:
        self.window.hold_splash(self.splash)
        self.assertEqual(self.splash.finished, 0)
        self.window._release_splash()
        self.assertEqual(self.splash.finished, 1)

    def test_releasing_twice_fades_once(self) -> None:
        """The backstop timer still fires after the read already released it."""
        self.window.hold_splash(self.splash)
        self.window._release_splash()
        self.window._release_splash()
        self.assertEqual(self.splash.finished, 1)

    def test_releasing_without_a_splash_is_harmless(self) -> None:
        self.window._release_splash()

    def test_the_electrodes_read_is_what_releases_it(self) -> None:
        """Checked in the source: exercising it needs a live backend."""
        source = (ROOT / "src" / "gui" / "gui.py").read_text(encoding="utf-8")
        branch = source[source.index("def _on_deferred("):]
        branch = branch[:branch.index('elif key == "sem_edx"')]
        self.assertIn("self._release_splash()", branch)


class TestPackaging(unittest.TestCase):
    """Shipping only the .ico leaves the splash blank in the built exe."""

    def test_both_icon_files_exist(self) -> None:
        for name in ("app_icon.ico", "app_icon.png"):
            self.assertTrue((ROOT / "assets" / name).exists(), name)

    def test_the_spec_ships_both_and_sets_the_exe_icon(self) -> None:
        spec = (ROOT / "tools" / "QC Data Manager.spec").read_text(
            encoding="utf-8")
        self.assertIn("icon='../assets/app_icon.ico'", spec)
        for name in ("app_icon.ico", "app_icon.png"):
            self.assertIn(f"('../assets/{name}', 'assets')", spec)

    def test_a_bootstrapped_spec_would_ship_them_too(self) -> None:
        """The spec is written once and reused, so a miss here is permanent."""
        from tools.generate_executable import ASSET_FILES, ICON_FILE

        self.assertTrue(ICON_FILE.exists())
        self.assertEqual(set(ASSET_FILES), {"app_icon.ico", "app_icon.png"})


if __name__ == "__main__":
    unittest.main()
