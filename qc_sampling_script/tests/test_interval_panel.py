"""The current sampling interval panel, beside each vat's plot.

Built against a bare instance rather than a real window, because the window
opens the production database in its constructor and none of this needs it.
"""

import os
import tempfile
import unittest

from PyQt6.QtWidgets import QApplication, QWidget

from qc_sampling_script.data import current_intervals as ci
from qc_sampling_script.gui.sampling_window import SamplingWindow, _optimum_note

_app = QApplication.instance() or QApplication([])


class TestHowFarOffTheOptimum(unittest.TestCase):

    def test_a_looser_optimum_says_so(self):
        self.assertEqual(_optimum_note(12, 15),
                         "Optimal interval is 3 electrodes less frequent.")

    def test_a_tighter_optimum_says_so(self):
        self.assertEqual(_optimum_note(12, 9),
                         "Optimal interval is 3 electrodes more frequent.")

    def test_one_electrode_is_singular(self):
        self.assertEqual(_optimum_note(12, 13),
                         "Optimal interval is 1 electrode less frequent.")

    def test_being_on_it_says_nothing(self):
        self.assertEqual(_optimum_note(12, 12), "")

    def test_no_worthwhile_rate_is_not_the_same_as_no_distance(self):
        self.assertEqual(_optimum_note(12, None),
                         "No interval here pays for itself.")

    def test_an_unmeasured_vat_says_nothing(self):
        # No runs in this range, so nothing honest to say about its interval.
        self.assertEqual(_optimum_note(12, None, measured=False), "")


class TestIntervalPanel(unittest.TestCase):

    def setUp(self) -> None:
        self._path, self._cache = ci._PATH, ci._cache
        handle, path = tempfile.mkstemp(suffix=".json")
        os.close(handle)
        os.remove(path)
        ci._PATH, ci._cache = path, None

        self.host = QWidget()
        self.window = SamplingWindow.__new__(SamplingWindow)
        self.window._interval_panels = []
        self.window._best_by_vat = {"28": 15, "29": 9, "30": None}
        self.panels = [self.window._new_interval_panel(self.host)
                       for _ in range(2)]
        self.window._interval_panels = self.panels
        self._rows(["28", "29", "30"])

    def tearDown(self) -> None:
        if os.path.exists(ci._PATH):
            os.remove(ci._PATH)
        ci._PATH, ci._cache = self._path, self._cache

    def _rows(self, vats: list[str]) -> None:
        for panel in self.panels:
            self.window._interval_rows(panel, vats)
        self.window._refresh_interval_panels()

    @staticmethod
    def _read(panel) -> dict:
        return {vat: (row["spin"].value(), row["note"].text())
                for vat, row in panel["rows"].items()}

    # ------------------------------------------------------------------
    def test_a_vat_this_range_says_nothing_about_still_gets_a_row(self):
        # 29 is in production and sampled at something, whether or not the
        # window happens to cover any of its runs.
        self._rows(["28", "29", "30", "31"])
        self.assertEqual(self._read(self.panels[0])["31"], (12, ""))

    def test_a_row_per_vat_against_its_own_optimum(self):
        self.assertEqual(
            self._read(self.panels[0]),
            {"28": (12, "Optimal interval is 3 electrodes less frequent."),
             "29": (12, "Optimal interval is 3 electrodes more frequent."),
             "30": (12, "No interval here pays for itself.")})

    def test_an_interval_typed_on_one_tab_shows_on_the_others(self):
        self.panels[0]["rows"]["28"]["spin"].setValue(15)
        self.assertEqual(self._read(self.panels[1])["28"],
                         (15, ""))

    def test_only_the_edited_vat_moves(self):
        self.panels[0]["rows"]["28"]["spin"].setValue(15)
        self.assertEqual(self._read(self.panels[1])["29"],
                         (12, "Optimal interval is 3 electrodes more frequent."))

    def test_it_is_saved_as_it_is_typed(self):
        self.panels[0]["rows"]["28"]["spin"].setValue(15)
        ci._cache = None
        self.assertEqual(ci.current_for("28"), 15)

    def test_rebuilding_for_other_vats_leaves_no_old_rows_behind(self):
        self.window._best_by_vat = {"28": 20}
        self._rows(["28"])
        self.assertEqual(list(self.panels[0]["rows"]), ["28"])
        # The two headings, plus a name, a box and a note for the one vat left.
        self.assertEqual(self.panels[0]["grid"].count(), 5)

    def test_rebuilding_keeps_what_was_typed(self):
        self.panels[0]["rows"]["28"]["spin"].setValue(15)
        self.window._best_by_vat = {"28": 20}
        self._rows(["28"])
        self.assertEqual(self._read(self.panels[0]),
                         {"28": (15, "Optimal interval is 5 electrodes less frequent.")})

    def test_a_part_typed_number_is_not_saved_on_the_way(self):
        # Committed on Enter or on focus out, not per keystroke.
        self.assertFalse(self.panels[0]["rows"]["28"]["spin"].keyboardTracking())


class TestClickingAwayFinishesAnEdit(unittest.TestCase):
    """The caret and its selection must not survive a click somewhere else."""

    def setUp(self) -> None:
        self.host = QWidget()
        self.inside = QWidget(self.host)
        self.editor = _spin(self.inside)
        self.elsewhere = QWidget(self.host)
        self.host.show()
        # Focus only lands for real once the window is the active one, which
        # offscreen it is not until it is asked to be.
        self.host.activateWindow()
        self.host.raise_()
        self.editor.setFocus()
        self.editor.lineEdit().selectAll()
        _app.processEvents()

    def tearDown(self) -> None:
        self.host.hide()

    def test_a_click_outside_drops_the_focus(self):
        SamplingWindow._finish_editing(self.elsewhere)
        self.assertFalse(self.editor.hasFocus())

    def test_a_click_outside_drops_the_selection(self):
        SamplingWindow._finish_editing(self.elsewhere)
        self.assertEqual(self.editor.lineEdit().selectedText(), "")

    def test_a_click_on_the_box_itself_keeps_it(self):
        SamplingWindow._finish_editing(self.editor)
        self.assertTrue(self.editor.hasFocus())

    def test_a_click_on_an_ancestor_of_the_box_does_not_keep_it(self):
        # The panel holding the box is not the box. Clicking the card around
        # it is a click somewhere else.
        SamplingWindow._finish_editing(self.inside)
        self.assertFalse(self.editor.hasFocus())


def _spin(parent):
    from PyQt6.QtWidgets import QSpinBox
    box = QSpinBox(parent)
    box.setValue(12)
    return box
