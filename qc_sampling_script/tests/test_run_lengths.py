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


class TestBridgingSmallGaps(unittest.TestCase):
    """Two bad runs joining when few enough good electrodes separate them.

    Off by default, and deliberately so: merging lengthens bad runs, longer
    runs are easier to catch, and the tool then asks for LESS testing. It is
    the one direction this tool can be wrong in expensively.
    """

    def runs(self, families, gap=0):
        return rl.runs_in(families, gap)

    def test_nothing_is_merged_at_zero(self) -> None:
        bad, good = self.runs([rl.BAD] * 3 + [rl.GOOD] + [rl.BAD] * 2)
        self.assertEqual((bad, good), ([3, 2], [1]))

    def test_one_good_between_two_bad_runs_joins_them(self) -> None:
        """And is absorbed: the merged run spans the whole zone, the anomaly
        in the middle included, because that is what the merge claims."""
        bad, good = self.runs([rl.BAD] * 3 + [rl.GOOD] + [rl.BAD] * 2, gap=1)
        self.assertEqual(bad, [6])
        self.assertEqual(good, [])

    def test_a_gap_wider_than_the_tolerance_still_splits(self) -> None:
        bad, good = self.runs([rl.BAD] * 3 + [rl.GOOD] * 2 + [rl.BAD] * 2, gap=1)
        self.assertEqual((bad, good), ([3, 2], [2]))

    def test_the_merge_cascades(self) -> None:
        """Three stretches and two small gaps become one run, not two."""
        bad, _good = self.runs(
            [rl.BAD] * 2 + [rl.GOOD] + [rl.BAD] * 2 + [rl.GOOD] + [rl.BAD] * 2,
            gap=1)
        self.assertEqual(bad, [8])

    def test_a_good_run_on_the_edge_is_never_absorbed(self) -> None:
        """Only a gap BETWEEN two bad runs is a gap. One at either end is the
        process being fine before or after, and belongs to the good runs."""
        bad, good = self.runs([rl.GOOD] + [rl.BAD] * 3 + [rl.GOOD], gap=2)
        self.assertEqual((bad, good), ([3], [1, 1]))

    def test_merging_makes_the_recommended_gap_wider(self) -> None:
        """The whole reason it is off by default, stated as a test."""
        split = [rl.BAD] * 4 + [rl.GOOD] + [rl.BAD] * 4
        loose = rl.interval_for(self.runs(split, gap=1)[0], 0.9)
        tight = rl.interval_for(self.runs(split, gap=0)[0], 0.9)
        self.assertGreater(loose, tight)

    def test_it_reaches_through_the_whole_analysis(self) -> None:
        rows = [(100 + n, "28", B) for n in range(3)]
        rows += [(103, "28", G)]
        rows += [(104 + n, "28", B) for n in range(3)]
        self.assertEqual(rl.analyse(frame(rows), 100, 106).overall.bad.lengths,
                         [3, 3])
        self.assertEqual(rl.analyse(frame(rows), 100, 106, gap=1)
                         .overall.bad.lengths, [7])


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

    def test_a_vat_with_nothing_decided_is_left_out(self) -> None:
        """A row of zeroes and a tab of nothing help nobody. Vat 29 running
        in the range but with no verdicts yet should simply not appear."""
        rows = rl.analyse(frame(
            [(100, "28", G), (101, "28", B), (102, "29", U), (103, "29", U)]),
            100, 103).per_vat
        self.assertEqual([row.vat for row in rows], ["28"])

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

    def test_a_short_run_among_long_ones_barely_moves_the_rate(self) -> None:
        """Because it is a small share of the bad ELECTRODES.

        Counting runs instead, that one stub of 4 dragged the recommended gap
        from 111 down to 5, demanding twenty times the testing to chase 2% of
        the damage. That is the mistake this weighting exists to fix.
        """
        wide = rl.interval_for([100, 100, 100], 0.9)
        narrow = rl.interval_for([100, 100, 4], 0.9)
        self.assertLess(narrow, wide)
        self.assertGreater(narrow, wide * 0.9)

    def test_a_long_run_is_worth_more_than_a_short_one(self) -> None:
        """Missing a stub of 3 costs three electrodes; missing an excursion of
        25 costs twenty-five, so the long one dominates the figure.

        Counting the two runs equally would have called a gap of 25 a coin
        flip, 56%. Weighted by what is actually at stake it is 93%.
        """
        lengths = [25, 3]
        caught = rl.detection(lengths, 25)
        self.assertAlmostEqual(caught, (25 * 1.0 + 3 * (3 / 25)) / 28)
        self.assertGreater(caught, 0.9)

    def test_a_gap_inside_every_run_catches_every_electrode(self) -> None:
        self.assertEqual(rl.detection([4, 9, 20], 4), 1.0)

    def test_the_widest_gap_that_still_hits_the_target(self) -> None:
        spacing = rl.interval_for([20, 20, 20], 0.8)
        self.assertGreaterEqual(rl.detection([20, 20, 20], spacing), 0.8)
        self.assertLess(rl.detection([20, 20, 20], spacing + 1), 0.8)

    def test_the_table_costs_each_target(self) -> None:
        option = rl.sampling_table([30, 30, 30], targets=(0.5,))[0]
        self.assertEqual(option.sampling_interval, 60)
        self.assertAlmostEqual(option.production_destroyed, 100 / 60)

    def test_the_table_reports_what_the_interval_actually_achieves(self) -> None:
        """The interval is a whole number, so it usually clears the target by
        a little. The worth of a row is figured on what it really catches."""
        option = rl.sampling_table([30, 30, 30], targets=(0.5,))[0]
        self.assertGreaterEqual(option.catch_rate, 0.5)


class TestWhatARateIsWorth(unittest.TestCase):
    """The missing term: how much bad there was to catch in the first place.

    A catch rate on its own cannot say whether testing is worth running.
    Catching 90% of a 2% problem and 90% of a 44% problem are the same row of
    the table and completely different trades.
    """

    def test_the_worked_example(self) -> None:
        """Vat 28: 25.8% bad, testing 1 in 11, catching 90%.

        Per 1000 electrodes, 258 bad exist and 232 are stopped. Testing
        destroys 91, but 24 of those were bad anyway, so 67 good ones are
        actually lost. 232 over 67 is 3.44.
        """
        self.assertAlmostEqual(rl.payback(0.258, 11, 0.90),
                               3.44, places=2)

    def test_only_the_good_electrodes_destroyed_count_as_a_loss(self) -> None:
        """A test lands on a random electrode, so some of what it destroys was
        bad anyway and cost nothing. Dividing by every test understated it."""
        naive = 0.258 * 11 * 0.90
        self.assertGreater(rl.payback(0.258, 11, 0.90), naive)

    def test_a_clean_vat_is_not_worth_testing_hard(self) -> None:
        """2% bad, chased to 90% at 1 in 3: twenty electrodes destroyed for
        each one stopped. The whole reason this term exists."""
        self.assertLess(rl.payback(0.02, 3, 0.90), 0.1)
        option = rl.sampling_table([2, 2, 3], 0.02, targets=(0.9,))[0]
        self.assertFalse(option.worth_it)

    def test_a_dirty_vat_is_worth_testing_hard(self) -> None:
        self.assertGreater(rl.payback(0.441, 12, 0.90), 1.0)

    def test_the_worth_falls_as_the_interval_tightens(self) -> None:
        """The first tests land in the long runs and are hugely profitable;
        every extra one is chasing shorter runs for less."""
        rows = rl.sampling_table([25, 22, 14, 9, 4, 3, 3], 0.258,
                                 (0.5, 0.8, 0.9, 0.95, 0.99))
        values = [row.value for row in rows]
        self.assertEqual(values, sorted(values, reverse=True))

    def test_no_bad_electrodes_at_all_is_worth_nothing(self) -> None:
        self.assertEqual(rl.payback(0.0, 10, 0.9), 0.0)

    def test_a_vat_with_no_good_electrodes_loses_none(self) -> None:
        """Everything it makes is bad, so testing costs no sellable product.
        Not a number, and the table says so rather than printing one."""
        self.assertEqual(rl.payback(1.0, 10, 0.9), float("inf"))


class TestWhatABadElectrodeIsStillWorth(unittest.TestCase):
    """``bad_efficiency``: a bad electrode that still half works is only half
    worth stopping, so the payback falls with it."""

    def test_a_worthless_bad_electrode_is_the_default(self) -> None:
        self.assertEqual(rl.payback(0.258, 11, 0.90),
                         rl.payback(0.258, 11, 0.90, 0.0))

    def test_half_as_good_halves_the_payback(self) -> None:
        full = rl.payback(0.258, 11, 0.90, 0.0)
        half = rl.payback(0.258, 11, 0.90, 0.5)
        self.assertAlmostEqual(half, full / 2)

    def test_a_bad_electrode_as_good_as_a_good_one_is_not_worth_stopping(self) -> None:
        self.assertEqual(rl.payback(0.258, 11, 0.90, 1.0), 0.0)

    def test_it_does_not_change_how_often_to_test(self) -> None:
        """Only what the testing is worth. The interval comes from the run
        lengths, which know nothing about efficiency."""
        lengths = [25, 22, 14, 9, 4, 3, 3]
        plain = rl.sampling_table(lengths, 0.258, (0.9,), 0.0)[0]
        derated = rl.sampling_table(lengths, 0.258, (0.9,), 0.7)[0]
        self.assertEqual(plain.sampling_interval, derated.sampling_interval)
        self.assertEqual(plain.production_destroyed, derated.production_destroyed)
        self.assertLess(derated.value, plain.value)

    def test_raising_the_worth_never_tightens_the_recommendation(self) -> None:
        """The property that catches erratic jumping.

        As a bad electrode becomes worth more, catching one is worth less, so
        the best interval should only ever grow or hold. Comparing raw floats
        near the flat top of the curve let a ten-thousandth of a percent decide
        the winner, and the answer could jump backwards as well as forwards.
        """
        runs = [25, 22, 22, 14, 14, 9, 9, 9, 4, 4, 3, 3, 3, 2]
        intervals = []
        for pct in range(0, 85):
            pick = rl.best_option(rl.interval_curve(runs, 0.258, pct / 100))
            if pick:
                intervals.append(pick.sampling_interval)
        self.assertEqual(intervals, sorted(intervals))
        self.assertGreater(len(intervals), 50)

    def test_it_is_the_strict_peak(self) -> None:
        """No tie band. The recommendation is the best interval, full stop."""
        runs = [25, 22, 22, 14, 14, 9, 9, 9, 4, 4, 3, 3, 3, 2]
        for pct in (0, 20, 40, 60, 80):
            curve = rl.interval_curve(runs, 0.258, pct / 100)
            best = rl.best_option(curve)
            if best:
                self.assertEqual(
                    best.net_percent,
                    max(option.net_percent for option in curve))

    def test_ties_go_to_the_shorter_interval(self) -> None:
        """When the gain is identical the extra testing is free."""
        tied = [_option(interval=8, net=4.0), _option(interval=3, net=4.0),
                _option(interval=5, net=4.0), _option(interval=2, net=1.0)]
        self.assertEqual(rl.best_option(tied).sampling_interval, 3)

    def test_a_hundredth_of_a_point_is_not_a_tie(self) -> None:
        """The band that used to swallow this is gone on purpose."""
        options = [_option(interval=14, net=4.00), _option(interval=48, net=4.01)]
        self.assertEqual(rl.best_option(options).sampling_interval, 48)

    def test_the_acceptable_band_holds_when_the_peak_jumps(self) -> None:
        """The point of the band.

        On a level ridge the peak slides from one end to the other on a step of
        the worth slider. What is acceptable does not move with it, and that is
        what the table bolds and the plot shades.
        """
        runs = [89, 78, 48, 38, 22, 21, 19, 18, 16, 15, 14, 13, 12, 11, 9, 8,
                6, 4, 3, 2, 1]
        bands = []
        for pct in range(30, 45):
            curve = rl.interval_curve(runs, 0.258, pct / 100)
            band = rl.acceptable_band(curve)
            if band:
                bands.append(band)
        widths = [high - low for low, high in bands]
        # Every band overlaps the one before it: no jumping.
        for (low, high), (next_low, next_high) in zip(bands, bands[1:]):
            self.assertLess(next_low, high)
            self.assertGreater(next_high, low)
        self.assertTrue(all(width > 0 for width in widths))

    def test_everything_acceptable_is_within_the_gain_of_the_best(self) -> None:
        curve = rl.interval_curve([25, 22, 14, 9, 4, 3], 0.258, 0.2)
        best = rl.best_option(curve)
        for option in rl.acceptable(curve):
            self.assertGreaterEqual(option.net_percent,
                                    best.net_percent - rl.ACCEPTABLE_GAIN)

    def test_the_best_is_always_acceptable(self) -> None:
        curve = rl.interval_curve([25, 22, 14, 9, 4, 3], 0.258, 0.2)
        self.assertIn(rl.best_option(curve), rl.acceptable(curve))

    def test_a_sharp_peak_gets_a_narrow_band(self) -> None:
        """A band is not a licence. Where the curve really does peak it says so."""
        curve = rl.interval_curve([12, 11, 11, 10, 10, 9], 0.45, 0.0)
        low, high = rl.acceptable_band(curve)
        self.assertLess(high - low, 10)

    def test_nothing_worth_doing_has_no_band(self) -> None:
        curve = rl.interval_curve([2, 2, 3], 0.02, 0.0)
        self.assertEqual(rl.acceptable(curve), [])
        self.assertIsNone(rl.acceptable_band(curve))

    def test_the_band_is_unbroken(self) -> None:
        """Drawn as one shaded region, so a stray match far out is not in it."""
        curve = rl.interval_curve([25, 22, 14, 9, 4, 3], 0.258, 0.2)
        low, high = rl.acceptable_band(curve)
        inside = [option for option in curve
                  if low <= option.sampling_interval <= high]
        self.assertEqual(len(inside), high - low + 1)

    def test_nothing_worth_doing_returns_nothing(self) -> None:
        """Not the least bad row dressed up as a recommendation."""
        curve = rl.interval_curve([2, 2, 3], 0.02, 0.0)
        self.assertIsNone(rl.best_option(curve))

    def test_no_curve_at_all(self) -> None:
        self.assertIsNone(rl.best_option([]))

    def test_the_curve_covers_the_whole_range(self) -> None:
        curve = rl.sampling_curve([25, 22, 14, 9, 4, 3, 3], 0.258)
        self.assertEqual(len(curve), len(rl.CURVE_RATES))
        self.assertTrue(all(0 < option.catch_rate <= 1.0 for option in curve))

    def test_no_runs_gives_no_recommendation_rather_than_a_wrong_one(self) -> None:
        self.assertEqual(rl.interval_for([], 0.9), 0)


if __name__ == "__main__":
    unittest.main()


def _option(interval: int, net: float) -> rl.SamplingOption:
    """A bare SamplingOption with a chosen gain, for the tie breaking tests."""
    return rl.SamplingOption(target=0.0, sampling_interval=interval,
                             catch_rate=0.0, production_destroyed=0.0,
                             value=0.0, bad_stopped=net, good_lost=0.0)
