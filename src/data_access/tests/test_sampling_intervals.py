"""The stored sampling intervals, and the gap to the best one."""

import json
import os
import tempfile
import unittest

from src.data_access.sampling import current_intervals as ci


class _Isolated(unittest.TestCase):
    """Each test gets its own file, so none of them touch the real one."""

    def setUp(self) -> None:
        self._path, self._cache = ci._PATH, ci._cache
        handle, path = tempfile.mkstemp(suffix=".json")
        os.close(handle)
        os.remove(path)
        ci._PATH, ci._cache = path, None

    def tearDown(self) -> None:
        if os.path.exists(ci._PATH):
            os.remove(ci._PATH)
        ci._PATH, ci._cache = self._path, self._cache


class TestWhatAVatIsSampledAt(_Isolated):

    def test_an_unknown_vat_gets_the_default(self):
        self.assertEqual(ci.current_for("28"), ci.DEFAULT_INTERVAL)

    def test_what_was_set_comes_back(self):
        ci.set_current("28", 17)
        self.assertEqual(ci.current_for("28"), 17)

    def test_vats_are_kept_apart(self):
        ci.set_current("28", 17)
        ci.set_current("29", 4)
        self.assertEqual((ci.current_for("28"), ci.current_for("29")), (17, 4))

    def test_it_survives_a_reload(self):
        ci.set_current("Vat 30", 9)
        ci._cache = None
        self.assertEqual(ci.current_for("Vat 30"), 9)

    def test_an_out_of_range_interval_is_refused(self):
        ci.set_current("28", 0)
        ci.set_current("29", ci.MAX_INTERVAL + 1)
        self.assertEqual(ci.current_for("28"), ci.DEFAULT_INTERVAL)
        self.assertEqual(ci.current_for("29"), ci.DEFAULT_INTERVAL)

    def test_a_corrupt_file_costs_the_defaults_not_the_window(self):
        with open(ci._PATH, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        ci._cache = None
        self.assertEqual(ci.current_for("28"), ci.DEFAULT_INTERVAL)

    def test_a_nonsense_entry_is_dropped_and_the_rest_kept(self):
        with open(ci._PATH, "w", encoding="utf-8") as handle:
            json.dump({"28": "often", "29": 6}, handle)
        ci._cache = None
        self.assertEqual(ci.current_for("28"), ci.DEFAULT_INTERVAL)
        self.assertEqual(ci.current_for("29"), 6)


class TestHowFarOffTheBestRate(unittest.TestCase):

    def test_it_is_the_plain_difference(self):
        self.assertEqual(ci.deviation(12, 15), 3)

    def test_it_is_unsigned(self):
        self.assertEqual(ci.deviation(15, 12), 3)

    def test_being_on_the_best_rate_reads_as_zero(self):
        self.assertEqual(ci.deviation(12, 12), 0)

    def test_no_best_rate_is_not_the_same_as_no_gap(self):
        self.assertIsNone(ci.deviation(12, None))
