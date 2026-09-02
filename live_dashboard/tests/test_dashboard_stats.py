"""The figures the wall screen shows, checked without opening a window.

This matters more here than in the app: nobody sits in front of the office
screen to notice a number going wrong, so the arithmetic has to be pinned down
somewhere that shouts when it moves.

Pure data in, data out. No Qt, no database, and ``now`` is injected so nothing
here depends on when it is run.
"""

import datetime as dt
import unittest

import pandas as pd

from src.data_access.electrodes.electrode_columns import (
    ElectrodeColumns,
    ElectrodeStatus,
    STATUS_COLUMN,
    VAT_COLUMN,
)
from live_dashboard.data import dashboard_stats as stats

NOW = dt.datetime(2026, 8, 31, 12, 0, tzinfo=dt.timezone.utc)


def frame(rows) -> pd.DataFrame:
    """``(hours ago, status, vat, kind)`` tuples as a processed electrode frame."""
    return pd.DataFrame([
        {str(ElectrodeColumns.DATE): (NOW - dt.timedelta(hours=ago)).isoformat(),
         str(ElectrodeColumns.MARKING): f"B{9000 + index}",
         str(ElectrodeColumns.FULL_NAME): kind,
         str(ElectrodeColumns.RECIPE): 11,
         VAT_COLUMN: vat,
         STATUS_COLUMN: str(status)}
        for index, (ago, status, vat, kind) in enumerate(rows)])


class TestTheWindow(unittest.TestCase):
    """Which rows each window takes in."""

    def test_only_rows_inside_the_span(self) -> None:
        df = frame([(1, ElectrodeStatus.RELEASED, "1", "CATHODE"),
                    (25, ElectrodeStatus.RELEASED, "1", "CATHODE"),
                    (24 * 6, ElectrodeStatus.RELEASED, "1", "CATHODE")])
        self.assertEqual(len(stats.window_frame(df, 1, NOW)), 1)
        self.assertEqual(len(stats.window_frame(df, 7, NOW)), 3)

    def test_an_undated_row_is_in_no_window(self) -> None:
        """It cannot be placed in time, and guessing would put it in the 24
        hour window, which is the one people act on."""
        df = frame([(1, ElectrodeStatus.RELEASED, "1", "CATHODE")])
        df.loc[1] = {**df.iloc[0].to_dict(), str(ElectrodeColumns.DATE): ""}
        self.assertEqual(len(stats.window_frame(df, 30, NOW)), 1)

    def test_no_rows_at_all_is_not_an_error(self) -> None:
        empty = stats.summarise(None, "Last 24 hours", 1, now=NOW)
        self.assertEqual(empty.produced, 0)
        self.assertEqual(empty.pass_rate, 0.0)
        self.assertEqual(empty.fault_rate, 0.0)
        # The chart still gets its full span, so the card draws an empty axis
        # rather than nothing at all.
        self.assertEqual(len(empty.series), 24)


class TestTheBlocks(unittest.TestCase):
    """The production and QC blocks, which have to add up on screen."""

    def summary(self) -> stats.WindowStats:
        return stats.summarise(frame([
            (1, ElectrodeStatus.RELEASED, "1", "CATHODE"),
            (2, ElectrodeStatus.RELEASED, "1", "CATHODE"),
            (3, ElectrodeStatus.SCRAPPED, "2", "ANODE"),
            (4, ElectrodeStatus.TESTED_AND_RELEASED, "2", "CATHODE"),
            (5, ElectrodeStatus.TESTED_AND_SCRAPPED, "2", "ANODE"),
            (6, ElectrodeStatus.NEEDS_TESTING, "3", "CATHODE"),
            (7, ElectrodeStatus.NOT_TESTED, "3", "CATHODE"),
            (8, ElectrodeStatus.TEST_ONGOING, "", "ANODE"),
        ]), "Last 24 hours", 1, now=NOW)

    def test_the_indented_rows_add_up_to_the_total(self) -> None:
        """The whole point of the block: a reader can see nothing was dropped."""
        rows = self.summary().production
        total = next(count for label, count, depth in rows if depth == 0)
        self.assertEqual(sum(count for _l, count, depth in rows if depth == 1),
                         total)

    def test_the_breakdown_adds_up_to_the_row_above_it(self) -> None:
        by_label = {label: count for label, count, _d in self.summary().production}
        self.assertEqual(by_label[stats.SCRAP_FAULTS] + by_label[stats.SCRAP_TESTING],
                         by_label[stats.SCRAPPED_TOTAL])

    def test_a_tested_electrode_is_scrapped_whichever_way_it_went(self) -> None:
        """Testing destroys it, so it can never be sold."""
        by_label = {label: count for label, count, _d in self.summary().production}
        self.assertEqual(by_label[stats.SCRAPPED_TOTAL], 3)   # 1 rejected + 2 tested

    def test_the_qc_block_counts_only_what_was_tested(self) -> None:
        summary = self.summary()
        self.assertEqual(summary.tested, 2)
        self.assertEqual(summary.passed, 1)
        self.assertEqual(summary.failed, 1)
        self.assertEqual(summary.pass_rate, 50.0)

    def test_a_status_at_zero_is_still_listed(self) -> None:
        """A category vanishing from the legend reads as a broken panel."""
        counts = stats.summarise(frame([
            (1, ElectrodeStatus.RELEASED, "1", "CATHODE")]),
            "Last 24 hours", 1, now=NOW).status_counts
        self.assertEqual(set(counts), {str(s) for s in ElectrodeStatus})
        self.assertEqual(counts[str(ElectrodeStatus.SCRAPPED)], 0)


class TestTheVatLeague(unittest.TestCase):
    """Which vat to go and look at, worst first."""

    def league(self):
        return stats.summarise(frame(
            [(1, ElectrodeStatus.SCRAPPED, "7", "CATHODE")] * 3
            + [(1, ElectrodeStatus.RELEASED, "7", "CATHODE")] * 7
            + [(1, ElectrodeStatus.SCRAPPED, "2", "CATHODE")]
            + [(1, ElectrodeStatus.RELEASED, "2", "CATHODE")] * 99
            + [(1, ElectrodeStatus.RELEASED, "", "CATHODE")] * 4),
            "Last 24 hours", 1, now=NOW).vats

    def test_worst_rate_first(self) -> None:
        rows = self.league()
        self.assertEqual(rows[0].vat, "7")
        self.assertAlmostEqual(rows[0].fault_rate, 30.0)

    def test_a_tested_failure_counts_as_a_fault(self) -> None:
        rows = stats.summarise(frame([
            (1, ElectrodeStatus.TESTED_AND_SCRAPPED, "4", "CATHODE"),
            (1, ElectrodeStatus.RELEASED, "4", "CATHODE")]),
            "Last 24 hours", 1, now=NOW).vats
        self.assertEqual(rows[0].faults, 1)

    def test_electrodes_with_no_vat_are_grouped_not_dropped(self) -> None:
        """A batch nobody can attribute is itself worth seeing."""
        rows = {row.vat: row for row in self.league()}
        self.assertIn(stats.NO_VAT, rows)
        self.assertEqual(rows[stats.NO_VAT].produced, 4)

    def test_the_list_is_capped(self) -> None:
        rows = stats.summarise(frame([
            (1, ElectrodeStatus.RELEASED, str(vat), "CATHODE")
            for vat in range(20)]), "Last 24 hours", 1, vat_limit=6, now=NOW).vats
        self.assertEqual(len(rows), 6)


class TestTheThroughputSeries(unittest.TestCase):
    """Hourly on the 24 hour window, daily on the others."""

    def test_twenty_four_hourly_buckets_on_the_day_window(self) -> None:
        """A single day drawn as one bar is a rectangle that says nothing."""
        summary = stats.summarise(frame([
            (1, ElectrodeStatus.RELEASED, "1", "CATHODE")]),
            "Last 24 hours", 1, now=NOW)
        self.assertEqual(summary.bucket, "hour")
        self.assertEqual(len(summary.series), 24)
        self.assertEqual(summary.series[-1].label, "12")

    def test_one_bucket_per_day_on_the_longer_windows(self) -> None:
        summary = stats.summarise(frame([
            (1, ElectrodeStatus.RELEASED, "1", "CATHODE")]),
            "Last 7 days", 7, now=NOW)
        self.assertEqual(summary.bucket, "day")
        self.assertEqual(len(summary.series), 7)

    def test_an_empty_step_is_present_rather_than_skipped(self) -> None:
        """A chart that skips a quiet night shift shows a flat stretch where
        there was a stoppage."""
        summary = stats.summarise(frame([
            (1, ElectrodeStatus.RELEASED, "1", "CATHODE")]),
            "Last 7 days", 7, now=NOW)
        self.assertEqual(sum(row.total for row in summary.series), 1)
        self.assertEqual(sum(1 for row in summary.series if row.total == 0), 6)

    def test_cathodes_and_anodes_are_split(self) -> None:
        summary = stats.summarise(frame([
            (1, ElectrodeStatus.RELEASED, "1", "CATHODE"),
            (1, ElectrodeStatus.RELEASED, "1", "ANODE"),
            (1, ElectrodeStatus.RELEASED, "1", "ANODE")]),
            "Last 24 hours", 1, now=NOW)
        # Looked up by label rather than taken as the last bucket: the rows
        # are an hour old, so they belong to 11:00, not to the 12:00 bucket
        # the series ends on.
        bucket = next(row for row in summary.series if row.label == "11")
        self.assertEqual((bucket.cathodes, bucket.anodes), (1, 2))


class TestTheWordingMatchesTheReport(unittest.TestCase):
    """The screen and the printed report must call things the same thing.

    The stats module cannot import the report's constants, since those live in
    a Qt module and this layer must stay free of it, so the two are separate
    copies. This is what stops them drifting apart.
    """

    def test_the_block_labels_are_the_report_labels(self) -> None:
        from src.gui import electrodes_gui as report

        self.assertEqual(stats.ALL_PRODUCED, report.ALL_PRODUCED)
        self.assertEqual(stats.SCRAPPED_TOTAL, report.SCRAPPED_TOTAL)
        self.assertEqual(stats.SCRAP_FAULTS, report.SCRAP_FAULTS)
        self.assertEqual(stats.SCRAP_TESTING, report.SCRAP_TESTING)
        self.assertEqual(stats.ALL_TESTED, report.ALL_TESTED)

    def test_there_is_one_window_per_screen_of_the_deck(self) -> None:
        self.assertEqual(len(stats.WINDOWS), 3)
        self.assertEqual([days for _title, days in stats.WINDOWS], [1, 7, 30])


if __name__ == "__main__":
    unittest.main()
