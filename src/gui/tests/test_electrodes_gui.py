"""Electrodes tab — rendering and the polling contract, with fake callbacks."""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pandas as pd
from PyQt6.QtWidgets import QApplication

from src.gui.electrodes_gui import ElectrodesWidget
from src.data_access.electrodes.electrode_columns import (
    ElectrodeColumns,
    ElectrodeStatus,
    STATUS_COLUMN,
)

_app = QApplication.instance() or QApplication([])


def electrode(marking: str = "B14020", electrode_type: str = "CATHODE") -> dict:
    """One row under the real column names. The tab renders only the columns
    its display map knows, so a fake name would be dropped before it reached
    the table."""
    return {
        str(ElectrodeColumns.DATE): "2026-07-01T08:00:00",
        str(ElectrodeColumns.MARKING): marking,
        str(ElectrodeColumns.FULL_NAME): electrode_type,
        str(ElectrodeColumns.RECIPE): "11",
        STATUS_COLUMN: str(ElectrodeStatus.NOT_TESTED),
    }


class TestElectrodesWidget(unittest.TestCase):
    """Starts empty, renders whatever frame it's given, survives poll errors."""

    def setUp(self) -> None:
        self.widget = ElectrodesWidget()

    def tearDown(self) -> None:
        self.widget.deleteLater()

    def test_starts_empty(self) -> None:
        self.assertEqual(self.widget.table.rowCount(), 0)
        self.assertEqual(self.widget.table.columnCount(), 0)
        self.assertTrue(self.widget.get_data().empty)

    def test_renders_a_frame(self) -> None:
        self.widget.load_data(pd.DataFrame([
            electrode("B14020"), electrode("B14023"),
        ]))
        self.assertEqual(self.widget.table.rowCount(), 2)
        self.assertEqual(self.widget.table.columnCount(), 5)
        marking_column = list(self.widget.df_visible.columns).index(
            str(ElectrodeColumns.MARKING))
        self.assertEqual(self.widget.table.item(0, marking_column).text(), "B14020")

    def test_reload_with_empty_frame_clears_the_table(self) -> None:
        self.widget.load_data(pd.DataFrame([electrode()]))
        self.widget.load_data(pd.DataFrame())
        self.assertEqual(self.widget.table.rowCount(), 0)

    def test_poll_is_a_noop_without_a_callback(self) -> None:
        self.assertFalse(self.widget.check_for_external_updates())

    def test_poll_reloads_when_the_watermark_moved(self) -> None:
        self.widget.on_check_for_updates = lambda: {
            "has_updates": True, "data": pd.DataFrame([electrode()])}
        self.assertTrue(self.widget.check_for_external_updates())
        self.assertEqual(self.widget.table.rowCount(), 1)

    def test_poll_failure_never_propagates(self) -> None:
        def _boom():
            raise RuntimeError("backend down")

        self.widget.on_check_for_updates = _boom
        self.assertFalse(self.widget.check_for_external_updates())


if __name__ == "__main__":
    unittest.main()
