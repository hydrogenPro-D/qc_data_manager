"""Reusable table widget for displaying and managing a DataFrame."""

from __future__ import annotations

import re

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Optional

import pandas as pd
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QTableWidget, QTableWidgetItem, QHeaderView
from PyQt6.QtGui import QColor, QBrush, QFont, QWheelEvent


@dataclass
class CellStyle:
    """Per-cell emphasis returned by a tab's ``cell_style_callback``.

    Lets a tab mark a value (a status, a limit breach) without the shared table
    knowing anything about the domain. Overrides the alternating row colour for
    that one cell.
    """

    background: str | None = None
    foreground: str | None = None
    bold: bool = False
    align_center: bool = False
    strikethrough: bool = False


class DataTableWidget(QTableWidget):

    # How far the zoom goes, and the row heights it scales. Bounded because a
    # table zoomed past either end stops being a table: too small to read, or
    # two columns wide.
    ZOOM_MIN = 0.8
    ZOOM_MAX = 1.8
    ZOOM_STEP = 0.1
    BASE_ROW_HEIGHT = 30
    LINE_HEIGHT = 25
    """Renders a DataFrame; cell text goes through an optional format callback."""

    def __init__(
        self,
        parent=None,
        min_column_width: int = 80,
        max_column_width: int = 250,
        enable_alternating_colors: bool = True,
        enable_row_coloring_on_add: bool = True,
        min_table_width: int = 0,
        min_table_height: int = 70,
        scroll_to_top_on_load: bool = False,
    ) -> None:
        """Initialize the data table widget.

        Args:
            parent: Parent widget
            min_column_width: Minimum column width (None for no minimum)
            max_column_width: Maximum column width (None for no maximum)
            enable_alternating_colors: Use alternating row colors
            enable_row_coloring_on_add: Color newly added rows
            min_table_width: Minimum table width
            min_table_height: Minimum table height. Low by default: a
                table scrolls its own rows, so it only has to leave the
                header and a row visible, and a larger floor here ends up
                as the whole window's minimum height.
            scroll_to_top_on_load: Land on the first row after a load. Use it on
                newest-first tables; the default lands on the last row, which is
                what oldest-first tables want.
        """
        super().__init__(parent)
        self.setColumnCount(0)
        self.setRowCount(0)
        self.setMinimumHeight(min_table_height)
        self.setMinimumWidth(min_table_width)
        self.horizontalHeader().setStretchLastSection(False)
        self.setHorizontalScrollMode(QTableWidget.ScrollMode.ScrollPerPixel)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QTableWidget.SelectionMode.NoSelection)

        # Measured from the size the table was built at, never from the last
        # step, so stepping up and back down lands exactly where it started.
        self._zoom = 1.0
        # Captured on the first zoom: the owning tab sets the sheet after this
        # constructor runs, so there is nothing to remember yet.
        self._base_stylesheet: str | None = None

        self.min_column_width = min_column_width
        self.max_column_width = max_column_width
        # Header names exempt from the max-width cap (sized to full content).
        self.uncapped_columns: set[str] = set()
        # Whether load_data auto-resizes columns. Tabs that resize explicitly
        # afterwards turn this off to skip a redundant O(rows×cols) pass.
        self._resize_on_load = True
        # When True, columns stretch to share the width equally instead of
        # being content-sized (no horizontal scrollbar).
        self._fill_columns = False
        # When True, content-sized columns share out any width left over, so
        # the table fills its pane without any column being cut short.
        self._fill_remaining = False
        self.enable_alternating_colors = enable_alternating_colors
        self.enable_row_coloring_on_add = enable_row_coloring_on_add
        self.scroll_to_top_on_load = scroll_to_top_on_load

        # Color palette for alternating rows
        self.light_color = QBrush(QColor(255, 255, 255))  # White
        self.dark_color = QBrush(QColor(248, 249, 250))   # Very light gray-blue

        # Colors for newly added rows
        self.new_row_light_color = QBrush(QColor(245, 245, 250))
        self.new_row_dark_color = QBrush(QColor(230, 240, 250))

        self.setAlternatingRowColors(enable_alternating_colors)

    def wheelEvent(self, event: QWheelEvent) -> None:
        """Scroll within the table; propagate to parent only at top/bottom edge."""
        scrollbar = self.verticalScrollBar()
        at_top = scrollbar.value() == scrollbar.minimum()
        at_bottom = scrollbar.value() == scrollbar.maximum()

        if (at_top and event.angleDelta().y() > 0) or (at_bottom and event.angleDelta().y() < 0):
            event.ignore()
            return

        super().wheelEvent(event)
        event.accept()

    def load_data(
        self,
        dataframe: pd.DataFrame,
        display_column_order: Optional[list[str]] = None,
        styling_callback: Optional[callable] = None,
        cell_style_callback: Optional[callable] = None,
    ) -> None:
        """Load data into the table.

        Args:
            dataframe: The dataframe to display
            display_column_order: Optional columns in display order
            styling_callback: Optional ``(col_index, value) -> str`` cell formatter
            cell_style_callback: Optional ``(row_index, column_name, value) ->
                CellStyle|None``. Keyed by header name rather than index, so it
                survives a column reorder; the row index lets a tab tint a whole
                row from a value in one of its other columns.
        """
        if dataframe is None or dataframe.empty:
            self.setRowCount(0)
            self.setColumnCount(0)
            return

        display_df = dataframe[display_column_order] if display_column_order else dataframe

        # Empty it first, which is what drops the cell widgets. setRowCount and
        # setColumnCount only delete the ones in rows or columns that
        # disappear, and setItem does not touch them at all, so a redraw of the
        # same shape keeps them and a redraw whose columns MOVED keeps them in
        # the wrong column. That is how the SEM tab's View buttons ended up
        # among the Ni:S numbers while it was still loading. Costs nothing:
        # _populate_row builds a new item for every cell either way.
        self.setRowCount(0)

        self.setColumnCount(len(display_df.columns))
        self.setHorizontalHeaderLabels(display_df.columns.astype(str))
        self.setRowCount(len(display_df))

        for row_idx, (_, row) in enumerate(display_df.iterrows()):
            self._populate_row(row_idx, row, is_new_row=False,
                               styling_callback=styling_callback,
                               cell_style_callback=cell_style_callback)

        # Size columns: stretch-to-fill, or content-size (unless the tab resizes
        # explicitly afterwards).
        if self._fill_columns:
            self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        elif self._resize_on_load:
            self._resize_columns(len(display_df.columns))

        if self.scroll_to_top_on_load:
            self.scrollToTop()
        else:
            self.scrollToBottom()

    @contextmanager
    def keep_scroll(self):
        """Reload without the view moving.

        load_data lands on the first or last row, which is what a new dataset
        wants and the opposite of what a row the operator just edited or
        deleted wants: the table would jump away from what they were reading.
        Clamped, since the list may now be shorter than where they were.
        """
        bar = self.verticalScrollBar()
        at = bar.value()
        try:
            yield
        finally:
            bar.setValue(min(at, bar.maximum()))

    def add_row(
        self,
        new_row: dict,
        display_column_order: Optional[list[str]] = None,
        styling_callback: Optional[callable] = None,
    ) -> None:
        """Append one row in place (the optimistic echo after a save)."""
        row_idx = self.rowCount()
        self.insertRow(row_idx)

        if display_column_order:
            row_data = {col: new_row.get(col, "") for col in display_column_order}
        else:
            row_data = new_row

        self._populate_row(row_idx, row_data, is_new_row=True, styling_callback=styling_callback)

    def update_row(
        self,
        row_idx: int,
        row: dict,
        display_column_order: Optional[list[str]] = None,
        styling_callback: Optional[callable] = None,
    ) -> None:
        """Rewrite one existing row in place (the optimistic echo after an edit)."""
        if row_idx < 0 or row_idx >= self.rowCount():
            return
        if display_column_order:
            row = {col: row.get(col, "") for col in display_column_order}
        self._populate_row(row_idx, row, is_new_row=False, styling_callback=styling_callback)

    def _populate_row(
        self,
        row_idx: int,
        row,
        is_new_row: bool = False,
        styling_callback: Optional[callable] = None,
        cell_style_callback: Optional[callable] = None,
    ) -> None:
        """Populate a single row (from a dict or pandas Series)."""
        if self.enable_row_coloring_on_add and is_new_row:
            row_color = self.new_row_dark_color if row_idx % 2 == 0 else self.new_row_light_color
        elif self.enable_alternating_colors:
            row_color = self.dark_color if row_idx % 2 == 0 else self.light_color
        else:
            row_color = None

        values = row.values() if isinstance(row, dict) else row.values

        for col_idx, value in enumerate(values):
            item = QTableWidgetItem(self._display_value(col_idx, value, styling_callback))
            if row_color:
                item.setBackground(row_color)
            if cell_style_callback:
                self._apply_cell_style(
                    item, cell_style_callback(row_idx, self._column_name(col_idx), value))
            self.setItem(row_idx, col_idx, item)

        self._update_row_height(row_idx)

    def _column_name(self, col_idx: int) -> str:
        """Header text for a column ('' if it has none)."""
        header = self.horizontalHeaderItem(col_idx)
        return header.text() if header is not None else ""

    @staticmethod
    def _apply_cell_style(item: QTableWidgetItem, style: Optional[CellStyle]) -> None:
        """Apply a tab's CellStyle over the row's alternating colour."""
        if style is None:
            return
        if style.background:
            item.setBackground(QBrush(QColor(style.background)))
        if style.foreground:
            item.setForeground(QBrush(QColor(style.foreground)))
        if style.bold or style.strikethrough:
            font = item.font()
            font.setBold(font.bold() or style.bold)
            font.setStrikeOut(style.strikethrough)
            item.setFont(font)
        if style.align_center:
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

    @staticmethod
    def _display_value(col_idx: int, value, styling_callback: Optional[callable]) -> str:
        """Cell value → display text. Blanks become '-'; whole floats lose the
        trailing '.0'; anything else goes through the tab's format callback."""
        if value is None or value is pd.NA or (isinstance(value, float) and pd.isna(value)):
            return "-"
        if styling_callback:
            formatted = styling_callback(col_idx, value)
            if formatted is not None:
                return str(formatted)
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)

    def _update_row_height(self, row_idx: int) -> None:
        """Set the row height from the tallest (most newlines) cell in the row."""
        max_lines = 1
        for col_idx in range(self.columnCount()):
            item = self.item(row_idx, col_idx)
            if item:
                max_lines = max(max_lines, item.text().count("\n") + 1)

        height = self.BASE_ROW_HEIGHT + (max_lines - 1) * self.LINE_HEIGHT
        self.setRowHeight(row_idx, round(height * self._zoom))

    def zoom(self) -> float:
        """The current scale, 1.0 being the size the table was built at."""
        return self._zoom

    def set_zoom(self, scale: float) -> None:
        """Scale the text and the rows together.

        Both, or the effect is useless: a bigger font in a row of fixed height
        is clipped, and taller rows on their own only waste the screen. The
        delegates take their font from the widget, so the badges follow along
        without knowing anything about this.
        """
        scale = max(self.ZOOM_MIN, min(self.ZOOM_MAX, round(scale, 2)))
        if scale == self._zoom:
            return
        self._zoom = scale

        # Through the stylesheet, not setFont. A Qt style sheet that names a
        # font-size wins over the widget's font, so setting the font here
        # scaled the header (which has none) and left the cells alone. Every
        # size in the sheet is scaled together, so the table keeps its
        # proportions and this stays domain-neutral: the sizes live in the
        # style, not in here.
        if self._base_stylesheet is None:
            self._base_stylesheet = self.styleSheet()
        self.setStyleSheet(re.sub(
            r"font-size:\s*(\d+)px",
            lambda m: f"font-size: {max(1, round(int(m.group(1)) * self._zoom))}px",
            self._base_stylesheet))

        for row in range(self.rowCount()):
            self._update_row_height(row)
        self.resize_columns_to_contents_constrained()

    @staticmethod
    def sync_row_heights(tables) -> None:
        """Align row heights across side-by-side tables so a multi-line cell in
        one grows the matching row in ALL of them, keeping them row-aligned."""
        tables = [t for t in tables if t is not None]
        if not tables:
            return
        row_count = max((t.rowCount() for t in tables), default=0)
        for row in range(row_count):
            heights = [t.rowHeight(row) for t in tables if row < t.rowCount()]
            if not heights:
                continue
            tallest = max(heights)
            for table in tables:
                if row < table.rowCount():
                    table.setRowHeight(row, tallest)

    @staticmethod
    def scroll_all_to_bottom(tables) -> None:
        """Scroll a group of row-synced tables to the bottom in lockstep.
        Deferred one event cycle so the scrollbar range already reflects the
        final row heights."""
        tables = [t for t in tables if t is not None]
        if not tables:
            return

        def _do():
            for table in tables:
                table.scrollToBottom()

        QTimer.singleShot(0, _do)

    def set_columns_fill(self, enabled: bool = True) -> None:
        """Make columns stretch to share the width equally (no content sizing,
        no horizontal scrollbar). Applied now and on every load_data."""
        self._fill_columns = enabled
        mode = QHeaderView.ResizeMode.Stretch if enabled else QHeaderView.ResizeMode.Interactive
        if self.columnCount() > 0:
            self.horizontalHeader().setSectionResizeMode(mode)

    def resizeColumnsToContents(self) -> None:  # noqa: N802 (Qt naming)
        """Content-size columns — but fill-mode tables keep their equal stretch
        (content sizing would override it and bring back the scrollbar)."""
        if self._fill_columns and self.columnCount() > 0:
            self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
            return
        super().resizeColumnsToContents()

    def set_resize_on_load(self, enabled: bool) -> None:
        """Enable/disable the automatic column resize inside ``load_data``."""
        self._resize_on_load = enabled

    def set_uncapped_columns(self, names) -> None:
        """Mark columns (by header name) to size to full content, exempt from the
        max-width cap. Re-applies the current column sizing."""
        self.uncapped_columns = set(names)
        if self.columnCount():
            self._resize_columns(self.columnCount())

    def resize_columns_to_contents_constrained(self) -> None:
        """Resize columns to fit content, clamped to min/max column width.

        Use instead of raw ``resizeColumnsToContents()`` to keep the constraints.
        """
        self._resize_columns(self.columnCount())

    def fit_last_column(self, padding: int = 16) -> None:
        """Give the last column the leftover viewport width when its content is
        short, but its full content width (with the table scrolling) when long.
        Better than stretch-last, which truncates long free-text cells."""
        if self.columnCount() == 0:
            return
        last = self.columnCount() - 1
        content = self.sizeHintForColumn(last) + padding
        others = sum(self.columnWidth(i) for i in range(last))
        remainder = self.viewport().width() - others
        self.horizontalHeader().setStretchLastSection(False)
        self.setColumnWidth(last, max(content, remainder))

    def set_fill_remaining_width(self, enabled: bool = True) -> None:
        """After content sizing, share any leftover width across the columns.

        Unlike ``set_columns_fill``, no column is ever squeezed below its
        content: this only ever hands out space the table already has spare.
        """
        self._fill_remaining = enabled
        if self.columnCount():
            self.fill_remaining_width()

    def fill_remaining_width(self) -> None:
        """Grow every column proportionally until the pane is full."""
        if not self._fill_remaining or self.columnCount() == 0:
            return
        widths = [self.columnWidth(col) for col in range(self.columnCount())]
        total = sum(widths)
        spare = self.viewport().width() - total
        if total <= 0 or spare <= 0:
            return

        # Proportional, so a wide column grows more than a narrow one and the
        # relative shape of the table is preserved.
        handed_out = 0
        for col, width in enumerate(widths[:-1]):
            share = int(spare * width / total)
            handed_out += share
            self.setColumnWidth(col, width + share)
        # Rounding leftovers go to the last column, so the fit is exact.
        self.setColumnWidth(len(widths) - 1, widths[-1] + spare - handed_out)

    def _resize_columns(self, num_columns: int) -> None:
        """Resize columns to fit content with min/max constraints."""
        self.resizeColumnsToContents()
        for col in range(num_columns):
            current_width = self.columnWidth(col)
            header = self.horizontalHeaderItem(col)

            # Columns flagged uncapped keep full content width (min still applies).
            if header is not None and header.text() in self.uncapped_columns:
                if self.min_column_width and current_width < self.min_column_width:
                    self.setColumnWidth(col, self.min_column_width)
                continue

            if self.min_column_width and current_width < self.min_column_width:
                self.setColumnWidth(col, self.min_column_width)
            elif self.max_column_width and current_width > self.max_column_width:
                self.setColumnWidth(col, self.max_column_width)

        self.fill_remaining_width()
