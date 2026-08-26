"""Shared table widget: formatting, sizing and in-place row edits."""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pandas as pd
from PyQt6.QtWidgets import QApplication

from src.gui.widgets.data_table_widget import DataTableWidget

_app = QApplication.instance() or QApplication([])


class TestDataTableWidget(unittest.TestCase):
    """Cell rendering rules and the add/update helpers."""

    def setUp(self) -> None:
        self.table = DataTableWidget(min_column_width=50, max_column_width=120)

    def tearDown(self) -> None:
        self.table.deleteLater()

    def test_blank_cells_render_as_a_dash(self) -> None:
        self.table.load_data(pd.DataFrame([{"A": None, "B": float("nan")}]))
        self.assertEqual(self.table.item(0, 0).text(), "-")
        self.assertEqual(self.table.item(0, 1).text(), "-")

    def test_whole_floats_lose_the_trailing_zero(self) -> None:
        self.table.load_data(pd.DataFrame([{"A": 5.0, "B": 5.25}]))
        self.assertEqual(self.table.item(0, 0).text(), "5")
        self.assertEqual(self.table.item(0, 1).text(), "5.25")

    def test_styling_callback_formats_cells(self) -> None:
        self.table.load_data(
            pd.DataFrame([{"A": 5.0}]), styling_callback=lambda col, value: f"{value:.2f} g/L")
        self.assertEqual(self.table.item(0, 0).text(), "5.00 g/L")

    def test_display_column_order_is_respected(self) -> None:
        self.table.load_data(pd.DataFrame([{"A": 1, "B": 2}]), display_column_order=["B", "A"])
        headers = [self.table.horizontalHeaderItem(i).text() for i in range(2)]
        self.assertEqual(headers, ["B", "A"])

    def test_empty_frame_clears_the_table(self) -> None:
        self.table.load_data(pd.DataFrame([{"A": 1}]))
        self.table.load_data(pd.DataFrame())
        self.assertEqual(self.table.rowCount(), 0)
        self.assertEqual(self.table.columnCount(), 0)

    def test_add_row_appends(self) -> None:
        self.table.load_data(pd.DataFrame([{"A": 1, "B": 2}]))
        self.table.add_row({"A": 3, "B": 4})
        self.assertEqual(self.table.rowCount(), 2)
        self.assertEqual(self.table.item(1, 0).text(), "3")

    def test_update_row_rewrites_in_place(self) -> None:
        self.table.load_data(pd.DataFrame([{"A": 1, "B": 2}]))
        self.table.update_row(0, {"A": 9, "B": 8})
        self.assertEqual(self.table.rowCount(), 1)
        self.assertEqual(self.table.item(0, 0).text(), "9")

    def test_update_row_ignores_an_out_of_range_index(self) -> None:
        self.table.load_data(pd.DataFrame([{"A": 1}]))
        self.table.update_row(7, {"A": 9})
        self.assertEqual(self.table.item(0, 0).text(), "1")

    def test_multiline_cells_get_a_taller_row(self) -> None:
        self.table.load_data(pd.DataFrame([{"A": "one\ntwo\nthree"}]))
        self.assertGreater(self.table.rowHeight(0), 30)

    def test_column_widths_are_clamped(self) -> None:
        self.table.load_data(pd.DataFrame([{"A": "x", "B": "y" * 200}]))
        self.table.resize_columns_to_contents_constrained()
        self.assertGreaterEqual(self.table.columnWidth(0), 50)
        self.assertLessEqual(self.table.columnWidth(1), 120)

    def test_uncapped_columns_keep_full_content_width(self) -> None:
        self.table.load_data(pd.DataFrame([{"A": "x", "Notes": "y" * 200}]))
        self.table.set_uncapped_columns({"Notes"})
        self.assertGreater(self.table.columnWidth(1), 120)


if __name__ == "__main__":
    unittest.main()
