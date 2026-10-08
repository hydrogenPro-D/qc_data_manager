"""Sorting the SEM EDX table by clicking a column header.

The order is worked out on the stored values, not on the text in the cells: the
measurements are rounded to one decimal on the way to the screen and the dates
are shown dd.mm.yyyy, so sorting what is displayed gets both wrong.
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pandas as pd
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from src.gui.sem_edx_gui import _sorted
from src.gui.widgets.data_table_widget import DataTableWidget

_app = QApplication.instance() or QApplication([])


class TestTheOrder(unittest.TestCase):
    """Numbers as numbers, identifiers by their number, the rest as text."""

    def _order(self, values, ascending=True) -> list:
        frame = pd.DataFrame({"column": values})
        return list(_sorted(frame, "column", ascending)["column"])

    def test_measurements_sort_as_numbers(self) -> None:
        """3.46 before 3.5, which sorting the rounded text gets backwards."""
        self.assertEqual(self._order([3.5, 31.9, 3.46, 28.8]),
                         [3.46, 3.5, 28.8, 31.9])

    def test_markings_sort_by_their_number(self) -> None:
        """B9 first. As text it lands after B14134, which reads as broken."""
        self.assertEqual(self._order(["B13872", "B9", "B14134", "B100"]),
                         ["B9", "B100", "B13872", "B14134"])

    def test_the_zoom_sorts_by_its_number_too(self) -> None:
        self.assertEqual(self._order(["x100", "x1000", "x50"]),
                         ["x50", "x100", "x1000"])

    def test_operators_sort_without_regard_to_case(self) -> None:
        self.assertEqual(self._order(["oniel", "Emil", "Beata"]),
                         ["Beata", "Emil", "oniel"])

    def test_a_note_is_text_even_when_it_starts_with_a_number(self) -> None:
        """Only a prefix and a number counts as an identifier."""
        self.assertEqual(self._order(["Fine", "1st run in vat 28", "Cracks"]),
                         ["1st run in vat 28", "Cracks", "Fine"])

    def test_dates_sort_as_dates(self) -> None:
        """They are stored ISO, so the text order is the date order."""
        self.assertEqual(self._order(["2026-12-01", "2026-01-02", "2026-08-25"]),
                         ["2026-01-02", "2026-08-25", "2026-12-01"])

    def test_a_blank_counts_as_the_smallest_value(self) -> None:
        """Increasing puts the empty ones at the top, decreasing at the bottom."""
        rising = self._order([3.5, None, 1.2], ascending=True)
        self.assertTrue(pd.isna(rising[0]), rising)
        self.assertEqual(rising[1:], [1.2, 3.5])

        falling = self._order([3.5, None, 1.2], ascending=False)
        self.assertTrue(pd.isna(falling[-1]), falling)
        self.assertEqual(falling[:-1], [3.5, 1.2])

    def test_undated_samples_come_first_when_dates_rise(self) -> None:
        order = self._order(["2026-09-16", "", "2026-08-28"], ascending=True)
        self.assertEqual(order, ["", "2026-08-28", "2026-09-16"])

    def test_an_unknown_column_leaves_the_frame_alone(self) -> None:
        frame = pd.DataFrame({"column": [3, 1, 2]})
        self.assertEqual(list(_sorted(frame, "missing", True)["column"]),
                         [3, 1, 2])

    def test_no_column_is_the_order_it_was_loaded_in(self) -> None:
        """What the third click asks for."""
        frame = pd.DataFrame({"column": [3, 1, 2]})
        self.assertEqual(list(_sorted(frame, "", True)["column"]), [3, 1, 2])


class TestTheHeaderClick(unittest.TestCase):
    """The table asks; it never sorts itself.

    QTableWidget's own sorting moves the items and leaves cell widgets behind,
    and this table puts a View button in its images column.
    """

    def setUp(self) -> None:
        self.table = DataTableWidget()
        self.table.load_data(pd.DataFrame({"Electrode": ["B1"], "Ni avg": [3.0]}))
        self.table.enable_sorting()
        self.asked: list[tuple[str, bool]] = []
        self.table.sort_requested.connect(
            lambda name, up: self.asked.append((name, up)))

    def tearDown(self) -> None:
        self.table.deleteLater()

    def test_the_table_does_not_sort_itself(self) -> None:
        self.assertFalse(self.table.isSortingEnabled())

    def test_the_first_click_asks_for_ascending(self) -> None:
        self.table._on_header_clicked(1)
        self.assertEqual(self.asked, [("Ni avg", True)])

    def test_clicking_the_same_column_again_reverses_it(self) -> None:
        self.table._on_header_clicked(1)
        self.table._on_header_clicked(1)
        self.assertEqual(self.asked[-1], ("Ni avg", False))

    def test_a_third_click_turns_sorting_off(self) -> None:
        """Back to the order the tab loaded the rows in."""
        for _ in range(3):
            self.table._on_header_clicked(1)
        self.assertEqual(self.asked[-1], ("", True))

    def test_and_a_fourth_starts_the_cycle_again(self) -> None:
        for _ in range(4):
            self.table._on_header_clicked(1)
        self.assertEqual(self.asked[-1], ("Ni avg", True))

    def test_turning_it_off_takes_the_arrow_away(self) -> None:
        for _ in range(3):
            self.table._on_header_clicked(1)
        self.assertEqual(self.table.horizontalHeader()._marked, -1)

    def test_moving_to_another_column_starts_ascending_again(self) -> None:
        self.table._on_header_clicked(1)
        self.table._on_header_clicked(1)
        self.table._on_header_clicked(0)
        self.assertEqual(self.asked[-1], ("Electrode", True))

    def test_the_arrow_points_at_the_sorted_column(self) -> None:
        self.table._on_header_clicked(1)
        header = self.table.horizontalHeader()
        self.assertEqual(header.sortIndicatorSection(), 1)
        self.assertEqual(header.sortIndicatorOrder(), Qt.SortOrder.AscendingOrder)

    def test_the_arrow_can_be_put_back_after_a_reload(self) -> None:
        """load_data rewrites the headers and the arrow does not survive it."""
        self.table._on_header_clicked(1)
        self.table.load_data(pd.DataFrame({"Electrode": ["B2"], "Ni avg": [4.0]}))
        self.table.show_sort_indicator("Ni avg", True)
        self.assertEqual(self.table.horizontalHeader().sortIndicatorSection(), 1)


if __name__ == "__main__":
    unittest.main()
