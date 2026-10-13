"""Reading a vat's sampling interval, and the gap to the best one.

The intervals used to live in a JSON file this module read and wrote. They are
now rows in qc_requests, fetched by the backend and handed in as a dict, so
what is left here is the two rules: what a vat reads at when nothing is set,
and how far that is from the best rate. Both are pure, which is why they are
still worth a test of their own.
"""

import unittest

from src.data_access.sampling import current_intervals as ci


class TestWhatAVatIsSampledAt(unittest.TestCase):

    def test_an_unknown_vat_gets_the_default(self) -> None:
        self.assertEqual(ci.current_for({}, "28"), ci.DEFAULT_INTERVAL)

    def test_so_does_a_vat_nobody_could_fetch(self) -> None:
        """An unreachable table reads as empty, not as an error on screen."""
        self.assertEqual(ci.current_for(None, "28"), ci.DEFAULT_INTERVAL)

    def test_what_is_stored_comes_back(self) -> None:
        self.assertEqual(ci.current_for({"28": 17}, "28"), 17)

    def test_vats_are_kept_apart(self) -> None:
        intervals = {"28": 17, "29": 4}
        self.assertEqual((ci.current_for(intervals, "28"),
                          ci.current_for(intervals, "29")), (17, 4))

    def test_the_vat_is_matched_as_text(self) -> None:
        """The column holds "28", and a caller may well have the number."""
        self.assertEqual(ci.current_for({"28": 17}, 28), 17)

    def test_an_out_of_range_interval_reads_as_unset(self) -> None:
        """The table has a CHECK, so this is a row written before it existed."""
        self.assertEqual(ci.current_for({"28": 0}, "28"), ci.DEFAULT_INTERVAL)
        self.assertEqual(ci.current_for({"29": ci.MAX_INTERVAL + 1}, "29"),
                         ci.DEFAULT_INTERVAL)

    def test_a_nonsense_entry_is_dropped_and_the_rest_kept(self) -> None:
        intervals = {"28": "often", "29": 6}
        self.assertEqual(ci.current_for(intervals, "28"), ci.DEFAULT_INTERVAL)
        self.assertEqual(ci.current_for(intervals, "29"), 6)


class TestWhatCountsAsAnInterval(unittest.TestCase):

    def test_the_ends_of_the_range_are_allowed(self) -> None:
        self.assertTrue(ci.sane(ci.MIN_INTERVAL))
        self.assertTrue(ci.sane(ci.MAX_INTERVAL))

    def test_outside_it_is_not(self) -> None:
        self.assertFalse(ci.sane(0))
        self.assertFalse(ci.sane(ci.MAX_INTERVAL + 1))

    def test_nor_is_anything_that_is_not_a_number(self) -> None:
        for value in (None, "", "often", []):
            self.assertFalse(ci.sane(value), value)


class TestHowFarOffTheBestRate(unittest.TestCase):

    def test_it_is_the_plain_difference(self) -> None:
        self.assertEqual(ci.deviation(12, 15), 3)

    def test_it_is_unsigned(self) -> None:
        self.assertEqual(ci.deviation(15, 12), 3)

    def test_being_on_the_best_rate_reads_as_zero(self) -> None:
        self.assertEqual(ci.deviation(12, 12), 0)

    def test_no_best_rate_is_not_the_same_as_no_gap(self) -> None:
        self.assertIsNone(ci.deviation(12, None))


if __name__ == "__main__":
    unittest.main()
