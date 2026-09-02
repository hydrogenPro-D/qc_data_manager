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

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPushButton, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from src.data_access import app_config, build_backend
from src.data_access.electrodes.electrode_columns import ElectrodeColumns
from src.gui.styles.button_styles import (
    PRIMARY_BUTTON_STYLE, SECONDARY_BUTTON_STYLE, apply_modern_shadow,
)
from src.gui.styles.input_styles import INPUT_FIELD_STYLE
from src.gui.styles.table_styles import SCROLLBAR_STYLE

from ..data.read_only import ReadOnlyBackend
from ..data.run_lengths import (
    Analysis, analyse, parse_marking, sampling_table,
)
from .charts import BAD, GOOD, INK, MUTED, LengthHistogramCanvas, VatSpreadCanvas

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
MAX_VISIBLE_ROWS = 12

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
SPACER_TINT = "#F3F7F8"
# The totals row, set apart by a wash rather than by a repeated header.
TOTALS_TINT = "#EDF3F4"


def _card(title: str) -> tuple[QFrame, QVBoxLayout]:
    """A white panel with a heading, and the layout to fill it."""
    frame = QFrame()
    # Scoped to this frame by name. A bare "QFrame" selector also matches every
    # QLabel inside it, because QLabel is a QFrame, so the border and the 14px
    # radius were being drawn around each individual number as well.
    frame.setObjectName("panel")
    frame.setStyleSheet(
        f"QFrame#panel {{ background: {CARD}; border: 1px solid {EDGE};"
        f"border-radius: 14px; }}")
    apply_modern_shadow(frame)
    box = QVBoxLayout(frame)
    box.setContentsMargins(16, 14, 16, 14)
    box.setSpacing(10)
    if title:
        heading = QLabel(title.upper())
        heading.setStyleSheet(f"color: {MUTED}; font-family: {FONT};"
                              f"font-size: 11pt; font-weight: 700;"
                              f"letter-spacing: 1.2px; border: none;")
        box.addWidget(heading, 0)
    return frame, box


def _fit(table: QTableWidget, rows: int) -> None:
    """Height the table to its contents, up to a cap, then let it scroll."""
    shown = min(max(rows, 1), MAX_VISIBLE_ROWS)
    body = (sum(table.rowHeight(row) or 30 for row in range(shown))
            if rows else 30)
    head = table.horizontalHeader().height() if not table.horizontalHeader(
    ).isHidden() else 0
    table.setFixedHeight(head + body + 6)


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


def _wide(headers) -> QTableWidget:
    """One of the two tables that share a column layout.

    Both stretch, so both divide the same width the same way and the totals row
    lands under what it totals without anything having to measure anything.
    Sizing them to their contents instead put them a few pixels apart and, with
    fifteen columns, overflowed into a horizontal scrollbar 35 pixels tall.
    """
    table = _table(headers)
    header = table.horizontalHeader()
    header.setSectionResizeMode(SPACER_COLUMN, QHeaderView.ResizeMode.Fixed)
    table.setColumnWidth(SPACER_COLUMN, SPACER_WIDTH)
    return table


def _bold(font: QFont) -> QFont:
    """The same font, heavier. Qt hands back a copy, so this cannot leak."""
    heavy = QFont(font)
    heavy.setBold(True)
    return heavy


def _table(headers) -> QTableWidget:
    """A read-only table that does not react to the mouse at all.

    Nothing here is clickable, so a row that lights up on hover or stays
    highlighted after a click is telling the reader something untrue. Selection
    is off at the widget level and the hover rule is overridden as well, since
    the style would otherwise still paint it.
    """
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(list(headers))
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
    table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    # Sixteen columns spread evenly are sixteen columns too narrow to read, so
    # the wide table sizes each one to what is actually in it instead.
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    table.horizontalHeader().setHighlightSections(False)
    table.setStyleSheet(
        f"QTableWidget {{ background: {CARD}; border: none;"
        f"font-family: {FONT}; font-size: 11pt; gridline-color: {EDGE}; }}"
        f"QTableWidget::item {{ padding: 3px 6px; }}"
        f"QTableWidget::item:hover, QTableWidget::item:selected {{"
        f"background: transparent; }}"
        f"QHeaderView::section {{ background: #F3F7F8; color: {MUTED};"
        f"border: none; padding: 7px; font-weight: 700; }}" + SCROLLBAR_STYLE)
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

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self._left())
        split.addWidget(self._right())
        split.setSizes([1000, 520])
        layout.addWidget(split, 1)
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
        for field, placeholder in ((self.first_input, "first electrode, e.g. B14000"),
                                   (self.last_input, "last electrode, e.g. B15000")):
            field.setStyleSheet(INPUT_FIELD_STYLE)
            field.setPlaceholderText(placeholder)
            field.setMinimumWidth(220)
            field.returnPressed.connect(self.run)

        run_button = QPushButton("Analyse")
        run_button.setStyleSheet(PRIMARY_BUTTON_STYLE)
        run_button.setMinimumWidth(130)
        run_button.clicked.connect(self.run)

        whole = QPushButton("Whole history")
        whole.setStyleSheet(SECONDARY_BUTTON_STYLE)
        whole.setToolTip("Reset the range to every electrode on record")
        whole.clicked.connect(self._reset_range)

        banner = QLabel(self._database_note())
        banner.setStyleSheet(
            f"color: {DANGER}; font-family: {FONT}; font-size: 11pt;"
            f"font-weight: 700; border: none;")
        banner.setToolTip("This window only ever reads. Nothing it does can "
                          "change anything in the database.")

        line = QHBoxLayout()
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(10)
        for widget in (QLabel("From"), self.first_input,
                       QLabel("to"), self.last_input,
                       run_button, whole):
            if isinstance(widget, QLabel):
                widget.setStyleSheet(
                    f"color: {INK}; font-family: {FONT};"
                    f"font-size: 11pt; border: none;")
            line.addWidget(widget, 0)
        line.addStretch(1)
        line.addWidget(banner, 0)
        box.addLayout(line)
        return frame

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

    def _left(self) -> QWidget:
        frame, box = _card("Per vat")
        self.table = _wide(VAT_HEADERS)
        self.table.horizontalHeaderItem(TOTAL_COLUMN).setForeground(QColor(INK))
        for columns, colour in ((BAD_COLUMNS, BAD), (GOOD_COLUMNS, GOOD)):
            for column in columns:
                self.table.horizontalHeaderItem(column).setForeground(QColor(colour))

        # The same columns, one row, no header of its own: it reads as the
        # bottom line of the table above rather than as a second table, and
        # every figure sits directly under the per vat figure it totals.
        # Set apart by a gap and a tint instead, since a repeated header row
        # would be the loudest thing in the card and say nothing new.
        self.totals = _wide(VAT_HEADERS)
        self.totals.horizontalHeader().setVisible(False)

        self.bars = VatSpreadCanvas()
        box.addWidget(self.table, 0)
        box.addSpacing(6)
        box.addWidget(self.totals, 0)
        box.addWidget(self.bars.widget, 1)
        return frame

    def _right(self) -> QWidget:
        holder = QWidget()
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(14)

        # No card heading: the plot carries its own title, and two headings
        # stacked on one panel is the look of a widget dropped in by accident.
        frame, box = _card("")
        self.histogram = LengthHistogramCanvas()
        box.addWidget(self.histogram.widget, 1)
        column.addWidget(frame, 3)

        advice, advice_box = _card("What that means for testing")
        self.advice_table = _table(
            ("Catch this many bad runs", "Test every", "Production destroyed"))
        advice_box.addWidget(self.advice_table, 1)

        self.caveat = QLabel("")
        self.caveat.setWordWrap(True)
        self.caveat.setStyleSheet(f"color: {MUTED}; font-family: {FONT}; font-size: 10pt; border: none;")
        advice_box.addWidget(self.caveat, 0)
        column.addWidget(advice, 2)
        return holder

    # ------------------------------------------------------------------
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

        self._analysis = analyse(self.frame, first, last)
        self._show_vats()
        self._show_totals()
        self._show_advice()

    # ------------------------------------------------------------------
    def _show_vats(self) -> None:
        rows = self._analysis.per_vat
        self.table.setRowCount(len(rows))
        _fit(self.table, len(rows))
        for index, row in enumerate(rows):
            _fill_row(self.table, index, row)
        self.bars.draw(rows)

    def _show_totals(self) -> None:
        """The bottom line, in the same columns, and the distribution beside it."""
        overall = self._analysis.overall
        self.totals.setRowCount(1)
        _fill_row(self.totals, 0, overall, bold_all=True, tint=TOTALS_TINT)
        _fit(self.totals, 1)
        self.histogram.draw(overall.bad.lengths, overall.good.lengths)

    def _show_advice(self) -> None:
        lengths = self._analysis.overall.bad.lengths
        rows = sampling_table(lengths) if lengths else []
        self.advice_table.setRowCount(len(rows))
        _fit(self.advice_table, len(rows))
        for index, (target, spacing, cost) in enumerate(rows):
            for column, text in enumerate((f"{target * 100:.0f} %",
                                           f"{spacing} electrodes",
                                           f"{cost:.1f} %")):
                item = QTableWidgetItem(text)
                if column:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                          | Qt.AlignmentFlag.AlignVCenter)
                self.advice_table.setItem(index, column, item)

        if not lengths:
            self.caveat.setText("No bad runs in this range, so there is "
                                "nothing to size a testing rate against.")
            return
        shortest = min(lengths)
        note = (f"Measured from {len(lengths)} bad runs, the shortest "
                f"{shortest} electrode(s) long. ")
        # The number that decides whether sampling can work at all, said out
        # loud rather than left for somebody to notice in the histogram.
        note += ("Short runs are what set the rate: catching them means "
                 "testing at least as often as they are long. If that column "
                 "asks for more than you can afford to destroy, the honest "
                 "answer is that sampling cannot cover this on its own.")
        self.caveat.setText(note)


def run_app() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName(TITLE)
    window = SamplingWindow()
    window.show()
    sys.exit(app.exec())
