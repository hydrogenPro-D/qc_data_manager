"""The default range dialog, and the config it reads and writes."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from PyQt6.QtWidgets import QApplication

from src.data_access import app_config
from src.gui.widgets.sampling_range_dialog import (
    BACKWARDS, NEEDS_A_MARKING, SamplingRangeDialog, _backwards,
)

_app = QApplication.instance() or QApplication([])


class TestWhatTheDialogGivesBack(unittest.TestCase):

    def _dialog(self, default=(13900, app_config.NEWEST),
                on_screen=(None, None)):
        return SamplingRangeDialog(default=default, on_screen=on_screen)

    def test_it_opens_showing_the_stored_default(self):
        dialog = self._dialog()
        self.assertTrue(dialog.start.fixed.isChecked())
        self.assertEqual(dialog.start.field.text(), "B13900")
        self.assertTrue(dialog.end.edge.isChecked())

    def test_an_edge_comes_back_as_a_word(self):
        dialog = self._dialog((app_config.OLDEST, app_config.NEWEST))
        self.assertTrue(dialog.start.edge.isChecked())
        dialog._on_save()
        self.assertEqual(dialog.chosen, (app_config.OLDEST, app_config.NEWEST))

    def test_a_marking_comes_back_as_a_number(self):
        dialog = self._dialog()
        dialog.end.fixed.setChecked(True)
        dialog.end.field.setText("B14500")
        dialog._on_save()
        self.assertEqual(dialog.chosen, (13900, 14500))

    def test_a_bare_number_is_accepted_too(self):
        dialog = self._dialog()
        dialog.start.field.setText("14100")
        dialog.end.fixed.setChecked(True)
        dialog.end.field.setText("14200")
        dialog._on_save()
        self.assertEqual(dialog.chosen, (14100, 14200))

    def test_the_marking_box_is_dead_until_its_option_is_chosen(self):
        # Which end is in force is visible, rather than inferred from a dot.
        dialog = self._dialog((app_config.OLDEST, app_config.NEWEST))
        self.assertFalse(dialog.start.field.isEnabled())
        dialog.start.fixed.setChecked(True)
        self.assertTrue(dialog.start.field.isEnabled())

    def test_it_opens_with_the_right_box_alive(self):
        # toggled only fires on a change, so the first setting is not one and
        # the box would open enabled whatever was stored.
        dialog = self._dialog()
        self.assertTrue(dialog.start.field.isEnabled())
        self.assertFalse(dialog.end.field.isEnabled())

    def test_an_empty_marking_is_refused_rather_than_guessed(self):
        dialog = self._dialog()
        dialog.start.field.clear()
        dialog._on_save()
        self.assertIsNone(dialog.chosen)

    def test_the_two_ends_are_independent(self):
        # One button group each. Sharing one, they would pick a single radio
        # between the two ends.
        dialog = self._dialog()
        dialog.start.edge.setChecked(True)
        self.assertTrue(dialog.start.edge.isChecked())
        self.assertTrue(dialog.end.edge.isChecked())

    def test_the_marking_boxes_show_the_range_on_screen(self):
        # The button sits beside those boxes, so it reads as being about them.
        dialog = self._dialog(on_screen=(2, 14995))
        self.assertEqual(dialog.start.field.text(), "B2")
        self.assertEqual(dialog.end.field.text(), "B14995")

    def test_they_show_it_even_under_an_edge(self):
        dialog = self._dialog((app_config.OLDEST, app_config.NEWEST),
                              on_screen=(2, 14995))
        self.assertTrue(dialog.start.edge.isChecked())
        self.assertEqual(dialog.start.field.text(), "B2")

    def test_which_option_is_chosen_still_comes_from_the_stored_default(self):
        dialog = self._dialog((app_config.OLDEST, 14500), on_screen=(2, 14995))
        self.assertTrue(dialog.start.edge.isChecked())
        self.assertTrue(dialog.end.fixed.isChecked())

    def test_with_nothing_on_screen_it_shows_what_is_stored(self):
        dialog = self._dialog(on_screen=(None, None))
        self.assertEqual(dialog.start.field.text(), "B13900")

    def test_the_range_on_screen_can_be_saved_as_it_stands(self):
        dialog = self._dialog(on_screen=(2, 14995))
        dialog.end.fixed.setChecked(True)
        dialog._on_save()
        self.assertEqual(dialog.chosen, (2, 14995))

    def test_an_empty_marking_at_either_end_is_refused(self):
        dialog = self._dialog()
        dialog.end.fixed.setChecked(True)   # chosen, and left empty
        dialog._on_save()
        self.assertIsNone(dialog.chosen)


class TestTheStoredDefault(unittest.TestCase):
    """Written to config.json, read back from it."""

    def setUp(self) -> None:
        self._cache = app_config._cache
        self._base = app_config.base_directory
        self.folder = tempfile.mkdtemp()
        (Path(self.folder) / "config").mkdir()
        app_config.base_directory = lambda: Path(self.folder)
        app_config._cache = {"ui": {"auto_refresh_interval_ms": 5000}}

    def tearDown(self) -> None:
        app_config.base_directory = self._base
        app_config._cache = self._cache

    def _written(self) -> dict:
        path = Path(self.folder) / "config" / "config.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_a_number_and_an_edge_survive_the_round_trip(self):
        app_config.set_sampling_default_range(13900, app_config.NEWEST)
        self.assertEqual(app_config.sampling_default_range(),
                         (13900, app_config.NEWEST))

    def test_it_is_stored_as_the_word_not_the_number_of_the_day(self):
        # "Up to the newest electrode" has to stay true tomorrow.
        app_config.set_sampling_default_range(app_config.OLDEST,
                                              app_config.NEWEST)
        stored = self._written()["sampling"]["default_range"]
        self.assertEqual(stored, {"from": "oldest", "to": "newest"})

    def test_nothing_else_in_the_file_moves(self):
        app_config.set_sampling_default_range(1, 2)
        self.assertEqual(self._written()["ui"]["auto_refresh_interval_ms"],
                         5000)

    def test_a_marking_string_is_taken_as_a_number(self):
        app_config.set_sampling_default_range("14000", app_config.NEWEST)
        self.assertEqual(app_config.sampling_default_range()[0], 14000)

    def test_nonsense_falls_back_rather_than_being_stored(self):
        app_config.set_sampling_default_range("later", "sometime")
        self.assertEqual(app_config.sampling_default_range(),
                         (13900, app_config.NEWEST))

    def test_no_section_at_all_still_has_a_default(self):
        app_config._cache = {}
        self.assertEqual(app_config.sampling_default_range(),
                         (13900, app_config.NEWEST))




class TestARangeThatEndsBeforeItStarts(unittest.TestCase):
    """It used to save, then quietly fall back to the whole history."""

    def _dialog(self, default=(13900, app_config.NEWEST)):
        return SamplingRangeDialog(default=default)

    def _both_fixed(self, start: int, end: int):
        dialog = self._dialog()
        dialog.start.fixed.setChecked(True)
        dialog.start.field.setText(f"B{start}")
        dialog.end.fixed.setChecked(True)
        dialog.end.field.setText(f"B{end}")
        dialog._on_save()
        return dialog

    def test_it_is_refused(self):
        dialog = self._both_fixed(13900, 1000)
        self.assertIsNone(dialog.chosen)

    def test_it_says_why(self):
        dialog = self._both_fixed(13900, 1000)
        self.assertEqual(dialog.problem.text(), BACKWARDS)

    def test_the_right_way_round_goes_through(self):
        dialog = self._both_fixed(13900, 14900)
        self.assertEqual(dialog.chosen, (13900, 14900))

    def test_one_electrode_is_a_range(self):
        dialog = self._both_fixed(14000, 14000)
        self.assertEqual(dialog.chosen, (14000, 14000))

    def test_an_edge_is_never_backwards(self):
        # The oldest electrode cannot be after another one, whatever the
        # numbers turn out to be on the day.
        self.assertFalse(_backwards(app_config.OLDEST, 1))
        self.assertFalse(_backwards(99999, app_config.NEWEST))
        self.assertFalse(_backwards(app_config.OLDEST, app_config.NEWEST))

    def test_fixing_it_clears_the_complaint(self):
        dialog = self._both_fixed(13900, 1000)
        dialog.end.field.setText("B14900")
        dialog.end.field.textEdited.emit("B14900")
        self.assertFalse(dialog.problem.isVisible())
        dialog._on_save()
        self.assertEqual(dialog.chosen, (13900, 14900))

    def test_an_empty_box_says_what_is_missing(self):
        dialog = self._dialog()
        dialog.end.fixed.setChecked(True)
        dialog._on_save()
        self.assertEqual(dialog.problem.text(), NEEDS_A_MARKING)


if __name__ == "__main__":
    unittest.main()
