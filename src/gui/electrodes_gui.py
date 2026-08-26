"""Electrodes tab — full-screen table of electrodes with status filtering.

Thin by design: it never imports the backend. Everything it needs arrives as
injected callables, so it can be unit-tested with fakes and hand-built frames.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QToolButton, QDialog,
    QPushButton, QScrollArea,
)
from PyQt6.QtCore import Qt, QEvent, QObject, QSize
from PyQt6.QtGui import QFont
import pandas as pd
import qtawesome as qta

from .styles.table_styles import (
    TABLE_STYLE,
    SCROLLBAR_STYLE,
    SCROLLBAR_HORIZONTAL_HEIGHT,
    SCROLLBAR_VERTICAL_WIDTH,
)
from .styles.button_styles import TOOLBAR_BUTTON_STYLE, apply_modern_shadow
from .styles.tab_styles import create_styled_container_layout
from .widgets.error_dialogs import ValidationErrorScrollDialog
from .widgets.report_dialog import ReportDialog, build_report_html
from src.data_access.electrodes.status_ranges import collapse_to_ranges
from .widgets.data_table_widget import DataTableWidget, CellStyle
from .widgets.filter_bar_widget import FilterBarWidget, FilterDropdown
from .widgets.table_delegates import (RowFillDelegate, StatusPillDelegate,
                                      PillStyle, RowHighlight)
from .widgets.status_donut_widget import StatusDonutWidget
from .widgets.status_edit_dialog import StatusEditDialog
from .date_display import format_timestamp_for_display
from src.data_access.electrodes.electrode_columns import (
    ElectrodeColumns,
    ElectrodeStatus,
    STATUS_COLUMN,
    VAT_COLUMN,
    PRIORITY_COLUMN,
    PRIORITY_FLAG,
    LOCKED_BY_COLUMN,
    status_family,
)

# Display text is separate from the contract: the keys are the stored column
# names (dbo.MetaData), the values are what the operator sees. Relabel here —
# never by renaming the source column.
COLUMN_DISPLAY_NAMES: dict[str, str] = {
    str(ElectrodeColumns.DATE): "Date",
    str(ElectrodeColumns.MARKING): "Marking",
    str(ElectrodeColumns.FULL_NAME): "Electrode type",
    str(ElectrodeColumns.RECIPE): "Recipes",
    # Present only when the process log could be read, and the render drops any
    # column the frame does not carry.
    VAT_COLUMN: "Vat",
    STATUS_COLUMN: "Status",
}

# Display text for the two statuses that are decided by testing the electrode
# itself. Testing destroys it, so it can never be sold: "Tested and Released"
# said the opposite of what happened to it. What the status really records is
# that THIS electrode was the one measured, and the verdict is what releases or
# scraps its neighbours.
#
# Display only. The stored values are a live database contract and are
# untouched; see status_label and status_value below for the round trip.
STATUS_DISPLAY_NAMES: dict[str, str] = {
    str(ElectrodeStatus.TESTED_AND_RELEASED): "Tested, passed",
    str(ElectrodeStatus.TESTED_AND_SCRAPPED): "Tested, failed",
}
_STORED_BY_LABEL = {label: stored for stored, label in STATUS_DISPLAY_NAMES.items()}

# The two scrap lines of the report summary, worded as the old workbook worded
# them. The first is the electrodes that were never tested; the second adds
# every tested one, passed or failed, since testing consumes it either way.
SCRAP_WITHOUT_QC = "Scrapped (without QC)"
SCRAP_WITH_QC = "Scrapped + scrapped due to QC"

# The first row of that summary: everything the report covers, which is what
# the rows beneath are a percentage of. Named as the old workbook named it.
ALL_PRODUCED = "All produced electrodes"


def status_label(stored) -> str:
    """The stored status as the operator should read it."""
    text = str(stored).strip()
    return STATUS_DISPLAY_NAMES.get(text, text)


def status_value(label) -> str:
    """Back the other way, for anything the operator picked."""
    text = str(label).strip()
    return _STORED_BY_LABEL.get(text, text)


# The test-first flag rides along in the Status cell rather than taking a column
# of its own; the delegate strips it back off to pick the badge style.
PRIORITY_SEPARATOR = "   "

# The ring beside the table. Sized so the panel holding it stays narrower than
# the table it sits next to.
DONUT_DIAMETER = 212

# That panel: the donut, the test-first list and the report button. Wide enough
# for the ring and its legend, plus the scrollbar it gets on a short window, so
# the ring never loses room to it.
CHART_PANEL_WIDTH = 330 + SCROLLBAR_VERTICAL_WIDTH


def status_pill_style(value: str) -> PillStyle | None:
    """Badge style for a Status cell, ignoring any test-first suffix.

    Matched by prefix, since the suffix is separated by spaces alone and the
    statuses themselves contain spaces.
    """
    text = str(value).strip()
    for status, style in PILL_STYLES.items():
        if text == status or text.startswith(status):
            if (status == status_label(ElectrodeStatus.NEEDS_TESTING)
                    and PRIORITY_FLAG in text):
                return NEEDS_TESTING_FIRST_STYLE
            return style
    return None

# Filter dropdown keys.
FILTER_STATUS = "status"
FILTER_TYPE = "type"
FILTER_RECIPE = "recipe"
FILTER_VAT = "vat"

# Row tint, per status — one flat colour across the whole row. Hue carries the
# outcome, depth carries the evidence: a verdict tested on the board gets the
# stronger shade, one inherited from a neighbour the lighter. Not Tested stays
# neutral, since it is not a verdict at all.
ROW_TINTS: dict[str, str] = {
    str(ElectrodeStatus.TESTED_AND_RELEASED): "#BBEFCD",
    str(ElectrodeStatus.RELEASED): "#DCF5E4",
    str(ElectrodeStatus.TESTED_AND_SCRAPPED): "#F3AFAF",
    str(ElectrodeStatus.SCRAPPED): "#F7C9C9",
    # Amber: an instruction rather than a verdict, so it shouldn't read as
    # either good or bad.
    str(ElectrodeStatus.NEEDS_TESTING): "#FDE8C4",
    str(ElectrodeStatus.NOT_TESTED): "#F3F4F6",
}
ROW_ACCENTS: dict[str, str] = {
    str(ElectrodeStatus.TESTED_AND_RELEASED): "#15803D",
    str(ElectrodeStatus.RELEASED): "#22C55E",
    str(ElectrodeStatus.TESTED_AND_SCRAPPED): "#991B1B",
    str(ElectrodeStatus.SCRAPPED): "#EF4444",
    str(ElectrodeStatus.NEEDS_TESTING): "#F59E0B",
    str(ElectrodeStatus.NOT_TESTED): "#9CA3AF",
}
# Solid badge = tested on the QC board; outlined = inherited from a neighbour.
# Keyed by the DISPLAYED text, since that is what the delegate is handed.
PILL_STYLES: dict[str, PillStyle] = {
    status_label(ElectrodeStatus.TESTED_AND_RELEASED): PillStyle(
        color="#15803D", background="#15803D", text="#FFFFFF", glyph="✓"),
    status_label(ElectrodeStatus.RELEASED): PillStyle(color="#15803D", glyph="✓"),
    status_label(ElectrodeStatus.TESTED_AND_SCRAPPED): PillStyle(
        color="#B91C1C", background="#B91C1C", text="#FFFFFF"),
    status_label(ElectrodeStatus.SCRAPPED): PillStyle(color="#B91C1C"),
    status_label(ElectrodeStatus.NEEDS_TESTING): PillStyle(color="#B45309"),
    status_label(ElectrodeStatus.NOT_TESTED): PillStyle(color="#6B7280"),
}

# The "!" is reserved for the electrodes to pull first, so it stays meaningful
# instead of appearing on every one of the hundreds needing testing.
NEEDS_TESTING_FIRST_STYLE = PillStyle(color="#B45309", glyph="!")

# The longest badge the Status column can ever hold: the longest status, its
# glyph, and the test-first suffix. Sizing for this keeps the column wide
# enough no matter which rows Qt happens to measure.
WIDEST_STATUS_LABEL = max(
    (f"{style.glyph}  {status}{PRIORITY_SEPARATOR}{PRIORITY_FLAG}"
     for status, style in PILL_STYLES.items()),
    key=len,
)


class ElectrodesWidget(QWidget):
    """Renders the electrode table with status chips and a search box."""

    def __init__(
        self,
        parent=None,
        on_data_saved=None,
        on_check_for_updates=None,
        on_status_override=None,
    ) -> None:
        super().__init__(parent)
        self.on_data_saved = on_data_saved
        self.on_check_for_updates = on_check_for_updates
        # ``(marking, status)``. There is no clearing one: see the button.
        self.on_status_override = on_status_override

        self.table: DataTableWidget | None = None
        # Everything loaded, original column names — the truth the filters work
        # from and the row-index → record mapping works against.
        self.df_source: pd.DataFrame | None = None
        # What's currently rendered (df_source after every active filter).
        self.df_visible: pd.DataFrame | None = None
        # Date column parsed once per load, so filtering doesn't re-parse.
        self._source_dates = None
        # Per-visible-row status, so the row tint and accent bar can be looked
        # up by row index while painting other columns.
        self._row_status: list[str] = []

        self.init_ui()

    # ------------------------------------------------------------------
    # UI initialization
    # ------------------------------------------------------------------
    def init_ui(self) -> None:
        """Title, filter bar, then the table filling the rest of the screen."""
        main_layout, content_container, content_layout = create_styled_container_layout()

        title = QLabel("Electrodes")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setMinimumHeight(25)
        title.setMaximumHeight(40)
        title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(6)
        toolbar.addWidget(title, 1)

        self.filter_bar = FilterBarWidget([
            FilterDropdown(FILTER_STATUS, "Status"),
            FilterDropdown(FILTER_TYPE, "Type"),
            FilterDropdown(FILTER_RECIPE, "Recipe"),
            FilterDropdown(FILTER_VAT, "Vat"),
        ])
        self.filter_bar.set_options(
            FILTER_STATUS, [status_label(s) for s in ElectrodeStatus])
        self.filter_bar.changed.connect(self._render)

        # The strip is one non-wrapping row of controls, so its own width would
        # otherwise become the window's minimum. Let it scroll sideways instead.
        filter_scroll = QScrollArea()
        filter_scroll.setWidgetResizable(True)
        filter_scroll.setFrameShape(QFrame.Shape.NoFrame)
        filter_scroll.setWidget(self.filter_bar)
        filter_scroll.setMinimumWidth(200)
        filter_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        filter_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # The app's own scrollbar, the same one under the table, so the tab
        # does not mix two thicknesses on one screen.
        filter_scroll.setFixedHeight(
            self.filter_bar.sizeHint().height() + SCROLLBAR_HORIZONTAL_HEIGHT + 6)
        filter_scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            + SCROLLBAR_STYLE)

        self.table = DataTableWidget(
            min_column_width=70,
            max_column_width=220,
            # Rows are coloured by status instead.
            enable_alternating_colors=False,
            enable_row_coloring_on_add=False,
            # Newest electrode first, so the top of the table is the useful end.
            scroll_to_top_on_load=True,
        )
        self.table.setStyleSheet(TABLE_STYLE)
        self.table.setFrameShape(QFrame.Shape.NoFrame)
        # No click highlighting: rows are coloured by status, and a selection
        # tint on top of that only muddies the signal.
        self.table.setSelectionMode(self.table.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        # Content-sized columns rather than an equal share of the width, since
        # most of these hold six characters. Any width left over is then handed
        # back out proportionally so the table still fills its pane.
        self.table.set_uncapped_columns({COLUMN_DISPLAY_NAMES[STATUS_COLUMN]})
        self.table.set_fill_remaining_width(True)
        # A minimum of 0 means "use the size hint", and the hint includes the
        # header's own minimum plus the (deliberately thick) scrollbar — which
        # became the window's width floor. An explicit minimum overrides it, so
        # the columns just compress instead.
        self.table.setMinimumWidth(160)
        self.table.viewport().installEventFilter(self)

        # A pencil that follows the cursor along the Status column. Selection is
        # off on this table, so a hover affordance is the way in.
        self.table.setMouseTracking(True)
        self.table.cellEntered.connect(self._on_cell_entered)
        self._edit_row: int | None = None
        self._edit_icon = qta.icon("fa6s.pen", color="#4B5563")
        self._locked_icon = qta.icon("fa6s.lock", color="#9CA3AF")
        self.edit_status_button = QToolButton(self.table.viewport())
        self.edit_status_button.setIcon(self._edit_icon)
        self.edit_status_button.setIconSize(QSize(11, 11))
        self.edit_status_button.setFixedSize(22, 22)
        self.edit_status_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.edit_status_button.setToolTip("Set this electrode's status by hand")
        self.edit_status_button.setStyleSheet("""
            QToolButton {
                background-color: rgba(255, 255, 255, 0.92);
                border: 1px solid #D1D5DB; border-radius: 11px;
            }
            QToolButton:hover { background-color: #FFFFFF; border-color: #55BDBD; }
            QToolButton:disabled {
                background-color: #E5E7EB;
                border: 1px dashed #9CA3AF;
            }
        """)
        self.edit_status_button.hide()
        self.edit_status_button.clicked.connect(self._on_edit_status_clicked)

        # Every column is painted by a delegate: a stylesheet ``::item`` rule
        # makes Qt ignore the item background brush, so the row fill has to be
        # drawn rather than set. Column 0 also carries the status stripe, and
        # the Status column is drawn as a badge.
        # Shared by both delegates, since a row is painted one cell at a time
        # and every cell has to know it is part of an outlined row. Which
        # electrode is picked is held by marking, not by row: a filter or a
        # reload would otherwise move the outline onto a different one.
        self._row_highlight = RowHighlight()
        self._picked_marking = ""
        self.table.setItemDelegate(
            RowFillDelegate(self._row_accent, 0, self.table,
                            highlight=self._row_highlight))
        self.table.setItemDelegateForColumn(
            list(COLUMN_DISPLAY_NAMES).index(STATUS_COLUMN),
            # Gutter matches the hover pencil (22px) plus its margin. The widest
            # label keeps the column wide enough even when no such row is among
            # the ones Qt samples for content sizing.
            StatusPillDelegate(status_pill_style, self.table, right_gutter=32,
                               widest_label=WIDEST_STATUS_LABEL,
                               highlight=self._row_highlight))
        self.table.cellClicked.connect(self._on_cell_clicked)

        # Status ratio of whatever the filters currently show, beside the table.
        self.status_chart = StatusDonutWidget(
            title="Status breakdown", unit_label="electrodes",
            diameter=DONUT_DIAMETER)
        self.report_button = QPushButton("Get report")
        self.report_button.setMinimumHeight(30)
        self.report_button.setStyleSheet(TOOLBAR_BUTTON_STYLE)
        self.report_button.setIcon(qta.icon("fa6s.file-lines", color="white"))
        apply_modern_shadow(self.report_button)
        self.report_button.clicked.connect(self._on_report_clicked)

        chart_panel = QWidget()
        chart_layout = QVBoxLayout()
        # A gap on the right so the percentages do not run into the scrollbar
        # when the panel is scrolling.
        chart_layout.setContentsMargins(14, 0, 5, 0)
        chart_layout.setSpacing(8)
        chart_layout.addWidget(self.status_chart, 0)
        chart_layout.addWidget(self._build_test_first_panel(), 1)
        chart_layout.addWidget(self.report_button, 0)
        chart_panel.setLayout(chart_layout)

        # The donut, the legend, the test-first list and the button stack up to
        # about 670px, and as a plain child that became the window's minimum
        # height: on a 200% display it simply would not fit and the chart was
        # squashed. In a scroll area the panel keeps its natural size and the
        # window is free to be any height, scrolling here when it has to.
        chart_scroll = QScrollArea()
        chart_scroll.setWidget(chart_panel)
        chart_scroll.setWidgetResizable(True)
        chart_scroll.setFrameShape(QFrame.Shape.NoFrame)
        chart_scroll.setFixedWidth(CHART_PANEL_WIDTH)
        # Without this the scroll area reports the panel's height upward and
        # nothing is gained, which is the same trap the filter bar has.
        chart_scroll.setMinimumHeight(0)
        chart_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        chart_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        chart_scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            + SCROLLBAR_STYLE)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self.table, 1)
        body.addWidget(chart_scroll, 0)

        content_layout.addLayout(toolbar, 0)
        content_layout.addSpacing(4)
        content_layout.addWidget(filter_scroll, 0)
        content_layout.addSpacing(4)
        content_layout.addLayout(body, 1)
        content_container.setLayout(content_layout)

        main_layout.addWidget(content_container, 1)
        self.setLayout(main_layout)

        # Start empty — headers arrive with the view's columns.
        self.load_data(pd.DataFrame())

    # ------------------------------------------------------------------
    # Data loading / filtering
    # ------------------------------------------------------------------
    def load_data(self, dataframe: pd.DataFrame | None) -> None:
        """Take a new dataset, refresh the filter options and re-render."""
        self.df_source = dataframe.copy() if dataframe is not None else None
        self._source_dates = self._parse_dates(self.df_source)
        self._refresh_filter_options()
        self._render()

    def _top_marking(self) -> str:
        """The marking of the row at the top of the viewport, '' if there is none.

        Keyed by marking rather than by scrollbar position: electrodes arrive at
        the top of this table, so a pixel offset means a different row after a
        reload while a marking means the same electrode.
        """
        if self.table is None or self.df_visible is None or self.df_visible.empty:
            return ""
        row = self.table.rowAt(0)
        if row < 0 or row >= len(self.df_visible):
            return ""
        return str(self.df_visible.iloc[row][str(ElectrodeColumns.MARKING)]).strip()

    def _scroll_to_marking(self, marking: str) -> None:
        """Put that electrode back under the top edge. Silent if it is gone."""
        if not marking or self.table is None or self.df_visible is None:
            return
        column = str(ElectrodeColumns.MARKING)
        if column not in self.df_visible.columns:
            return
        rows = self.df_visible.index[
            self.df_visible[column].astype(str).str.strip() == marking]
        if not len(rows):
            return
        item = self.table.item(int(rows[0]), 0)
        if item is not None:
            self.table.scrollToItem(item, self.table.ScrollHint.PositionAtTop)

    @staticmethod
    def _recipe_text(value) -> str:
        """Recipe number → the text the table shows, so the filter compares
        against the same thing ('11', not '11.0')."""
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return ""
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value).strip()

    @staticmethod
    def _parse_dates(df: pd.DataFrame | None):
        """Parse the Date column once per load, so filtering stays vectorised.
        Mixed UTC offsets are normalised to UTC before the date is taken."""
        date_column = str(ElectrodeColumns.DATE)
        if df is None or df.empty or date_column not in df.columns:
            return None
        return pd.to_datetime(df[date_column], errors="coerce", utc=True)

    def _refresh_filter_options(self) -> None:
        """Offer only the electrode types and months the data actually holds."""
        df = self.df_source
        if df is None or df.empty:
            self.filter_bar.set_options(FILTER_TYPE, [])
            self.filter_bar.set_options(FILTER_RECIPE, [])
            self.filter_bar.set_options(FILTER_VAT, [])
            self.filter_bar.set_month_options([])
            return

        type_column = str(ElectrodeColumns.FULL_NAME)
        if type_column in df.columns:
            types = sorted({str(v).strip() for v in df[type_column] if str(v).strip()})
            self.filter_bar.set_options(FILTER_TYPE, types)

        recipe_column = str(ElectrodeColumns.RECIPE)
        if recipe_column in df.columns:
            # Recipe numbers sort numerically, not as the strings they render as.
            recipes = {self._recipe_text(v) for v in df[recipe_column]}
            recipes.discard("")
            self.filter_bar.set_options(
                FILTER_RECIPE,
                sorted(recipes, key=lambda r: (not r.isdigit(), int(r) if r.isdigit() else r)))

        # Empty until the process log has been read, and empty for good on a
        # database that has none. The dropdown then offers nothing but "All".
        vats = set()
        if VAT_COLUMN in df.columns:
            vats = {str(v).strip() for v in df[VAT_COLUMN] if str(v).strip()}
        # A single vat sorts before the pairs, and both sort numerically: "30"
        # must not come before "28, 30" the way text would put it.
        self.filter_bar.set_options(
            FILTER_VAT,
            sorted(vats, key=lambda v: [int(n) for n in v.replace(" ", "").split(",")]))

        if self._source_dates is not None:
            months = sorted(
                {(d.year, d.month) for d in self._source_dates.dropna()}, reverse=True)
            self.filter_bar.set_month_options(months)

    def _filtered(self) -> pd.DataFrame:
        """df_source narrowed by every active filter — they combine."""
        df = self.df_source
        if df is None or df.empty:
            return pd.DataFrame(columns=list(COLUMN_DISPLAY_NAMES))

        mask = pd.Series(True, index=df.index)

        status = self.filter_bar.value(FILTER_STATUS)
        if status and STATUS_COLUMN in df.columns:
            # The dropdown offers labels, the frame holds stored values.
            mask &= df[STATUS_COLUMN].astype(str).str.strip() == status_value(status)

        electrode_type = self.filter_bar.value(FILTER_TYPE)
        type_column = str(ElectrodeColumns.FULL_NAME)
        if electrode_type and type_column in df.columns:
            mask &= df[type_column].astype(str).str.strip() == electrode_type

        vat = self.filter_bar.value(FILTER_VAT)
        if vat and VAT_COLUMN in df.columns:
            # Matched whole, not by substring: an electrode plated in 28 and 30
            # is its own answer and does not belong under either on its own.
            mask &= df[VAT_COLUMN].astype(str).str.strip() == vat

        recipe = self.filter_bar.value(FILTER_RECIPE)
        recipe_column = str(ElectrodeColumns.RECIPE)
        if recipe and recipe_column in df.columns:
            mask &= df[recipe_column].map(self._recipe_text) == recipe

        start, end = self.filter_bar.date_bounds()
        if (start or end) and self._source_dates is not None:
            days = self._source_dates.dt.date
            if start:
                mask &= days >= start
            if end:
                mask &= days <= end
            mask &= self._source_dates.notna()

        search = self.filter_bar.search_text().lower()
        if search:
            # Match across every column, so an operator can type a marking, a
            # date fragment or a status without choosing a field first.
            haystack = df.astype(str).apply(lambda col: col.str.lower())
            mask &= haystack.apply(lambda row: search in " ".join(row), axis=1)

        return df[mask].reset_index(drop=True)

    def _render(self) -> None:
        """Render the filtered frame; ``df_source`` keeps the raw values."""
        if self.table is None:
            return

        self.df_visible = self._filtered()
        self._row_status = (
            [str(s).strip() for s in self.df_visible[STATUS_COLUMN]]
            if STATUS_COLUMN in self.df_visible.columns else []
        )
        self.filter_bar.set_result_count(
            len(self.df_visible), 0 if self.df_source is None else len(self.df_source))
        self._update_status_chart()
        self._update_test_first_table()

        if self.df_visible.empty:
            self.table.load_data(pd.DataFrame())
            return

        display_df = self.df_visible.copy()
        date_column = str(ElectrodeColumns.DATE)
        if date_column in display_df.columns:
            # DATETIMEOFFSET → 'dd.mm.yyyy HH:MM'; df_source keeps the raw value.
            display_df[date_column] = display_df[date_column].map(format_timestamp_for_display)

        if STATUS_COLUMN in display_df.columns:
            display_df[STATUS_COLUMN] = display_df[STATUS_COLUMN].map(status_label)

        # The test-first flag is shown beside the status, not in its own column.
        if PRIORITY_COLUMN in display_df.columns:
            flagged = display_df[PRIORITY_COLUMN].astype(str).str.strip() == PRIORITY_FLAG
            display_df.loc[flagged, STATUS_COLUMN] = (
                display_df.loc[flagged, STATUS_COLUMN].astype(str)
                + PRIORITY_SEPARATOR + PRIORITY_FLAG)
            display_df = display_df.drop(columns=[PRIORITY_COLUMN])

        display_df = display_df[[c for c in COLUMN_DISPLAY_NAMES if c in display_df.columns]]
        display_df.columns = [COLUMN_DISPLAY_NAMES.get(c, c) for c in display_df.columns]

        try:
            self.table.load_data(display_df, cell_style_callback=self._cell_style)
        except Exception as e:
            print(f"Error loading electrodes table: {e}")
        self._sync_picked_row()

    # ------------------------------------------------------------------
    # Row styling
    # ------------------------------------------------------------------
    def _build_test_first_panel(self) -> QWidget:
        """The pull-list: which electrodes to take to SEM/IC first."""
        self.test_first_heading = QLabel("Electrodes to be tested first")
        heading_font = QFont()
        heading_font.setPointSize(11)
        heading_font.setBold(True)
        self.test_first_heading.setFont(heading_font)
        self.test_first_heading.setStyleSheet("color: #1F1F1F;")

        self.test_first_table = DataTableWidget(
            min_column_width=70,
            max_column_width=200,
            enable_alternating_colors=True,
            enable_row_coloring_on_add=False,
            min_table_height=90,
            scroll_to_top_on_load=True,
        )
        self.test_first_table.setStyleSheet(TABLE_STYLE)
        self.test_first_table.setFrameShape(QFrame.Shape.NoFrame)
        self.test_first_table.setSelectionMode(
            self.test_first_table.SelectionMode.NoSelection)
        self.test_first_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.test_first_table.verticalHeader().setVisible(False)
        self.test_first_table.set_columns_fill(True)
        self.test_first_table.setMinimumWidth(120)

        panel = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.test_first_heading, 0)
        layout.addWidget(self.test_first_table, 1)
        panel.setLayout(layout)
        return panel

    def _update_test_first_table(self) -> None:
        """Fill the pull-list from whatever the filters currently show."""
        if self.test_first_table is None:
            return
        visible = self.df_visible
        if visible is None or visible.empty or PRIORITY_COLUMN not in visible.columns:
            self.test_first_table.load_data(pd.DataFrame())
            self.test_first_heading.setText("Electrodes to be tested first")
            return

        flagged = visible[visible[PRIORITY_COLUMN].astype(str).str.strip() == PRIORITY_FLAG]
        self.test_first_heading.setText(f"Electrodes to be tested first")
        if flagged.empty:
            self.test_first_table.load_data(pd.DataFrame())
            return

        self.test_first_table.load_data(pd.DataFrame({
            "Electrode": flagged[str(ElectrodeColumns.MARKING)].astype(str).values,
            "Type": flagged[str(ElectrodeColumns.FULL_NAME)].astype(str).values,
            "Date": flagged[str(ElectrodeColumns.DATE)].map(
                format_timestamp_for_display).values,
        }))

    def _update_status_chart(self) -> None:
        """Feed the donut the status split of the rows currently shown.

        Every status is listed even at zero, so filtering one out reads as
        "none of these" rather than the category silently vanishing.
        """
        counts = {status_label(status): 0 for status in ElectrodeStatus}
        if self.df_visible is not None and STATUS_COLUMN in self.df_visible.columns:
            for status, count in self.df_visible[STATUS_COLUMN].astype(str).str.strip(
            ).value_counts().items():
                label = status_label(status)
                if label in counts:
                    counts[label] = int(count)
        self.status_chart.update_data(
            counts, {status_label(s): c for s, c in ROW_ACCENTS.items()})

    def _status_at(self, row_index: int) -> str | None:
        """The status of a rendered row."""
        if 0 <= row_index < len(self._row_status):
            return self._row_status[row_index]
        return None

    def _row_accent(self, row_index: int) -> str | None:
        """Colour of the stripe down the left edge of a row."""
        return ROW_ACCENTS.get(self._status_at(row_index) or "")

    def _cell_style(self, row_index: int, _column_name: str, _value) -> CellStyle | None:
        """Tint the whole row by status. The tint and the accent stripe carry
        the signal, so the text stays plain black and fully legible."""
        tint = ROW_TINTS.get(self._status_at(row_index) or "")
        return CellStyle(background=tint) if tint else None

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------
    def patch_statuses(self, dataframe: pd.DataFrame | None) -> None:
        """Adopt a freshly processed frame, repainting only the changed rows.

        A QC-board move changes one electrode's status; reloading would rebuild
        every cell in the table for it. This diffs the Status column and touches
        just the rows that moved.
        """
        if (self.df_source is None or dataframe is None
                or len(dataframe) != len(self.df_source)
                or STATUS_COLUMN not in dataframe.columns):
            self.load_data(dataframe)
            return

        # A board move changes the test-first flags and the lock owner as well
        # as the status, so all three are diffed and adopted together.
        tracked = [c for c in (STATUS_COLUMN, PRIORITY_COLUMN, LOCKED_BY_COLUMN)
                   if c in dataframe.columns and c in self.df_source.columns]
        differs = None
        for column in tracked:
            changed = (self.df_source[column].astype(str).values
                       != dataframe[column].astype(str).values)
            differs = changed if differs is None else (differs | changed)
        if differs is None or not differs.any():
            return

        marking_column = str(ElectrodeColumns.MARKING)
        moved = set(dataframe.loc[differs, marking_column].astype(str).str.strip())
        for column in tracked:
            self.df_source[column] = dataframe[column].values

        # With a status filter on, the changed rows may no longer belong on
        # screen at all, so it is cheaper to be correct than clever.
        if self.filter_bar.value(FILTER_STATUS) is not None:
            was_at = self._top_marking()
            self._render()
            self._scroll_to_marking(was_at)
            return

        if self.df_visible is not None and not self.df_visible.empty:
            source_markings = self.df_source[marking_column].astype(str).str.strip()
            visible = self.df_visible[marking_column].astype(str).str.strip()
            for row_index in [i for i, m in enumerate(visible) if m in moved]:
                match = source_markings == visible.iloc[row_index]
                for column in tracked:
                    self.df_visible.at[row_index, column] = str(
                        self.df_source.loc[match, column].iloc[0])
                self._row_status[row_index] = str(
                    self.df_visible.at[row_index, STATUS_COLUMN])
                self._repaint_row(row_index)
            self.table.viewport().update()

        self._update_status_chart()
        self._update_test_first_table()

    def _status_display(self, row_index: int) -> str:
        """The Status cell's text: the status, plus the test-first suffix.

        Through status_label, exactly as _render does. The badge styles are
        keyed by what is on screen, so writing the stored value here left the
        two tested statuses matching nothing and the cell drew empty.
        """
        status = status_label(self._status_at(row_index) or "")
        if (self.df_visible is not None and row_index < len(self.df_visible)
                and PRIORITY_COLUMN in self.df_visible.columns
                and str(self.df_visible.iloc[row_index][PRIORITY_COLUMN]).strip()
                == PRIORITY_FLAG):
            return f"{status}{PRIORITY_SEPARATOR}{PRIORITY_FLAG}"
        return status

    def _repaint_row(self, row_index: int) -> None:
        """Rewrite one rendered row's status text and tint, in place."""
        if self.table is None or row_index >= self.table.rowCount():
            return
        status_header = COLUMN_DISPLAY_NAMES[STATUS_COLUMN]
        for column in range(self.table.columnCount()):
            item = self.table.item(row_index, column)
            if item is None:
                continue
            header = self.table.horizontalHeaderItem(column)
            name = header.text() if header else ""
            if name == status_header:
                item.setText(self._status_display(row_index))
            DataTableWidget._apply_cell_style(
                item, self._cell_style(row_index, name, item.text()))

    def get_data(self) -> pd.DataFrame:
        """The full source DataFrame (original column names, unfiltered)."""
        return self.df_source if self.df_source is not None else pd.DataFrame()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802 (Qt naming)
        """Re-share the spare width on resize, and drop the hover button when
        the cursor leaves the table."""
        if self.table is not None and obj is self.table.viewport():
            if event.type() == QEvent.Type.Resize and self.table.columnCount():
                self.table.resize_columns_to_contents_constrained()
            elif event.type() == QEvent.Type.Leave:
                self.edit_status_button.hide()
                self._edit_row = None
                self._row_highlight.hover_row = -1
                self.table.viewport().update()
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------
    # Report
    # ------------------------------------------------------------------
    def _on_report_clicked(self) -> None:
        """Build a report over exactly what the filters currently show."""
        visible = self.df_visible if self.df_visible is not None else pd.DataFrame()
        if visible.empty:
            ValidationErrorScrollDialog(
                parent=self, errors=["No electrodes match the current filters."],
                title="Nothing to report", header_color="#FF9800", icon="⚠️",
                subtitle_noun="note").exec()
            return

        # Released here means SELLABLE, so a tested electrode is not one: it was
        # destroyed to produce the verdict.
        families = (visible[STATUS_COLUMN].astype(str)
                    .map(status_family).map(status_label))

        # The tested ones are left OUT of the range list rather than listed
        # between the stretches they interrupt. There is nothing to ship in
        # them, and one tested electrode every thirty turned a handful of
        # ranges into a page of one-row entries. Their count still foots the
        # table below, so the report reconciles.
        #
        # Leaving a gap in the numbering is safe: a range ends when the STATUS
        # changes, not when a number is missing, so the stretch either side of
        # a tested electrode closes up into one.
        consumed = families == status_label(ElectrodeStatus.TESTED_AND_RELEASED)
        markings = visible[str(ElectrodeColumns.MARKING)].astype(str)
        # Oldest first, which is both how a range has to be built (it needs
        # ascending numbers) and how the report reads.
        ranges = collapse_to_ranges(
            list(markings[~consumed]), list(families[~consumed]))

        # The summary, in the shape the old workbook reported it. Counted from
        # the STATUS rather than the range family, because the split is simply
        # whether this electrode was itself tested.
        #
        # Testing destroys the electrode, so a tested one is a loss whichever
        # way its result went: both land in the second scrap line. A neighbour
        # swept by a failed test was never tested itself, so it sits in the
        # first, which is what "without QC" means here.
        raw = visible[STATUS_COLUMN].astype(str).str.strip()

        def count(status) -> int:
            return int((raw == str(status)).sum())

        scrapped_untested = count(ElectrodeStatus.SCRAPPED)
        tested_passed = count(ElectrodeStatus.TESTED_AND_RELEASED)
        tested_failed = count(ElectrodeStatus.TESTED_AND_SCRAPPED)

        totals = {
            ALL_PRODUCED: len(visible),
            status_label(ElectrodeStatus.RELEASED): count(ElectrodeStatus.RELEASED),
            status_label(ElectrodeStatus.TESTED_AND_RELEASED): tested_passed,
            SCRAP_WITHOUT_QC: scrapped_untested,
            SCRAP_WITH_QC: scrapped_untested + tested_passed + tested_failed,
            status_label(ElectrodeStatus.NEEDS_TESTING):
                count(ElectrodeStatus.NEEDS_TESTING),
            status_label(ElectrodeStatus.NOT_TESTED): count(ElectrodeStatus.NOT_TESTED),
        }

        date_from, date_to = self._visible_date_span()
        html = build_report_html(
            ranges=ranges, totals=totals,
            tints={**{status_label(s): c for s, c in ROW_TINTS.items()},
                   ALL_PRODUCED: "#E9ECEF",
                   SCRAP_WITHOUT_QC: ROW_TINTS[str(ElectrodeStatus.SCRAPPED)],
                   SCRAP_WITH_QC: ROW_TINTS[str(ElectrodeStatus.TESTED_AND_SCRAPPED)]},
            date_from=date_from, date_to=date_to,
            total_shown=len(visible),
            total_all=0 if self.df_source is None else len(self.df_source))

        ReportDialog(self, html=html,
                     suggested_name=f"qc_report_{date_from}_{date_to}.pdf"
                     .replace(".", "-").replace("-pdf", ".pdf")).exec()

    def _visible_date_span(self) -> tuple[str, str]:
        """First and last production date among the rendered rows."""
        if self.df_visible is None or self.df_visible.empty:
            return "—", "—"
        dates = pd.to_datetime(
            self.df_visible[str(ElectrodeColumns.DATE)], errors="coerce", utc=True).dropna()
        if dates.empty:
            return "—", "—"
        return dates.min().strftime("%d.%m.%Y"), dates.max().strftime("%d.%m.%Y")

    # ------------------------------------------------------------------
    # Manual status override
    # ------------------------------------------------------------------
    def _on_cell_entered(self, row: int, column: int) -> None:
        """Outline the row under the cursor, and park the pencil on its Status
        cell. The outline follows every column, the pencil only that one."""
        if self._row_highlight.hover_row != row:
            self._row_highlight.hover_row = row
            self.table.viewport().update()

        header = self.table.horizontalHeaderItem(column)
        if header is None or header.text() != COLUMN_DISPLAY_NAMES[STATUS_COLUMN]:
            self.edit_status_button.hide()
            self._edit_row = None
            return

        rect = self.table.visualRect(self.table.model().index(row, column))
        self._edit_row = row

        # A live board decision can still be dragged elsewhere, taking every
        # electrode it reached with it — so those aren't safe to pin by hand.
        locked_by = self._locked_by(row)
        self.edit_status_button.setEnabled(not locked_by)
        self.edit_status_button.setToolTip(
            f"{locked_by} drives this status. Mark it as done on the QC tab "
            f"first" if locked_by else "Set this electrode's status by hand")
        # A lock reads as "not available here" far faster than a greyed pencil.
        self.edit_status_button.setIcon(
            self._locked_icon if locked_by else self._edit_icon)

        self.edit_status_button.move(
            rect.right() - self.edit_status_button.width() - 6,
            rect.center().y() - self.edit_status_button.height() // 2)
        self.edit_status_button.show()
        self.edit_status_button.raise_()

    def _on_cell_clicked(self, row: int, _column: int) -> None:
        """Pick the row, or let it go if it was already picked."""
        if self.df_visible is None or row >= len(self.df_visible):
            return
        marking = str(self.df_visible.iloc[row][str(ElectrodeColumns.MARKING)]).strip()
        self._picked_marking = "" if marking == self._picked_marking else marking
        self._sync_picked_row()
        self.table.viewport().update()

    def _sync_picked_row(self) -> None:
        """Find the picked electrode's row again, after anything redrew.

        Held by marking rather than by row index, so a filter, a sort or a
        reload cannot leave the outline sitting on somebody else.
        """
        self._row_highlight.last_column = self.table.columnCount() - 1
        self._row_highlight.picked_row = -1
        if not self._picked_marking or self.df_visible is None:
            return
        column = str(ElectrodeColumns.MARKING)
        if column not in self.df_visible.columns:
            return
        rows = self.df_visible.index[
            self.df_visible[column].astype(str).str.strip() == self._picked_marking]
        if len(rows):
            self._row_highlight.picked_row = int(rows[0])

    def _locked_by(self, row_index: int) -> str:
        """The live decision driving this row's status, or '' if none does."""
        if self.df_visible is None or row_index >= len(self.df_visible):
            return ""
        if LOCKED_BY_COLUMN not in self.df_visible.columns:
            return ""
        return str(self.df_visible.iloc[row_index][LOCKED_BY_COLUMN]).strip()

    def _on_edit_status_clicked(self) -> None:
        """Set the hovered electrode's status by hand. Never sweeps.

        There is no undoing it: the value it replaces cannot be worked out
        again, since a status inherited from the tested list is not stored
        anywhere the app can reach. To change it you set another one.
        """
        if self._edit_row is None or self.df_visible is None:
            return
        if self._edit_row >= len(self.df_visible):
            return

        marking_column = str(ElectrodeColumns.MARKING)
        marking = str(self.df_visible.iloc[self._edit_row][marking_column]).strip()
        current = status_label(
            self.df_visible.iloc[self._edit_row][STATUS_COLUMN])

        dialog = StatusEditDialog(
            self, marking=marking, current=current,
            statuses=[status_label(s) for s in ElectrodeStatus])
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.chosen:
            return

        if callable(self.on_status_override):
            # Back to the stored value: the dialog dealt in labels.
            self.on_status_override(marking, status_value(dialog.chosen))

    def apply_external_updates(self, result) -> bool:
        """Take the answer to a poll and render it. Runs on the UI thread.

        Patched rather than reloaded. Every write of our own moves the
        watermark, so this runs a few seconds after each one; rebuilding all 12k
        rows for it froze the window for about a second and threw away the
        operator's place in the table. Nearly every poll that finds anything
        found a status, and patch_statuses falls back to a full load when the
        row count moves, which is what a new electrode does.
        """
        if not result or not result.get("has_updates") or result.get("data") is None:
            return False
        was_at = self._top_marking()
        self.patch_statuses(result["data"])
        self._scroll_to_marking(was_at)
        return True

    def check_for_external_updates(self) -> bool:
        """Poll and apply in one go, for callers with no worker thread."""
        if self.on_check_for_updates is None:
            return False
        try:
            return self.apply_external_updates(self.on_check_for_updates())
        except Exception as e:
            print(f"Error checking for electrodes external updates: {e}")
            return False
