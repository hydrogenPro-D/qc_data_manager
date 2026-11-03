"""The on/off switch on the QC Sampling tab, with a fake behind it.

The question the tests are really asking: does the control ever say something
the database has not agreed to? Turning it on asks where the count starts, and
a cancel or a refused write has to leave the switch where it was.
"""

import unittest

from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox, QWidget

from src.gui import sampling_gui
from src.gui.sampling_gui import QcSamplingWidget
from src.gui.widgets.sampling_start_dialog import interval_suffix
from src.gui.widgets.toggle_switch import ToggleSwitch

_app = QApplication.instance() or QApplication([])


class _Dialog:
    """Stands in for the modal, so nothing in here blocks on a window."""

    answer: bool | None = True
    accepted: bool = True
    asked: int = 0
    intervals: dict | None = None

    def __init__(self, parent=None, intervals=None) -> None:
        _Dialog.asked += 1
        _Dialog.intervals = intervals
        self.start_now = _Dialog.answer

    def exec(self) -> int:
        return QDialog.DialogCode.Accepted if _Dialog.accepted else 0


class _Switch(unittest.TestCase):

    def setUp(self) -> None:
        _Dialog.asked = 0
        _Dialog.answer = True
        _Dialog.accepted = True
        self.real_dialog = sampling_gui.SamplingStartDialog
        sampling_gui.SamplingStartDialog = _Dialog
        # A modal box waits for a click that is never coming. What it says is
        # checked by reading the argument, not by putting it on screen.
        self.complaints: list[str] = []
        self.real_warning = QMessageBox.warning
        QMessageBox.warning = lambda parent, title, text, *rest:             self.complaints.append(text)
        self.state: bool | None = False
        self.written: list[tuple] = []
        self.refuse = False
        self.host = QWidget()
        self.tab = QcSamplingWidget(
            get_intervals=lambda: {"28": 25, "30": 25},
            on_interval_changed=lambda vat, value: True,
            get_switch=lambda: self.state,
            on_switch_changed=self._save)
        self.tab.setParent(self.host)
        self.tab.reload_intervals()
        self.tab.reload_switch()

    def _save(self, on: bool, start_now: bool) -> bool:
        if self.refuse:
            return False
        self.written.append((on, start_now))
        self.state = on
        return True

    def tearDown(self) -> None:
        sampling_gui.SamplingStartDialog = self.real_dialog
        QMessageBox.warning = self.real_warning
        self.host.deleteLater()


class TestWhatItSays(_Switch):

    def test_off_reads_as_off(self) -> None:
        self.assertIn("off", self.tab.switch_label.text())
        self.assertFalse(self.tab.switch.is_on())

    def test_on_reads_as_on(self) -> None:
        self.state = True
        self.tab.reload_switch()
        self.assertIn("on", self.tab.switch_label.text())
        self.assertTrue(self.tab.switch.is_on())

    def test_the_two_states_are_told_apart_by_colour(self) -> None:
        off = self.tab.switch_label.styleSheet()
        self.state = True
        self.tab.reload_switch()
        self.assertNotEqual(off, self.tab.switch_label.styleSheet())

    def test_a_database_without_the_switch_shows_none_of_it(self) -> None:
        """Migration 016 has not run there. Nothing to offer, so nothing shown."""
        self.state = None
        self.tab.reload_switch()
        self.assertFalse(self.tab.switch.isVisible())
        self.assertFalse(self.tab.switch_label.isVisible())

    def test_a_tab_with_no_backend_shows_none_of_it_either(self) -> None:
        bare = QcSamplingWidget()
        try:
            self.assertFalse(bare.switch.isVisible())
        finally:
            bare.deleteLater()


class TestThrowingIt(_Switch):

    def test_turning_it_on_asks_where_the_count_starts(self) -> None:
        self.tab.switch.click()
        self.assertEqual(_Dialog.asked, 1)
        self.assertEqual(self.written, [(True, True)])
        self.assertTrue(self.tab.switch.is_on())

    def test_the_other_answer_is_passed_through(self) -> None:
        _Dialog.answer = False
        self.tab.switch.click()
        self.assertEqual(self.written, [(True, False)])

    def test_the_dialog_is_given_the_intervals_it_quotes(self) -> None:
        self.tab.switch.click()
        self.assertEqual(_Dialog.intervals, {"28": 25, "30": 25})

    def test_cancelling_leaves_it_off_and_writes_nothing(self) -> None:
        _Dialog.accepted = False
        self.tab.switch.click()
        self.assertEqual(self.written, [])
        self.assertFalse(self.tab.switch.is_on())
        self.assertIn("off", self.tab.switch_label.text())

    def test_turning_it_off_asks_nothing(self) -> None:
        self.state = True
        self.tab.reload_switch()
        self.tab.switch.click()
        self.assertEqual(_Dialog.asked, 0)
        self.assertEqual(self.written, [(False, False)])
        self.assertFalse(self.tab.switch.is_on())

    def test_a_refused_write_leaves_the_control_where_it_was(self) -> None:
        """What is on screen is what is stored, or it is saying something false."""
        self.refuse = True
        self.tab.switch.click()
        self.assertFalse(self.tab.switch.is_on())
        self.assertIn("off", self.tab.switch_label.text())
        self.assertEqual(len(self.complaints), 1)


class TestTheControlItself(unittest.TestCase):
    """The switch on its own, with nothing listening to it."""

    def test_a_click_asks_and_does_not_move(self) -> None:
        """Otherwise it would be saying something the database has not agreed
        to, and a refusal would have to put it back."""
        switch = ToggleSwitch()
        asked = []
        switch.requested.connect(asked.append)
        switch.click()
        self.assertEqual(asked, [True])
        self.assertFalse(switch.is_on())

    def test_setting_it_announces_nothing(self) -> None:
        """So that reading the database back cannot come round as an edit."""
        switch = ToggleSwitch()
        asked = []
        switch.requested.connect(asked.append)
        switch.set_state(True)
        self.assertTrue(switch.is_on())
        self.assertEqual(asked, [])


class TestTheSentenceItQuotes(unittest.TestCase):

    def test_one_figure_for_the_line_is_said_once(self) -> None:
        self.assertEqual(interval_suffix({"28": 25, "30": 25}),
                         " (one in every 25)")

    def test_vats_that_disagree_are_listed(self) -> None:
        said = interval_suffix({"28": 25, "29": 30})
        self.assertIn("28 every 25", said)
        self.assertIn("29 every 30", said)

    def test_nothing_known_says_nothing(self) -> None:
        self.assertEqual(interval_suffix({}), "")
        self.assertEqual(interval_suffix(None), "")
