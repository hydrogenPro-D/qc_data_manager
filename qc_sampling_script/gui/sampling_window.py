"""The window: pick a range of electrodes, get the run lengths back.

Composition root, the same shape the app uses. It builds the backend once,
reads the electrodes once, and every analysis after that runs against the
frame already in memory, so changing the range is instant.

Left half is per vat, right half is the same measurement pooled across all of
them, plus what it implies for the testing rate. That last panel is the point
of the whole tool: the rest is the evidence for it.
"""

from __future__ import annotations

import sys

from PyQt6.QtCore import QEvent, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QFontMetricsF
from PyQt6.QtWidgets import (
    QAbstractSpinBox, QApplication, QCheckBox, QFrame, QGridLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPushButton, QScrollArea, QSlider, QSpinBox, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from src.gui.widgets.no_wheel_tabs import NoWheelTabWidget

from src.data_access import app_config, build_backend
from src.data_access.electrodes.electrode_columns import ElectrodeColumns
from src.gui.styles.button_styles import (
    PRIMARY_BUTTON_STYLE, SECONDARY_BUTTON_STYLE, apply_modern_shadow,
)
from src.gui.electrodes_gui import SCRAP_TESTING
from src.gui.styles.input_styles import INPUT_FIELD_STYLE

from ..data import current_intervals
from ..data.read_only import ReadOnlyBackend
from ..data.run_lengths import (
    ACCEPTABLE_GAIN, Analysis, KEPT_VATS, analyse, best_option, interval_curve,
    parse_marking, sampling_table,
)
from .charts import BAD, GOOD, INK, MUTED, NetSavedCanvas
from .flow_layout import DIVIDER, flow_host

TITLE = "QC Sampling Analysis"
BACKDROP = "#EEF4F5"
CARD = "#FFFFFF"
EDGE = "#DCE8EA"
# Named rather than left to Qt's default. Every other style in this app names
# its family, and a widget that does not inherits whatever the platform picks,
# which is not always a font with the glyphs to draw a number in.
FONT = "'Segoe UI', 'Inter', Arial, sans-serif"
# Loud on purpose, and the same red the app warns with. Somebody reading these
# numbers has to know whether they are the real ones, and the only way to be
# sure is to have the database name in front of them the whole time.
DANGER = "#C81E1E"
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
# The plot never shrinks below this, however little room is left. Under it the
# curves are a smear and the axis labels collide, and a scrollbar is the better
# answer than an unreadable chart.
MIN_PLOT_HEIGHT = 260

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
PANEL_HEADERS = ("Vat", "Interval")
_LEFT = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
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


def _fit(table: QTableWidget, rows: int, scrolls: bool = False) -> None:
    """Height the table to its contents, up to a cap, then let it scroll.

    ``scrolls`` reserves room for the horizontal scrollbar. The app's are
    deliberately thick, 35px, so a table that grew one without being told about
    it lost a whole row behind it.
    """
    shown = min(max(rows, 1), MAX_VISIBLE_ROWS)
    body = (sum(table.rowHeight(row) or 30 for row in range(shown))
            if rows else 30)
    header = table.horizontalHeader()
    head = 0 if header.isHidden() else header.height()
    bar = table.horizontalScrollBar().sizeHint().height() if scrolls else 0
    table.setFixedHeight(head + body + bar + 6)


def _open_backend() -> tuple[object, str]:
    """The backend on the production database, or the configured one saying why.

    Tried against production first, because that is the point of this tool: the
    electrode list already comes from there, and reading the QC statuses from
    somewhere else would quietly mix real electrodes with test verdicts.

    A failure falls back rather than refusing to start, since the banner names
    whatever is actually in use and cannot therefore mislead anyone. What it
    must not do is fall back SILENTLY, so the reason comes back with it.
    """
    production = app_config.production_database()
    if not production:
        return build_backend(), ""
    try:
        return build_backend(database=production), ""
    except Exception as e:
        return build_backend(), (
            f"Could not open {production}, so the figures below do not all "
            f"come from it.\n\n{e}\n\nThe banner at the top right names "
            f"what is actually being read.")


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
    total = sum(widths)
    for table in tables:
        for column, width in enumerate(widths):
            table.setColumnWidth(column, width)
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
    # Explicitly transparent. The backdrop sets a plain "background" on itself,
    # which cascades to every plain QWidget under it, so a bare container shows
    # as a grey slab on the white card.
    box.setStyleSheet("background: transparent;")
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
        self.setStyleSheet("background: transparent; border: none;")
        line = QFrame(self)
        line.setGeometry(margin, 4, 1, BAR_HEIGHT - 8)
        line.setStyleSheet(f"background: {EDGE}; border: none;")

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


def _table(headers, compact: bool = False, parent=None) -> QTableWidget:
    """A read-only table that does not react to the mouse at all.

    Nothing here is clickable, so a row that lights up on hover or stays
    highlighted after a click is telling the reader something untrue. Selection
    is off at the widget level and the hover rule is overridden as well, since
    the style would otherwise still paint it.
    """
    # Parented at construction. A widget with no parent is a top-level
    # WINDOW in Qt, and on Windows one built and reparented in the same breath
    # can flash on screen as a real window before it is adopted.
    table = QTableWidget(0, len(headers), parent)
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


class SamplingWindow(QMainWindow):
    """Range in, run lengths out."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(TITLE)
        self.resize(1500, 940)

        backend, self._warning = _open_backend()
        # Wrapped, not used directly. This tool now opens the real database, so
        # "it only reads" is enforced rather than remembered: see ReadOnlyBackend.
        self.backend = ReadOnlyBackend(backend)
        # Vats first: the whole tool is per vat, so a run without them would be
        # one meaningless group rather than a partial answer.
        self.backend.load_vats()
        self.frame = self.backend.get_metadata()
        self._analysis = Analysis()

        backdrop = QWidget()
        backdrop.setStyleSheet(f"background: {BACKDROP};")
        layout = QVBoxLayout(backdrop)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(14)
        layout.addWidget(self._controls(), 0)

        # Full width, both of them. The per vat table has fifteen columns and
        # wants every pixel; the tabs below hold a table and a plot side by
        # side and want the same.
        layout.addWidget(self._summary_card(), 0)
        layout.addWidget(self._vat_tabs_card(), 1)
        self.setCentralWidget(backdrop)

        self._seed_range()
        self.run()
        if self._warning:
            QMessageBox.warning(self, TITLE, self._warning)

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
            field.setToolTip(f"{tip}, for example B14000. A bare number works "
                             f"too.")
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
        # One height for the pair, so the secondary does not sit short beside
        # the primary the way two differently padded styles otherwise do.
        for button in (run_button, whole):
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
            "as good as a good one and\nstopping it is worth nothing.\n\n"
            "Only the payback moves. How often to test comes from the run "
            "lengths.")
        self.efficiency_label = _bar_value(WORTH_VALUE_WIDTH)
        self.efficiency_slider.valueChanged.connect(self._on_efficiency)
        self._on_efficiency(0)

        banner = QLabel(self._database_note())
        banner.setStyleSheet(
            f"color: {DANGER}; font-family: {FONT}; font-size: {COMPACT_PT}pt;"
            f"font-weight: 700; background: transparent; border: none;")
        banner.setToolTip("This window only ever reads. Nothing it does can "
                          "change anything in the database.")

        # Three groups, separated by hairlines: what to analyse, then the two
        # assumptions. Spacing used to be leading blanks inside the label text,
        # which is why nothing lined up.
        #
        # In a flow layout, so that on a screen too narrow for one row the
        # groups wrap onto a second instead of the right hand end being cut
        # off. The banner sits outside it, pinned to the right, because it is
        # the one thing that must never be the piece that scrolls away.
        host, flow = flow_host(spacing=BAR_GAP, line_spacing=BAR_GAP + 2)
        host.setStyleSheet("background: transparent;")
        groups = (
            (_bar_label("From"), self.first_input,
             _bar_label("to"), self.last_input, run_button, whole),
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

        line = QHBoxLayout()
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(BAR_GROUP_GAP)
        line.addWidget(host, 1)
        line.addWidget(banner, 0, Qt.AlignmentFlag.AlignTop)
        box.addLayout(line)
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

    def _database_note(self) -> str:
        """What the banner says: one name, or both when they differ.

        Both, when the QC schema and the electrode list come from different
        databases, because then "the database" is not a single thing and naming
        only one of them would be the misleading half of the truth.
        """
        qc, plant = self.backend.databases()
        if qc.lower() == plant.lower():
            return f"Database: {qc}  ·  READ ONLY"
        return f"QC data: {qc}  ·  Plant: {plant}  ·  READ ONLY"

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
        return frame

    def _vat_tabs_card(self) -> QWidget:
        """One tab per vat, each holding that vat's numbers AND its curve.

        Together rather than in two columns: the table is the same figures the
        plot is drawn from, and reading one against the other was a trip across
        the window.
        """
        frame, box = _card("")
        self.vat_tabs = NoWheelTabWidget()
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
            self._fill_advice(table, entity, entity is analysis.overall)
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
        a press on a widget that accepts it never reaches the window.
        """
        if event.type() == QEvent.Type.MouseButtonPress:
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

        box = QVBoxLayout(frame)
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
        return {"frame": frame, "grid": grid, "rows": {}}

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
            spin = QSpinBox(panel["frame"])
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
        """Open on the most recent thousand, which is the useful default.

        The whole history is one button away, but a first look at every
        electrode ever made mostly measures how the process used to be.
        """
        numbers = self._numbers()
        if not numbers:
            return
        self.first_input.setText(f"B{max(numbers[0], numbers[-1] - 999)}")
        self.last_input.setText(f"B{numbers[-1]}")

    def _reset_range(self) -> None:
        numbers = self._numbers()
        if numbers:
            self.first_input.setText(f"B{numbers[0]}")
            self.last_input.setText(f"B{numbers[-1]}")
        self.run()

    def run(self) -> None:
        """Measure the current range and redraw everything."""
        first = parse_marking(self.first_input.text())
        last = parse_marking(self.last_input.text())
        if first is None or last is None:
            QMessageBox.warning(
                self, TITLE,
                "Give both ends of the range as electrode markings, "
                "for example B14000 and B15000.")
            return

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
        _fit(self.table, self.table.rowCount(), scrolls=overflows)
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
        table = _table(ADVICE_HEADERS, compact=True, parent=parent)
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

    def _fill_advice(self, table: QTableWidget, entity,
                     combined: bool) -> None:
        """Put one vat's figures into a table that already exists."""
        if not entity.bad.lengths:
            # One line, not the same sentence nine times. Nothing was measured
            # here, which is not the same as this vat needing no testing.
            table.setRowCount(1)
            empty = QTableWidgetItem("no bad runs in this range")
            empty.setForeground(QColor(MUTED))
            empty.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                   | Qt.AlignmentFlag.AlignVCenter)
            table.setItem(0, 1, empty)
            _fit(table, 1)
            table.setFixedWidth(
                sum(table.columnWidth(column)
                    for column in range(table.columnCount())) + 2)
            return

        efficiency = self.efficiency_slider.value() / 100.0
        rows = list(sampling_table(entity.bad.lengths, entity.bad_fraction,
                                   ADVICE_TARGETS, efficiency))
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
                # be a number this tool has no business inventing.
                if row == peak:
                    item.setBackground(QColor(PEAK_TINT))
                if floor is not None and option.net_percent >= floor:
                    item.setFont(_bold(item.font()))
                if combined:
                    # The pooled table is the one a plant-wide rate would come
                    # from, so it reads as the conclusion of the ones above it.
                    item.setFont(_bold(item.font()))
                table.setItem(row, column, item)
        _fit(table, len(rows))
        table.setFixedWidth(
            sum(table.columnWidth(column)
                for column in range(table.columnCount())) + 2)



def run_app() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName(TITLE)
    window = SamplingWindow()
    window.show()
    sys.exit(app.exec())
