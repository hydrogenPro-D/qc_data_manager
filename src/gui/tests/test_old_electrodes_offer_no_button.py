"""Past production's window there is nothing to ask for, so nothing is offered.

They list the last 30 days of finished electrodes. An older one has no row on
their screen to answer on, and a request for it is expired by the database
within the minute, so offering the button and then expiring what it raised is
worse than leaving the cell empty.

The button is painted, not a widget, so there is nothing to look for in the
widget tree: the delegate is asked instead, by the same two questions the tab
asks it when the cursor moves and when a cell is clicked.
"""

import datetime as dt
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pandas as pd
from PyQt6.QtCore import QPointF, QRectF
from PyQt6.QtWidgets import QApplication

from src.data_access.electrodes.electrode_columns import ElectrodeColumns
from src.gui.electrodes_gui import ElectrodesWidget

_app = QApplication.instance() or QApplication([])

WINDOW = 30
CELL = QRectF(0, 0, 220, 30)


def _frame(*ages_in_days: int) -> pd.DataFrame:
    now = dt.datetime.now(dt.timezone.utc)
    return pd.DataFrame([{
        str(ElectrodeColumns.DATE):
            (now - dt.timedelta(days=age)).strftime("%Y-%m-%dT%H:%M:%S"),
        str(ElectrodeColumns.MARKING): f"B1600{row}",
        str(ElectrodeColumns.FULL_NAME): "CATHODE",
        str(ElectrodeColumns.RECIPE): "11",
    } for row, age in enumerate(ages_in_days)])


class TestWhichRowsStillOfferTheButton(unittest.TestCase):

    def _widget(self, *ages: int, window: int = WINDOW) -> ElectrodesWidget:
        widget = ElectrodesWidget(get_request_window_days=lambda: window)
        widget.load_data(_frame(*ages))
        return widget

    def test_inside_the_window_keeps_it(self) -> None:
        widget = self._widget(1, 29)
        self.assertTrue(widget._worth_asking_for(0))
        self.assertTrue(widget._worth_asking_for(1))

    def test_outside_it_loses_it(self) -> None:
        widget = self._widget(31, 400)
        self.assertFalse(widget._worth_asking_for(0))
        self.assertFalse(widget._worth_asking_for(1))

    def test_no_window_set_offers_every_row(self) -> None:
        """What a database with no expiry rule looks like."""
        widget = self._widget(400, window=0)
        self.assertTrue(widget._worth_asking_for(0))

    def test_a_click_on_an_old_row_is_not_a_request(self) -> None:
        """The hit test has to agree with the paint, or the button is invisible
        and still live."""
        widget = self._widget(31)
        self.assertEqual(
            widget._qc_delegate.action_at(CELL, QPointF(40, 15), "", "", row=0),
            "")

    def test_a_click_on_a_young_row_still_is(self) -> None:
        widget = self._widget(1)
        self.assertEqual(
            widget._qc_delegate.action_at(CELL, QPointF(40, 15), "", "", row=0),
            "request")


if __name__ == "__main__":
    unittest.main()
