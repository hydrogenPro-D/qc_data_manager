"""The run measurement, checked on hand-built sequences.

Every number the tool reports comes from ``runs_in``, so that is where the
tests concentrate: a stretch counted wrongly here would put a wrong sampling
rate in front of somebody's boss.
"""

import unittest

import pandas as pd

from src.data_access.electrodes.electrode_columns import (
    ElectrodeColumns,
    ElectrodeStatus,
    STATUS_COLUMN,
    VAT_COLUMN,
)
from qc_sampling_script.data import run_lengths as rl

G = str(ElectrodeStatus.RELEASED)
GT = str(ElectrodeStatus.TESTED_AND_RELEASED)
B = str(ElectrodeStatus.SCRAPPED)
BT = str(ElectrodeStatus.TESTED_AND_SCRAPPED)
U = str(ElectrodeStatus.NOT_TESTED)


def frame(rows) -> pd.DataFrame:
    """``(marking number, vat, status)`` tuples, newest first as the DB returns."""
    return pd.DataFrame([
        {str(ElectrodeColumns.MARKING): f"B{number}",
         str(ElectrodeColumns.DATE): "2026-08-27 08:00:00",
         str(ElectrodeColumns.FULL_NAME): "CATHODE",
         str(ElectrodeColumns.RECIPE): 11,
         VAT_COLUMN: vat,
         STATUS_COLUMN: status}
        for number, vat, status in reversed(rows)])


class TestWhatCountsAsGoodOrBad(unittest.TestCase):
    """A verdict says something about the process, not just the electrode."""

    def test_a_tested_pass_is_evidence_the_process_was_good(self) -> None:
        """It is destroyed, but the chemistry was right at that point."""
        self.assertEqual(rl.family(GT), rl.GOOD)
        self.assertEqual(rl.family(G), rl.GOOD)

    def test_a_tested_failure_and_a_rejection_are_both_bad(self) -> None:
        self.assertEqual(rl.family(BT), rl.BAD)
        self.assertEqual(rl.family(B), rl.BAD)

    def test_everything_else_says_nothing(self) -> None:
        for status in (U, str(ElectrodeStatus.NEEDS_TESTING),
                       str(ElectrodeStatus.TEST_ONGOING), ""):
            self.assertEqual(rl.family(status), rl.UNKNOWN)


class TestMeasuringTheRuns(unittest.TestCase):
    """``runs_in`` on sequences short enough to check by eye."""

    def lengths(self, families):
        return rl.runs_in(families)

    def test_one_unbroken_stretch_of_each(self) -> None:
        bad, good = self.lengths([rl.GOOD] * 4 + [rl.BAD] * 3)
        self.assertEqual((bad, good), ([3], [4]))

    def test_alternating_stretches_are_counted_separately(self) -> None:
        bad, good = self.lengths([rl.BAD, rl.BAD, rl.GOOD, rl.BAD, rl.GOOD])
        self.assertEqual(bad, [2, 1])
        self.assertEqual(good, [1, 1])

    def test_an_undecided_electrode_is_stepped_over(self) -> None:
        """It is a gap in what has been looked at, not evidence the chemistry
        changed there. Ending the run at one chopped genuine runs into short
        pieces, and short runs are what drive the testing rate down."""
        bad, _good = self.lengths([rl.BAD, rl.BAD, rl.UNKNOWN, rl.BAD])
        self.assertEqual(bad, [3])

    def test_it_does_not_join_two_different_verdicts_though(self) -> None:
        bad, good = self.lengths([rl.BAD, rl.UNKNOWN, rl.GOOD])
        self.assertEqual((bad, good), ([1], [1]))

    def test_nothing_decided_is_no_runs_rather_than_an_error(self) -> None:
        self.assertEqual(self.lengths([rl.UNKNOWN] * 5), ([], []))

    def test_an_empty_sequence(self) -> None:
        self.assertEqual(self.lengths([]), ([], []))


class TestTheStatistics(unittest.TestCase):
    """Mean, median and spread over the measured runs."""

    def test_the_usual_figures(self) -> None:
        run = rl.RunStats([2, 4, 6])
        self.assertEqual(run.count, 3)
        self.assertEqual(run.total, 12)
        self.assertEqual(run.mean, 4.0)
        self.assertEqual(run.median, 4)
        self.assertEqual(run.shortest, 2)
        self.assertEqual(run.longest, 6)
        self.assertAlmostEqual(run.stdev, 2.0)

    def test_a_single_run_has_no_spread(self) -> None:
        self.assertEqual(rl.RunStats([5]).stdev, 0.0)

    def test_no_runs_reads_as_zero_not_an_error(self) -> None:
        run = rl.RunStats([])
        self.assertEqual((run.count, run.mean, run.median, run.stdev),
                         (0, 0.0, 0.0, 0.0))


class TestTheWholeAnalysis(unittest.TestCase):
    """End to end, over a frame shaped like a real read."""

    def build(self) -> rl.Analysis:
        # Two vats interleaved on the line, which is the case the per vat
        # walk exists for: read in plant order these runs would be shredded.
        rows = []
        for number in range(100, 120):
            vat = "28" if number % 2 == 0 else "29"
            bad_stretch = 104 <= number <= 111
            rows.append((number, vat, B if bad_stretch else G))
        return rl.analyse(frame(rows), 100, 119)

    def test_the_range_is_inclusive_and_order_does_not_matter(self) -> None:
        df = frame([(n, "28", G) for n in range(100, 111)])
        self.assertEqual(rl.analyse(df, 102, 105).matched, 4)
        self.assertEqual(rl.analyse(df, 105, 102).matched, 4)

    def test_each_vat_is_walked_in_its_own_sequence(self) -> None:
        """Vat 1 has four bad electrodes in a row of ITS OWN, even though the
        plant sequence puts vat 2's electrodes between them."""
        by_vat = {row.vat: row for row in self.build().per_vat}
        self.assertEqual(by_vat["28"].bad.lengths, [4])
        self.assertEqual(by_vat["29"].bad.lengths, [4])

    def test_the_combined_figure_pools_the_same_runs(self) -> None:
        analysis = self.build()
        pooled = sorted(analysis.overall.bad.lengths)
        from_vats = sorted(length for row in analysis.per_vat
                           for length in row.bad.lengths)
        self.assertEqual(pooled, from_vats)

    def test_only_the_vats_in_service_are_measured(self) -> None:
        """A retired vat, a blank entry and a row naming two vats are all
        dropped before anything is counted, so the pooled figures on the right
        of the window are the same electrodes as the rows on the left."""
        analysis = rl.analyse(frame(
            [(100, "28", B), (101, "28", B),
             (102, "", B), (103, "12", B), (104, "28, 30", B)]), 100, 104)
        self.assertEqual([row.vat for row in analysis.per_vat], ["28"])
        self.assertEqual(analysis.matched, 2)
        self.assertEqual(analysis.excluded, 3)
        self.assertEqual(analysis.overall.bad.lengths, [2])

    def test_a_range_holding_none_of_the_working_vats(self) -> None:
        analysis = rl.analyse(frame([(n, "12", B) for n in range(100, 105)]),
                              100, 104)
        self.assertTrue(analysis.empty)
        self.assertEqual(analysis.excluded, 5)

    def test_the_vats_are_listed_in_their_own_order(self) -> None:
        """28, 29, 30 every time. Ordering by how bad each looked reshuffled
        the rows on every change of range, which makes two runs of the tool
        tedious to compare."""
        rows = rl.analyse(frame(
            [(100, "30", G), (101, "29", B), (102, "28", G)]), 100, 102).per_vat
        self.assertEqual([row.vat for row in rows], ["28", "29", "30"])

    def test_only_the_four_verdicts_are_counted(self) -> None:
        """An electrode nobody has ruled on says nothing about the chemistry,
        so it is not in the total, not in a run, and not in a share."""
        analysis = rl.analyse(frame(
            [(100, "28", G), (101, "28", G), (102, "28", B),
             (103, "28", U), (104, "28", U)]), 100, 104)
        row = analysis.per_vat[0]
        self.assertEqual(row.electrodes, 3)
        self.assertEqual((row.good.total, row.bad.total), (2, 1))
        self.assertEqual(row.electrodes, row.bad.total + row.good.total)
        # Counted behind the scenes, so a mostly untested range can be spotted.
        self.assertEqual(analysis.skipped, 2)
        self.assertEqual(analysis.matched, 3)

    def test_the_two_shares_add_up_to_a_hundred(self) -> None:
        row = rl.analyse(frame(
            [(100, "28", G), (101, "28", G), (102, "28", G), (103, "28", B)]),
            100, 103).per_vat[0]
        self.assertEqual(row.bad_share, 25.0)
        self.assertEqual(row.good_share, 75.0)

    def test_a_backlog_does_not_move_the_shares(self) -> None:
        """Eighteen untested electrodes beside one good and one bad still
        reads 50/50: they are not evidence either way."""
        row = rl.analyse(frame(
            [(100, "28", G), (101, "28", B)] + [(n, "28", U)
                                                for n in range(102, 120)]),
            100, 119).per_vat[0]
        self.assertEqual(row.bad_share, 50.0)
        self.assertEqual(row.good_share, 50.0)

    def test_a_range_with_nothing_decided_in_it(self) -> None:
        analysis = rl.analyse(frame([(n, "28", U) for n in range(100, 105)]),
                              100, 104)
        self.assertTrue(analysis.empty)
        self.assertEqual(analysis.skipped, 5)
        self.assertEqual(analysis.skipped_share, 100.0)

    def test_a_range_that_matches_nothing(self) -> None:
        analysis = rl.analyse(frame([(100, "28", G)]), 5000, 6000)
        self.assertTrue(analysis.empty)
        self.assertEqual(analysis.overall.bad.count, 0)

    def test_no_data_at_all(self) -> None:
        self.assertTrue(rl.analyse(None, 1, 100).empty)


class TestWhatItMeansForTheTestingRate(unittest.TestCase):
    """The bridge from measured runs to a sampling interval."""

    def test_a_gap_no_wider_than_the_run_always_catches_it(self) -> None:
        self.assertEqual(rl.detection([10, 10, 10], 10), 1.0)
        self.assertEqual(rl.detection([10, 10, 10], 5), 1.0)

    def test_a_wider_gap_catches_proportionally_fewer(self) -> None:
        self.assertAlmostEqual(rl.detection([10], 20), 0.5)
        self.assertAlmostEqual(rl.detection([10], 40), 0.25)

    def test_the_short_runs_are_what_set_the_rate(self) -> None:
        """One short run among long ones drags the achievable gap right
        down, which is the whole reason the distribution matters."""
        wide = rl.spacing_for([100, 100, 100], 0.9)
        narrow = rl.spacing_for([100, 100, 4], 0.9)
        self.assertLess(narrow, wide)

    def test_the_widest_gap_that_still_hits_the_target(self) -> None:
        spacing = rl.spacing_for([20, 20, 20], 0.8)
        self.assertGreaterEqual(rl.detection([20, 20, 20], spacing), 0.8)
        self.assertLess(rl.detection([20, 20, 20], spacing + 1), 0.8)

    def test_the_table_costs_each_target(self) -> None:
        rows = rl.sampling_table([30, 30, 30], targets=(0.5,))
        _target, spacing, cost = rows[0]
        self.assertEqual(spacing, 60)
        self.assertAlmostEqual(cost, 100 / 60)

    def test_no_runs_gives_no_recommendation_rather_than_a_wrong_one(self) -> None:
        self.assertEqual(rl.spacing_for([], 0.9), 0)


if __name__ == "__main__":
    unittest.main()
