"""The screen itself, driven with a fake backend and no database.

Two things are worth pinning down, and both are about a program nobody is
watching: the deck has to keep rotating, and a database that goes away must not
take the board with it.
"""

import datetime as dt
import unittest

import pandas as pd
from PyQt6.QtWidgets import QApplication

from src.data_access.electrodes.electrode_columns import (
    ElectrodeColumns,
    ElectrodeStatus,
    STATUS_COLUMN,
    VAT_COLUMN,
)
from live_dashboard.gui import dashboard_window as window_module
from live_dashboard.gui.dashboard_window import DashboardWindow

_app = QApplication.instance() or QApplication([])


def electrodes(count: int = 40) -> pd.DataFrame:
    now = dt.datetime.now(dt.timezone.utc)
    return pd.DataFrame([
        {str(ElectrodeColumns.DATE): (now - dt.timedelta(hours=i % 20)).isoformat(),
         str(ElectrodeColumns.MARKING): f"B{9000 + i}",
         str(ElectrodeColumns.FULL_NAME): "CATHODE" if i % 3 else "ANODE",
         str(ElectrodeColumns.RECIPE): 11,
         VAT_COLUMN: str(i % 4),
         STATUS_COLUMN: str(ElectrodeStatus.RELEASED if i % 5
                            else ElectrodeStatus.SCRAPPED)}
        for i in range(count)])


class FakeBackend:
    """Just the three calls the screen makes of the backend."""

    def __init__(self, fails: bool = False) -> None:
        self.fails = fails
        self.reads = 0
        self._vats_pending = True

    def vats_pending(self) -> bool:
        return self._vats_pending

    def load_vats(self) -> dict:
        self._vats_pending = False
        return {}

    def get_metadata(self) -> pd.DataFrame:
        self.reads += 1
        if self.fails:
            raise RuntimeError("database is away")
        return electrodes()


class DashboardWindowTest(unittest.TestCase):
    """Builds a real window around a fake backend."""

    def build(self, fails: bool = False) -> tuple[DashboardWindow, FakeBackend]:
        backend = FakeBackend(fails)
        original = window_module.build_backend
        window_module.build_backend = lambda *a, **k: backend
        try:
            window = DashboardWindow()
        finally:
            window_module.build_backend = original
        self.addCleanup(window.close)
        return window, backend


class TestTheDeckRotates(DashboardWindowTest):
    """Three screens, in order, forever."""

    def test_there_is_one_page_per_window(self) -> None:
        window, _backend = self.build()
        self.assertEqual(window.deck.count(), 3)

    def test_advancing_moves_on_and_wraps(self) -> None:
        window, _backend = self.build()
        # Instant, so the test does not wait on an animation.
        window.deck.duration_ms = 1
        for expected in (1, 2, 0):
            window.deck.advance()
            window.deck._on_finished()
            self.assertEqual(window.deck.current_index(), expected)

    def test_the_header_follows_the_deck(self) -> None:
        window, _backend = self.build()
        window.deck.duration_ms = 1
        window.deck.advance()
        window.deck._on_finished()
        self.assertIn("7 DAYS", window.header._title.text())


class TestItSurvivesTheDatabaseGoingAway(DashboardWindowTest):
    """The one failure mode that matters on an unattended screen."""

    def test_a_failed_read_does_not_raise(self) -> None:
        window, backend = self.build(fails=True)
        window._refresh()          # must not propagate
        self.assertGreaterEqual(backend.reads, 1)

    def test_a_failed_first_read_says_so(self) -> None:
        window, _backend = self.build(fails=True)
        window._refresh()
        self.assertIn("No connection", window.header._note)

    def test_the_last_good_figures_stay_on_screen(self) -> None:
        """Numbers that were true an hour ago beat a blank board, as long as
        the header admits how old they are."""
        window, backend = self.build()
        window._refresh()
        # The tile's value rather than its label: the label is mid count-up
        # until the event loop runs, and this is about the figure, not the
        # animation.
        tile = window._pages[0]._tiles["produced"]
        shown = tile.value()
        self.assertGreater(shown, 0)

        backend.fails = True
        window._refresh()
        self.assertEqual(tile.value(), shown)

    def test_the_vats_are_read_once_not_every_refresh(self) -> None:
        window, backend = self.build()
        window._refresh()
        window._refresh()
        self.assertFalse(backend.vats_pending())


class TestTheRefreshWaitsForAStillScreen(DashboardWindowTest):
    """Reading blocks the UI thread, so it never happens mid slide."""

    def test_the_timer_only_marks_it_due(self) -> None:
        window, backend = self.build()
        window._refresh()
        before = backend.reads
        window._mark_due()
        self.assertEqual(backend.reads, before)
        self.assertTrue(window._refresh_due)

    def test_and_the_deck_settling_does_the_read(self) -> None:
        window, backend = self.build()
        window._refresh()
        before = backend.reads
        window._mark_due()
        window._on_settled(0)
        self.assertEqual(backend.reads, before + 1)
        self.assertFalse(window._refresh_due)


if __name__ == "__main__":
    unittest.main()
