"""Reusable table widget for displaying and managing a DataFrame."""

from __future__ import annotations

import re

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Optional

import pandas as pd
from PyQt6.QtCore import Qt, QPointF, QTimer, pyqtSignal
from PyQt6.QtWidgets import QTableWidget, QTableWidgetItem, QHeaderView
from PyQt6.QtGui import (QBrush, QColor, QFont, QPainter, QPolygonF,
                         QWheelEvent)


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


class SortHeader(QHeaderView):
    """A header that draws its own sort arrow.

    Qt's indicator is a few grey pixels that vanish against a coloured header,
    and an operator could not tell a sorted column from an unsorted one. This
    one is a solid white triangle, the same colour as the header text, sized to
    be read at a glance rather than looked for.
    """

    ARROW_WIDTH = 14
    ARROW_HEIGHT = 8
    ARROW_MARGIN = 7

    @classmethod
    def arrow_room(cls) -> int:
        """How much wider a column has to be to carry the arrow.

        TWICE what the arrow occupies, because the heading is centred: widen a
        column by X and only X/2 of it lands on the right, so half the arrow
        still sat on the last letters. At twice, the gap on the right is at
        least the arrow's own width however long the heading is.
        """
        return (cls.ARROW_WIDTH + cls.ARROW_MARGIN * 2) * 2

    def __init__(self, parent=None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        # Qt's own is off: two arrows on one header is worse than none.
        self.setSortIndicatorShown(False)
        self._marked = -1
        self._ascending = True

    def mark(self, column: int, ascending: bool) -> None:
        """Point the arrow at one column, or nowhere with a column of -1."""
        self._marked, self._ascending = column, ascending
        self.viewport().update()

    def paintSection(self, painter, rect, index) -> None:
        super().paintSection(painter, rect, index)
        if index != self._marked:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        width, height = self.ARROW_WIDTH, self.ARROW_HEIGHT
        left = rect.right() - self.ARROW_MARGIN - width
        top = rect.center().y() - height / 2
        if self._ascending:
            corners = [(left, top + height), (left + width, top + height),
                       (left + width / 2, top)]
        else:
            corners = [(left, top), (left + width, top),
                       (left + width / 2, top + height)]
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in corners]))
        painter.restore()


class DataTableWidget(QTableWidget):

    # Asks the owning tab to re-sort its frame: ``(header text, ascending)``.
    #
    # A SIGNAL RATHER THAN QTableWidget's OWN SORTING, which moves the items
    # and leaves the cell widgets where they were. The SEM tab puts a real
    # button in its images column, so its own sorting would have scattered
    # those buttons across the wrong samples. The tab owns the frame; it
    # re-sorts that and renders again, and every row is rebuilt in step.
    sort_requested = pyqtSignal(str, bool)

    # How far the zoom goes, and the row heights it scales. Bounded because a
    # table zoomed past either end stops being a table: too small to read, or
    # two columns wide.
    ZOOM_MIN = 0.8
    ZOOM_MAX = 1.8
    ZOOM_STEP = 0.1
    # Four more than it was. The badges very nearly filled a thirty pixel row
    # and a table of them read as one solid block; the air between is what
    # lets the eye find a row again after looking away.
    BASE_ROW_HEIGHT = 34
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
        # Installed for every table, drawing nothing until one is marked. Set
        # here rather than in enable_sorting, so nothing that took a reference
        # to the header afterwards is left holding the one it replaced.
        self.setHorizontalHeader(SortHeader(self))
        # Which column a header click last asked for, and which way.
        self._sorted_by = ""
        self._sorted_ascending = True
        # Which column has already been widened to hold the arrow. Reset by
        # every load, which recomputes the widths from the content again.
        self._arrow_room_column = -1
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
        # Columns that keep their content width rather than taking a share of
        # the spare, see set_no_growth_columns.
        self.no_growth_columns: set[str] = set()
        # Whether the spare goes to the last column alone, see
        # set_fill_last_only.
        self._fill_last_only = False
        # Whether columns give up width to stay on screen, see set_shrink_to_fit.
        self._shrink_to_fit = False
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
        # The widths below are worked out from the content, so whatever room
        # the arrow was given last time has gone with them.
        self._arrow_room_column = -1
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

    def enable_sorting(self) -> None:
        """Let a header click ask for a sort. Off unless a tab asks for it.

        The tab decides what the order means, so all this does is turn clicks
        into ``sort_requested`` and show which way the arrow points.
        """
        header = self.horizontalHeader()
        header.setSectionsClickable(True)
        header.sectionClicked.connect(self._on_header_clicked)

    def _on_header_clicked(self, column: int) -> None:
        """Ascending, then descending, then off again.

        Three states rather than two, because the order the tab loaded the rows
        in means something of its own, production order on the SEM tab, and
        without a third click there was no way back to it short of leaving the
        tab and coming back.
        """
        name = self._column_name(column)
        if not name:
            return
        if self._sorted_by != name:
            wanted = (name, True)
        elif self._sorted_ascending:
            wanted = (name, False)
        else:
            wanted = ("", True)
        self.show_sort_indicator(*wanted)
        self.sort_requested.emit(*wanted)

    def show_sort_indicator(self, name: str, ascending: bool) -> None:
        """Point the arrow at this column. Re-applied after every load.

        ``load_data`` rewrites the headers, and the arrow does not survive
        that, so the tab puts it back rather than leaving a table that is
        sorted but does not say so. An empty name takes the arrow off.
        """
        self._sorted_by, self._sorted_ascending = name, ascending
        header = self.horizontalHeader()
        if not name:
            if isinstance(header, SortHeader):
                header.mark(-1, ascending)
            self._arrow_room_column = -1
            return
        for column in range(self.columnCount()):
            if self._column_name(column) != name:
                continue
            header.setSortIndicator(
                column, Qt.SortOrder.AscendingOrder if ascending
                else Qt.SortOrder.DescendingOrder)
            if isinstance(header, SortHeader):
                header.mark(column, ascending)
                # Room for the arrow, rather than letting it sit on the last
                # letters of the heading. Once per load: the widths are worked
                # out again by each one, and widening a column that had already
                # been widened would walk it across the table.
                if self._arrow_room_column != column:
                    header.resizeSection(
                        column, header.sectionSize(column) + header.arrow_room())
                    self._arrow_room_column = column
            return

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

    def set_shrink_to_fit(self, enabled: bool = True) -> None:
        """Squeeze the widest columns until the table fits, rather than scroll.

        Content sizing gives every column what it asks for, which on a small
        screen is more than there is: the last columns then sit off the right
        edge and an operator has to scroll sideways to find out whether an
        electrode has been requested. Taking the shortfall off the widest
        columns, which are the ones holding a long badge that elides cleanly,
        keeps every column on screen at the cost of a few truncated words.
        """
        self._shrink_to_fit = enabled
        if self.columnCount():
            self.shrink_columns_to_fit()

    def _column_needs(self, col: int) -> int:
        """What the cells and the heading of one column actually take."""
        return max(self.sizeHintForColumn(col),
                   self.horizontalHeader().sectionSizeHint(col))

    def shrink_columns_to_fit(self) -> None:
        """Give up width until the table fits, cheapest pixels first.

        Two passes, because they cost different things. First the slack above a
        column's own content, which costs nothing at all to lose. Only if that
        is not enough does it start cutting into content, and then off the
        widest each time, so the long badges give way before the short ones and
        no column is flattened while another keeps room it is not using.
        """
        if not self._shrink_to_fit or self.columnCount() == 0:
            return
        widths = [self.columnWidth(col) for col in range(self.columnCount())]
        over = sum(widths) - self.viewport().width()
        if over <= 0:
            return
        floor = self.min_column_width or 40

        for col, width in enumerate(widths):
            if over <= 0:
                break
            spare = width - max(self._column_needs(col), floor)
            if spare > 0:
                take = min(spare, over)
                widths[col] -= take
                over -= take

        while over > 0:
            widest = max(range(len(widths)), key=lambda col: widths[col])
            if widths[widest] <= floor:
                break
            take = min(over, widths[widest] - floor, 8)
            widths[widest] -= take
            over -= take

        for col, width in enumerate(widths):
            self.setColumnWidth(col, width)

    def set_fill_last_only(self, enabled: bool = True) -> None:
        """Hand every spare pixel to the last column instead of sharing them.

        Sharing in proportion suits a table whose columns all hold free text.
        It suits one with a Vat and a Recipes column badly: a three character
        column grew with the rest and ended up mostly empty, and on a small
        screen that waste is what pushes the last column off the edge. With
        this, every column keeps its content width and the one at the end takes
        whatever the window happens to have spare.
        """
        self._fill_last_only = enabled
        if self.columnCount():
            self.fill_remaining_width()

    def set_no_growth_columns(self, names) -> None:
        """Columns that keep their content width when spare space is shared.

        Spare width goes out in proportion to what a column already has, so the
        widest column grows the most. That is right for one holding free text
        and wrong for one holding a fixed shape: a timestamp is the same twelve
        characters on every row, and growing it only takes room from a column
        that could have used it.
        """
        self.no_growth_columns = {str(name) for name in (names or ())}
        if self.columnCount():
            self.fill_remaining_width()

    def _header_text(self, col: int) -> str:
        header = self.horizontalHeaderItem(col)
        return header.text() if header is not None else ""

    def fill_remaining_width(self) -> None:
        """Grow the columns that may grow, proportionally, until the pane is full."""
        if not self._fill_remaining or self.columnCount() == 0:
            return
        widths = [self.columnWidth(col) for col in range(self.columnCount())]
        spare = self.viewport().width() - sum(widths)
        if spare <= 0:
            return
        growable = [col for col in range(self.columnCount())
                    if self._header_text(col) not in self.no_growth_columns]
        if not growable:
            return
        # All of it to the last one, for a table of mostly fixed-shape columns.
        if self._fill_last_only:
            last = growable[-1]
            self.setColumnWidth(last, widths[last] + spare)
            return
        total = sum(widths[col] for col in growable)
        if total <= 0:
            return

        # Proportional, so a wide column grows more than a narrow one and the
        # relative shape of the table is preserved.
        handed_out = 0
        for col in growable[:-1]:
            share = int(spare * widths[col] / total)
            handed_out += share
            self.setColumnWidth(col, widths[col] + share)
        # Rounding leftovers go to the last growable column, so the fit is exact.
        last = growable[-1]
        self.setColumnWidth(last, widths[last] + spare - handed_out)

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
        # Last, because it only acts on what the two above have settled on.
        self.shrink_columns_to_fit()
