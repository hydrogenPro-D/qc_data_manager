"""The QC Sampling tab: pick a range of electrodes, get the run lengths back.

A thin tab like the others. It never touches the backend: the electrode frame
arrives through ``load_data``, the same processed frame the Electrodes tab and
the QC board are given, so a decision on the board shows up in these numbers
without a second read. Every analysis after that runs against the frame already
in memory, so changing the range is instant.

Which database that frame came from is therefore the app's business, not this
tab's. That is the point of wiring it this way: the standalone
``qc_sampling_script`` forces production, and this one reads whatever the app is
connected to, so the test database stays the test database.

Across the top is the per vat measurement, and under it a tab per vat holding
what it implies for the testing rate. That last part is the point: the rest is
the evidence for it.
"""

from __future__ import annotations

import pandas as pd
from PyQt6.QtCore import QEvent, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QFontMetricsF
from PyQt6.QtWidgets import (
    QAbstractSpinBox, QApplication, QFrame, QGridLayout, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea,
    QSlider, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from src.data_access import app_config
from src.data_access.electrodes.electrode_columns import ElectrodeColumns
from src.data_access.sampling import current_intervals
from src.data_access.sampling.run_lengths import (
    ACCEPTABLE_GAIN, Analysis, KEPT_VATS, analyse, best_option, interval_curve,
    parse_marking, sampling_table,
)
from .electrodes_gui import SCRAP_TESTING
from .sampling_charts import BAD, GOOD, INK, MUTED, NetSavedCanvas
from .styles.button_styles import (
    PRIMARY_BUTTON_STYLE, SECONDARY_BUTTON_STYLE, apply_modern_shadow,
)
from .styles.input_styles import INPUT_FIELD_STYLE
from .widgets.flow_layout import DIVIDER, flow_host
from .widgets.no_wheel_tabs import NoWheelTabWidget
from .widgets.sampling_range_dialog import SamplingRangeDialog
from .widgets.skeleton_overlay import SkeletonOverlay

TITLE = "QC Sampling"
BACKDROP = "#EEF4F5"
CARD = "#FFFFFF"
EDGE = "#DCE8EA"
# Named rather than left to Qt's default. Every other style in this app names
# its family, and a widget that does not inherits whatever the platform picks,
# which is not always a font with the glyphs to draw a number in.
FONT = "'Segoe UI', 'Inter', Arial, sans-serif"
# Header plus this many rows before the table starts scrolling. Sized to its
# contents below that, so a plant with five vats does not leave a hand's width
# of white between the table and the chart under it.
MAX_VISIBLE_ROWS = 20
# The recommendation column carries four tables of nine rows each, so it is set
# tighter than the per vat table on the left: smaller type, barely any cell
# padding, and shorter rows. Nothing in it needs to be read across a room.
COMPACT_PT = 10
COMPACT_ROW = 21
WIDE_ROW = 25
COMPACT_PAD = 12
# The plot never shrinks below this, however little room is left. Low, because
# once the panel beside it was made to scroll this became the one thing holding
# the window open: at 260 it was worth 110px of height that could not be
# dragged away. Small enough to squash to, large enough that the curve is still
# a curve.
MIN_PLOT_HEIGHT = 150
# No floor on its WIDTH, deliberately. One wide enough to keep the plot worth
# looking at, 280px, put the window's own minimum back up to 900 and undid the
# squashing this was all for. The table gives way first because it can scroll
# and still be read a column at a time; past that the plot narrows, and a
# window dragged that small is a window nobody is reading the plot in.

# Two blocks of the same five figures, split by a blank column so the eye can
# tell which half it is reading without going back up to the header. The
# headers of each block are tinted to match.
VAT_HEADERS = ("Vat", "Electrodes",
               "Bad", "Runs", "Avg", "Median", "Std dev", "Bad %",
               "",
               "Good", "Runs", "Avg", "Median", "Std dev", "Good %")
# Electrodes = Bad + Good, and the two shares add up to 100. Only the four
# verdicts are counted, so there is nothing else in the row to reconcile.
TOTAL_COLUMN = 1
BAD_COLUMNS = range(2, 8)
SPACER_COLUMN = 8
GOOD_COLUMNS = range(9, 15)
SPACER_WIDTH = 26
# Barely rounded. These panels are dense tables of numbers, and a large radius
# on a card that is mostly grid reads as a bubble with a spreadsheet in it.
CARD_RADIUS = 6
# Breathing room each side of a cell's text, on top of what Qt measures.
CELL_PAD = 12
# A column may be squeezed to this, and no further. Past it the figures stop
# being readable and a scrollbar is the lesser evil.
MIN_COLUMN = 46
# How far the recommendation table may be squeezed before it starts to scroll
# instead. Sized at its own contents it pinned the window to about 925px wide
# and 825 tall, and no amount of dragging would get past that.
MIN_ADVICE_WIDTH = 250
MIN_ADVICE_ROWS = 3
# The widest gap the slider offers. Past a handful the merge stops being a
# tolerance for one odd reading and starts inventing a single run out of a
# whole marginal period.
MAX_GAP = 5
# A single good electrode between two bad runs is bridged by default. One
# passing measurement inside a bad stretch is more often a marginal electrode
# than a genuine recovery, and treating it as a boundary splits a real run in
# two. Drag to 0 for no merging at all.
DEFAULT_GAP = 1
# A slim scrollbar for the recommendation column only. The app's shared one is
# deliberately 35px for tablet use, which beside a narrow table of figures is
# wider than some of the columns.
SLIM_SCROLLBAR = """
QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #C2D3D6; border-radius: 5px;
                              min-height: 40px; }
QScrollBar::handle:vertical:hover { background: #9BB4B9; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical
                             { background: transparent; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 0; }
QScrollBar::handle:horizontal { background: #C2D3D6; border-radius: 5px;
                                min-width: 40px; }
QScrollBar::handle:horizontal:hover { background: #9BB4B9; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal
                               { background: transparent; }
"""
# The catch rates the recommendation is quoted at, as a share of the bad
# ELECTRODES rather than of the runs.
ADVICE_TARGETS = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.75, 0.8, 0.85,
                  0.9, 0.95, 0.99)
# ``Gain`` is ``Bad stopped`` minus the electrodes consumed by testing, both as
# percentages of production. Bad stopped is hidden, so the subtraction no
# longer shows its working; the figures behind it are unchanged.
#
# "Production destroyed" is deliberately NOT the cost in that sum. It counts
# every test, including the ones that landed on an electrode that was bad
# anyway, so it is larger than what testing actually costs in sellable stock.
#
# The cost column takes its name straight from the app's printed report, so the
# two cannot drift apart.
ADVICE_HEADERS = ("% of bad electrodes caught", "Test every",
                  "Production destroyed", "Payback",
                  "Bad stopped", SCRAP_TESTING, "Gain")
# The best row in each table gets a wash behind it. Net saved is the one figure
# here with a peak, and the peak is the recommendation.
PEAK_TINT = "#E6F5F2"
# The panel beside each plot. Fixed, so the plot beside it does not resize as
# the figures in it change, and wide enough for its longest sentence on one
# line.
PANEL_WIDTH = 250
# The panel scrolls below this rather than propping the window open. It was the
# single biggest thing stopping the tab being made shorter, 283px of the 736
# the window would not go under.
PANEL_MIN_HEIGHT = 96
PANEL_INNER = "panelInner"
PANEL_HEADERS = ("Vat", "Interval")
_LEFT = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
# Every stylesheet in here is scoped to one object name. A bare "QWidget" or
# "QFrame" selector matches a QToolTip as well, because a tooltip is a label in
# a window of its own and Qt resolves its style from the ancestors of the widget
# it belongs to. "background: transparent" then reached the tip window, which
# paints nothing, and on Windows nothing comes out black: every tooltip in the
# control bar was an unreadable slab.
TAB_NAME = "samplingTab"
GROUP_NAME = "barGroup"
HOST_NAME = "barHost"
SEPARATOR_NAME = "barSeparator"
# Payback is still computed, still filled in, and still on the SamplingOption.
# It is only hidden, because it climbs for ever as the interval widens and so
# always points at the loosest row. Set this to True to bring the column back.
SHOW_PAYBACK = False
PAYBACK_COLUMN = 3
# Same treatment for the catch rate. It is a RELATIVE figure, so a vat that is
# barely producing anything bad shows a low one at its own optimum and reads
# as a failure when it is the better vat. Still computed, still filled in.
SHOW_CATCH_RATE = False
CATCH_COLUMN = 0
SHOW_BAD_STOPPED = False
BAD_STOPPED_COLUMN = 4
# Gain is the one column that can be a loss, so it is the one worth colouring:
# green where testing at that rate leaves you ahead, red where it costs more
# than it saves. The same two colours the per vat table uses for its good and
# bad halves, so a colour means the same thing on both tables.
GAIN_COLUMN = 6
# Which curves are drawn before anybody touches a checkbox. The interval is
# left off: it is the reciprocal of the cost, so showing both at once says the
# same thing twice on two axes.
DEFAULT_METRICS = ("net", "destroyed")
# The axis names alone do not say what the numbers mean, and Payback in
# particular is a word this tool made up.
METRIC_TOOLTIPS = {
    "net": "Electrodes ahead per 1000 produced: what testing stops, less "
           "the good ones it destroys. Its PEAK is the recommendation.",
    "interval": "Test one electrode in every this many.",
    "destroyed": "Share of everything produced that testing destroys.",
    "payback": "Bad electrodes stopped for each GOOD electrode testing "
               "costs you.\n\nAbove 1.0 you stop more than you "
               "destroy. Where the line really sits depends on what an "
               "escape costs beyond its lost efficiency, which is a "
               "business judgement and is not in this tool.",
}
SLIDER_STYLE = f"""
QSlider::groove:horizontal {{ height: 4px; background: {EDGE};
                              border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {GOOD}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {CARD}; border: 2px solid {GOOD};
                              width: 13px; height: 13px; margin: -6px 0;
                              border-radius: 8px; }}
"""
SPACER_TINT = "#F3F7F8"
# The totals row, set apart by a wash rather than by a repeated header.
TOTALS_TINT = "#EDF3F4"


def _card(title: str, padding: int = 0) -> tuple[QFrame, QVBoxLayout]:
    """A white panel with a heading, and the layout to fill it."""
    frame = QFrame()
    # Scoped to this frame by name. A bare "QFrame" selector also matches every
    # QLabel inside it, because QLabel is a QFrame, so the border and the 14px
    # radius were being drawn around each individual number as well.
    frame.setObjectName("panel")
    frame.setStyleSheet(
        f"QFrame#panel {{ background: {CARD}; border: 1px solid {EDGE};"
        f"border-radius: {CARD_RADIUS}px; }}")
    apply_modern_shadow(frame)
    box = QVBoxLayout(frame)
    inset = padding or 16
    box.setContentsMargins(inset, inset - 2, inset, inset - 2)
    box.setSpacing(10 if not padding else 6)
    if title:
        heading = QLabel(title.upper())
        heading.setStyleSheet(f"color: {MUTED}; font-family: {FONT};"
                              f"font-size: {COMPACT_PT}pt; font-weight: 700;"
                              f"letter-spacing: 1.2px;"
                              f"background: transparent; border: none;")
        box.addWidget(heading, 0)
    return frame, box


def _fit(table: QTableWidget, rows: int, scrolls: bool = False,
         squashable: bool = False, least: int = MIN_ADVICE_ROWS) -> None:
    """Height the table to its contents, up to a cap, then let it scroll.

    ``scrolls`` reserves room for the horizontal scrollbar. The app's are
    deliberately thick, 35px, so a table that grew one without being told about
    it lost a whole row behind it.

    ``squashable`` asks for a ceiling rather than a fixed height: the table
    still opens at its content height, but the window can be dragged smaller
    and the table gives way and scrolls. A fixed height is a floor as well as a
    ceiling, and a tall one stops the window shrinking at all.
    """
    shown = min(max(rows, 1), MAX_VISIBLE_ROWS)
    body = (sum(table.rowHeight(row) or 30 for row in range(shown))
            if rows else 30)
    header = table.horizontalHeader()
    head = 0 if header.isHidden() else header.height()
    bar = table.horizontalScrollBar().sizeHint().height() if scrolls else 0
    height = head + body + bar + 6
    if not squashable:
        table.setFixedHeight(height)
        return
    row_height = (body / shown) if shown else 30
    table.setMinimumHeight(int(head + bar + row_height * min(least, shown) + 6))
    table.setMaximumHeight(height)


def _fill_row(table: QTableWidget, index: int, row, bold_all: bool = False,
              tint: str = "") -> None:
    """Write one ``VatStats`` across a table row.

    Shared by the per vat table and the totals row beneath it, so the two
    cannot drift into showing the same figure in different columns.
    """
    cells = (row.vat, f"{row.electrodes}",
             f"{row.bad.total}", f"{row.bad.count}", f"{row.bad.mean:.1f}",
             f"{row.bad.median:.0f}", f"{row.bad.stdev:.1f}",
             f"{row.bad_share:.1f} %",
             "",
             f"{row.good.total}", f"{row.good.count}", f"{row.good.mean:.1f}",
             f"{row.good.median:.0f}", f"{row.good.stdev:.1f}",
             f"{row.good_share:.1f} %")
    for column, text in enumerate(cells):
        item = QTableWidgetItem(text)
        if column:
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                  | Qt.AlignmentFlag.AlignVCenter)
        # Tinted per block, so the two halves of a row are told apart without
        # going back up to the header every time.
        if column in BAD_COLUMNS:
            item.setForeground(QColor(BAD))
        elif column in GOOD_COLUMNS:
            item.setForeground(QColor(GOOD))
        elif column == SPACER_COLUMN:
            item.setBackground(QColor(SPACER_TINT))
        # The electrode count is the line the rest of the row is measured
        # against, so it carries the weight; on the totals row everything does.
        if bold_all or column == TOTAL_COLUMN:
            item.setFont(_bold(item.font()))
        if tint and column != SPACER_COLUMN:
            item.setBackground(QColor(tint))
        table.setItem(index, column, item)


def _wide(headers, parent=None) -> QTableWidget:
    """One of the two tables that share a column layout.

    Widths are set by ``_align`` rather than by the header, so this only has to
    stop Qt resizing them back.
    """
    table = _table(headers, parent=parent)
    table.horizontalHeader().setSectionResizeMode(
        QHeaderView.ResizeMode.Interactive)
    # Never scrolls vertically: there is one row per vat and _fit heights the
    # table to all of them. Left on, a bar that appeared for a pixel of
    # rounding took width off the viewport, which then could not hold the
    # columns, which raised a horizontal bar over the last row.
    table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    # The blank column between the two halves is narrower than a section is
    # allowed to be by default: 32px on Windows, which silently widened it and
    # pushed the table six pixels past the width it had been given.
    table.horizontalHeader().setMinimumSectionSize(SPACER_WIDTH)
    return table


def _wanted(tables, column: int) -> int:
    """How wide one column needs to be across every table, cells and header.

    From the items' own size hints rather than from the current column widths.
    Reading the widths back meant each pass measured what the previous pass had
    already squeezed, so the columns crept narrower every time the window was
    resized until the table was two thirds the width of its card.
    """
    want = max(table.sizeHintForColumn(column) for table in tables)
    for table in tables:
        header = table.horizontalHeader()
        item = table.horizontalHeaderItem(column)
        if header.isHidden() or item is None or not item.text():
            continue
        metrics = QFontMetricsF(header.font())
        want = max(want, int(metrics.horizontalAdvance(item.text())) + CELL_PAD)
    return want


def _align(*tables: QTableWidget) -> int:
    """Give the tables one shared set of column widths. Returns their total.

    Measured across BOTH tables and the widest wins, which is what stops the
    totals row being elided: its figures are bold and therefore wider than the
    per vat ones above them, and a column sized for only one of the two cut the
    other short. Stretching instead gave every column an identical width, which
    starved "Electrodes" and "All vats" to feed columns holding a single digit.

    Every column is EXACTLY as wide as its content, and the table stops there.
    Spare width in the card used to be shared out among the columns so the
    table filled it, which made a column holding "28" as wide as one holding a
    date, and, worse, overflowed: the leftover was measured against a viewport
    width that had not settled, so the last column ended up clipped behind a
    scrollbar that should never have appeared.
    """
    columns = tables[0].columnCount()
    widths = [SPACER_WIDTH if column == SPACER_COLUMN
              else _wanted(tables, column) + CELL_PAD
              for column in range(columns)]

    # No squeezing either. Every column is at least as wide as what is in it,
    # and a window too narrow for that scrolls sideways instead of eliding
    # figures. _fit is told, so the scrollbar gets its own room rather than
    # eating a row.
    for table in tables:
        for column, width in enumerate(widths):
            table.setColumnWidth(column, width)
    # Read back, not summed from what was asked for. Qt clamps a section to the
    # header's minimum, so a column can end up wider than it was set to, and a
    # container sized from the intention is then too small for the fact. This
    # is safe where _wanted's own read-back was not: _wanted measures the items
    # and would feed its own squeezing back into the next pass, this only
    # measures what the columns already are.
    total = sum(tables[0].columnWidth(column) for column in range(columns))

    for table in tables:
        # A maximum rather than a fixed width: it stops the table stretching
        # past its content on a wide screen, and still lets it be squeezed and
        # scrolled on a narrow one.
        #
        # The columns live in the VIEWPORT, which is narrower than the widget
        # by its frame. Setting the widget to the column total left the
        # viewport a few pixels short of holding them, which is a horizontal
        # scrollbar over the bottom row and a clipped last column.
        table.setMaximumWidth(total + table.frameWidth() * 2 + 2)
        # Or the parent layout keeps the geometry it worked out from the
        # PREVIOUS maximum, and the widget stays a pass behind its own
        # columns, which is a horizontal scrollbar over the bottom row.
        table.updateGeometry()
    return total


def _squash_width(table: QTableWidget) -> None:
    """Open at the width of the columns, but give way when the window shrinks.

    A maximum and a minimum rather than a fixed width. Fixed, the table was a
    floor under the whole window: it could not be made narrower than the sum of
    its own columns however small the screen was.
    """
    wanted = sum(table.columnWidth(column)
                 for column in range(table.columnCount())) + 2
    table.setMinimumWidth(min(MIN_ADVICE_WIDTH, wanted))
    table.setMaximumWidth(wanted)


def _bold(font: QFont) -> QFont:
    """The same font, heavier. Qt hands back a copy, so this cannot leak."""
    heavy = QFont(font)
    heavy.setBold(True)
    return heavy


def _value_text(value: float) -> str:
    """The worth of one row, or a mark when there is nothing to divide."""
    if value == float("inf"):
        return "no good ones lost"
    return f"{value:.2f}"


# The control bar's own metrics. Everything in it lines up on one height so
# the inputs, the buttons and the sliders share a baseline.
BAR_HEIGHT = 32
BAR_GAP = 6          # between things that belong together
BAR_GROUP_GAP = 10   # either side of a separator
# The value labels are fixed width so the sliders do not shuffle sideways as
# the text under them changes. Sized to their longest reading and no more.
GAP_VALUE_WIDTH = 78
WORTH_VALUE_WIDTH = 40
# A marking is six or seven characters. The fields were nearly three times
# this, which is most of why the bar did not fit on a laptop.
FIELD_WIDTH = 96
SLIDER_WIDTH = 92
# Appended to the app's own button styles, for this window only. Those two are
# written for different places and disagree on padding, font size and corner
# radius, and the primary carries a min-height that beats setFixedHeight. Side
# by side in one bar that read as two buttons from two different programs.
# The height goes in the STYLESHEET, not through setFixedHeight. A stylesheet
# min-height overrides the widget's own minimum, so setting it here and the
# height there left the buttons free to shrink to their text.
# Padding rather than a fixed width, so each button is as wide as its own
# label and no wider. The height has to be in the stylesheet: a min-height
# there overrides setFixedHeight, which is how the buttons ended up 15px tall.
BUTTON_OVERRIDE = (f"QPushButton {{ padding: 0px 11px;"
                   f" min-height: {BAR_HEIGHT - 2}px;"
                   f" font-size: 12px; font-weight: 600; border-radius: 8px;"
                   f" letter-spacing: 0.2px; }}")


def _bar_group(widgets, parent) -> QWidget:
    """One group of controls, boxed so a wrapping row cannot split it."""
    box = QWidget(parent)
    # Explicitly transparent, and scoped: the backdrop sets a background on
    # itself which cascades to every plain QWidget under it, so a bare
    # container shows as a grey slab on the white card. See TAB_NAME.
    box.setObjectName(GROUP_NAME)
    box.setStyleSheet(f"QWidget#{GROUP_NAME} {{ background: transparent; }}")
    line = QHBoxLayout(box)
    line.setContentsMargins(0, 0, 0, 0)
    line.setSpacing(BAR_GAP)
    for widget in widgets:
        line.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
    box.setFixedHeight(BAR_HEIGHT)
    return box


def _bar_label(text: str, strong: bool = False) -> QLabel:
    """A caption in the control bar.

    ``background: transparent`` is the point of this helper. Without it a label
    paints the window's backdrop colour, which on the white control card showed
    as a grey block behind every word.
    """
    label = QLabel(text)
    label.setFixedHeight(BAR_HEIGHT)
    label.setStyleSheet(
        f"color: {INK if strong else MUTED}; font-family: {FONT};"
        f"font-size: {COMPACT_PT}pt; font-weight: {700 if strong else 600};"
        f"background: transparent; border: none;")
    return label


def _bar_value(width: int) -> QLabel:
    """A slider's current value, at a fixed width.

    Fixed, because the text changes as the slider moves and a label that
    resizes drags everything to its right along with it.
    """
    label = QLabel()
    # Fixed both ways. In a wrapping layout every item is placed at its own
    # size hint, so a short label would sit at a different height from the
    # field beside it.
    label.setFixedSize(width, BAR_HEIGHT)
    label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    label.setStyleSheet(
        f"color: {INK}; font-family: {FONT}; font-size: {COMPACT_PT}pt;"
        f"font-weight: 700; background: transparent; border: none;")
    return label


class _Separator(QFrame):
    """A hairline between two groups of controls.

    It carries its own breathing room as transparent padding, because a flow
    layout has no addSpacing to put a gap either side of it. The width is a
    size HINT rather than a fixed size, so the layout can squeeze it to
    nothing when it lands at the edge of a wrapped row and separates nothing.
    """

    def __init__(self, margin: int) -> None:
        super().__init__()
        self._hint = QSize(1 + margin * 2, BAR_HEIGHT)
        self.setProperty(DIVIDER, True)
        self.setFixedHeight(BAR_HEIGHT)
        self.setObjectName(SEPARATOR_NAME)
        self.setStyleSheet(f"QFrame#{SEPARATOR_NAME} {{ background: transparent;"
                           f" border: none; }}")
        line = QFrame(self)
        line.setObjectName(SEPARATOR_NAME + "Line")
        line.setGeometry(margin, 4, 1, BAR_HEIGHT - 8)
        line.setStyleSheet(f"QFrame#{SEPARATOR_NAME}Line {{ background: {EDGE};"
                           f" border: none; }}")

    def sizeHint(self) -> QSize:
        return self._hint

    def minimumSizeHint(self) -> QSize:
        return QSize(0, self._hint.height())


def _panel_caption(text: str, strong: bool = False) -> QLabel:
    """A heading or a vat name in the sampling interval panel."""
    label = QLabel(text)
    # The same ink the tables print in. Grey headings beside black figures read
    # as a different, lesser kind of information, and they are not.
    label.setStyleSheet(
        f"color: {INK}; font-family: {FONT}; font-size: {COMPACT_PT}pt;"
        f"font-weight: {700 if strong else 600};"
        f"background: transparent; border: none;")
    return label


def _panel_note() -> QLabel:
    """The line under one vat's interval, saying how far off the optimum it is."""
    label = QLabel()
    label.setWordWrap(True)
    label.setStyleSheet(
        f"color: {INK}; font-family: {FONT}; font-size: {COMPACT_PT}pt;"
        f"background: transparent; border: none; margin-bottom: 8px;")
    return label


def _optimum_note(current: int, best: int | None,
                  measured: bool = True) -> str:
    """How far this vat's interval is from the best one, and which way.

    In electrodes and in words rather than as a signed number, because a longer
    interval means LESS testing and a bare plus sign reads as more.
    """
    if not measured:
        # Nothing. This range has no runs for the vat, so there is no honest
        # thing to say about its interval, and saying that in words on a row
        # that already shows no figures is noise.
        return ""
    if best is None:
        return "No interval here pays for itself."
    step = best - current
    if not step:
        # Nothing. The box says 12 and the table says 12, and a line spelling
        # that out is a line to read on every other vat as well.
        return ""
    unit = "electrode" if abs(step) == 1 else "electrodes"
    return (f"Optimal interval is {abs(step)} {unit} "
            f"{'less' if step > 0 else 'more'} frequent.")


# Qt's own limit for "no maximum", so a hint can tell whether one was set.
_NO_MAX = 16777215


class _SquashableTable(QTableWidget):
    """Asks for the width and height of its contents, and gives way below it.

    QTableWidget's sizeHint is a flat 256x192 with nothing to do with what is
    in it. While the table was pinned with setFixedWidth and setFixedHeight
    that never showed, because a fixed size overrides the hint; the moment
    those became a maximum and a minimum, so the window could be made smaller,
    the layout started handing this table the 256x192 stub instead.

    So the maximum, which _squash_width and _fit set to the real contents, is
    also what it asks for.
    """

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        width = self.maximumWidth() if self.maximumWidth() < _NO_MAX else hint.width()
        height = (self.maximumHeight() if self.maximumHeight() < _NO_MAX
                  else hint.height())
        return QSize(width, height)


def _table(headers, compact: bool = False, parent=None,
           squashable: bool = False) -> QTableWidget:
    """A read-only table that does not react to the mouse at all.

    Nothing here is clickable, so a row that lights up on hover or stays
    highlighted after a click is telling the reader something untrue. Selection
    is off at the widget level and the hover rule is overridden as well, since
    the style would otherwise still paint it.
    """
    # Parented at construction. A widget with no parent is a top-level
    # WINDOW in Qt, and on Windows one built and reparented in the same breath
    # can flash on screen as a real window before it is adopted.
    builder = _SquashableTable if squashable else QTableWidget
    table = builder(0, len(headers), parent)
    table.setHorizontalHeaderLabels(list(headers))
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
    table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    # Sixteen columns spread evenly are sixteen columns too narrow to read, so
    # the wide table sizes each one to what is actually in it instead.
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    table.horizontalHeader().setHighlightSections(False)
    # One size for every table in the window. The per vat table used to be a
    # point larger, which bought nothing and cost a row of height each time.
    size = COMPACT_PT
    table.setStyleSheet(
        f"QTableWidget {{ background: {CARD}; border: none;"
        f"font-family: {FONT}; font-size: {size}pt; gridline-color: {EDGE}; }}"
        f"QTableWidget::item {{ padding: {'0px 11px' if compact else '3px 6px'}; }}"
        f"QTableWidget::item:hover, QTableWidget::item:selected {{"
        f"background: transparent; }}"
        f"QHeaderView::section {{ background: #F3F7F8; color: {MUTED};"
        f"border: none; font-weight: 700;"
        f"padding: {'3px 5px' if compact else '7px'}; }}" + SLIM_SCROLLBAR)
    # Rows sized here rather than left to the font, which leaves a couple of
    # pixels of slack each and nine of those add up. The wide tables keep a
    # little more, since their rows carry the figures somebody reads first.
    table.verticalHeader().setDefaultSectionSize(
        COMPACT_ROW if compact else WIDE_ROW)
    return table


class QcSamplingWidget(QWidget):
    """Range in, run lengths out. A tab, with no backend of its own."""

    def __init__(self) -> None:
        super().__init__()
        self.frame: pd.DataFrame | None = None
        self._analysis = Analysis()
        # Whether the range boxes have been filled in from real data yet, so a
        # later reload does not overwrite a range somebody has typed.
        self._seeded = False

        self.setObjectName(TAB_NAME)
        self.setStyleSheet(f"QWidget#{TAB_NAME} {{ background: {BACKDROP}; }}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(14)
        layout.addWidget(self._controls(), 0)

        # Full width, both of them. The per vat table has fifteen columns and
        # wants every pixel; the tabs below hold a table and a plot side by
        # side and want the same.
        layout.addWidget(self._summary_card(), 0)
        layout.addWidget(self._vat_tabs_card(), 1)

        # Scoped to this tab. The filter is on the application, so without a
        # check that the caret is inside here it would be ending edits in the
        # Electrodes filter bar and the SEM forms too.
        application = QApplication.instance()
        if application is not None:
            application.installEventFilter(self)

    # ------------------------------------------------------------------
    # What MainWindow calls
    # ------------------------------------------------------------------
    def show_loading(self) -> None:
        """Say the first read is on its way, until ``load_data`` arrives."""
        self._skeleton.start()

    def hide_loading(self) -> None:
        """Give up on the placeholder, for a read that never answered."""
        self._skeleton.stop()

    def load_data(self, dataframe: pd.DataFrame | None) -> None:
        """Take the processed electrode frame and re-measure.

        The same frame the Electrodes tab is given, so the statuses these runs
        are built from are the ones on screen there: hand-set overrides and
        live board decisions included.
        """
        self._skeleton.stop()
        self.frame = dataframe
        if not self._seeded:
            self._seed_range()
        self.run(quiet=True)

    def is_busy(self) -> bool:
        """True while there is an unfinished edit in here.

        A reload re-measures and rebuilds every table on the tab, so it has to
        wait rather than take the ground out from under a range being typed or
        a slider mid-drag.

        An UNFINISHED edit, not merely the focus. Qt puts the caret in the first
        range box the moment the tab is shown, so treating focus as work in
        progress held every reload off for the life of the window.
        """
        if self.gap_slider.isSliderDown() or self.efficiency_slider.isSliderDown():
            return True
        focused = QApplication.focusWidget()
        if focused is None or not self.isAncestorOf(focused):
            return False
        if isinstance(focused, QAbstractSpinBox):
            focused = focused.lineEdit()
        return bool(focused is not None and isinstance(focused, QLineEdit)
                    and focused.isModified())

    def apply_external_updates(self, result) -> bool:
        """Somebody else changed the electrodes. Reload from the new frame."""
        frame = (result or {}).get("data")
        if frame is None:
            return False
        self.load_data(frame)
        return True

    # ------------------------------------------------------------------
    def _controls(self) -> QWidget:
        frame, box = _card("")
        self.first_input = QLineEdit()
        self.last_input = QLineEdit()
        for field, placeholder, tip in (
                (self.first_input, "B14000", "First electrode in the range"),
                (self.last_input, "B15000", "Last electrode in the range")):
            field.setStyleSheet(INPUT_FIELD_STYLE)
            # Short placeholders, because the field is now the width of a
            # marking. The sentence they used to carry is in the tooltip.
            field.setPlaceholderText(placeholder)
            field.setToolTip(f"{tip}, for example B14000.")
            field.setFixedWidth(FIELD_WIDTH)
            field.setFixedHeight(BAR_HEIGHT)
            field.returnPressed.connect(self.run)

        run_button = QPushButton("Analyse")
        run_button.setStyleSheet(PRIMARY_BUTTON_STYLE + BUTTON_OVERRIDE)
        run_button.clicked.connect(self.run)

        whole = QPushButton("Whole history")
        whole.setStyleSheet(SECONDARY_BUTTON_STYLE + BUTTON_OVERRIDE)
        whole.setToolTip("Reset the range to every electrode on record")
        whole.clicked.connect(self._reset_range)

        default = QPushButton("Set default range")
        default.setStyleSheet(SECONDARY_BUTTON_STYLE + BUTTON_OVERRIDE)
        default.setToolTip("Set where these boxes start each time the tab "
                           "opens. Saved in config.json.")
        default.clicked.connect(self._edit_default_range)
        # One height for the three, so the secondaries do not sit short beside
        # the primary the way two differently padded styles otherwise do.
        for button in (run_button, whole, default):
            button.setFixedHeight(BAR_HEIGHT)

        self.gap_slider = QSlider(Qt.Orientation.Horizontal)
        self.gap_slider.setRange(0, MAX_GAP)
        self.gap_slider.setValue(DEFAULT_GAP)
        self.gap_slider.setFixedWidth(SLIDER_WIDTH)
        self.gap_slider.setPageStep(1)
        self.gap_slider.setStyleSheet(SLIDER_STYLE)
        self.gap_slider.setFixedHeight(BAR_HEIGHT)
        self.gap_slider.setToolTip(
            "Two bad runs are treated as one if at most this many good\n"
            "electrodes sit between them, and those are counted as part of it.\n\n"
            "Starts at 1. At 0 nothing is merged. Raising it makes bad runs longer, which\n"
            "makes them easier to catch, which asks for LESS testing. That is\n"
            "the one direction this tool can be wrong in expensively, so it is\n"
            "left to you rather than chosen for you.")
        self.gap_label = _bar_value(GAP_VALUE_WIDTH)
        self.gap_slider.valueChanged.connect(self._on_gap)
        # Read back rather than repeated, so the label and the slider cannot
        # start out disagreeing if the default is changed.
        self._on_gap(self.gap_slider.value())

        self.efficiency_slider = QSlider(Qt.Orientation.Horizontal)
        self.efficiency_slider.setRange(0, 100)
        self.efficiency_slider.setValue(0)
        self.efficiency_slider.setFixedWidth(SLIDER_WIDTH)
        self.efficiency_slider.setSingleStep(5)
        self.efficiency_slider.setPageStep(10)
        self.efficiency_slider.setStyleSheet(SLIDER_STYLE)
        self.efficiency_slider.setFixedHeight(BAR_HEIGHT)
        self.efficiency_slider.setToolTip(
            "How much of a good electrode a bad one still delivers if it "
            "ships.\n\n"
            "At 0% a bad electrode is worthless, so stopping one is worth a "
            "whole\nelectrode and the payback is at its highest. At 50% it "
            "still does half\nthe job, so stopping it only saves the half it "
            "was missing and the\npayback halves. At 100% a bad electrode is "
            "as good as a good one and\nstopping it is worth nothing.")
        self.efficiency_label = _bar_value(WORTH_VALUE_WIDTH)
        self.efficiency_slider.valueChanged.connect(self._on_efficiency)
        self._on_efficiency(0)

        # Three groups, separated by hairlines: what to analyse, then the two
        # assumptions. Spacing used to be leading blanks inside the label text,
        # which is why nothing lined up.
        #
        # In a flow layout, so that on a screen too narrow for one row the
        # groups wrap onto a second instead of the right hand end being cut
        # off.
        host, flow = flow_host(spacing=BAR_GAP, line_spacing=BAR_GAP + 2)
        host.setObjectName(HOST_NAME)
        host.setStyleSheet(f"QWidget#{HOST_NAME} {{ background: transparent; }}")
        groups = (
            (_bar_label("From"), self.first_input,
             _bar_label("to"), self.last_input, run_button, whole, default),
            (_bar_label("Merge gaps"), self.gap_slider, self.gap_label),
            (_bar_label("A bad electrode is worth"),
             self.efficiency_slider, self.efficiency_label),
        )
        # Each group is boxed before it goes in, so a wrap can only ever fall
        # BETWEEN groups. Adding the widgets one by one let a row break
        # between a slider and its own label.
        for index, group in enumerate(groups):
            if index:
                flow.addWidget(_Separator(BAR_GROUP_GAP))
            flow.addWidget(_bar_group(group, host))

        box.addWidget(host, 0)
        return frame

    def _on_gap(self, value: int) -> None:
        """Retitle the slider and re-measure at the new tolerance."""
        self.gap_label.setText(
            "off" if not value
            else f"{value} electrode{'s' if value > 1 else ''}")
        # Guarded on the TABLES, not the data. This is called once while the
        # control bar is still being built, to set the label, and at that point
        # the frame already exists but nothing it would draw into does.
        if getattr(self, "totals", None) is not None:
            self.run()

    def _on_efficiency(self, value: int) -> None:
        """Retitle the slider and re-score everything at the new worth."""
        self.efficiency_label.setText(f"{value} %")
        if getattr(self, "totals", None) is not None:
            self._show_vat_tabs()

    def _summary_card(self) -> QWidget:
        """The per vat table and its totals row, across the whole width."""
        frame, box = _card("Per vat")
        self.table = _wide(VAT_HEADERS)
        self.table.horizontalHeaderItem(TOTAL_COLUMN).setForeground(QColor(INK))
        for columns, colour in ((BAD_COLUMNS, BAD), (GOOD_COLUMNS, GOOD)):
            for column in columns:
                self.table.horizontalHeaderItem(column).setForeground(QColor(colour))

        # The same columns, one row, no header of its own: it reads as the
        # bottom line of the table above rather than as a second table, and
        # every figure sits directly under the per vat figure it totals.
        self.totals = _wide(VAT_HEADERS)
        self.totals.horizontalHeader().setVisible(False)
        # One scrollbar for the pair, on the table that has the header. The
        # totals row follows it, so the columns stay lined up when it is
        # scrolled and the card is not carrying two identical bars.
        self.totals.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.horizontalScrollBar().valueChanged.connect(
            self.totals.horizontalScrollBar().setValue)

        # No alignment flag. An aligned item is given its size HINT, which for
        # a QTableWidget is a default 256px and nothing to do with its columns.
        # Unaligned it is given the card's width and capped by the maximum
        # _align sets, which is what makes it stop at its last column.
        box.addWidget(self.table, 0)
        box.addSpacing(6)
        box.addWidget(self.totals, 0)
        # Over the table, the same placeholder the Electrodes tab uses while
        # its first read is in flight.
        self._skeleton = SkeletonOverlay(self.table, columns=8)
        return frame

    def _vat_tabs_card(self) -> QWidget:
        """One tab per vat, each holding that vat's numbers AND its curve.

        Together rather than in two columns: the table is the same figures the
        plot is drawn from, and reading one against the other was a trip across
        the window.
        """
        frame, box = _card("")
        # No crossfade on this strip. It is nested inside a page that already
        # fades on the way in, and every page here holds a pyqtgraph plot that
        # costs about 9ms to repaint. Dissolving one over another repaints it
        # on every animation frame under an opacity effect, so the fade itself
        # stuttered: switching vats felt slow because the ANIMATION was slow,
        # not because there was work to do. Instant, and it is crisp.
        self.vat_tabs = NoWheelTabWidget(fade_ms=0)
        self.vat_tabs.setStyleSheet(
            f"QTabWidget::pane {{ border: none; }}"
            f"QTabBar::tab {{ background: transparent; color: {MUTED};"
            f"font-family: {FONT}; font-size: {COMPACT_PT}pt; font-weight: 700;"
            f"padding: 5px 14px; border: none; }}"
            f"QTabBar::tab:selected {{ color: {INK};"
            f"border-bottom: 3px solid {GOOD}; }}")
        self._vat_pages: dict = {}
        # Every tab carries the same panel listing every vat, so they are kept
        # in a flat list and refreshed together.
        self._interval_panels: list = []
        self._best_by_vat: dict = {}
        # What the shared columns last came to, so a pass that changes it can
        # ask for one more.
        self._table_width = 0
        application = QApplication.instance()
        if application is not None:
            application.installEventFilter(self)
        box.addWidget(self.vat_tabs, 1)
        return frame

    def _show_vat_tabs(self) -> None:
        """Build or refill one page per vat, plus one for all of them.

        Pages are rebuilt only when the vats change, so moving a slider redraws
        in place and leaves you on the tab you were looking at.
        """
        analysis = self._analysis
        entities = list(analysis.per_vat) + [analysis.overall]
        names = [entity.vat if entity is analysis.overall else f"Vat {entity.vat}"
                 for entity in entities]

        if list(self._vat_pages) != names:
            remembered = self.vat_tabs.currentIndex()
            self.vat_tabs.clear()
            self._vat_pages = {}
            self._interval_panels = []
            for name in names:
                page = QWidget()
                line = QHBoxLayout(page)
                line.setContentsMargins(0, 6, 0, 0)
                line.setSpacing(14)
                table_host = QWidget(page)
                holder = QVBoxLayout(table_host)
                holder.setContentsMargins(0, 0, 0, 0)
                table = self._new_advice_table(table_host)
                holder.addWidget(table, 0)
                holder.addStretch(1)
                canvas = NetSavedCanvas(page)
                canvas.widget.setMinimumHeight(MIN_PLOT_HEIGHT)
                panel = self._new_interval_panel(page)
                self._interval_panels.append(panel)
                line.addWidget(table_host, 0)
                line.addWidget(canvas.widget, 1)
                line.addWidget(panel["frame"], 0)
                self._vat_pages[name] = (table, canvas)
                self.vat_tabs.addTab(page, name)
            if 0 <= remembered < len(names):
                self.vat_tabs.setCurrentIndex(remembered)

        # REFILLED, not rebuilt. Throwing the table away and making a new one
        # on every step of a slider meant a brand new top-level widget several
        # times a second, which is what was flashing on screen mid drag.
        efficiency = self.efficiency_slider.value() / 100.0
        self._best_by_vat = {}
        for name, entity in zip(names, entities):
            table, canvas = self._vat_pages[name]
            self._fill_advice(table, entity)
            curve = interval_curve(entity.bad.lengths, entity.bad_fraction,
                                   efficiency)
            canvas.draw(curve)
            if entity is not analysis.overall:
                best = best_option(curve)
                self._best_by_vat[entity.vat] = (best.sampling_interval
                                                 if best else None)

        # Every vat, every time, not only the ones this range has runs for.
        # A vat is in production whether or not the window happens to cover any
        # of it, and it is still being sampled at something.
        #
        # The pooled tab is left out: an interval is set on a vat, not on the
        # plant, so a row for all of them is nothing anybody can act on.
        for panel in self._interval_panels:
            self._interval_rows(panel, list(KEPT_VATS))
        self._refresh_interval_panels()

    def eventFilter(self, watched, event):
        """Let a click anywhere else finish an edit.

        Neither the plot nor the cards take focus when clicked, so an interval
        box kept the caret and its blue selection long after the pointer had
        moved on. Filtered on the application rather than handled here, because
        a press on a widget that accepts it never reaches the tab.
        """
        if event.type() == QEvent.Type.MouseButtonPress:
            focused = QApplication.focusWidget()
            # This tab's boxes only. The filter sees every click in the whole
            # window, and ending an edit in the Electrodes filter bar or a SEM
            # form is not this tab's business.
            if focused is not None and self.isAncestorOf(focused):
                self._finish_editing(watched)
        return super().eventFilter(watched, event)

    @staticmethod
    def _finish_editing(clicked) -> None:
        """Drop focus unless the click landed inside the editor that holds it."""
        focused = QApplication.focusWidget()
        if not isinstance(focused, (QLineEdit, QAbstractSpinBox)):
            return
        if not isinstance(clicked, QWidget):
            return
        # A spin box keeps its text in a line edit of its own. Either of the
        # two can be the one holding focus, and the box is what a click lands
        # on, so compare against the box.
        editor = focused
        if isinstance(focused.parent(), QAbstractSpinBox):
            editor = focused.parent()
        if clicked is editor or editor.isAncestorOf(clicked):
            return
        # The blue block is the line edit's selection, and it outlives the
        # focus unless it is cleared too.
        text = (editor.lineEdit() if isinstance(editor, QAbstractSpinBox)
                else editor)
        text.deselect()
        editor.clearFocus()

    def _new_interval_panel(self, parent) -> dict:
        """What each vat is sampled at today, set against what it should be.

        A stand-in for a setting the app will own. Typing here writes a small
        JSON file beside the script, never the database.
        """
        frame = QFrame(parent)
        frame.setObjectName("panel")
        frame.setStyleSheet(
            f"QFrame#panel {{ background: {BACKDROP}; border: 1px solid {EDGE};"
            f"border-radius: {CARD_RADIUS}px; }}")
        frame.setFixedWidth(PANEL_WIDTH)
        # A floor low enough to be worth having, and it scrolls under it. Sized
        # to its contents the panel was the tallest thing in the row and set
        # the whole window's minimum height on its own.
        frame.setMinimumHeight(PANEL_MIN_HEIGHT)

        # The rows live in a scroll area so the panel can be squeezed. Its own
        # frame is off and its background transparent, so it reads as part of
        # the card rather than as a box inside a box.
        area = QScrollArea(frame)
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setStyleSheet("QScrollArea { background: transparent;"
                           " border: none; }" + SLIM_SCROLLBAR)
        area.viewport().setStyleSheet("background: transparent;")

        inner = QWidget()
        inner.setObjectName(PANEL_INNER)
        inner.setStyleSheet(f"QWidget#{PANEL_INNER} {{ background: transparent; }}")
        area.setWidget(inner)

        outer = QVBoxLayout(frame)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(area)

        box = QVBoxLayout(inner)
        box.setContentsMargins(COMPACT_PAD, COMPACT_PAD, COMPACT_PAD, COMPACT_PAD)
        box.setSpacing(12)
        box.addWidget(_panel_caption("Current sampling intervals", strong=True))

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        for column, heading in enumerate(PANEL_HEADERS):
            grid.addWidget(_panel_caption(heading), 0, column, _LEFT)
        box.addLayout(grid, 0)
        box.addStretch(1)
        return {"frame": frame, "inner": inner, "grid": grid, "rows": {}}

    def _interval_rows(self, panel: dict, vats: list[str]) -> None:
        """One row per vat. Built once and refilled after that, like the tables."""
        if list(panel["rows"]) == vats:
            return
        grid = panel["grid"]
        while grid.count() > len(PANEL_HEADERS):
            widget = grid.takeAt(grid.count() - 1).widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()

        panel["rows"] = {}
        for line, vat in enumerate(vats):
            spin = QSpinBox(panel["inner"])
            spin.setRange(current_intervals.MIN_INTERVAL,
                          current_intervals.MAX_INTERVAL)
            spin.setValue(current_intervals.current_for(vat))
            spin.setMinimumHeight(BAR_HEIGHT)
            spin.setStyleSheet(INPUT_FIELD_STYLE)
            # Otherwise typing 15 over 12 saves 1 on the way to 15. The value
            # is committed on Enter or when the box loses focus.
            spin.setKeyboardTracking(False)
            note = _panel_note()
            # Two rows per vat: the interval, then how far off it is. The note
            # spans both columns, it is a sentence and not a figure.
            top = 1 + line * 2
            grid.addWidget(_panel_caption(vat), top, 0, _LEFT)
            grid.addWidget(spin, top, 1, _LEFT)
            grid.addWidget(note, top + 1, 0, 1, 2)
            # Saved on every change rather than on close, so nothing typed here
            # is lost when the window goes away without ceremony.
            spin.valueChanged.connect(
                lambda value, name=vat: self._on_interval_changed(name, value))
            panel["rows"][vat] = {"spin": spin, "note": note}

    def _on_interval_changed(self, vat: str, value: int) -> None:
        current_intervals.set_current(vat, value)
        self._refresh_interval_panels()

    def _refresh_interval_panels(self) -> None:
        """The stored intervals against the best rates, on every tab.

        Every tab shows every vat, so an interval typed on one has to show up
        on all of them.
        """
        for panel in self._interval_panels:
            for vat, row in panel["rows"].items():
                current = current_intervals.current_for(vat)
                if row["spin"].value() != current:
                    # Blocked, or setting the other tabs' boxes would come
                    # straight back in here as another edit.
                    row["spin"].blockSignals(True)
                    row["spin"].setValue(current)
                    row["spin"].blockSignals(False)
                # Absent and None are different: absent means this range has
                # no runs for the vat, None means it has them and none of the
                # intervals is worth running.
                row["note"].setText(
                    _optimum_note(current, self._best_by_vat.get(vat),
                                  vat in self._best_by_vat))

    def _numbers(self) -> list[int]:
        """Every marking number on record, for seeding and resetting the range."""
        column = str(ElectrodeColumns.MARKING)
        if self.frame is None or column not in self.frame.columns:
            return []
        return sorted(n for n in self.frame[column].map(parse_marking)
                      if n is not None)

    def _seed_range(self) -> None:
        """Open on the configured default range."""
        numbers = self._numbers()
        if not numbers:
            return
        first, last = self._default_range(numbers)
        self.first_input.setText(f"B{first}")
        self.last_input.setText(f"B{last}")
        self._seeded = True

    @staticmethod
    def _resolve(edge, numbers: list[int]) -> int:
        """One configured end as a marking number."""
        if edge == app_config.OLDEST:
            return numbers[0]
        if edge == app_config.NEWEST:
            return numbers[-1]
        return int(edge)

    def _default_range(self, numbers: list[int]) -> tuple[int, int]:
        """The configured range, or the whole history if it misses the data.

        A fixed marking written down months ago can end up past everything on
        record, and an empty tab at startup looks like the read failed. Falling
        back to everything is at least a picture of something.
        """
        try:
            stored = app_config.sampling_default_range()
            first, last = (self._resolve(edge, numbers) for edge in stored)
        except Exception as e:
            print(f"Could not read the default sampling range: {e}")
            return numbers[0], numbers[-1]
        if first > last or first > numbers[-1] or last < numbers[0]:
            return numbers[0], numbers[-1]
        return first, last

    def _edit_default_range(self) -> None:
        """Set where the boxes start next time, and apply it now."""
        try:
            current = app_config.sampling_default_range()
        except Exception as e:
            print(f"Could not read the default sampling range: {e}")
            current = (13900, app_config.NEWEST)
        # The marking boxes open on what the tab's own range boxes say, so
        # setting a range and then making it the default is one press.
        dialog = SamplingRangeDialog(
            self, default=current,
            on_screen=(parse_marking(self.first_input.text()),
                       parse_marking(self.last_input.text())))
        if not dialog.exec() or dialog.chosen is None:
            return
        try:
            app_config.set_sampling_default_range(*dialog.chosen)
        except Exception as e:
            QMessageBox.warning(
                self, TITLE,
                f"The default range could not be saved.\n\n{e}")
            return
        # Applied at once. Saving a default and seeing nothing happen reads as
        # the button not having worked.
        self._seed_range()
        self.run()

    def _reset_range(self) -> None:
        numbers = self._numbers()
        if numbers:
            self.first_input.setText(f"B{numbers[0]}")
            self.last_input.setText(f"B{numbers[-1]}")
        self.run()

    def run(self, quiet: bool = False) -> None:
        """Measure the current range and redraw everything.

        ``quiet`` for a reload rather than a click: an empty range box is
        somebody mid-thought, or a frame that has not arrived, and neither
        deserves a dialog.
        """
        first = parse_marking(self.first_input.text())
        last = parse_marking(self.last_input.text())
        if first is None or last is None:
            if not quiet:
                QMessageBox.warning(
                    self, TITLE,
                    "Give both ends of the range as electrode markings, "
                    "for example B14000 and B15000.")
            return

        # Measured, so the boxes are no longer an edit in progress and a
        # reload may go ahead again. setText clears the flag by itself, which
        # covers seeding and Whole history.
        for field in (self.first_input, self.last_input):
            field.setModified(False)
        self._analysis = analyse(self.frame, first, last,
                                 self.gap_slider.value())
        self._show_vats()
        self._show_totals()
        self._show_vat_tabs()

    # ------------------------------------------------------------------
    def _show_vats(self) -> None:
        rows = self._analysis.per_vat
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            _fill_row(self.table, index, row)

    def _show_totals(self) -> None:
        """The bottom line, in the same columns as the table it totals."""
        overall = self._analysis.overall
        self.totals.setRowCount(1)
        _fill_row(self.totals, 0, overall, bold_all=True, tint=TOTALS_TINT)
        self._layout_tables()

    def _layout_tables(self) -> None:
        """Width them together, then height them. Order matters: the height
        depends on whether the shared widths ended up overflowing.

        Run again when the total moves. The first pass measures text in
        whatever font the widget has before the stylesheet is applied, so the
        columns come out narrower than they end up needing, and the widget is
        left holding a maximum worked out from the old figures. One more pass
        settles it, and ``_wanted`` measures the items rather than the current
        widths, so it converges rather than oscillating.
        """
        total = _align(self.table, self.totals)
        overflows = total > self.table.viewport().width()
        # Squashable: three vats and a header is 94px that the window could not
        # be dragged under. One row and the header is enough to keep, the rest
        # scrolls.
        _fit(self.table, self.table.rowCount(), scrolls=overflows,
             squashable=True, least=1)
        _fit(self.totals, 1, scrolls=overflows)
        if total != self._table_width:
            self._table_width = total
            QTimer.singleShot(0, self._layout_tables)

    def showEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        """Lay the columns out once more, after Qt has settled the geometry.

        The first pass runs from the constructor, when the tables still report
        a placeholder viewport width and the columns get a share of far less
        room than they will actually have. Deferred by a single event loop turn
        rather than done here, because the real width is not known until this
        event has been handled.
        """
        super().showEvent(event)
        QTimer.singleShot(0, self._layout_tables)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        """Re-share the width whenever there is a different amount of it.

        The first layout happens before the window is on screen, when the
        viewport still reports a placeholder width, so without this the columns
        would keep whatever they were given in the dark.
        """
        super().resizeEvent(event)
        if getattr(self, "totals", None) is not None:
            self._layout_tables()

    def _new_advice_table(self, parent) -> QTableWidget:
        """One vat's table, built once. ``_fill_advice`` puts figures in it."""
        table = _table(ADVICE_HEADERS, compact=True, parent=parent,
                       squashable=True)
        header = table.horizontalHeader()
        # The two number columns take what they need and the question takes
        # the rest, so the longest header is the one that cannot be cut off.
        # Sharing the width equally clipped it to "atch this many bad electrode".
        # Every column to its contents, then the table to the sum of them.
        # Beside a plot that wants the rest of the width, a table sized by
        # Qt's default hint came out narrow enough to need its own scrollbar.
        for column in range(len(ADVICE_HEADERS)):
            header.setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents)
        # Hidden, not removed. A hidden section reports a width of zero, so the
        # table still sizes itself to what is actually on screen.
        table.setColumnHidden(PAYBACK_COLUMN, not SHOW_PAYBACK)
        table.setColumnHidden(CATCH_COLUMN, not SHOW_CATCH_RATE)
        table.setColumnHidden(BAD_STOPPED_COLUMN, not SHOW_BAD_STOPPED)
        return table

    def _fill_advice(self, table: QTableWidget, entity) -> None:
        """Put one vat's figures into a table that already exists.

        The pooled table used to bold every row, from when it sat under the per
        vat ones in a single column and had to be told apart from them. It is
        its own tab now, so all that did was drown out the bold that means
        something: the rates within a whisker of the best.
        """
        if not entity.bad.lengths:
            # One line, not the same sentence nine times. Nothing was measured
            # here, which is not the same as this vat needing no testing.
            table.setRowCount(1)
            empty = QTableWidgetItem("no bad runs in this range")
            empty.setForeground(QColor(MUTED))
            empty.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                   | Qt.AlignmentFlag.AlignVCenter)
            table.setItem(0, 1, empty)
            _fit(table, 1, squashable=True)
            _squash_width(table)
            return

        efficiency = self.efficiency_slider.value() / 100.0
        rows = list(sampling_table(entity.bad.lengths, entity.bad_fraction,
                                   ADVICE_TARGETS, efficiency))
        # Two adjacent catch rates often need the SAME interval, and with the
        # catch rate column hidden the two rows are then identical figures
        # twice over. Keep the first, which is the loosest rate that needs it.
        unique, seen = [], set()
        for option in rows:
            if option.sampling_interval not in seen:
                seen.add(option.sampling_interval)
                unique.append(option)
        rows = unique
        # And the actual optimum, which almost never lands on one of the round
        # catch rates above. Payback climbs for ever as the interval widens, so
        # on its own it always points at the loosest row and would have you
        # testing almost nothing. Net saved is the difference rather than the
        # ratio, so it peaks, and this is that peak.
        curve = interval_curve(entity.bad.lengths, entity.bad_fraction,
                               efficiency)
        # Shared with the plot, so the tinted row and the marked peak can
        # never name different intervals.
        best = best_option(curve)
        peak = None
        if best is not None:
            # If one of the round catch rates already lands on the best
            # interval, mark THAT row rather than adding a second one saying
            # the same thing. Only when the optimum falls between two of them
            # is a row inserted, slotted in by interval so the table still runs
            # from the loosest rate to the tightest.
            peak = next((index for index, option in enumerate(rows)
                         if option.sampling_interval == best.sampling_interval),
                        None)
            if peak is None:
                peak = next((index for index, option in enumerate(rows)
                             if option.sampling_interval
                             < best.sampling_interval), len(rows))
                rows.insert(peak, best)
        # Bold marks every row that is a defensible choice, not only the peak.
        # The top of the curve is level over a wide stretch, and a single bold
        # row implies the others were rejected when they were not.
        floor = (best.net_percent - ACCEPTABLE_GAIN) if best else None
        table.setRowCount(len(rows))
        for row, option in enumerate(rows):
            cells = (f"best, {option.catch_rate * 100:.0f} %"
                     if row == peak else f"{option.target * 100:.0f} %",
                     f"{option.sampling_interval} electrodes",
                     f"{option.production_destroyed:.1f} %",
                     _value_text(option.value),
                     f"{option.bad_stopped:.1f} %",
                     f"{option.good_lost:.1f} %",
                     f"{option.net_percent:+.2f} %")
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if column:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                          | Qt.AlignmentFlag.AlignVCenter)
                # No colouring by payback. A cutoff at 1.0 said "fine" to a
                # row that is really a coin flip, and any higher cutoff would
                # be a number this tool has no business inventing. Gain needs
                # no invented cutoff: zero is the line, and it is meaningful.
                if column == GAIN_COLUMN and option.net_percent:
                    item.setForeground(
                        QColor(GOOD if option.net_percent > 0 else BAD))
                if row == peak:
                    item.setBackground(QColor(PEAK_TINT))
                if floor is not None and option.net_percent >= floor:
                    item.setFont(_bold(item.font()))
                table.setItem(row, column, item)
        _fit(table, len(rows), squashable=True)
        _squash_width(table)
