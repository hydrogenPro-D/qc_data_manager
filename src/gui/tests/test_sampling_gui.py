"""The QC Sampling tab, fed hand-built frames.

No backend anywhere: the tab is given the same processed frame MainWindow hands
the Electrodes table, which is the whole point of wiring it this way.
"""

import os
import tempfile
import unittest

import pandas as pd
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication, QScrollArea, QTableWidgetItem, QWidget,
)

from src.data_access.electrodes.electrode_columns import (
    ElectrodeColumns,
    ElectrodeStatus,
    STATUS_COLUMN,
    VAT_COLUMN,
)
from src.data_access import app_config
from src.data_access.sampling import current_intervals as ci
from src.gui.sampling_gui import (
    BAD, GAIN_COLUMN, GOOD, MIN_ADVICE_WIDTH, MIN_PLOT_HEIGHT,
    PANEL_MIN_HEIGHT, SPACER_COLUMN, SPACER_WIDTH, QcSamplingWidget,
    _align, _optimum_note, _wide,
)

_app = QApplication.instance() or QApplication([])

G = str(ElectrodeStatus.RELEASED)
B = str(ElectrodeStatus.SCRAPPED)


def frame(rows) -> pd.DataFrame:
    """``(marking number, vat, status)`` tuples, newest first as the DB returns."""
    return pd.DataFrame([
        {str(ElectrodeColumns.MARKING): f"B{number}",
         str(ElectrodeColumns.DATE): "2026-08-27 08:00:00",
         VAT_COLUMN: vat,
         STATUS_COLUMN: status}
        for number, vat, status in reversed(rows)])


def a_vat(vat: str, start: int, pattern: str):
    """One vat's electrodes from a string of g and b."""
    return [(start + index, vat, G if letter == "g" else B)
            for index, letter in enumerate(pattern)]


class _Tab(unittest.TestCase):

    def setUp(self) -> None:
        self._path, self._cache = ci._PATH, ci._cache
        handle, path = tempfile.mkstemp(suffix=".json")
        os.close(handle)
        os.remove(path)
        ci._PATH, ci._cache = path, None

        self.host = QWidget()
        self.tab = QcSamplingWidget()
        self.tab.setParent(self.host)

    def tearDown(self) -> None:
        if os.path.exists(ci._PATH):
            os.remove(ci._PATH)
        ci._PATH, ci._cache = self._path, self._cache

    @staticmethod
    def _rows():
        return (a_vat("28", 14000, "gggggbbbbbbggggggggggbbbggggg")
                + a_vat("30", 14100, "ggggbbbbggggggggbbggggggg"))


class TestItNeedsNoBackend(_Tab):

    def test_it_opens_with_nothing_at_all(self):
        # MainWindow builds it before the electrode read has landed.
        self.assertIsNone(self.tab.frame)
        self.assertEqual(self.tab.table.rowCount(), 0)

    def test_a_frame_fills_the_per_vat_table(self):
        self.tab.load_data(frame(self._rows()))
        self.assertEqual(self.tab.table.rowCount(), 2)
        self.assertEqual(self.tab.table.item(0, 0).text(), "28")
        self.assertEqual(self.tab.table.item(1, 0).text(), "30")

    def test_it_measures_the_runs_it_was_given(self):
        self.tab.load_data(frame(self._rows()))
        vat28 = self.tab._analysis.per_vat[0]
        self.assertEqual(sorted(vat28.bad.lengths), [3, 6])
        self.assertEqual(sorted(vat28.good.lengths), [5, 5, 10])

    def test_the_totals_row_covers_every_vat(self):
        self.tab.load_data(frame(self._rows()))
        self.assertEqual(self.tab.totals.rowCount(), 1)
        self.assertEqual(self.tab._analysis.overall.electrodes,
                         sum(entity.electrodes
                             for entity in self.tab._analysis.per_vat))

    def test_an_empty_frame_is_not_a_crash(self):
        self.tab.load_data(pd.DataFrame())
        self.assertEqual(self.tab.table.rowCount(), 0)

    def test_no_frame_at_all_is_not_a_crash(self):
        self.tab.load_data(None)
        self.assertEqual(self.tab.table.rowCount(), 0)


class TestTheRange(_Tab):

    def _seed_with(self, default, rows=None):
        """Seed the boxes against one configured default."""
        original = app_config.sampling_default_range
        app_config.sampling_default_range = lambda: default
        try:
            self.tab.load_data(frame(rows or a_vat("28", 13000, "gb" * 1200)))
        finally:
            app_config.sampling_default_range = original
        return self.tab.first_input.text(), self.tab.last_input.text()

    def test_it_opens_on_the_configured_default(self):
        self.assertEqual(self._seed_with((13900, app_config.NEWEST)),
                         ("B13900", "B15399"))

    def test_the_oldest_edge_follows_the_data(self):
        self.assertEqual(self._seed_with((app_config.OLDEST, 14000))[0],
                         "B13000")

    def test_both_edges_at_once_is_the_whole_history(self):
        self.assertEqual(
            self._seed_with((app_config.OLDEST, app_config.NEWEST)),
            ("B13000", "B15399"))

    def test_a_default_past_everything_on_record_falls_back(self):
        # A marking written down months ago can end up past the whole table,
        # and an empty tab at startup reads as a failed read.
        self.assertEqual(self._seed_with((99000, app_config.NEWEST)),
                         ("B13000", "B15399"))

    def test_a_default_the_wrong_way_round_falls_back(self):
        self.assertEqual(self._seed_with((14000, 13500)),
                         ("B13000", "B15399"))

    def test_an_unreadable_default_is_not_a_crash(self):
        def boom():
            raise OSError("no config")
        original = app_config.sampling_default_range
        app_config.sampling_default_range = boom
        try:
            self.tab.load_data(frame(a_vat("28", 13000, "gb" * 1200)))
        finally:
            app_config.sampling_default_range = original
        self.assertEqual(self.tab.first_input.text(), "B13000")

    def test_a_reload_does_not_overwrite_a_typed_range(self):
        rows = self._rows()
        self.tab.load_data(frame(rows))
        self.tab.first_input.setText("B14005")
        self.tab.last_input.setText("B14015")
        self.tab.load_data(frame(rows))
        self.assertEqual(self.tab.first_input.text(), "B14005")

    def test_whole_history_widens_it_again(self):
        self.tab.load_data(frame(a_vat("28", 20000, "gb" * 1200)))
        self.tab._reset_range()
        self.assertEqual(self.tab.first_input.text(), "B20000")

    def test_a_range_that_is_not_a_marking_is_ignored_on_a_reload(self):
        # No dialog on a reload: an empty box is somebody mid-thought.
        self.tab.load_data(frame(self._rows()))
        self.tab.first_input.setText("")
        self.tab.run(quiet=True)
        self.assertEqual(self.tab.table.rowCount(), 2)


class TestItRidesTheElectrodesReload(_Tab):

    def test_an_external_update_reloads_from_the_new_frame(self):
        self.tab.load_data(frame(self._rows()))
        changed = a_vat("28", 14000, "b" * 29) + a_vat("30", 14100, "g" * 25)
        self.assertTrue(self.tab.apply_external_updates(
            {"data": frame(changed)}))
        self.assertEqual(self.tab._analysis.per_vat[0].bad.lengths, [29])

    def test_an_update_carrying_nothing_changes_nothing(self):
        self.tab.load_data(frame(self._rows()))
        before = self.tab._analysis.per_vat[0].bad.lengths
        self.assertFalse(self.tab.apply_external_updates({"data": None}))
        self.assertEqual(self.tab._analysis.per_vat[0].bad.lengths, before)


class TestTheIntervalPanel(_Tab):

    def test_every_vat_gets_a_row_whatever_the_range_measured(self):
        self.tab.load_data(frame(self._rows()))
        panel = self.tab._interval_panels[0]
        self.assertEqual(list(panel["rows"]), ["28", "29", "30"])

    def test_a_vat_the_range_says_nothing_about_stays_silent(self):
        self.tab.load_data(frame(self._rows()))
        panel = self.tab._interval_panels[0]
        self.assertEqual(panel["rows"]["29"]["note"].text(), "")

    def test_what_it_says_about_the_optimum(self):
        self.assertEqual(_optimum_note(12, 15),
                         "Optimal interval is 3 electrodes less frequent.")


if __name__ == "__main__":
    unittest.main()


class TestWhenItMustNotBeReloaded(_Tab):
    """A reload rebuilds every table, so it has to wait for an edit to finish."""

    def setUp(self) -> None:
        super().setUp()
        self.host.resize(1400, 900)
        self.host.show()
        self.host.activateWindow()
        self.host.raise_()
        self.tab.load_data(frame(self._rows()))
        _app.processEvents()

    def tearDown(self) -> None:
        self.host.hide()
        super().tearDown()

    def test_it_is_free_when_nobody_is_touching_it(self):
        self.assertFalse(self.tab.is_busy())

    def test_the_caret_sitting_in_a_box_is_not_work_in_progress(self):
        # Qt puts it there when the tab is shown. Treating that as busy held
        # every reload off for the life of the window.
        self.tab.first_input.setFocus()
        _app.processEvents()
        self.assertFalse(self.tab.is_busy())

    def test_a_half_typed_range_holds_a_reload_off(self):
        self.tab.first_input.setFocus()
        self.tab.first_input.setModified(True)
        _app.processEvents()
        self.assertTrue(self.tab.is_busy())

    def test_measuring_the_range_frees_it_again(self):
        self.tab.first_input.setFocus()
        self.tab.first_input.setModified(True)
        self.tab.run()
        self.assertFalse(self.tab.is_busy())

    def test_a_slider_mid_drag_holds_a_reload_off(self):
        self.tab.efficiency_slider.setSliderDown(True)
        try:
            self.assertTrue(self.tab.is_busy())
        finally:
            self.tab.efficiency_slider.setSliderDown(False)


class TestTheWidthTheColumnsActuallyTake(unittest.TestCase):
    """The per vat table had a horizontal scrollbar that would not go away.

    _align sized the table from the widths it ASKED for, but Qt clamps a
    section to the header's minimum, 32px on Windows, and the blank divider
    column is narrower than that. The columns ended up six pixels past the
    width the table had been given, and the convergence pass could not see it
    because it was comparing its own intention with itself.
    """

    HEADERS = ("Vat", "Electrodes", "Bad", "Runs", "Avg", "Median", "Std dev",
               "Bad %", "", "Good", "Runs", "Avg", "Median", "Std dev",
               "Good %")

    def setUp(self) -> None:
        self.host = QWidget()
        self.host.resize(3000, 300)
        self.table = _wide(self.HEADERS, parent=self.host)
        self.totals = _wide(self.HEADERS, parent=self.host)
        self.totals.horizontalHeader().setVisible(False)
        for table, first in ((self.table, "28"), (self.totals, "All vats")):
            table.setRowCount(1)
            table.setItem(0, 0, QTableWidgetItem(first))
            for column in range(1, len(self.HEADERS)):
                table.setItem(0, column, QTableWidgetItem("12.3 %"))
            table.resize(self.host.width(), 80)
        self.host.show()
        _app.processEvents()
        self.total = _align(self.table, self.totals)

    def tearDown(self) -> None:
        self.host.hide()

    def _actual(self) -> int:
        return sum(self.table.columnWidth(column)
                   for column in range(self.table.columnCount()))

    def test_the_divider_may_be_narrower_than_a_section_minimum(self):
        self.assertLessEqual(
            self.table.horizontalHeader().minimumSectionSize(), SPACER_WIDTH)

    def test_the_divider_keeps_the_width_it_was_given(self):
        self.assertEqual(self.table.columnWidth(SPACER_COLUMN), SPACER_WIDTH)

    def test_it_reports_what_the_columns_take_not_what_was_asked_for(self):
        self.assertEqual(self.total, self._actual())

    def test_the_table_is_wide_enough_for_its_own_columns(self):
        self.table.resize(self.table.maximumWidth(), self.table.height())
        _app.processEvents()
        self.assertGreaterEqual(self.table.viewport().width(), self._actual())


class TestTheGainColumnIsColouredBySign(_Tab):
    """The one column that can be a loss, so the one worth colouring."""

    # Streaks of mixed length, so the table spans both signs: the loosest
    # rates catch the long ones and pay, the tightest chase the stubs and
    # cost more than they save.
    PATTERN = ("b" + "g" * 12 + "b" + "g" * 12 + "bb" + "g" * 12
               + "bb" + "g" * 12 + "bbb" + "g" * 12 + "b" * 5 + "g" * 12
               + "b" * 9 + "g" * 12 + "b" * 20 + "g" * 12 + "b" * 40
               + "g" * 120)

    def setUp(self) -> None:
        super().setUp()
        self.tab.load_data(frame(a_vat("28", 14000, self.PATTERN)))
        self.table = self.tab._vat_pages["Vat 28"][0]

    def _gains(self):
        """(gain text, colour) down the Gain column."""
        out = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, GAIN_COLUMN)
            if item is not None:
                out.append((item.text(), item.foreground().color().name()))
        return out

    def test_a_gain_is_green(self):
        ahead = [colour for text, colour in self._gains()
                 if text.startswith("+")]
        self.assertTrue(ahead)
        self.assertTrue(all(colour.lower() == GOOD.lower() for colour in ahead))

    def test_a_loss_is_red(self):
        behind = [colour for text, colour in self._gains()
                  if text.startswith("-")]
        self.assertTrue(behind, "no losing rate in this fixture")
        self.assertTrue(all(colour.lower() == BAD.lower() for colour in behind))

    def test_the_other_columns_are_left_alone(self):
        # Colouring every figure would make the one that matters ordinary.
        item = self.table.item(0, 2)
        self.assertNotEqual(item.foreground().color().name().lower(),
                            BAD.lower())


class TestTheAdviceTableRows(_Tab):
    """What the bold means, and no row saying the same thing twice."""

    PATTERN = TestTheGainColumnIsColouredBySign.PATTERN

    def setUp(self) -> None:
        super().setUp()
        self.tab.load_data(frame(a_vat("28", 14000, self.PATTERN)
                                 + a_vat("30", 20000, self.PATTERN)))

    def _table(self, name: str):
        return self.tab._vat_pages[name][0]

    @staticmethod
    def _intervals(table):
        return [table.item(row, 1).text() for row in range(table.rowCount())]

    @staticmethod
    def _bold_rows(table):
        return [row for row in range(table.rowCount())
                if table.item(row, GAIN_COLUMN).font().bold()]

    def test_the_pooled_table_does_not_bold_everything(self):
        # It used to, from when it sat under the per vat tables in one column.
        table = self._table("All vats")
        self.assertLess(len(self._bold_rows(table)), table.rowCount())

    def test_bold_means_the_same_on_the_pooled_table_as_on_a_vat(self):
        for name in ("Vat 28", "All vats"):
            table = self._table(name)
            bold = self._bold_rows(table)
            self.assertTrue(bold, f"{name} marks no acceptable rate")
            self.assertLess(len(bold), table.rowCount(), name)

    def test_no_interval_is_listed_twice(self):
        # Two adjacent catch rates often need the same interval, and with the
        # catch rate column hidden the rows were identical figures twice over.
        for name in ("Vat 28", "Vat 30", "All vats"):
            intervals = self._intervals(self._table(name))
            self.assertEqual(len(intervals), len(set(intervals)), name)

    def test_the_rows_still_run_loosest_to_tightest(self):
        for name in ("Vat 28", "All vats"):
            numbers = [int(text.split()[0])
                       for text in self._intervals(self._table(name))]
            self.assertEqual(numbers, sorted(numbers, reverse=True), name)


class TestSwitchingBetweenVats(_Tab):
    """The strip is nested inside a page that already fades on the way in."""

    def setUp(self) -> None:
        super().setUp()
        self.tab.load_data(frame(self._rows()))

    def test_the_vat_strip_does_not_crossfade(self):
        # Every page holds a plot that costs milliseconds to repaint, and a
        # dissolve repaints it on every frame of the animation.
        self.assertEqual(self.tab.vat_tabs.fade_ms, 0)

    def test_switching_takes_no_photograph(self):
        # The photograph is only for the dissolve, and it is a full grab of
        # the page being left.
        tabs = self.tab.vat_tabs
        tabs._photograph_current(1)
        self.assertIsNone(tabs._overlay)

    def test_switching_leaves_nothing_on_screen(self):
        tabs = self.tab.vat_tabs
        for index in (1, 2, 0, 1):
            tabs._photograph_current(index)
            tabs.setCurrentIndex(index)
        self.assertTrue(tabs._overlay is None or not tabs._overlay.isVisible())

    def test_every_vat_is_still_reachable(self):
        tabs = self.tab.vat_tabs
        for index in range(tabs.count()):
            tabs.setCurrentIndex(index)
            self.assertEqual(tabs.currentIndex(), index)


class TestTheWindowCanBeMadeSmaller(_Tab):
    """The recommendation table used to be a floor under the whole window."""

    def setUp(self) -> None:
        super().setUp()
        self.tab.load_data(frame(self._rows()))
        self.table = self.tab._vat_pages["Vat 28"][0]

    def test_it_opens_at_the_width_of_its_columns(self):
        # QTableWidget's own sizeHint is a flat 256x192 with nothing to do with
        # its contents, which is what a squeezable table gets handed unless it
        # asks for something better.
        columns = sum(self.table.columnWidth(column)
                      for column in range(self.table.columnCount()))
        self.assertGreaterEqual(self.table.sizeHint().width(), columns)

    def test_it_opens_at_the_height_of_its_rows(self):
        self.assertGreater(self.table.sizeHint().height(),
                           self.table.minimumHeight())

    def test_it_can_be_squeezed_horizontally(self):
        self.assertLess(self.table.minimumWidth(),
                        self.table.maximumWidth())
        self.assertLessEqual(self.table.minimumWidth(), MIN_ADVICE_WIDTH)

    def test_it_can_be_squeezed_vertically(self):
        self.assertLess(self.table.minimumHeight(),
                        self.table.maximumHeight())

    def test_it_scrolls_rather_than_hiding_figures(self):
        self.table.resize(self.table.minimumWidth(),
                          self.table.minimumHeight())
        _app.processEvents()
        self.assertEqual(self.table.horizontalScrollBarPolicy(),
                         Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.assertEqual(self.table.verticalScrollBarPolicy(),
                         Qt.ScrollBarPolicy.ScrollBarAsNeeded)

    def test_the_plot_keeps_a_floor_on_its_height_only(self):
        # A floor on its width put the window's minimum back where it started.
        plot = self.tab._vat_pages["Vat 28"][1].widget
        self.assertEqual(plot.minimumHeight(), MIN_PLOT_HEIGHT)
        self.assertEqual(plot.minimumWidth(), 0)


class TestTheTabCanBeMadeShorter(_Tab):
    """Three things held the window's height open, and each hid the next."""

    def setUp(self) -> None:
        super().setUp()
        self.tab.load_data(frame(self._rows()))

    def test_the_panel_scrolls_rather_than_propping_the_window_open(self):
        panel = self.tab._interval_panels[0]
        area = panel["frame"].findChild(QScrollArea)
        self.assertIsNotNone(area, "the panel does not scroll")
        self.assertTrue(area.widgetResizable())
        self.assertIs(area.widget(), panel["inner"])

    def test_the_panel_keeps_a_floor_worth_having(self):
        frame_ = self.tab._interval_panels[0]["frame"]
        self.assertEqual(frame_.minimumHeight(), PANEL_MIN_HEIGHT)
        # Its own contents are taller than that, which is the whole point.
        self.assertGreater(self.tab._interval_panels[0]["inner"].sizeHint().height(),
                           PANEL_MIN_HEIGHT)

    def test_the_summary_table_gives_way_too(self):
        self.assertLess(self.tab.table.minimumHeight(),
                        self.tab.table.maximumHeight())

    def test_the_summary_table_always_keeps_a_row(self):
        # least=1: the header and one vat, so it never collapses to nothing.
        self.assertGreater(self.tab.table.minimumHeight(),
                           self.tab.table.horizontalHeader().height())

    def test_the_plot_floor_is_low_enough_to_squash_to(self):
        plot = self.tab._vat_pages["Vat 28"][1].widget
        self.assertEqual(plot.minimumHeight(), MIN_PLOT_HEIGHT)
        self.assertLess(MIN_PLOT_HEIGHT, 200)
