"""Electrodes tab — full-screen table of electrodes with status filtering.

Thin by design: it never imports the backend. Everything it needs arrives as
injected callables, so it can be unit-tested with fakes and hand-built frames.
"""

from __future__ import annotations

import datetime as dt
import math

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QToolButton, QDialog,
    QPushButton, QScrollArea, QSizePolicy, QHeaderView,
)
from PyQt6.QtCore import (Qt, QEvent, QObject, QPointF, QRectF, QSize,
                          QTimer)
from PyQt6.QtGui import QCursor, QFont, QFontMetrics
import pandas as pd
import qtawesome as qta

from .styles.table_styles import (
    PULL_LIST_TABLE_STYLE,
    TABLE_STYLE,
    SCROLLBAR_STYLE,
    SCROLLBAR_HORIZONTAL_HEIGHT,
    SCROLLBAR_VERTICAL_WIDTH,
)
from .styles.button_styles import (QUIET_BUTTON_STYLE,
                                   SECONDARY_BUTTON_STYLE,
                                   TOOLBAR_BUTTON_STYLE, apply_modern_shadow)
from .styles.tab_styles import create_styled_container_layout
from .widgets.error_dialogs import ValidationErrorScrollDialog
from .widgets.coupon_dialogs import CouponSettingsDialog, ask_with_coupons
from .widgets.report_dialog import ReportDialog, build_report_html
from src.data_access.electrodes.electrode_processor import DERIVED_COLUMNS
from src.data_access.electrodes.status_ranges import collapse_to_ranges
from .widgets.data_table_widget import DataTableWidget, CellStyle
from .widgets.filter_bar_widget import FilterBarWidget, FilterDropdown
from .widgets.error_dialogs import WarningDialog, ask
from .widgets.table_delegates import (
    CouponTextDelegate,
    RowTintDelegate,
    blend,
    RequestActionDelegate,RowFillDelegate, StatusPillDelegate,
                                      PillStyle, RowHighlight, RowFlasher,
                                      LoadingCellDelegate)
from .widgets.skeleton_overlay import (SkeletonOverlay, SHIMMER_TICK_MS,
                                       pulse_strength)
from .widgets.status_donut_widget import StatusDonutWidget, render_donut_png
from src.data_access.electrodes.status_propagation import electrode_number
from .widgets.status_edit_dialog import StatusEditDialog
from .date_display import (format_utc_date_for_display,
                           format_utc_timestamp_for_display)
from src.data_access.qc_requests.request_columns import (
    AWAITING_PRODUCTION,
    OPEN_STATES,
    QC_COUPONS_COLUMN,
    QC_REQUESTED_COLUMN,
    AWAITING_QC,
    DECLINED,
    EXPIRED,
    QC_STATUS_COLUMN,
    SENT,
    TO_BE_SENT,
    state_label as request_label,
)
from src.data_access.electrodes.electrode_columns import (
    ElectrodeColumns,
    ElectrodeStatus,
    STATUS_COLUMN,
    VAT_COLUMN,
    PRIORITY_COLUMN,
    PRIORITY_FLAG,
    LOCKED_BY_COLUMN,
    FAILED_CRACK,
    FAILED_IC,
    FAILURE_COLUMN,
    FINALISED_COLUMN,
    NEVER_STORED,
    NOTHING_LEFT_TO_ASK,
    status_family,
)

# The statuses above as the frame spells them, since a row holds the text
# rather than the enum.
_NOTHING_LEFT = frozenset(str(status) for status in NOTHING_LEFT_TO_ASK)

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
    # Between the verdict and when it settled: the request is what happens
    # before an electrode has a verdict at all.
    QC_STATUS_COLUMN: "QC request",
    FINALISED_COLUMN: "Status finalised",
}

# Display text for the two statuses that are decided by testing the electrode
# itself. Testing destroys it, so it can never be sold: "Tested and Released"
# said the opposite of what happened to it. What the status really records is
# that THIS electrode was the one measured, and the verdict is what releases or
# scraps its neighbours.
#
# Display only. The stored values are a live database contract and are
# untouched; see status_label and status_value below for the round trip.
# Display only. The stored values are the database contract and are untouched;
# renaming one of those would be a coordinated migration, not a wording change.
STATUS_DISPLAY_NAMES: dict[str, str] = {
    str(ElectrodeStatus.TESTED_AND_RELEASED): "Tested, passed",
    str(ElectrodeStatus.TESTED_AND_SCRAPPED): "Tested, failed",
    # An electrode rejected on the strength of a neighbour, as opposed to the
    # report's "Scrapped" line, which is every electrode that will not ship:
    # these plus the tested ones, which testing destroys whatever the verdict.
    str(ElectrodeStatus.SCRAPPED): "Rejected",
}
_STORED_BY_LABEL = {label: stored for stored, label in STATUS_DISPLAY_NAMES.items()}

# The production block. Its four indented rows add up to the first, so a reader
# can see nothing has been dropped: an electrode is released, scrapped, or still
# waiting for QC to say which.
ALL_PRODUCED = "All electrodes"
SCRAPPED_TOTAL = "Scrapped"
# Under it, the two reasons an electrode is gone, as management counts them.
# A failed test is counted as a production fault, because the test is what found
# the fault; only an electrode that passed and was destroyed anyway is counted
# as the cost of testing.
SCRAP_FAULTS = "Production faults"
SCRAP_TESTING = "Consumed by QC testing"

# The chart calls a tested-and-passed electrode by what became of it rather
# than by its verdict, because on a chart of what the line produced that is the
# useful reading: it passed, and testing destroyed it, so it is a cost of QC
# rather than something shippable. The table badge keeps the verdict.
CHART_LABEL_OVERRIDES: dict[str, str] = {
    str(ElectrodeStatus.TESTED_AND_RELEASED): SCRAP_TESTING,
}
# Released first and the QC cost second, so the two green slices read as a pair
# and the order never depends on how the enum happens to be written.
CHART_ORDER: tuple[ElectrodeStatus, ...] = (
    ElectrodeStatus.RELEASED,
    ElectrodeStatus.TESTED_AND_RELEASED,
    ElectrodeStatus.TESTED_AND_SCRAPPED,
    ElectrodeStatus.SCRAPPED,
    ElectrodeStatus.NEEDS_TESTING,
    ElectrodeStatus.WAITING_FOR_SEM,
    ElectrodeStatus.WAITING_FOR_IC,
    ElectrodeStatus.NOT_TESTED,
    ElectrodeStatus.TEST_ONGOING,
)


def chart_label(stored) -> str:
    """The status as the chart names it, which is not always the badge."""
    text = str(stored).strip()
    return CHART_LABEL_OVERRIDES.get(text, status_label(text))

# The QC table's footing. Its rows are a share of this rather than of everything
# produced, so a pass rate reads as a pass rate.
ALL_TESTED = "All tested electrodes"


def status_label(stored) -> str:
    """The stored status as the operator should read it."""
    text = str(stored).strip()
    return STATUS_DISPLAY_NAMES.get(text, text)


def status_value(label) -> str:
    """Back the other way, for anything the operator picked."""
    text = str(label).strip()
    return _STORED_BY_LABEL.get(text, text)


# The ring beside the table. Sized so the panel holding it stays narrower than
# the table it sits next to.
DONUT_DIAMETER = 212

# That panel: the donut, the test-first list and the report button. Wide enough
# for the ring and its legend, plus the scrollbar it gets on a short window, so
# the ring never loses room to it.
#
# WIDE BECAUSE THE PULL LIST IS FOUR COLUMNS. The request state is held at its
# longest wording so it does not jump about, 163px; the marking, the production
# date and the request time take what they take; and the list's own scrollbar is
# 35 of it. Below this the marking is squeezed to nothing. The main table pays,
# and the quickest way to give it room back is to drop a column from the list.
CHART_PANEL_WIDTH = 440 + SCROLLBAR_VERTICAL_WIDTH
# What the panel may shrink and grow to once the pull list has measured itself.
# The floor is what the donut and its legend need; the ceiling is so a long
# request state cannot push the table off the screen.
CHART_PANEL_MIN = 330 + SCROLLBAR_VERTICAL_WIDTH
CHART_PANEL_MAX = 560 + SCROLLBAR_VERTICAL_WIDTH
# Left, top, right, bottom. Shared by the part that scrolls and the part
# pinned under it, so the two columns of content line up.
PANEL_MARGINS = (14, 0, 5, 0)
# How tall the pull list may get. Beyond it the list scrolls in itself rather
# than pushing the breakdown off the top: it is pinned, so whatever height it
# claims is height the rest of the panel never sees.
#
# WHAT IS LEFT AFTER THE BREAKDOWN, not a fixed number and not a share. A
# fixed 240 was four rows whether the window was tall or short, which left a
# tall screen with a short list under a large gap. A share of the panel instead
# took height off the breakdown on a small screen, which is the thing the cap
# was there to stop. So the breakdown is served first, at its own natural
# height, and the list gets the rest.
#
# The floor is the old fixed height, so a small screen is never worse off than
# it was; the ceiling stops a very tall window handing the list more rows than
# anybody reads at once.
TEST_FIRST_BREAKDOWN_ROOM = 470
TEST_FIRST_MIN_HEIGHT = 240
TEST_FIRST_MAX_HEIGHT = 440
# What the pull list says where production has not been asked for one. The main
# table draws a button there instead; this list is read only, so it says it.
NOT_REQUESTED = "Not requested"
# Shorter than the table's own rows: this one is scanned, not read. Four more
# than it was, so the coupon icon has room to be read rather than guessed at.
TEST_FIRST_ROW_HEIGHT = 28
# The pull list's heading. Named rather than written out at each use: it was in
# three places, two of them setting it to what it already said.
# The quiet button the filter bar's "Clear all filters" wears, a point larger:
# it sits beside a heading rather than among inputs, and at 12px it read as
# part of the table underneath it. Last, so the size wins.
# Tighter than the quiet style it is built on, which is written for a row of
# buttons rather than for one squeezed between a figure and a table. Last, so
# both the size and the padding win.
COUPON_BUTTON_STYLE = QUIET_BUTTON_STYLE + """
    QPushButton { font-size: 13px; padding: 3px 10px; }
"""
# Between the pinned blocks: the scrapped figure, the pull list, the report
# button. Every pixel here is a pixel the chart above gives up to a scrollbar,
# and these three read as one block rather than as three panels.
# Left, top, right, bottom inside the tab's white card. Tighter at the top and
# bottom than the shared default of 16, which on this tab is 32px taken off the
# side panel before anything is drawn in it.
CARD_PADDING = (16, 8, 16, 8)
PINNED_GAP = 3
# Between the Coupon Settings button and the list it governs. They belong
# together, so it is the tightest gap in the panel.
PULL_LIST_GAP = 2
# What a cell costs around its text in the pull list: the delegate's margins
# either side and a little slack so the longest state is not clipped.
CELL_PADDING = 16


def status_pill_style(value: str) -> PillStyle | None:
    """Badge style for a Status cell, ignoring any test-first suffix.

    Matched by prefix, since the suffix is separated by spaces alone and the
    statuses themselves contain spaces.
    """
    text = str(value).strip()
    for status, style in PILL_STYLES.items():
        if text == status or text.startswith(status):
            return style
    return None

# What a QC request looks like in its column. The same vocabulary as the
# status pills: an outline for something still moving, a filled badge for an
# outcome. Amber for a question nobody has answered, blue for one pointed at
# this application, teal for agreed, grey for closed.
QC_PILL_STYLES: dict[str, PillStyle] = {
    request_label(AWAITING_PRODUCTION): PillStyle(color="#B45309"),
    request_label(AWAITING_QC): PillStyle(color="#1D4ED8", background="#DBEAFE"),
    request_label(TO_BE_SENT): PillStyle(color="#0B7268", glyph="✓"),
    request_label(SENT): PillStyle(
        color="#0B7268", background="#0B7268", text="#FFFFFF", glyph="✓"),
    request_label(DECLINED): PillStyle(color="#555B64"),
    # Fainter than declined, and on purpose: nobody refused this one, it just
    # ran out of anybody who could answer it.
    request_label(EXPIRED): PillStyle(color="#94A3B8"),
}

# The longest of them, so the column is measured for its widest badge rather
# than for whatever happens to be in the first thousand rows.
WIDEST_QC_LABEL = max(QC_PILL_STYLES, key=len)


def qc_pill_style(text: str) -> PillStyle | None:
    """The badge for one QC request state, or None for a row with no request."""
    return QC_PILL_STYLES.get(str(text).strip())


# What the Failure filter offers. The two reasons a test can give, plus the
# electrodes that failed and never had one written down, which is the only way
# to find the ones somebody forgot to fill in.
FAILURE_IC = "IC failed"
FAILURE_CRACK = "Cracked"
FAILURE_NONE = "No reason recorded"
FAILURE_FILTER_CODES = {FAILURE_IC: FAILED_IC, FAILURE_CRACK: FAILED_CRACK}

# Filter dropdown keys.
FILTER_FAILURE = "failure"
FILTER_QC_REQUEST = "qc_request"
FILTER_STATUS = "status"
FILTER_TYPE = "type"
FILTER_RECIPE = "recipe"
FILTER_VAT = "vat"

# The QC request filter's first option. Most electrodes have no request, and
# without this there is no way to ask for the ones that do not.
QC_NONE = "No request"

# The Vat filter's last option. The process log covers most electrodes but not
# all, and without this the ones it misses can be seen but never picked out.
VAT_UNKNOWN = "Unidentified"

# Row tint, per status — one flat colour across the whole row. Hue carries the
# outcome, depth carries the evidence: a verdict tested on the board gets the
# stronger shade, one inherited from a neighbour the lighter. Not Tested stays
# neutral, since it is not a verdict at all.
#
# Teal for good and rose for bad, not green and red. Red and green sit on the
# axis that red-green colour blindness collapses, so the old pair was very hard
# to tell apart: the worst two tints measured 2.8 (OKLab dE, simulated), where
# 8 is the usual target. These measure 4.2. Pale fills can never do much better
# than that, which is why the row also carries a coloured bar and a badge with
# the status written on it.
ROW_TINTS: dict[str, str] = {
    str(ElectrodeStatus.TESTED_AND_RELEASED): "#84CDBC",
    str(ElectrodeStatus.RELEASED): "#D2ECE5",
    str(ElectrodeStatus.TESTED_AND_SCRAPPED): "#E88FA4",
    str(ElectrodeStatus.SCRAPPED): "#F9CDC9",
    # Amber: an instruction rather than a verdict, so it shouldn't read as
    # either good or bad.
    str(ElectrodeStatus.NEEDS_TESTING): "#FDE8C4",
    # Paler ambers: these are waits, not instructions, and they should not
    # compete with the queue itself for attention.
    str(ElectrodeStatus.WAITING_FOR_SEM): "#FBF0DC",
    str(ElectrodeStatus.WAITING_FOR_IC): "#EDF0DC",
    str(ElectrodeStatus.NOT_TESTED): "#F3F4F6",
    # Blue: the only family not already meaning good, bad or waiting, and it
    # reads as work in progress rather than as an outcome.
    str(ElectrodeStatus.TEST_ONGOING): "#CFE2FA",
}
# The accent bar and the donut slices. Chosen by measurement, not by eye: every
# pair was scored in OKLab through protanope and deuteranope simulation, and
# the worst pair is 9.0 where the old green/red palette scored 3.5, which is
# indistinguishable. Two moves did it. The families sit on the teal/crimson
# axis rather than green/red, which colour blindness leaves intact, and within
# each family the two shades differ in saturation as well as lightness, one
# deep and one soft, so they cannot collapse into each other either.
ROW_ACCENTS: dict[str, str] = {
    str(ElectrodeStatus.TESTED_AND_RELEASED): "#0D9488",
    str(ElectrodeStatus.RELEASED): "#66C4B0",
    str(ElectrodeStatus.TESTED_AND_SCRAPPED): "#7A0F2E",
    str(ElectrodeStatus.SCRAPPED): "#E45A52",
    str(ElectrodeStatus.NEEDS_TESTING): "#E09A26",
    # The two waits sit on the same amber axis as the queue they belong to,
    # one softened and one turned towards olive so they are told apart.
    str(ElectrodeStatus.WAITING_FOR_SEM): "#E8C98A",
    str(ElectrodeStatus.WAITING_FOR_IC): "#C3CE8A",
    str(ElectrodeStatus.NOT_TESTED): "#555B64",
    str(ElectrodeStatus.TEST_ONGOING): "#3566C7",
}
# Solid badge = tested on the QC board; outlined = inherited from a neighbour.
# Keyed by the DISPLAYED text, since that is what the delegate is handed.
PILL_STYLES: dict[str, PillStyle] = {
    status_label(ElectrodeStatus.TESTED_AND_RELEASED): PillStyle(
        color="#0B7268", background="#0B7268", text="#FFFFFF", glyph="✓"),
    status_label(ElectrodeStatus.RELEASED): PillStyle(color="#0B7268", glyph="✓"),
    status_label(ElectrodeStatus.TESTED_AND_SCRAPPED): PillStyle(
        color="#8E1537", background="#8E1537", text="#FFFFFF"),
    status_label(ElectrodeStatus.SCRAPPED): PillStyle(color="#8E1537"),
    status_label(ElectrodeStatus.NEEDS_TESTING): PillStyle(color="#B45309"),
    status_label(ElectrodeStatus.WAITING_FOR_SEM): PillStyle(color="#946A1F"),
    status_label(ElectrodeStatus.WAITING_FOR_IC): PillStyle(color="#5F6E20"),
    status_label(ElectrodeStatus.NOT_TESTED): PillStyle(color="#555B64"),
    status_label(ElectrodeStatus.TEST_ONGOING): PillStyle(
        color="#1D4ED8", background="#DBEAFE"),
}

# The dots that march after "Test ongoing". Padded to a fixed width so the badge
# does not resize three times a second, which would read as a flicker rather
# than as progress.
ONGOING_DOTS = ("", ".", "..", "...")
ONGOING_TICK_MS = 320

# The breath on anything waiting for QC to answer. Slow and shallow on purpose:
# it runs all day beside somebody working, and a quick or deep one stops being
# a signal and becomes a thing to look away from. One period is a calm breath;
# the tick is just fast enough that the fade has no steps in it.
# How often the table is repainted purely so the request window can expire
# under it. The button's own test reads the clock on every paint, so the
# decision is always current; what is not current is the pixels, and a row
# nobody touches would keep a button it no longer offers until something else
# happened to repaint it. Five minutes on a thirty day window is as fine as
# anybody needs, and it costs one repaint of the rows actually on screen.
WINDOW_TICK_MS = 5 * 60 * 1000

PULSE_PERIOD_MS = 2600
PULSE_TICK_MS = 60
# How far a pull list row travels from its own background towards the blue.
# Lighter than the badge's, because a whole row of it is a lot more colour than
# a pill of it.
PULSE_ROW_DEPTH = 0.22
# The blue the offer badge is outlined in, so the row and the badge pulse in
# one colour rather than two that nearly match.
PULSE_BLUE = "#1D4ED8"

# How long a row somebody else changed stays lit, and how often the wash is
# stepped down. Long enough to catch the eye across the table, short enough
# that it is gone before it becomes something to ignore.

# The zoom strip above the table. Round, and big enough to hit with a thumb,
# since this tab is used standing at a tablet.
ZOOM_BUTTON_STYLE = """
    QPushButton {
        background-color: #FFFFFF;
        border: 1px solid #D1D5DB;
        border-radius: 14px;
        min-width: 28px;
        max-width: 28px;
        min-height: 28px;
        max-height: 28px;
        font-family: 'Segoe UI', Arial, sans-serif;
        font-size: 15px;
        font-weight: 700;
        color: #6B7280;
        padding: 0px;
    }
    QPushButton:hover { border: 1px solid #55BDBD; color: #14B8A6; }
    QPushButton:pressed { background-color: #F0FAFA; }
    QPushButton:disabled { color: #D1D5DB; border: 1px solid #EDEFF2; }
"""
ZOOM_LABEL_STYLE = ("color: #6B6B6B; font-size: 12px; font-weight: 600; "
                    "min-width: 38px;")

# The Vat column's placeholder while the process log is being read. Rate and
# shape come from the skeleton overlay, so the two placeholders on this tab,
# the whole-table one at launch and this column, breathe together.
# What the badge is measured against, whichever frame is showing.
ONGOING_WIDEST = status_label(ElectrodeStatus.TEST_ONGOING) + ONGOING_DOTS[-1]


def ongoing_width_text(label: str) -> str:
    """Measure an animated badge at its longest frame, so it holds still.

    Anything riding along after the status, the test-first flag, is kept in the
    measurement. Dropping it sized the badge for the status alone, so a flagged
    one overflowed and Qt elided the flag to an ellipsis, which read as three
    more dots on the end of the animation.
    """
    text = str(label).strip()
    base = status_label(ElectrodeStatus.TEST_ONGOING)
    if not text.startswith(base):
        return label
    return ONGOING_WIDEST + text[len(base):].lstrip(".")

# The longest badge the Status column can ever hold: the longest status and
# its glyph. Sizing for this keeps the column wide enough no matter which rows
# Qt happens to measure.
WIDEST_STATUS_LABEL = max(
    (f"{style.glyph}  {status}" for status, style in PILL_STYLES.items()),
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
        on_qc_request=None,
        get_coupon_settings=None,
        on_coupon_setting=None,
        on_qc_answer=None,
        get_request_window_days=None,
    ) -> None:
        super().__init__(parent)
        self.on_data_saved = on_data_saved
        self.on_check_for_updates = on_check_for_updates
        # ``(marking, status)``. There is no clearing one: see the button.
        self.on_status_override = on_status_override
        # ``on_qc_request(marking) -> bool`` asks production for one electrode,
        # ``on_qc_answer(marking, approve) -> bool`` answers one they offered.
        # Absent, the column still shows requests and the buttons do nothing,
        # which is what a test with no backend gets.
        self._on_qc_request = on_qc_request
        # ``get_coupon_settings() -> {reason: coupons}`` and
        # ``on_coupon_setting(reason, coupons) -> bool``. Absent, the dialog
        # opens on the defaults and saves nowhere, which is what a database
        # without the coupon migration looks like.
        self._get_coupon_settings = get_coupon_settings
        self._on_coupon_setting = on_coupon_setting
        self._on_qc_answer = on_qc_answer
        # ``get_request_window_days() -> int``, how old an electrode may be
        # and still be worth asking production for. Absent, or 0 or less,
        # every row keeps its button, which is what a database with no
        # expiry rule behaves like.
        self._get_request_window_days = get_request_window_days

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
        main_layout, content_container, content_layout = (
            create_styled_container_layout(inner_margins=CARD_PADDING))

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
            FilterDropdown(FILTER_QC_REQUEST, "QC request"),
            FilterDropdown(FILTER_FAILURE, "Failure"),
        ], number_range="Electrode", stacked=True, with_clear=True,
           extra_date_range="Status finalised")
        self.filter_bar.set_options(
            FILTER_STATUS, [status_label(s) for s in ElectrodeStatus])
        # Every state, plus the absence of one. Fixed rather than taken from
        # the data: a state with nothing in it today is still worth being able
        # to ask for, and the list is six long.
        self.filter_bar.set_options(
            FILTER_QC_REQUEST, [QC_NONE] + list(QC_PILL_STYLES))
        self.filter_bar.set_options(
            FILTER_FAILURE, [FAILURE_IC, FAILURE_CRACK, FAILURE_NONE])
        self.filter_bar.changed.connect(self._render)
        # On the filters' second row rather than a line of its own, and in with
        # them rather than at the right edge: out there it belonged to nothing,
        # and on a narrow window the Status finalised range wrapped away and
        # left it stranded on a row of its own.
        self.filter_bar.add_before_extra_range(self._build_zoom_widget())

        # The strip scrolls sideways rather than forcing its own width on the
        # window. Its height then has to cover BOTH rows plus the scrollbar
        # whenever that scrollbar is showing, or the scrollbar sits on top of
        # the second row and hides it. Grown and shrunk as it comes and goes,
        # rather than reserving the space permanently: on a wide window there
        # is no scrollbar and those 40 pixels belong to the table.
        self._filter_scroll = QScrollArea()
        filter_scroll = self._filter_scroll
        filter_scroll.setWidgetResizable(True)
        filter_scroll.setFrameShape(QFrame.Shape.NoFrame)
        filter_scroll.setWidget(self.filter_bar)
        filter_scroll.setMinimumWidth(200)
        filter_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        filter_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # The app's own scrollbar, the same one under the table, so the tab
        # does not mix two thicknesses on one screen.
        filter_scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            + SCROLLBAR_STYLE)
        bar = filter_scroll.horizontalScrollBar()
        bar.rangeChanged.connect(lambda _lo, _hi: self._fit_filter_height())
        self._fit_filter_height()

        self.table = DataTableWidget(
            # Low, because Vat and Recipes hold two or three characters and a
            # 70px floor on those was pure empty space in a table that has to
            # fit a small screen.
            min_column_width=52,
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
        # Both sized to their content: the Status column holds a long wait and
        # the QC request column holds a badge with two buttons beside it.
        self.table.set_uncapped_columns({COLUMN_DISPLAY_NAMES[STATUS_COLUMN],
                                         COLUMN_DISPLAY_NAMES[QC_STATUS_COLUMN]})
        # A timestamp is the same width on every row, so handing it a share of
        # the spare only takes room from the columns that can use it.
        # Every column its content, the slack to the one at the end. Shared out
        # in proportion, as it was, the widest column grew the most and the
        # narrow ones grew too, which is how the table ran off a small screen.
        self.table.set_fill_last_only(True)
        # And on a small screen, give width back rather than run off it.
        self.table.set_shrink_to_fit(True)
        self.table.set_no_growth_columns(
            {COLUMN_DISPLAY_NAMES[str(ElectrodeColumns.DATE)]})
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
        # Which frame of the marching dots is showing. Only ticks while a row
        # on screen is actually being tested, so a table of settled electrodes
        # costs nothing.
        self._ongoing_frame = 0
        self._ongoing_timer = QTimer(self)
        self._ongoing_timer.setInterval(ONGOING_TICK_MS)
        self._ongoing_timer.timeout.connect(self._advance_ongoing)

        # A second animation, slower than the dots and with nothing in
        # common with them: that one marches through frames of text, this one
        # fades a colour. Sharing a timer would have tied the breath's speed to
        # the dots' speed.
        # Always running, unlike the animations: this one is not about
        # anything on screen moving, it is about time passing while nothing
        # does.
        self._window_timer = QTimer(self)
        self._window_timer.setInterval(WINDOW_TICK_MS)
        self._window_timer.timeout.connect(self._age_out_request_buttons)
        self._window_timer.start()

        self._pulse_phase = 0.0
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(PULSE_TICK_MS)
        self._pulse_timer.timeout.connect(self._advance_pulse)

        self._flasher = RowFlasher(self._row_highlight, self.table, self)

        self._shimmer_step = 0
        self._shimmer_timer = QTimer(self)
        self._shimmer_timer.setInterval(SHIMMER_TICK_MS)
        self._shimmer_timer.timeout.connect(self._advance_shimmer)
        self._vat_column: int | None = None
        self.table.setItemDelegate(
            RowFillDelegate(self._row_accent, 0, self.table,
                            highlight=self._row_highlight))
        self._loading_delegate = LoadingCellDelegate(
            self._shimmer_strength, self._row_accent, 0, self.table,
            highlight=self._row_highlight)
        # Covers the table until the first read lands. Not fake rows: those
        # would reach the filter bar and the row styling.
        self._skeleton = SkeletonOverlay(self.table, columns=6)
        # Gutter matches the hover pencil (22px) plus its margin. The widest
        # label keeps the column wide enough even when no such row is among the
        # ones Qt samples for content sizing.
        self._pill_delegate = StatusPillDelegate(
            status_pill_style, self.table, right_gutter=32,
            widest_label=WIDEST_STATUS_LABEL, highlight=self._row_highlight,
            width_text=ongoing_width_text, display_text=self._animated_status,
            badges_for=self._failure_badges)
        self._qc_delegate = RequestActionDelegate(
            qc_pill_style, self.table,
            answerable=request_label(AWAITING_QC),
            coupons_for=self._coupons_of_row,
            askable=self._worth_asking_for,
            pulse=self._pulse_strength,
            display_text=self._animated_request,
            animated=request_label(TO_BE_SENT),
            highlight=self._row_highlight)
        self._qc_delegate.set_labels(QC_PILL_STYLES)
        # Which column each is on, decided after every render rather than here.
        self._status_column: int | None = None
        self._qc_column: int | None = None
        self._side_panel: QWidget | None = None
        # What each pull list row asks be cut, in the order the list shows them.
        self._pull_coupons: list[str] = []
        # And their states, in the same order, for the rows that are bolded.
        self._pull_states: list[str] = []
        # Whether the cursor was over one of the request buttons last time it
        # moved, so the cell it has just left gets repainted back to resting.
        self._qc_hovering = False
        self.table.cellClicked.connect(self._on_cell_clicked)
        # The ask button is drawn asleep and wakes under the cursor, which the
        # delegate reads as State_MouseOver. The view only tracks that with
        # hover turned on for the viewport.
        self.table.setMouseTracking(True)
        self.table.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)

        # Status ratio of whatever the filters currently show, beside the table.
        self.status_chart = StatusDonutWidget(
            title="Status breakdown", unit_label="electrodes",
            diameter=DONUT_DIAMETER)
        self.status_chart.set_info(self._status_definitions())
        # Preferred lets a layout shrink a widget to nothing, which is what the
        # scroll area did: the donut was clipped to a sliver and the panel
        # never scrolled, because its minimum came out as almost zero. Minimum
        # means it may grow but not shrink, so the panel keeps its real height
        # and the scroll area has something to scroll.
        self.status_chart.setSizePolicy(QSizePolicy.Policy.Preferred,
                                        QSizePolicy.Policy.Minimum)
        self.report_button = QPushButton("Get report")
        self.report_button.setMinimumHeight(30)
        self.report_button.setStyleSheet(TOOLBAR_BUTTON_STYLE)
        self.report_button.setIcon(qta.icon("fa6s.file-lines", color="white"))
        apply_modern_shadow(self.report_button)
        self.report_button.clicked.connect(self._on_report_clicked)

        # ONLY THE BREAKDOWN SCROLLS. The pull list and the report button are
        # what an operator came to this panel to use, and they were the two
        # things that fell off the bottom on a short screen, with nothing on
        # the page to say they were down there. The donut and its legend are a
        # summary: scrolling to read the last line of one costs nothing.
        chart_panel = QWidget()
        chart_layout = QVBoxLayout()
        # A gap on the right so the percentages do not run into the scrollbar
        # when the panel is scrolling.
        chart_layout.setContentsMargins(*PANEL_MARGINS)
        chart_layout.setSpacing(8)
        chart_layout.addWidget(self.status_chart, 0)
        chart_layout.addStretch(1)
        chart_panel.setLayout(chart_layout)
        self._chart_panel = chart_panel

        # The donut and the legend stack up to about 500px, and as a plain
        # child that became the window's minimum height: on a 200% display it
        # simply would not fit and the chart was squashed. In a scroll area the
        # panel keeps its natural size and the window is free to be any height,
        # scrolling here when it has to.
        chart_scroll = QScrollArea()
        chart_scroll.setWidget(chart_panel)
        chart_scroll.setWidgetResizable(True)
        chart_scroll.setFrameShape(QFrame.Shape.NoFrame)
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

        pinned = QWidget()
        pinned_layout = QVBoxLayout()
        pinned_layout.setContentsMargins(*PANEL_MARGINS)
        pinned_layout.setSpacing(PINNED_GAP)
        # Scrapped is pinned too. It was asked for as a number that is on
        # screen whatever the filters say, and in the scrolling half it was
        # only on screen when the window happened to be tall enough.
        pinned_layout.addWidget(self._build_scrap_panel(), 0)
        pinned_layout.addWidget(self._build_test_first_panel(), 0)
        pinned_layout.addWidget(self.report_button, 0)
        pinned.setLayout(pinned_layout)
        # May grow, may not shrink. Without this the layout squeezed the pinned
        # half when the panel ran short and clipped the pull list's one row,
        # which is the row it exists to show. The scroll area above it has a
        # minimum of nothing, so it is the one that gives way.
        pinned.setSizePolicy(QSizePolicy.Policy.Preferred,
                             QSizePolicy.Policy.Minimum)

        side_panel = QWidget()
        side_panel.setFixedWidth(CHART_PANEL_WIDTH)
        # Kept, because the pull list resizes it to whatever its columns need.
        self._side_panel = side_panel
        side_layout = QVBoxLayout()
        side_layout.setContentsMargins(0, 0, 0, 0)
        # Between the breakdown that scrolls and the block pinned under it.
        side_layout.setSpacing(PINNED_GAP)
        side_layout.addWidget(chart_scroll, 1)
        side_layout.addWidget(pinned, 0)
        side_panel.setLayout(side_layout)

        # THE FILTERS SIT OVER THE TABLE, not over the panel beside it. They
        # used to span the whole window, so the panel began three rows down and
        # that is the height the donut and the pull list were short of. The
        # search box and the count move with them, which is no loss: they
        # describe the table, which is what they now sit above.
        # THE TITLE SITS OVER THE TABLE TOO, for the same reason the filters
        # do. Spanning the window it left a band of empty white the width of
        # the side panel and the height of a heading, directly above the donut,
        # and the panel is the one column on this tab that is short of height.
        left_column = QVBoxLayout()
        left_column.setContentsMargins(0, 0, 0, 0)
        left_column.setSpacing(0)
        left_column.addLayout(toolbar, 0)
        left_column.addSpacing(4)
        left_column.addWidget(filter_scroll, 0)
        left_column.addSpacing(4)
        left_column.addWidget(self.table, 1)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addLayout(left_column, 1)
        body.addWidget(side_panel, 0)

        content_layout.addLayout(body, 1)
        content_container.setLayout(content_layout)

        main_layout.addWidget(content_container, 1)
        self.setLayout(main_layout)

        # Start empty — headers arrive with the view's columns.
        self.load_data(pd.DataFrame())

    # ------------------------------------------------------------------
    # Data loading / filtering
    # ------------------------------------------------------------------
    def show_loading(self) -> None:
        """Say the first read is on its way, until ``load_data`` arrives."""
        self._skeleton.start()
        self.status_chart.set_loading(True)

    def hide_loading(self) -> None:
        """Give up on the placeholder, for a read that never answered.

        The chart goes back to its ordinary empty state, which is the honest
        one once there is nothing still coming.
        """
        self._skeleton.stop()
        self.status_chart.set_loading(False)
        self._update_status_chart()

    def load_data(self, dataframe: pd.DataFrame | None) -> None:
        """Take a new dataset, refresh the filter options and re-render."""
        self._skeleton.stop()
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
        Mixed UTC offsets are normalised to UTC, then put on the operator's
        clock: the Date column beside the filter reads local, and a month list
        built from UTC would disagree with it either side of midnight."""
        date_column = str(ElectrodeColumns.DATE)
        if df is None or df.empty or date_column not in df.columns:
            return None
        parsed = pd.to_datetime(df[date_column], errors="coerce", utc=True)
        return parsed.dt.tz_convert(dt.datetime.now().astimezone().tzinfo)

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
        vats, unknown = set(), False
        if VAT_COLUMN in df.columns:
            for value in df[VAT_COLUMN]:
                text = str(value).strip()
                if text:
                    vats.add(text)
                else:
                    unknown = True
        # While the column is still loading every row is blank, and offering
        # nothing but Unidentified would be wrong. It comes back with the vats.
        unknown = unknown and bool(vats)
        # A single vat sorts before the pairs, and both sort numerically: "30"
        # must not come before "28, 30" the way text would put it. Unidentified
        # goes last, since it is not a vat.
        options = sorted(
            vats, key=lambda v: [int(n) for n in v.replace(" ", "").split(",")])
        self.filter_bar.set_options(
            FILTER_VAT, options + ([VAT_UNKNOWN] if unknown else []))

        if self._source_dates is not None:
            months = sorted(
                {(d.year, d.month) for d in self._source_dates.dropna()}, reverse=True)
            self.filter_bar.set_month_options(months)

    def _fit_filter_height(self) -> None:
        """Keep every filter row on screen, scrollbar or no scrollbar.

        The bar is two rows now. When the window is narrow enough for the
        horizontal scrollbar to appear it needs its own height on top, or it
        covers the bottom row and the date and range filters vanish.
        """
        scroll = getattr(self, "_filter_scroll", None)
        if scroll is None:
            return
        needed = self.filter_bar.sizeHint().height() + 6
        if scroll.horizontalScrollBar().maximum() > 0:
            needed += SCROLLBAR_HORIZONTAL_HEIGHT
        if scroll.height() != needed:
            scroll.setFixedHeight(needed)

    def _filtered(self) -> pd.DataFrame:
        """df_source narrowed by every active filter — they combine."""
        df = self.df_source
        if df is None or df.empty:
            return pd.DataFrame(columns=list(COLUMN_DISPLAY_NAMES))

        mask = pd.Series(True, index=df.index)

        statuses = self.filter_bar.values(FILTER_STATUS)
        if statuses and STATUS_COLUMN in df.columns:
            # The dropdown offers labels, the frame holds stored values.
            wanted = {status_value(s) for s in statuses}
            mask &= df[STATUS_COLUMN].astype(str).str.strip().isin(wanted)

        wanted_qc = self.filter_bar.values(FILTER_QC_REQUEST)
        if wanted_qc and QC_STATUS_COLUMN in df.columns:
            # The column already holds the label, so no mapping back: what is
            # ticked is what is in the cell, with the blank standing for the
            # electrodes nobody has asked about.
            chosen = {"" if value == QC_NONE else value for value in wanted_qc}
            mask &= df[QC_STATUS_COLUMN].astype(str).str.strip().isin(chosen)

        failures = self.filter_bar.values(FILTER_FAILURE)
        if failures and FAILURE_COLUMN in df.columns:
            # Either, not both: ticking two reasons asks for the electrodes
            # that gave one OR the other, the same as every other dropdown
            # here. An electrode with both is in either answer.
            codes = df[FAILURE_COLUMN].astype(str).str.strip()
            wanted = pd.Series(False, index=df.index)
            for label in failures:
                code = FAILURE_FILTER_CODES.get(label)
                if code:
                    wanted |= codes.str.split(",").apply(lambda got: code in got)
                elif label == FAILURE_NONE and STATUS_COLUMN in df.columns:
                    # Failed on a rig, and nobody wrote down what went wrong.
                    wanted |= ((codes == "")
                               & (df[STATUS_COLUMN].astype(str).str.strip()
                                  == str(ElectrodeStatus.TESTED_AND_SCRAPPED)))
            mask &= wanted

        types = self.filter_bar.values(FILTER_TYPE)
        type_column = str(ElectrodeColumns.FULL_NAME)
        if types and type_column in df.columns:
            mask &= df[type_column].astype(str).str.strip().isin(set(types))

        vats = self.filter_bar.values(FILTER_VAT)
        if vats and VAT_COLUMN in df.columns:
            column = df[VAT_COLUMN].astype(str).str.strip()
            # Matched whole, not by substring: an electrode plated in 28 and 30
            # is its own answer and does not belong under either on its own.
            wanted = column.isin({v for v in vats if v != VAT_UNKNOWN})
            if VAT_UNKNOWN in vats:
                wanted |= column == ""
            mask &= wanted

        recipes = self.filter_bar.values(FILTER_RECIPE)
        recipe_column = str(ElectrodeColumns.RECIPE)
        if recipes and recipe_column in df.columns:
            mask &= df[recipe_column].map(self._recipe_text).isin(set(recipes))

        low, high = self.filter_bar.number_bounds()
        marking_column = str(ElectrodeColumns.MARKING)
        if (low is not None or high is not None) and marking_column in df.columns:
            # By the number inside the marking, not by the text: as text B9
            # sorts after B14144, so a typed range would cut in the wrong place.
            numbers = pd.to_numeric(
                df[marking_column].map(electrode_number), errors="coerce")
            within = numbers.notna()
            if low is not None:
                within &= numbers >= low
            if high is not None:
                within &= numbers <= high
            mask &= within

        start, end = self.filter_bar.date_bounds()
        if (start or end) and self._source_dates is not None:
            days = self._source_dates.dt.date
            if start:
                mask &= days >= start
            if end:
                mask &= days <= end
            mask &= self._source_dates.notna()

        # The other date on the row, and a different question: when QC settled
        # the electrode, not when the line made it. An electrode nothing has
        # settled has no such date, so it drops out rather than being kept on
        # the strength of a blank.
        finalised = self.filter_bar.extra_date_range
        if finalised is not None and finalised.is_set() and FINALISED_COLUMN in df:
            settled = pd.to_datetime(df[FINALISED_COLUMN], errors="coerce",
                                     utc=True).dt.tz_convert(
                dt.datetime.now().astimezone().tzinfo)
            start, end = finalised.range()
            days = settled.dt.date
            if start:
                mask &= days >= start
            if end:
                mask &= days <= end
            mask &= settled.notna()

        search = self.filter_bar.search_text().lower()
        if search:
            # Match across every column, so an operator can type a marking, a
            # date fragment or a status without choosing a field first.
            # Cell by cell, because astype(str) leaves a missing value as a
            # real NaN rather than the string "nan", and joining the row then
            # fails on a float. A blank note is exactly that case.
            haystack = df.map(
                lambda v: "" if v is None or v != v else str(v).lower())
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
            display_df[date_column] = display_df[date_column].map(
                format_utc_timestamp_for_display)

        # Same shape as the Date column, but converted: these are stamped
        # in UTC by the database and the operator reads a local clock.
        if FINALISED_COLUMN in display_df.columns:
            display_df[FINALISED_COLUMN] = display_df[FINALISED_COLUMN].map(
                format_utc_timestamp_for_display)

        if STATUS_COLUMN in display_df.columns:
            display_df[STATUS_COLUMN] = display_df[STATUS_COLUMN].map(status_label)

        # The test-first flag has no column and no longer rides on the badge:
        # the panel beside the table is where the queue is read.
        if PRIORITY_COLUMN in display_df.columns:
            display_df = display_df.drop(columns=[PRIORITY_COLUMN])

        display_df = display_df[[c for c in COLUMN_DISPLAY_NAMES if c in display_df.columns]]
        display_df.columns = [COLUMN_DISPLAY_NAMES.get(c, c) for c in display_df.columns]

        try:
            self.table.load_data(display_df, cell_style_callback=self._cell_style)
        except Exception as e:
            print(f"Error loading electrodes table: {e}")
        self._place_status_delegate()
        self._place_qc_delegate()
        self._place_vat_delegate()
        self._sync_picked_row()
        self._sync_ongoing_timer()
        self._sync_pulse_timer()

    # ------------------------------------------------------------------
    # Row styling
    # ------------------------------------------------------------------
    def _fit_chart_panel(self) -> QWidget:
        """Give the scrolling panel the height its contents actually need.

        The legend grows and shrinks with how many statuses are on screen, and
        the scroll area is not resizing the panel for us, so the one thing it
        needs telling is this. Without it the panel keeps whatever height it
        had when the window was last resized, which at startup is the height of
        an empty chart, and everything below the fold stays unreachable.
        """
        panel = getattr(self, "_chart_panel", None)
        if panel is None:
            return
        # A MINIMUM, not a resize. The scroll area sizes the panel itself and
        # would undo a resize on its next pass, but it never shrinks the panel
        # below an explicit minimum, and setting one makes it rescroll there
        # and then.
        wanted = panel.sizeHint().height()
        if wanted and wanted != panel.minimumHeight():
            panel.setMinimumHeight(wanted)

    def _build_test_first_panel(self) -> QWidget:
        """The pull-list: which electrodes to take to SEM/IC first."""
        self.test_first_table = DataTableWidget(
            min_column_width=70,
            max_column_width=200,
            enable_alternating_colors=True,
            enable_row_coloring_on_add=False,
            min_table_height=90,
            scroll_to_top_on_load=True,
        )
        self.test_first_table.setStyleSheet(PULL_LIST_TABLE_STYLE)
        self.test_first_table.setFrameShape(QFrame.Shape.NoFrame)
        self.test_first_table.setSelectionMode(
            self.test_first_table.SelectionMode.NoSelection)
        self.test_first_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # Never sideways. The columns are sized to fit and the panel to them, so
        # a horizontal scrollbar here would only ever be a rounding error made
        # visible.
        self.test_first_table.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Shadows the class default on this instance alone. It is a list to run
        # an eye down, not a table to read, so it holds nine rows in the height
        # the full size ones took for six.
        self.test_first_table.BASE_ROW_HEIGHT = TEST_FIRST_ROW_HEIGHT
        # One click goes to that electrode in the table beside it. The pull list
        # is built from what the filters already show, so the row is always
        # there to jump to.
        self.test_first_table.setCursor(Qt.CursorShape.PointingHandCursor)
        self.test_first_table.cellClicked.connect(self._jump_from_pull_list)
        self._pull_delegate = CouponTextDelegate(self._pull_coupons_of_row,
                                                 self.test_first_table,
                                                 fill_for=self._pull_row_tint)
        # Every other column of the list, so a row breathes across its whole
        # width rather than only where the coupon delegate reaches.
        self.test_first_table.setItemDelegate(
            RowTintDelegate(self._pull_row_tint, self.test_first_table))
        self.test_first_table.verticalHeader().setVisible(False)
        self.test_first_table.set_columns_fill(True)
        self.test_first_table.setMinimumWidth(120)
        # The absolute ceiling; the share of the panel is worked out on
        # every fit, once the panel has a height to take a share of.
        self.test_first_table.setMaximumHeight(TEST_FIRST_MAX_HEIGHT)
        self.test_first_table.setMinimumHeight(0)

        # Beside the heading, because it governs what this list contains: a
        # failure QC wants nothing from puts no electrodes in it.
        self.coupon_settings_button = QPushButton("Coupon Settings")
        self.coupon_settings_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.coupon_settings_button.setStyleSheet(COUPON_BUTTON_STYLE)
        self.coupon_settings_button.clicked.connect(self._open_coupon_settings)

        # NO HEADING. The list is getting a new purpose, and a title naming the
        # old one would be the first thing read and the last thing changed.
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(10)
        title_row.addStretch(1)
        title_row.addWidget(self.coupon_settings_button, 0)

        panel = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(PULL_LIST_GAP)
        layout.addLayout(title_row)
        layout.addWidget(self.test_first_table, 1)
        panel.setLayout(layout)
        return panel

    def _open_coupon_settings(self) -> None:
        """What QC wants cut, per reason. Saved one setting at a time.

        The tab is handed the two callables, as always. Without them the dialog
        still opens and shows the defaults, which is what a database that has
        not had the migration looks like.
        """
        settings = (self._get_coupon_settings() if self._get_coupon_settings
                    else {})
        dialog = CouponSettingsDialog(self, settings=settings,
                                      on_save=self._on_coupon_setting)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            # The statuses are computed from these, so the screen has to be
            # rebuilt rather than merely repainted.
            self.check_for_external_updates()

    def _update_test_first_table(self) -> None:
        """Fill the pull-list from whatever the filters currently show."""
        if self.test_first_table is None:
            return
        # Scheduled rather than called at the end: it has to run after the rows
        # exist and have been laid out, and this covers every way out of here.
        QTimer.singleShot(0, self._fit_test_first_table)
        visible = self.df_visible
        if visible is None or visible.empty or PRIORITY_COLUMN not in visible.columns:
            self.test_first_table.load_data(pd.DataFrame())
            return

        flagged = self._in_answer_order(visible[self._still_going_on(visible)])
        if flagged.empty:
            self.test_first_table.load_data(pd.DataFrame())
            return

        # NO TYPE COLUMN. It is the same for every row in practice and it is on
        # the electrode's own row in the table beside this, which is now one
        # click away.
        columns = {
            "Electrode": flagged[str(ElectrodeColumns.MARKING)].astype(str).values,
        }
        # TO THE MINUTE, unlike Produced. Requests arrive in bursts, several
        # within one finalise, so the day alone would show a column of one
        # repeated date and say nothing about the order they came in.
        if QC_REQUESTED_COLUMN in flagged.columns:
            columns["Requested"] = flagged[QC_REQUESTED_COLUMN].map(
                format_utc_timestamp_for_display).values
        # Feature by presence: a database without the qc_requests schema returns
        # no such column, and the list reads as it always did.
        #
        # SPELT OUT WHEN THERE IS NO REQUEST, rather than left blank. These are
        # the electrodes a failure says to pull next, and one nobody has asked
        # production for is the gap worth seeing. The flags frozen before the
        # requests existed are all in exactly that state.
        if QC_STATUS_COLUMN in flagged.columns:
            asked = flagged[QC_STATUS_COLUMN].astype(str).str.strip()
            columns[COLUMN_DISPLAY_NAMES[QC_STATUS_COLUMN]] = asked.where(
                asked != "", NOT_REQUESTED).values
        # Kept per row rather than put in a column of its own: the icon is
        # painted in front of the state, which is where it reads.
        self._pull_coupons = (
            list(flagged[QC_COUPONS_COLUMN].astype(str).str.strip())
            if QC_COUPONS_COLUMN in flagged.columns else [])
        # And the states, for the one row shape that is emphasised.
        self._pull_states = (list(asked) if QC_STATUS_COLUMN in flagged.columns
                             else [])
        self.test_first_table.load_data(
            pd.DataFrame(columns), cell_style_callback=self._pull_cell_style)
        self._fit_test_first_columns()
        self._place_pull_coupon_delegate()

    def _jump_from_pull_list(self, row: int, _column: int) -> None:
        """Go to the electrode this row names, in the table beside the list.

        The list says which to fetch next and the table is where everything
        about it is, so the two were being read side by side with the marking
        carried across by eye. Any column jumps: the whole row is the target.
        """
        item = self.test_first_table.item(row, 0)
        if item is None:
            return
        self._jump_to_marking(item.text().strip())

    def _jump_to_marking(self, marking: str) -> None:
        """Centre that electrode in the main table and pick it out.

        Centred rather than put under the top edge, which is what restoring the
        scroll position does: this is an answer to a click, and an answer in the
        middle of the screen is one the eye finds without hunting the edges.
        """
        if not marking or self.table is None or self.df_visible is None:
            return
        column = str(ElectrodeColumns.MARKING)
        if column not in self.df_visible.columns:
            return
        # By position, not by index label: the row number in the table is the
        # position in the frame, which an index that has been filtered is not.
        markings = self.df_visible[column].astype(str).str.strip().tolist()
        if marking not in markings:
            return
        row = markings.index(marking)
        item = self.table.item(row, 0)
        if item is None:
            return
        self.table.scrollToItem(item, self.table.ScrollHint.PositionAtCenter)
        # Picked, not selected: this table has selection turned off and marks
        # its rows with a painted outline instead. Held by marking, so the
        # outline follows the electrode through a filter or a reload rather
        # than staying on whatever row number it had.
        self._picked_marking = marking
        self._sync_picked_row()
        self.table.viewport().update()

    @staticmethod
    def _in_answer_order(flagged):
        """Whoever is waiting on QC first, then whoever QC is waiting on.

        An offer is the only row here that nobody can act on until QC answers
        it, so it goes to the top. Then Pending, which production has agreed to
        and not yet sent, then Requested, which it has not answered. Anything
        with no request at all is last: it is waiting on the sweep rather than
        on a person.

        A stable sort, so inside each group the list keeps the order it had,
        which is newest electrode first.
        """
        if flagged.empty or QC_STATUS_COLUMN not in flagged.columns:
            return flagged
        rank = {request_label(state): order for order, state in
                enumerate((AWAITING_QC, TO_BE_SENT, AWAITING_PRODUCTION))}
        asked = flagged[QC_STATUS_COLUMN].astype(str).str.strip()
        return flagged.assign(
            _rank=asked.map(lambda label: rank.get(label, len(rank)))
        ).sort_values("_rank", kind="stable").drop(columns="_rank")

    @staticmethod
    def _still_going_on(visible) -> "pd.Series":
        """Which rows the panel lists: the ones somebody is still waiting on.

        A request that is open, whichever side has it, and an electrode a
        failure says to test that nobody has asked for yet. Both are work in
        flight.

        DECLINED AND SENT DROP OUT, even on a flagged electrode. Both are final:
        production has answered, and a list of what is going on that keeps
        showing answered questions stops being read.
        """
        open_now = {request_label(state) for state in OPEN_STATES}
        asked = (visible[QC_STATUS_COLUMN].astype(str).str.strip()
                 if QC_STATUS_COLUMN in visible.columns
                 else pd.Series("", index=visible.index))
        flagged = (visible[PRIORITY_COLUMN].astype(str).str.strip()
                   == PRIORITY_FLAG)
        return asked.isin(open_now) | (flagged & (asked == ""))

    def _place_pull_coupon_delegate(self) -> None:
        """Draw the coupon icon in the pull list's request column."""
        table = self.test_first_table
        wanted = COLUMN_DISPLAY_NAMES[QC_STATUS_COLUMN]
        for column in range(table.columnCount()):
            header = table.horizontalHeaderItem(column)
            if header is not None and header.text() == wanted:
                table.setItemDelegateForColumn(column, self._pull_delegate)
                return

    def _pull_cell_style(self, row: int, _column: str, _value):
        """Bold the whole row where the next move is QC's.

        Offered to QC is the only shape on this list that somebody here has to
        answer: the rest are waiting on production. The list is read in a
        hurry, so the rows that need an answer are the ones that have to be
        seen first, and weight says that faster than a colour would in a panel
        that is already carrying a badge and an icon.
        """
        offered = request_label(AWAITING_QC)
        if 0 <= row < len(self._pull_states) and self._pull_states[row] == offered:
            return CellStyle(bold=True)
        return None

    def _pull_coupons_of_row(self, row: int) -> str:
        """What the pull list row at this index asks be cut."""
        return (self._pull_coupons[row]
                if 0 <= row < len(self._pull_coupons) else "")

    def _fit_test_first_columns(self) -> None:
        """Hold the request column at its longest state, share the rest equally.

        The table splits its width evenly, which suited three short columns and
        does not once a request state is among them: an even quarter elided
        every one of them. Sized from the labels rather than from a number, so
        a wording change cannot quietly start clipping them.
        """
        table = self.test_first_table
        header = table.horizontalHeader()
        last = header.count() - 1
        heading = table.horizontalHeaderItem(last)
        if last < 1 or heading is None:
            return
        if heading.text() != COLUMN_DISPLAY_NAMES[QC_STATUS_COLUMN]:
            return
        # SIZED BY QT, NOT BY OUR OWN METRICS. The table's stylesheet paints its
        # items a point larger than table.font() reports, so measuring the text
        # here came out short and the dates were clipped on screen. Qt's own hint
        # asks the items, so it is right whatever the stylesheet and the screen
        # scaling say.
        #
        # EVERY COLUMN TO ITS CONTENT, and then the panel to their sum. The
        # panel used to be a fixed width, so whatever the columns did not use
        # had to go somewhere and one of them was always fat: first the
        # marking, then the request state. Fitting the panel to the table
        # instead leaves no slack to hand out, and it holds whatever the font
        # and the screen's scaling turn out to be, which a number here cannot.
        for index in range(last + 1):
            header.setSectionResizeMode(index, QHeaderView.ResizeMode.Fixed)
            # The wider of the cells and the heading. sizeHintForColumn asks
            # the items only, so a column of six character markings came out
            # narrower than the word Electrode above it and clipped its own
            # heading.
            table.setColumnWidth(index, max(table.sizeHintForColumn(index),
                                            header.sectionSizeHint(index)))
        # The request column is held at its longest wording rather than at what
        # is on screen now, so the panel does not breathe in and out as states
        # change under it.
        table.setColumnWidth(last, max(table.columnWidth(last),
                                       self._request_column_width(last)))
        self._fit_panel_to_list()

    def _fit_panel_to_list(self) -> None:
        """Make the side panel as wide as the pull list needs, and no wider.

        Clamped at both ends: the donut and its legend live in the same panel
        and have their own minimum, and a long request state must not be able
        to push the table off the screen.
        """
        table = self.test_first_table
        if self._side_panel is None or not table.columnCount():
            return
        columns = sum(table.columnWidth(index)
                      for index in range(table.columnCount()))
        # Plus a few pixels: fitted to the exact sum, Qt's own rounding was
        # enough to overflow by one and hang a horizontal scrollbar under a
        # table that fits.
        wanted = (columns + SCROLLBAR_VERTICAL_WIDTH + PANEL_MARGINS[0]
                  + PANEL_MARGINS[2] + 6)
        width = max(CHART_PANEL_MIN, min(CHART_PANEL_MAX, wanted))
        if self._side_panel.width() != width:
            self._side_panel.setFixedWidth(width)

    def _request_column_width(self, index: int) -> int:
        """Wide enough for the longest state, not just the ones on screen now.

        Held at the longest wording so the column does not jump about as states
        change under it. Scaled from Qt's hint for what is there rather than
        measured outright, because a ratio of two text widths survives the
        stylesheet's font and the screen's scaling, and an absolute measurement
        in this module does not.
        """
        table = self.test_first_table
        metrics = QFontMetrics(table.font())
        shown = [table.horizontalHeaderItem(index).text()]
        shown += [table.item(row, index).text()
                  for row in range(table.rowCount())
                  if table.item(row, index) is not None]
        here = max(metrics.horizontalAdvance(text) for text in shown)
        # The ones this list can show, not every state there is. Sent and
        # declined never appear here, so holding the column wide enough for
        # them would spend pixels on words it cannot draw.
        can_show = [request_label(state) for state in OPEN_STATES]
        longest = max(metrics.horizontalAdvance(text) for text in
                      (*can_show, NOT_REQUESTED, shown[0]))
        hint = table.sizeHintForColumn(index)
        wanted = hint if here <= 0 else max(hint, round(hint * longest / here))
        # Plus the coupon icon, which is painted in front of the text and which
        # the hint knows nothing about: Qt measured the words alone.
        return wanted + CouponTextDelegate.EXTRA

    def _fit_test_first_table(self) -> None:
        """Give the pull list the height its rows need, within its own band.

        It is pinned under the breakdown, so every pixel it reserves is a pixel
        the donut and its legend do not get, and a long list is capped rather
        than cutting the legend off.

        THE FLOOR IS NOT NEGOTIABLE, though. Sized to three rows the list came
        out shorter than its own header and read as a scrap of table, with the
        room it had just left blank above it. A block of a known size is read
        at a glance on any screen; a block that changes height with its
        contents has to be found first.
        """
        table = self.test_first_table
        if table is None:
            return
        # A nominal row height when the rows have not been measured yet, or an
        # empty list collapses to a header with nowhere to put the first row.
        row_height = table.rowHeight(0) or table.BASE_ROW_HEIGHT
        header = table.horizontalHeader().height()
        wanted = header + max(1, table.rowCount()) * row_height + 4
        table.setFixedHeight(max(TEST_FIRST_MIN_HEIGHT,
                                 min(self._pull_list_ceiling(), wanted)))

    def _pull_list_ceiling(self) -> int:
        """The tallest the pull list may be on this window.

        Measured off the panel it sits in rather than the window, since that is
        what it actually competes with for height.
        """
        panel = self._side_panel
        room = panel.height() if panel is not None else 0
        left_over = room - TEST_FIRST_BREAKDOWN_ROOM
        return max(TEST_FIRST_MIN_HEIGHT,
                   min(TEST_FIRST_MAX_HEIGHT, left_over))

    def statuses_in_scope(self) -> list[str]:
        """The stored statuses the report and the chart should cover.

        Whatever the Status filter asks for, or all of them when it asks for
        nothing. A status nobody ticked is not a zero worth printing: it was
        excluded on purpose, and a row of zeros reads as a finding.
        """
        chosen = [status_value(label) for label in self.filter_bar.values(FILTER_STATUS)]
        known = [str(status) for status in ElectrodeStatus]
        return [status for status in known if status in chosen] if chosen else known

    def _status_split(self) -> tuple[dict[str, int], dict[str, str]]:
        """``(counts, colours)`` for the rows currently shown.

        A status that is in scope is listed even at zero, so an empty category
        reads as "none of these" rather than silently vanishing. Shared by the
        donut and the report, so the ring on paper is the ring on screen.
        """
        chosen = set(self.statuses_in_scope())
        counts = {chart_label(status): 0 for status in CHART_ORDER
                  if str(status) in chosen}
        if self.df_visible is not None and STATUS_COLUMN in self.df_visible.columns:
            for status, count in self.df_visible[STATUS_COLUMN].astype(str).str.strip(
            ).value_counts().items():
                label = chart_label(status)
                if label in counts:
                    counts[label] = int(count)
        return counts, {chart_label(s): c for s, c in ROW_ACCENTS.items()}

    # An electrode that will not ship, whichever way it got there: rejected by
    # a neighbour's fault, failed on the rig, or passed and destroyed by the
    # test itself.
    SCRAP_STATUSES = (ElectrodeStatus.SCRAPPED,
                      ElectrodeStatus.TESTED_AND_RELEASED,
                      ElectrodeStatus.TESTED_AND_SCRAPPED)

    def _scrap_rate(self) -> tuple[int, int]:
        """``(scrapped, shown)`` over the rows currently on screen.

        Measured against everything shown, tested or not, so it is the same
        share the ring above it draws and the report foots to. It therefore
        moves with the untested backlog as well as with quality.
        """
        if self.df_visible is None or STATUS_COLUMN not in self.df_visible.columns:
            return 0, 0
        shown = self.df_visible[STATUS_COLUMN].astype(str).str.strip()
        scrapped = shown.isin([str(s) for s in self.SCRAP_STATUSES]).sum()
        return int(scrapped), int(len(shown))

    def _update_scrap_rate(self) -> None:
        """Refresh the scrap line under the donut."""
        scrapped, shown = self._scrap_rate()
        share = f"{scrapped / shown * 100:.1f} %" if shown else "n/a"
        self.scrap_value.setText(f"{scrapped:,}".replace(",", " ")
                                 + f"   {share}")

    def _build_scrap_panel(self) -> QWidget:
        """The scrapped figure, under a rule so it reads apart from the ring.

        Always on show, whatever the status filter is doing, because it is
        the one number management asks for. Shares the ring's denominator, so
        the two agree.
        """
        rule = QFrame()
        rule.setFrameShape(QFrame.Shape.HLine)
        rule.setStyleSheet("color: #E4E7EB;")

        caption = QLabel("Scrapped")
        caption.setStyleSheet("color: #1F1F1F; font-size: 12px; font-weight: 700;")
        self.scrap_value = QLabel()
        self.scrap_value.setStyleSheet(
            "color: #8E1537; font-size: 15px; font-weight: 700;")

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(rule)
        layout.addWidget(caption)
        layout.addWidget(self.scrap_value)
        panel = QWidget()
        panel.setLayout(layout)
        self._update_scrap_rate()
        return panel

    def _build_zoom_widget(self) -> QWidget:
        """Minus, the current scale, plus, as one widget for the filter bar."""
        self.zoom_out_button = QPushButton("\u2212")     # a real minus sign
        self.zoom_out_button.setToolTip("Smaller")
        self.zoom_in_button = QPushButton("+")
        self.zoom_in_button.setToolTip("Bigger")
        self.zoom_label = QLabel()
        self.zoom_label.setStyleSheet(ZOOM_LABEL_STYLE)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        for button, step in ((self.zoom_out_button, -DataTableWidget.ZOOM_STEP),
                             (self.zoom_in_button, DataTableWidget.ZOOM_STEP)):
            button.setStyleSheet(ZOOM_BUTTON_STYLE)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _c, s=step: self._step_zoom(s))

        strip = QHBoxLayout()
        strip.setContentsMargins(0, 0, 0, 0)
        strip.setSpacing(6)
        strip.addWidget(self.zoom_out_button, 0)
        strip.addWidget(self.zoom_label, 0)
        strip.addWidget(self.zoom_in_button, 0)
        holder = QWidget()
        holder.setLayout(strip)
        self._sync_zoom_label()
        return holder

    def _step_zoom(self, step: float) -> None:
        """One step in or out, then say where we ended up."""
        if self.table is None:
            return
        self.table.set_zoom(self.table.zoom() + step)
        self._sync_zoom_label()

    def _sync_zoom_label(self) -> None:
        """The percentage, and the buttons greyed at either end.

        Reads 100% before the table is built: the zoom goes into the filter bar,
        which is assembled first, so waiting for the table left the label blank
        until somebody pressed a button.
        """
        table = getattr(self, "table", None)
        scale = table.zoom() if table is not None else 1.0
        self.zoom_label.setText(f"{round(scale * 100)}%")
        self.zoom_out_button.setEnabled(scale > DataTableWidget.ZOOM_MIN)
        self.zoom_in_button.setEnabled(scale < DataTableWidget.ZOOM_MAX)

    def _update_status_chart(self) -> None:
        """Feed the donut the status split of the rows currently shown."""
        self.status_chart.update_data(*self._status_split())
        # Next turn of the event loop, not now: the legend is rebuilt as part
        # of this call and the panel does not know its new height until it has
        # settled. Measured here it reports the height of the chart it is
        # replacing, which at startup is an empty one.
        QTimer.singleShot(0, self._fit_chart_panel)
        self._update_scrap_rate()

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
    def patch_statuses(self, dataframe: pd.DataFrame | None,
                       flash: bool = False) -> None:
        """Adopt a freshly processed frame, repainting only the changed rows.

        ``flash`` lights the rows that moved, for a change that came from
        somebody else rather than from something this operator just did.

        A QC-board move changes one electrode's status; reloading would rebuild
        every cell in the table for it. This diffs the Status column and touches
        just the rows that moved.
        """
        if (self.df_source is None or dataframe is None
                or len(dataframe) != len(self.df_source)
                or STATUS_COLUMN not in dataframe.columns):
            self.load_data(dataframe)
            return

        # Every column the processor works out, diffed and adopted together.
        # Leaving one out keeps a stale cell: the backend recomputes it and the
        # patch throws the new value away. That happened with the finalised
        # date, then the QC request state, then the coupons, so the list is no
        # longer written out here. It comes from the processor, which is what
        # computes them, and the two cannot drift apart again.
        tracked = [c for c in DERIVED_COLUMNS
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
        if self.filter_bar.values(FILTER_STATUS):
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
        if flash:
            self.flash_markings(moved)
        self._sync_ongoing_timer()
        self._sync_pulse_timer()

        self._update_status_chart()
        self._update_test_first_table()

    def _animated_status(self, label: str) -> str:
        """The badge's text this instant: Test ongoing grows its dots.

        Resolved by the delegate on every paint rather than written into the
        cell, which is set once when the table is built.
        """
        text = str(label).strip()
        base = status_label(ElectrodeStatus.TEST_ONGOING)
        if not text.startswith(base):
            return label
        # Any dots already on the text are dropped, not built on. Replacing the
        # label in place used to keep them, so a cell that somehow arrived
        # reading "Test ongoing..." animated from three dots to six: the first
        # three never cleared, and the badge read as stuck rather than moving.
        # What follows the dots, the test-first suffix, is kept.
        rest = text[len(base):].lstrip(".")
        return self._ongoing_text() + rest

    def _place_status_delegate(self) -> None:
        """Put the badge delegate on whichever column the Status ended up in.

        Not a fixed index: the Vat column is only there when the process log
        could be read, so the Status column moves. Pinning it meant that on a
        database with no vats the badges were painted onto a column that was
        not there, and the statuses came out as plain text.
        """
        if self.table is None:
            return
        found = self._column_of(COLUMN_DISPLAY_NAMES[STATUS_COLUMN])
        if found == self._status_column:
            return
        if self._status_column is not None:
            self.table.setItemDelegateForColumn(self._status_column, None)
        self._status_column = found
        if found is not None:
            self.table.setItemDelegateForColumn(found, self._pill_delegate)

    def _qc_action_clicked(self, row: int, column: int) -> bool:
        """Handle a click in the QC request column. True if it was one of ours.

        Worked out from where the click landed rather than from a widget per
        row: there are thirteen thousand rows and three buttons on screen.
        """
        if column != self._qc_column or self.df_visible is None:
            return False
        if not 0 <= row < len(self.df_visible):
            return False
        item = self.table.item(row, column)
        text = item.text() if item is not None else ""
        where = self.table.visualItemRect(item) if item is not None else None
        if where is None:
            return False
        point = self.table.viewport().mapFromGlobal(QCursor.pos())
        action = self._qc_delegate.action_at(QRectF(where), QPointF(point), text,
                                            self._coupons_of_row(row), row=row)
        if not action:
            return False

        marking = str(self.df_visible.iloc[row][str(ElectrodeColumns.MARKING)]).strip()
        if action == RequestActionDelegate.REQUEST:
            self._ask_for_qc(marking)
        else:
            self._answer_offer(marking,
                               action == RequestActionDelegate.APPROVE)
        return True

    def _ask_for_qc(self, marking: str) -> None:
        """Ask production to send one electrode, after confirming.

        ASKED HERE, not taken from a setting. The three settings cover the
        requests this application raises on its own; a hand raised one is
        somebody deciding about this electrode in particular, so they say what
        they want from it in the same breath.
        """
        if self._on_qc_request is None:
            return
        agreed, coupons = ask_with_coupons(
            self, "QC request", f"Request {marking} for QC?",
            confirm_text="Request")
        if not agreed:
            return
        if not self._on_qc_request(marking, coupons):
            self._say_it_failed(marking)

    def _answer_offer(self, marking: str, approve: bool) -> None:
        """Accept or turn down an electrode production offered."""
        if self._on_qc_answer is None:
            return
        what = "Accept" if approve else "Turn down"
        # Named for what it is, the same way the request side reads. "Accept
        # B15778?" left the operator to remember what they were accepting.
        #
        # Accepting also asks what to cut: the offer arrived with nothing
        # recorded, because production proposes the electrode and QC decides
        # what it wants from it. Turning one down asks nothing.
        if approve:
            agreed, coupons = ask_with_coupons(
                self, "QC request", f"{what} {marking} for QC?",
                confirm_text=what)
        else:
            agreed, coupons = ask(self, "QC request",
                                  f"{what} {marking} for QC?",
                                  confirm_text=what, danger=True), ""
        if not agreed:
            return
        if not self._on_qc_answer(marking, approve, coupons):
            self._say_it_failed(marking)

    def _say_it_failed(self, marking: str) -> None:
        """Nothing changed, and saying nothing would read as it having worked.

        The usual cause is somebody else getting there first: one row per
        electrode, ever, so a request raised on another machine a moment ago
        means this one does nothing.
        """
        WarningDialog(self, title="QC request",
                      message=f"Nothing changed for {marking}. Somebody may "
                              f"have got there first.",
                      icon="!", header_color="#B45309").exec()

    def _place_qc_delegate(self) -> None:
        """Put the request badge on whichever column the QC request ended up in.

        Same reasoning as the status one: the Vat column comes and goes, so
        the index is not fixed.
        """
        if self.table is None:
            return
        found = self._column_of(COLUMN_DISPLAY_NAMES[QC_STATUS_COLUMN])
        if found == self._qc_column:
            return
        if self._qc_column is not None:
            self.table.setItemDelegateForColumn(self._qc_column, None)
        self._qc_column = found
        if found is not None:
            self.table.setItemDelegateForColumn(found, self._qc_delegate)

    def _column_of(self, header: str) -> int | None:
        """The column carrying this header, or None when it is not shown."""
        if self.table is None:
            return None
        for column in range(self.table.columnCount()):
            item = self.table.horizontalHeaderItem(column)
            if item is not None and item.text() == header:
                return column
        return None

    def _place_vat_delegate(self) -> None:
        """Show the Vat column as loading for as long as it is empty.

        The column is there from the first render, but the process log is read
        after the window opens, so for a second or two it has nothing in it.
        """
        if self.table is None:
            return
        found = self._column_of(COLUMN_DISPLAY_NAMES[VAT_COLUMN])
        waiting = found is not None and not self._any_vat_shown(found)
        if waiting and not self._shimmer_timer.isActive():
            self._shimmer_timer.start()
        elif not waiting and self._shimmer_timer.isActive():
            self._shimmer_timer.stop()

        if found == self._vat_column:
            return
        if self._vat_column is not None:
            self.table.setItemDelegateForColumn(self._vat_column, None)
        self._vat_column = found
        if found is not None:
            self.table.setItemDelegateForColumn(found, self._loading_delegate)

    def _any_vat_shown(self, column: int) -> bool:
        """Whether any row in the Vat column has a value yet."""
        frame = self.df_visible
        if frame is not None and VAT_COLUMN in frame.columns:
            return bool(frame[VAT_COLUMN].astype(str).str.strip().any())
        return any(self.table.item(row, column) is not None
                   and self.table.item(row, column).text().strip()
                   for row in range(self.table.rowCount()))

    def _shimmer_strength(self) -> float:
        """How bright the placeholder is now, or below zero once it is done."""
        if not self._shimmer_timer.isActive():
            return -1.0
        return pulse_strength(self._shimmer_step)

    def _advance_shimmer(self) -> None:
        self._shimmer_step += 1
        if self.table is not None:
            self.table.viewport().update()

    def flash_markings(self, markings) -> None:
        """Light the rows for these electrodes, wherever they are on screen.

        By marking rather than by row: the frame is rebuilt around the change,
        so a row index taken before it means nothing afterwards.
        """
        if self.table is None or self.df_visible is None or not markings:
            return
        column = str(ElectrodeColumns.MARKING)
        if column not in self.df_visible.columns:
            return
        wanted = {str(m).strip() for m in markings}
        visible = self.df_visible[column].astype(str).str.strip()
        rows = [index for index, marking in enumerate(visible) if marking in wanted]
        if not rows:
            return
        self._flasher.flash(rows)

    def _age_out_request_buttons(self) -> None:
        """Repaint, so an electrode that has just passed the window loses its
        button without waiting for somebody to scroll past it."""
        if self.table is not None:
            self.table.viewport().update()

    def _advance_pulse(self) -> None:
        """Next instant of the breath, and repaint whatever is drawing it."""
        self._pulse_phase = (self._pulse_phase
                             + PULSE_TICK_MS / PULSE_PERIOD_MS) % 1.0
        if self.table is not None:
            self.table.viewport().update()
        if self.test_first_table is not None:
            self.test_first_table.viewport().update()

    def _pulse_strength(self) -> float:
        """Where the breath is now, 0 at rest and 1 at the top.

        A cosine rather than a sawtooth: it slows at both ends, which is what
        makes it read as breathing instead of as flashing.
        """
        return (1.0 - math.cos(2.0 * math.pi * self._pulse_phase)) / 2.0

    def _pull_row_tint(self, row: int):
        """The breathing colour behind a pull list row, or None for a still one.

        Only the rows waiting on an answer from here. Blended from the row's
        own background so the alternating stripes still show through, which
        keeps the list readable as a list while one of its rows is moving.
        """
        offered = request_label(AWAITING_QC)
        if not (0 <= row < len(self._pull_states)
                and self._pull_states[row] == offered):
            return None
        table = self.test_first_table
        base = (table.palette().alternateBase().color() if row % 2
                else table.palette().base().color())
        return blend(base.name(), PULSE_BLUE,
                     PULSE_ROW_DEPTH * self._pulse_strength())

    def _sync_pulse_timer(self) -> None:
        """Run the breath only while something is actually waiting on QC.

        Nothing offered means nothing to notice, and a timer repainting two
        tables forty times a second for no reason is a laptop fan.
        """
        showing = self._any_row_is(QC_STATUS_COLUMN, request_label(AWAITING_QC))
        if showing and not self._pulse_timer.isActive():
            self._pulse_timer.start()
        elif not showing and self._pulse_timer.isActive():
            self._pulse_timer.stop()
            self._pulse_phase = 0.0
            if self.table is not None:
                self.table.viewport().update()
            if self.test_first_table is not None:
                self.test_first_table.viewport().update()

    def _advance_ongoing(self) -> None:
        """Next frame of the dots, and repaint the rows showing them."""
        self._ongoing_frame = (self._ongoing_frame + 1) % len(ONGOING_DOTS)
        if self.table is not None:
            self.table.viewport().update()

    def _any_row_is(self, column: str, value: str) -> bool:
        """Whether any visible row holds that value in that column."""
        frame = self.df_visible
        if frame is None or column not in frame.columns:
            return False
        return bool((frame[column].astype(str).str.strip() == value).any())

    def _dots(self) -> str:
        """This frame of the marching dots, shared by every animated badge."""
        return ONGOING_DOTS[self._ongoing_frame]

    def _animated_request(self, label: str) -> str:
        """The QC request badge's text this instant: Pending grows its dots."""
        text = str(label).strip()
        base = request_label(TO_BE_SENT)
        if text != base:
            return label
        return base + self._dots()

    def _ongoing_text(self) -> str:
        """"Test ongoing" with this frame's dots after it."""
        return (status_label(ElectrodeStatus.TEST_ONGOING)
                + ONGOING_DOTS[self._ongoing_frame])

    def _sync_ongoing_timer(self) -> None:
        """Run the animation only while something on screen is still moving.

        Two badges use it now: an electrode being tested, and a request QC has
        agreed to that production has not sent yet. Both mean the same thing to
        an operator, that this row is waiting on somebody, and the dots are what
        says so without a column for it.
        """
        showing = self._any_row_is(STATUS_COLUMN,
                                   str(ElectrodeStatus.TEST_ONGOING)) or             self._any_row_is(QC_STATUS_COLUMN, request_label(TO_BE_SENT))
        if showing and not self._ongoing_timer.isActive():
            self._ongoing_timer.start()
        elif not showing and self._ongoing_timer.isActive():
            self._ongoing_timer.stop()
            self._ongoing_frame = 0

    def _status_display(self, row_index: int) -> str:
        """The Status cell's text: the status, plus the test-first suffix.

        Through status_label, exactly as _render does. The badge styles are
        keyed by what is on screen, so writing the stored value here left the
        two tested statuses matching nothing and the cell drew empty.
        """
        return status_label(self._status_at(row_index) or "")

    def _finalised_display(self, row_index: int) -> str:
        """The Status finalised cell's text, formatted as _render does."""
        if (self.df_visible is None or row_index >= len(self.df_visible)
                or FINALISED_COLUMN not in self.df_visible.columns):
            return ""
        return format_utc_timestamp_for_display(
            self.df_visible.iloc[row_index][FINALISED_COLUMN])

    def _qc_display(self, row_index: int) -> str:
        """The QC request cell's text, which the frame already holds as a label."""
        if (self.df_visible is None or row_index >= len(self.df_visible)
                or QC_STATUS_COLUMN not in self.df_visible.columns):
            return ""
        return str(self.df_visible.iloc[row_index][QC_STATUS_COLUMN])

    def _repaint_row(self, row_index: int) -> None:
        """Rewrite one rendered row's status text and tint, in place."""
        if self.table is None or row_index >= self.table.rowCount():
            return
        # Both derived cells, not just the status: setting one by hand stamps
        # the finalise time too, and repainting the status alone left that
        # showing until the next full reload.
        rewrite = {COLUMN_DISPLAY_NAMES[STATUS_COLUMN]: self._status_display,
                   COLUMN_DISPLAY_NAMES[FINALISED_COLUMN]: self._finalised_display,
                   COLUMN_DISPLAY_NAMES[QC_STATUS_COLUMN]: self._qc_display}
        for column in range(self.table.columnCount()):
            item = self.table.item(row_index, column)
            if item is None:
                continue
            header = self.table.horizontalHeaderItem(column)
            name = header.text() if header else ""
            if name in rewrite:
                item.setText(rewrite[name](row_index))
            DataTableWidget._apply_cell_style(
                item, self._cell_style(row_index, name, item.text()))

    def get_data(self) -> pd.DataFrame:
        """The full source DataFrame (original column names, unfiltered)."""
        return self.df_source if self.df_source is not None else pd.DataFrame()

    def _point_at_qc_buttons(self, event) -> None:
        """A pointing hand over the QC request buttons, an arrow everywhere else.

        The badges are painted, not widgets, so nothing else tells an operator
        the tick and the cross can be pressed. The cursor is what everyone
        already reads a button by, and it costs one hit test per move.
        """
        viewport = self.table.viewport()
        point = event.position().toPoint()
        index = self.table.indexAt(point)
        over = False
        cell = None
        if index.isValid() and index.column() == self._qc_column:
            item = self.table.item(index.row(), index.column())
            if item is not None:
                cell = self.table.visualItemRect(item)
                over = bool(self._qc_delegate.action_at(
                    QRectF(cell), QPointF(point), item.text(),
                    self._coupons_of_row(index.row()), row=index.row()))
        wanted = (Qt.CursorShape.PointingHandCursor if over
                  else Qt.CursorShape.ArrowCursor)
        if viewport.cursor().shape() != wanted:
            viewport.setCursor(wanted)
        # The discs light up from the cursor position, which Qt does not repaint
        # for on its own: hover is a per-cell flag and both buttons share a cell.
        # Repainting the cell under the cursor is enough while there is one; the
        # whole viewport only when the cursor has just left the last button.
        if cell is not None:
            viewport.update(cell)
        elif self._qc_hovering:
            viewport.update()
        self._qc_hovering = over

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802 (Qt naming)
        """Re-share the spare width on resize, and drop the hover button when
        the cursor leaves the table."""
        if self.table is not None and obj is self.table.viewport():
            if event.type() == QEvent.Type.Resize and self.table.columnCount():
                self.table.resize_columns_to_contents_constrained()
            elif event.type() == QEvent.Type.MouseMove:
                self._point_at_qc_buttons(event)
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

        # The status as stored, not its family: a tested electrode gets its own
        # entry. Merged into the neighbours it sits between, a label like
        # "B14267 - B14292: Released" silently spanned the three electrodes in
        # it that were destroyed on the rig, and only the count gave it away.
        # Whether they appear at all is the filter's business, not this one's.
        families = visible[STATUS_COLUMN].astype(str).map(status_label)
        markings = visible[str(ElectrodeColumns.MARKING)].astype(str)
        # To the day, not the minute: one "mark all as done" stamps its whole
        # batch with one time, but a hand edit carries its own, and grouping on
        # those split a stretch at every corrected electrode.
        if FINALISED_COLUMN in visible.columns:
            settled = visible[FINALISED_COLUMN].map(format_utc_date_for_display)
        else:
            settled = pd.Series("", index=visible.index)

        # Oldest first, which is both how a range has to be built (it needs
        # ascending numbers) and how the report reads.
        ranges = collapse_to_ranges(
            list(markings), list(families), list(settled))

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
        needs_testing = count(ElectrodeStatus.NEEDS_TESTING)
        test_ongoing = count(ElectrodeStatus.TEST_ONGOING)
        not_tested = count(ElectrodeStatus.NOT_TESTED)
        consumed = tested_passed + tested_failed

        # ``(label, count, depth)``. Depth 0 is the line the rest are measured
        # against, 1 a row of the block, 2 a breakdown of the row above it.
        # A tested electrode is scrapped whatever the verdict: testing destroys
        # it, so it can never be sold.
        # A row belongs in the report when at least one status feeding it is in
        # scope. The composite rows name every status they add up, so filtering
        # to Released alone leaves a block that still balances.
        scope = set(self.statuses_in_scope())
        scrap_statuses = {str(ElectrodeStatus.SCRAPPED),
                          str(ElectrodeStatus.TESTED_AND_RELEASED),
                          str(ElectrodeStatus.TESTED_AND_SCRAPPED)}
        tested_statuses = {str(ElectrodeStatus.TESTED_AND_RELEASED),
                           str(ElectrodeStatus.TESTED_AND_SCRAPPED)}

        def wanted(rows: list) -> list:
            return [(label, number, depth) for label, number, depth, feeds in rows
                    if not feeds or (feeds & scope)]

        production = wanted([
            (ALL_PRODUCED, len(visible), 0, set()),
            (status_label(ElectrodeStatus.RELEASED),
             count(ElectrodeStatus.RELEASED), 1, {str(ElectrodeStatus.RELEASED)}),
            (SCRAPPED_TOTAL, scrapped_untested + consumed, 1, scrap_statuses),
            (SCRAP_FAULTS, scrapped_untested + tested_failed, 2,
             {str(ElectrodeStatus.SCRAPPED), str(ElectrodeStatus.TESTED_AND_SCRAPPED)}),
            (SCRAP_TESTING, tested_passed, 2,
             {str(ElectrodeStatus.TESTED_AND_RELEASED)}),
            (status_label(ElectrodeStatus.TEST_ONGOING), test_ongoing, 1,
             {str(ElectrodeStatus.TEST_ONGOING)}),
            (status_label(ElectrodeStatus.NEEDS_TESTING), needs_testing, 1,
             {str(ElectrodeStatus.NEEDS_TESTING)}),
            (status_label(ElectrodeStatus.NOT_TESTED), not_tested, 1,
             {str(ElectrodeStatus.NOT_TESTED)}),
        ])
        # What QC itself did. Needs testing and Not Tested are deliberately not
        # repeated here: they are in the production block, where they are what
        # makes it add up, and the same two numbers twice on one page read as a
        # mistake rather than as two views of the same thing.
        qc = wanted([
            (status_label(ElectrodeStatus.TESTED_AND_RELEASED), tested_passed, 1,
             {str(ElectrodeStatus.TESTED_AND_RELEASED)}),
            (status_label(ElectrodeStatus.TESTED_AND_SCRAPPED), tested_failed, 1,
             {str(ElectrodeStatus.TESTED_AND_SCRAPPED)}),
            (ALL_TESTED, consumed, 0, tested_statuses),
        ])

        # The same split the donut on screen is showing, so the two agree, and
        # it already honours the Status filter.
        chart_counts, chart_colors = self._status_split()

        # Rendered once: the builder needs it to work out how far down the
        # page the glossary goes, and the dialog needs it to draw.
        chart_png = render_donut_png(chart_counts, chart_colors,
                                     unit_label="electrodes")

        date_from, date_to = self._visible_date_span()

        def render(title: str = "") -> str:
            """The whole report under one title, so the dialog can retitle it."""
            return build_report_html(
                ranges=ranges, production=production, qc=qc, qc_total=consumed,
                tints={**{status_label(s): c for s, c in ROW_TINTS.items()},
                       ALL_PRODUCED: "#E9ECEF",
                       SCRAPPED_TOTAL: ROW_TINTS[str(ElectrodeStatus.SCRAPPED)],
                       SCRAP_FAULTS: ROW_TINTS[str(ElectrodeStatus.SCRAPPED)],
                       SCRAP_TESTING: ROW_TINTS[str(ElectrodeStatus.TESTED_AND_RELEASED)],
                       ALL_TESTED: "#E9ECEF"},
                filters=self.filter_bar.active(),
                date_from=date_from, date_to=date_to,
                total_shown=len(visible),
                total_all=0 if self.df_source is None else len(self.df_source),
                chart_counts=chart_counts, chart_colors=chart_colors,
                definitions=self._report_definitions(), chart_png=chart_png,
                title=title)

        ReportDialog(self, html=render(), chart_png=chart_png, rebuild=render,
                     suggested_name=f"qc_report_{date_from}_{date_to}.pdf"
                     .replace(".", "-").replace("-pdf", ".pdf")).exec()

    @staticmethod
    def _status_definitions() -> list[tuple[str, str]]:
        """What each status means, in the order the chart lists them.

        Built from the same labels everything else uses, so renaming a status
        renames it here too. Shared by the chart's info icon and the report.
        """
        return [
            (status_label(ElectrodeStatus.RELEASED),
             "Presumably good electrode, can be sold."),
            (status_label(ElectrodeStatus.TESTED_AND_RELEASED),
             "Electrode that was tested and passed the tests."),
            (status_label(ElectrodeStatus.TESTED_AND_SCRAPPED),
             "Electrode that was tested and failed the tests."),
            (status_label(ElectrodeStatus.SCRAPPED),
             "Presumably bad electrode, should not be sold."),
            (status_label(ElectrodeStatus.NEEDS_TESTING),
             "The electrode that should be tested next."),
            (status_label(ElectrodeStatus.WAITING_FOR_SEM),
             "In doubt because a nearby electrode failed. No SEM result that "
             "could settle it has come back yet."),
            (status_label(ElectrodeStatus.WAITING_FOR_IC),
             "In doubt because a nearby electrode failed. The electrode being "
             "tested for it has passed SEM, so it is now waiting on the IC "
             "result."),
            (status_label(ElectrodeStatus.NOT_TESTED),
             "A new electrode that has not been tested yet."),
            (status_label(ElectrodeStatus.TEST_ONGOING),
             "An electrode on the QC board now, waiting for its result."),
        ]

    def _report_definitions(self) -> list[tuple[str, str]]:
        """Every term the report prints: the statuses, plus its summary lines.

        Scrapped and Rejected are the pair worth reading together: one is a
        single electrode nobody tested, the other is the whole of what cannot
        be sold.
        """
        return self._status_definitions() + [
            (ALL_PRODUCED, "Number of electrodes matching the filters."),
            (SCRAPPED_TOTAL, "Everything that cannot be sold."),
            (SCRAP_FAULTS, "Rejected + Tested, failed."),
            (SCRAP_TESTING,
             "An electrode that passed the test but therefore cannot be sold."),
            (ALL_TESTED, "Number of electrodes that have been tested."),
        ]

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
        """Act on a QC request button, or pick the row.

        A click on one of the buttons is not also a row selection: the
        operator pressed a button, and having the row jump underneath the
        confirmation box reads as two things happening.
        """
        if self._qc_action_clicked(row, _column):
            return
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

    def _worth_asking_for(self, row_index: int) -> bool:
        """Whether production could still be asked for the electrode on this row.

        Two reasons there is nothing to ask. The electrode is finished, or
        it is on a rig already, in which case the question has no answer
        anybody is waiting for. Or production cannot be shown it: they list
        the last 30 days of finished electrodes, so an older one has no row on
        their screen to answer on and the database expires the request within
        the minute. Offering the button and then expiring what it raised is
        worse than not offering it.
        """
        if self._status_of_row(row_index) in _NOTHING_LEFT:
            return False
        days = (self._get_request_window_days() if self._get_request_window_days
                else 0)
        if not days or days <= 0:
            return True
        made = self._date_of_row(row_index)
        if made is None:
            # No date to judge by, so no reason to take the button away.
            return True
        return (dt.datetime.now(dt.timezone.utc) - made).days < days

    def _status_of_row(self, row_index: int) -> str:
        """The electrode status on this visible row, as the frame holds it."""
        frame = self.df_visible
        if frame is None or STATUS_COLUMN not in frame.columns:
            return ""
        if not 0 <= row_index < len(frame):
            return ""
        return str(frame.iloc[row_index][STATUS_COLUMN]).strip()

    def _date_of_row(self, row_index: int):
        """When the electrode on this visible row was made, UTC, or None."""
        frame = self.df_visible
        column = str(ElectrodeColumns.DATE)
        if frame is None or column not in frame.columns:
            return None
        if not 0 <= row_index < len(frame):
            return None
        made = pd.to_datetime(frame.iloc[row_index][column], errors="coerce",
                              utc=True)
        return None if pd.isna(made) else made.to_pydatetime()

    def _coupons_of_row(self, row_index: int) -> str:
        """What the request on this row asks be cut, read off the frame.

        By row, the way the lock and the failure badges are: the delegate is
        handed a row number and the frame is what that row was built from.
        """
        frame = self.df_visible
        if frame is None or QC_COUPONS_COLUMN not in frame.columns:
            return ""
        if not 0 <= row_index < len(frame):
            return ""
        return str(frame.iloc[row_index][QC_COUPONS_COLUMN]).strip()

    def _failure_badges(self, row_index: int) -> tuple:
        """Why the electrode on this row failed, for the badges beside its pill.

        Read off the rendered frame by row, the way the lock is: the delegate
        is handed a row number and the frame is what the row was built from.
        """
        if (self.df_visible is None or row_index < 0
                or row_index >= len(self.df_visible)
                or FAILURE_COLUMN not in self.df_visible.columns):
            return ()
        codes = str(self.df_visible.iloc[row_index][FAILURE_COLUMN]).strip()
        return tuple(code for code in codes.split(",") if code)

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

        # The derived ones are left out: they are worked out on every
        # render, so a hand set one would be overwritten the moment a card
        # moved, or linger over an electrode nothing is testing.
        dialog = StatusEditDialog(
            self, marking=marking, current=current,
            statuses=[status_label(s) for s in ElectrodeStatus
                      if s not in NEVER_STORED])
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
        self.patch_statuses(result["data"], flash=True)
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
