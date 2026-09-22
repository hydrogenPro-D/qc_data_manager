"""How much of the gain curve the plot draws, and what it shades.

Pure functions, no widget: the drawing is pyqtgraph's problem, the framing is
ours.
"""

import unittest

from qc_sampling_script.data.run_lengths import (
    ACCEPTABLE_GAIN, acceptable_band, best_option, interval_curve,
)
from qc_sampling_script.gui.charts import (
    MAX_INTERVAL_SHOWN, MIN_INTERVAL_SHOWN, WINDOW_MULTIPLE, ZOOM_RATIO,
    _anchor, _window, _y_range,
)

# One real vat's bad runs, long tail and all.
RUNS = [89, 78, 48, 38, 22, 21, 19, 18, 16, 15, 14, 14, 13, 12, 11, 11, 10,
        9, 9, 8, 6, 5, 4, 3, 3, 2, 2, 1, 1, 1]
FLAT = [2, 3, 4, 6, 8]


def _curve(runs=RUNS, share=0.26, worth=0.0):
    return interval_curve(runs, share, worth)


class TestHowFarTheWindowRuns(unittest.TestCase):

    def test_it_is_a_multiple_of_the_best_interval(self):
        points = _window(_curve())
        best = best_option(points)
        self.assertEqual(points[-1].sampling_interval,
                         best.sampling_interval * WINDOW_MULTIPLE)

    def test_the_peak_is_inside_it(self):
        points = _window(_curve())
        self.assertIsNotNone(best_option(points))

    def test_the_gain_has_fallen_by_the_right_edge(self):
        points = _window(_curve())
        peak = max(option.net_percent for option in points)
        self.assertLess(points[-1].net_percent, peak)

    def test_a_vat_with_no_worthwhile_rate_still_gets_a_window(self):
        points = _window(_curve(FLAT, share=0.05))
        self.assertTrue(points)
        self.assertIsNone(best_option(points))

    def test_nothing_to_draw_draws_nothing(self):
        self.assertEqual(_window([]), [])
        self.assertEqual(_window(None), [])

    def test_a_tight_vat_still_gets_a_window_worth_looking_at(self):
        # Four times a best of three is a window of twelve, which is a wall at
        # the left edge rather than a curve.
        runs = [2, 2, 3, 3, 4]
        drawn = _window(_curve(runs, share=0.5))[-1].sampling_interval
        self.assertGreaterEqual(drawn, MIN_INTERVAL_SHOWN)

    def test_the_window_is_capped(self):
        points = _window(_curve([600, 700, 800], share=0.4))
        self.assertLessEqual(points[-1].sampling_interval, MAX_INTERVAL_SHOWN)


class TestTheBandAroundTheBest(unittest.TestCase):
    """The shading, which is the same band the table prints in bold."""

    def setUp(self) -> None:
        self.points = _window(_curve())
        self.best = best_option(self.points)
        self.low, self.high = acceptable_band(self.points, self.best)

    def test_a_vat_with_nothing_worth_doing_has_no_band(self):
        self.assertIsNone(acceptable_band(_window(_curve(FLAT, share=0.05))))

    def test_it_contains_the_best(self):
        self.assertLessEqual(self.low, self.best.sampling_interval)
        self.assertLessEqual(self.best.sampling_interval, self.high)

    def test_everything_in_it_scores_within_the_band(self):
        floor = self.best.net_percent - ACCEPTABLE_GAIN
        inside = [option for option in self.points
                  if self.low <= option.sampling_interval <= self.high]
        self.assertTrue(all(option.net_percent >= floor for option in inside))

    def test_it_stops_at_the_first_interval_that_does_not(self):
        floor = self.best.net_percent - ACCEPTABLE_GAIN
        beyond = [option for option in self.points
                  if option.sampling_interval == self.high + 1]
        if beyond:
            self.assertLess(beyond[0].net_percent, floor)


class TestKeepingLabelsOnThePlot(unittest.TestCase):
    """A label anchored by its centre ran off the edge on every tight vat."""

    XS = list(range(1, 101))

    def test_a_point_near_the_left_pins_its_label_by_the_left_edge(self):
        self.assertEqual(_anchor(5, self.XS), 0.0)

    def test_a_point_near_the_right_pins_it_by_the_right_edge(self):
        self.assertEqual(_anchor(95, self.XS), 1.0)

    def test_a_point_in_the_middle_centres_it(self):
        self.assertEqual(_anchor(50, self.XS), 0.5)

    def test_a_single_point_window_centres_it(self):
        self.assertEqual(_anchor(1, [1]), 0.5)


class TestZoomingInOnThePeak(unittest.TestCase):

    def setUp(self) -> None:
        self.points = _window(_curve())
        self.best = best_option(self.points)
        self.low, self.high = _y_range(self.points)

    def test_the_peak_is_in_view(self):
        self.assertLess(self.best.net_percent, self.high)
        self.assertGreater(self.best.net_percent, self.low)

    def test_it_ignores_the_crash_at_the_left(self):
        # Testing every electrode scores about minus fifty percent. Fitting the
        # axis to that is what made the plateau read as dead flat.
        self.assertGreater(self.low, 0)

    def test_it_is_fitted_to_the_intervals_around_the_best(self):
        near = [option.net_percent for option in self.points
                if self.best.sampling_interval / ZOOM_RATIO
                <= option.sampling_interval
                <= self.best.sampling_interval * ZOOM_RATIO]
        self.assertLess(self.low, min(near))
        self.assertGreater(self.high, max(near))

    def test_a_tight_best_does_not_drag_the_crash_into_view(self):
        # Ten intervals either side of a best of nine reaches interval one,
        # where the gain is about minus fifty percent.
        low, _high = _y_range(_window(_curve(share=0.4)))
        self.assertGreater(low, 0)

    def test_it_is_tighter_than_fitting_the_whole_curve(self):
        whole = max(option.net_percent for option in self.points) -             min(option.net_percent for option in self.points)
        self.assertLess(self.high - self.low, whole)

    def test_a_vat_with_no_best_still_gets_a_range(self):
        low, high = _y_range(_window(_curve(FLAT, share=0.05)))
        self.assertLess(low, high)
