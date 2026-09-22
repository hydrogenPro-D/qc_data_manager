"""The control bar has to fit on a laptop screen.

Built against a bare instance, because the window opens the production
database in its constructor and the bar does not need it.
"""

import unittest

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QTableWidgetItem, QWidget,
)

from qc_sampling_script.gui.flow_layout import flow_host
from qc_sampling_script.gui.sampling_window import (
    BAR_HEIGHT, SamplingWindow, _Separator, _align, _wide,
)

_app = QApplication.instance() or QApplication([])

# Narrow enough to be a real laptop, and narrower than any one row of controls.
NARROW = 900
WIDE = 1920


class _Bar(unittest.TestCase):

    def setUp(self) -> None:
        self.window = SamplingWindow.__new__(SamplingWindow)
        # The Qt half has to exist, the analysis half does not.
        QMainWindow.__init__(self.window)
        self.window.backend = _FakeBackend()
        self.card = self.window._controls()
        self.host = QWidget()
        self.card.setParent(self.host)

    def _at(self, width: int):
        """Lay the card out at one width, and hand it back to be measured.

        Sized with heightForWidth, not sizeHint. sizeHint ignores the width by
        definition, which is exactly the thing under test here.
        """
        self.host.resize(width, 600)
        self.card.resize(width, self.card.heightForWidth(width))
        self.host.show()
        _app.processEvents()
        return self.card

    def _flow(self):
        """The wrapping layout inside the card."""
        line = self.card.layout().itemAt(0).layout()
        return line.itemAt(0).widget().layout()


class TestItFitsOnASmallScreen(_Bar):

    def _controls(self):
        return [child for child in self.card.findChildren(QWidget)
                if child.isVisible() and child.width() > 1
                and child.height() > 1]

    def test_nothing_hangs_off_the_right_edge_when_narrow(self):
        card = self._at(NARROW)
        for widget in self._controls():
            right = widget.mapTo(card, widget.rect().bottomRight()).x()
            self.assertLessEqual(right, card.width(),
                                 f"{widget.__class__.__name__} is cut off")

    def test_nothing_hangs_off_the_right_edge_when_wide(self):
        card = self._at(WIDE)
        for widget in self._controls():
            right = widget.mapTo(card, widget.rect().bottomRight()).x()
            self.assertLessEqual(right, card.width())

    def test_it_takes_more_height_when_it_has_to_wrap(self):
        self.assertGreater(self.card.heightForWidth(NARROW),
                           self.card.heightForWidth(WIDE))

    def test_it_only_ever_gets_taller_as_it_narrows(self):
        heights = [self.card.heightForWidth(width)
                   for width in (WIDE, 1366, 1280, 1024, NARROW, 800)]
        self.assertEqual(heights, sorted(heights))

    def test_one_row_when_there_is_plenty_of_room(self):
        # Measured on the flow layout itself, so the assertion does not depend
        # on the font the test platform happens to pick.
        self.assertEqual(self._flow().heightForWidth(6000), BAR_HEIGHT)

    def test_the_database_banner_is_never_the_piece_that_is_lost(self):
        # It sits outside the wrapping part on purpose: somebody reading these
        # numbers has to know which database they came from.
        card = self._at(NARROW)
        banner = [child for child in card.findChildren(QWidget)
                  if getattr(child, "text", None)
                  and "dataloggingDev" in child.text()]
        self.assertTrue(banner)
        right = banner[0].mapTo(card, banner[0].rect().topRight()).x()
        self.assertLessEqual(right, card.width())


class TestTheWrappingLayout(unittest.TestCase):

    def setUp(self) -> None:
        self.host, self.flow = flow_host(spacing=6, line_spacing=8)
        for _ in range(6):
            block = QWidget(self.host)
            block.setFixedSize(100, 30)
            self.flow.addWidget(block)

    def test_everything_on_one_row_when_there_is_room(self):
        self.assertEqual(self.flow.heightForWidth(1000), 30)

    def test_two_rows_when_there_is_not(self):
        self.assertEqual(self.flow.heightForWidth(330), 30 + 8 + 30)

    def test_three_rows_when_there_is_much_less(self):
        self.assertEqual(self.flow.heightForWidth(220), 30 * 3 + 8 * 2)

    def test_it_does_not_claim_a_whole_row_as_its_minimum(self):
        # Claiming it is what makes a layout refuse to shrink at all.
        self.assertLess(self.flow.minimumSize().width(), 200)

    def _row_end(self, width: int):
        """Two blocks with the real separator between them, at one width."""
        # Kept on self: a local host is garbage collected on the way out and
        # takes its Qt children with it.
        self.strip, flow = flow_host(spacing=6, line_spacing=8)
        host = self.strip
        first, second = QWidget(host), QWidget(host)
        for block in (first, second):
            block.setFixedSize(100, 30)
        divider = _Separator(10)
        divider.setParent(host)
        flow.addWidget(first)
        flow.addWidget(divider)
        flow.addWidget(second)
        host.resize(width, 200)
        flow.setGeometry(host.rect())
        return divider, second

    def test_a_separator_at_a_row_end_is_squeezed_away(self):
        """A hairline that ends a wrapped row separates nothing."""
        divider, second = self._row_end(200)
        self.assertEqual(divider.width(), 0)
        self.assertGreater(second.width(), 0)

    def test_a_separator_between_two_groups_on_one_row_stays(self):
        divider, second = self._row_end(600)
        self.assertGreater(divider.width(), 0)
        self.assertGreater(second.width(), 0)

    def test_it_keeps_every_item(self):
        self.assertEqual(self.flow.count(), 6)
        self.assertIsNotNone(self.flow.itemAt(5))
        self.assertIsNone(self.flow.itemAt(6))


class _FakeBackend:
    """Only what the banner asks for."""

    @staticmethod
    def databases() -> tuple[str, str]:
        return "dataloggingDev", "dataloggingDev"

    @staticmethod
    def database_name() -> str:
        return "dataloggingDev"

    @staticmethod
    def plant_database_name() -> str:
        return "dataloggingDev"


class TestThePerVatTableIsOnlyAsWideAsItsColumns(unittest.TestCase):
    """It used to share spare card width out among the columns.

    That made a column holding "28" as wide as one holding a date, and it
    overflowed: the leftover was measured against a viewport width that had not
    settled, so the last column ended up clipped behind a scrollbar that should
    never have been there at all.
    """

    HEADERS = ("Vat", "Electrodes", "Bad", "Runs", "Avg", "Median", "Std dev",
               "Bad %", "", "Good", "Runs", "Avg", "Median", "Std dev",
               "Good %")

    def setUp(self) -> None:
        self.host = QWidget()
        # Deliberately far wider than the content needs. The test platform has
        # no real fonts, so measured text is much wider than on a desktop and a
        # realistic width would fail here for the wrong reason.
        self.host.resize(3000, 300)
        self.table = _wide(self.HEADERS, parent=self.host)
        self.totals = _wide(self.HEADERS, parent=self.host)
        self.totals.horizontalHeader().setVisible(False)
        for table, first in ((self.table, "28"), (self.totals, "All vats")):
            table.setRowCount(1)
            table.setItem(0, 0, QTableWidgetItem(first))
            for column in range(1, len(self.HEADERS)):
                table.setItem(0, column, QTableWidgetItem("12.3 %"))
        for table in (self.table, self.totals):
            table.resize(self.host.width(), 80)
        self.host.show()
        _app.processEvents()
        self.total = _align(self.table, self.totals)

    def _widths(self):
        return [self.table.columnWidth(column)
                for column in range(self.table.columnCount())]

    def test_it_stops_at_its_last_column(self):
        self.assertEqual(self.table.maximumWidth(), sum(self._widths()) + 2)

    def test_it_does_not_fill_the_card(self):
        self.assertLess(self.table.maximumWidth(), self.host.width())

    def test_a_column_holding_a_vat_number_stays_narrow(self):
        # Wide enough for "All vats" in the totals row, and no wider.
        widths = self._widths()
        self.assertLess(widths[0], max(widths))

    def test_it_reports_what_the_columns_came_to(self):
        self.assertEqual(self.total, sum(self._widths()))

    def test_running_it_again_changes_nothing(self):
        # It has to converge: _layout_tables runs it again whenever the total
        # moves, and a measurement that read the current widths back would
        # creep instead of settling.
        self.assertEqual(_align(self.table, self.totals), self.total)
        self.assertEqual(_align(self.table, self.totals), self.total)

    def test_both_tables_share_one_set_of_widths(self):
        self.assertEqual(
            self._widths(),
            [self.totals.columnWidth(column)
             for column in range(self.totals.columnCount())])

    def test_no_scrollbar_when_the_columns_fit(self):
        # The columns live in the viewport, not in the widget. Sizing the
        # widget to the column total left the viewport short of holding them,
        # which raised a bar over the bottom row and clipped the last column.
        for table in (self.table, self.totals):
            table.resize(table.maximumWidth(), table.height())
        _app.processEvents()
        for table in (self.table, self.totals):
            self.assertGreaterEqual(table.viewport().width(),
                                    sum(self._widths()))

    def test_these_tables_never_scroll_vertically(self):
        # One row per vat, and _fit heights the table to all of them. A bar
        # that appears for a pixel of rounding costs viewport width.
        for table in (self.table, self.totals):
            self.assertEqual(table.verticalScrollBarPolicy(),
                             Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
