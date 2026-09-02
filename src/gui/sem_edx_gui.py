"""SEM EDX tab: a generation, then an electrode type, then the samples.

    SEM EDX
    |-- Gen4.1
    |   |-- Anodes
    |   `-- Cathodes
    `-- (a later generation)

Two levels because the data has two axes. The generation picks the file; the
electrode type picks the sheet, and the sheets do not measure the same things.
Anodes carry Ni, S and Co; cathodes gained C and N in August 2026 along with
three more ratios. So each type widget is handed a *layout*, the quantities its
sheet holds in the sheet's own order, and builds its columns from that. Nothing
about which elements exist is written down here.

Thin by design: it never imports the backend. Everything arrives as injected
callables, so it can be unit-tested with fakes and hand-built frames.

A sample is a position on one electrode. Most electrodes have a single sample,
at the top right; a full plate map has five. Each sample is measured three
times, and the table lays those points out beside their average the way the
workbook does. Double clicking a row opens the sample on its own.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QFrame, QDialog,
    QPushButton, QScrollArea, QLineEdit, QFileDialog, QListWidget,
    QListWidgetItem, QTableWidget, QTableWidgetItem, QApplication,
    QStyledItemDelegate, QProgressBar,
)
from PyQt6.QtCore import (
    Qt, QEvent, QObject, QSize, pyqtSignal,
)
from PyQt6.QtGui import QFont, QPixmap, QBrush, QColor
import pandas as pd
import qtawesome as qta

from .styles.table_styles import TABLE_STYLE, COMPACT_TABLE_STYLE, SLIM_SCROLLBAR_STYLE
from .styles.button_styles import (
    DANGER_BUTTON_STYLE,
    PRIMARY_BUTTON_STYLE,
    SECONDARY_BUTTON_STYLE,
    TOOLBAR_BUTTON_STYLE,
    apply_modern_shadow,
)
from .styles.input_styles import COMBO_BOX_STYLE, INPUT_FIELD_STYLE
from .styles.tab_styles import create_styled_container_layout, PLOT_TABS_STYLE
from .widgets.data_table_widget import DataTableWidget, CellStyle
from .widgets.table_delegates import (RowFlasher, RowHighlight,
                                      RowHighlightDelegate)
from .widgets.error_dialogs import (
    ConfirmationDialog,
    ValidationErrorScrollDialog,
    build_header,
)
from .widgets.filter_bar_widget import FilterBarWidget, FilterDropdown
from .widgets.no_wheel_tabs import NoWheelTabWidget
from .widgets.no_scroll_inputs import NoScrollComboBox
from .widgets.frozen_columns import FrozenColumns
from .widgets.busy_spinner import BusySpinner
from .widgets.skeleton_overlay import SkeletonOverlay
from src.data_access.electrodes.status_propagation import electrode_number
from src.data_access.sem_edx.sem_edx_columns import (
    SemEdxColumns,
    Quantity,
    average_column,
    quantities,
)

GEN4_1_LABEL = "Gen4.1"

# Display text is separate from the contract: the keys are the stored column
# names, the values are what the operator sees. Relabel here, never by renaming
# the source column. Quantity columns are named by rule instead, since which
# ones exist is not known until the layout is read.
COLUMN_DISPLAY_NAMES: dict[str, str] = {
    str(SemEdxColumns.ELECTRODE): "Electrode",
    # "Type", not "Electrode type": the header was the widest thing in the
    # column and the electrode is named right beside it.
    str(SemEdxColumns.ELECTRODE_TYPE): "Type",
    # "Sample location", not the workbook's "Sample name": it holds where on
    # the plate the sample came from, top right and the like.
    str(SemEdxColumns.POSITION): "Sample location",
    str(SemEdxColumns.ZOOM): "Zoom",
    str(SemEdxColumns.MEASUREMENT): "Point",
    str(SemEdxColumns.OPERATOR): "Operator",
    str(SemEdxColumns.DATE): "Date",
    str(SemEdxColumns.FOLDER): "SEM images",
    str(SemEdxColumns.NOTE): "Notes",
}

# What a sub tab is called. Anything not listed is title cased as it comes.
TYPE_LABELS: dict[str, str] = {"ANODE": "Anodes", "CATHODE": "Cathodes"}

# The folder column renders as a button rather than as its path.
IMAGES_COLUMN = str(SemEdxColumns.FOLDER)
# Carried on the frame but never shown as a column. The filenames would be as
# wide as the rest of the table put together, and the electrode type is what
# the sub tab you are looking at already says.
HIDDEN_COLUMNS = {str(SemEdxColumns.IMAGES), str(SemEdxColumns.ELECTRODE_TYPE)}
FOLDER_DIALOG_TITLE = "Open SEM-EDX result folder"

# Filter dropdown keys.
FILTER_POSITION = "position"
FILTER_OPERATOR = "operator"

# Percentages read to one decimal, ratios to two: a Ni:S of 3.50 and 3.55 are
# meaningfully different, a Ni of 33.4 and 33.45 are not.
ELEMENT_DECIMALS = 1
RATIO_DECIMALS = 2

# What ``electrode_average_column`` appends. Split off the base the same way as
# a point number or "avg".
ELECTRODE_AVERAGE_SUFFIX = "electrode_avg"

EMPTY = "—"

# In the sample window: a cell that can be corrected, and one that is worked
# out from the others. The tint is the affordance, since a table gives no other
# sign that a cell will accept a double click.
EDITABLE_CELL = "#F0FDFA"
DERIVED_CELL_TEXT = "#6B7280"

# The capture picker. A whole row highlights, so the click target is the row
# rather than a checkbox someone has to aim at.
PICKER_LIST_STYLE = """
    QListWidget {
        background-color: #FFFFFF;
        /* Banded rows, so a list six hundred long reads as rows rather than a
           wall of text. Only takes effect where the view asks for it with
           setAlternatingRowColors. */
        alternate-background-color: #F6F8FA;
        border: 1px solid #E5E7EB;
        border-radius: 8px;
        font-family: 'Segoe UI', Arial, sans-serif;
        font-size: 12px;
        outline: none;
        padding: 4px;
    }
    QListWidget::item {
        border-radius: 6px;
        /* A margin, not just padding: without it two chosen rows share an edge
           and read as one block rather than two choices. */
        margin: 3px 2px;
        padding: 0px 10px;
        color: #1F1F1F;
        /* Reserved, not decoration: hover and selection both draw a border,
           and without the space held open the text shifts a pixel when the
           mouse arrives. Deliberately NO background here, which would paint
           over the banding. */
        border: 1px solid transparent;
    }
    /* An outline rather than another wash. The bands already own the
       background, and a second colour on top of them read as noise. */
    QListWidget::item:hover { border: 1px solid #55BDBD; }
    QListWidget::item:selected {
        background-color: #CCF3EF;
        color: #0F766E;
        border: 1px solid #55BDBD;
    }
"""

# The batch import strip along the bottom of each import dialog. Thin and
# quiet: it reports on work the operator started and already expects, so it
# should not be the loudest thing on screen.
IMPORT_PROGRESS_LABEL_STYLE = "font-size: 12px; color: #64748B;"
IMPORT_PROGRESS_STYLE = """
    QProgressBar {
        background-color: #E5E7EB;
        border: none;
        border-radius: 4px;
    }
    QProgressBar::chunk {
        background-color: #14B8A6;
        border-radius: 4px;
    }
"""

# Small enough to sit inside a table row without stretching it.
IMAGE_BUTTON_STYLE = """
    QPushButton {
        background-color: #F0FDFA;
        color: #0F766E;
        border: 1px solid #99E0DA;
        border-radius: 5px;
        padding: 1px 8px;
        font-family: 'Segoe UI', Arial, sans-serif;
        font-size: 11px;
        font-weight: 600;
    }
    QPushButton:hover { background-color: #CCF3EF; border-color: #55BDBD; }
    QPushButton:pressed { background-color: #99E0DA; }
"""

# The same button when the folder cannot be found on this machine. Still
# clickable: pressing it explains what is wrong, which is more use than a dead
# control, and the row is not broken, only unreachable from here.
# One size for every View button, so a taller row cannot stretch its button and
# put it out of line with the rest of the column. The height is what a cell
# actually leaves: a 30px row less the 6px top and bottom of COMPACT_TABLE_STYLE
# and the gridline. Anything larger is drawn clipped at the bottom.
IMAGE_BUTTON_WIDTH = 62
IMAGE_BUTTON_HEIGHT = 17

IMAGE_BUTTON_MISSING_STYLE = """
    QPushButton {
        background-color: #FEF2F2;
        color: #B91C1C;
        border: 1px solid #FCA5A5;
        border-radius: 5px;
        padding: 1px 8px;
        font-family: 'Segoe UI', Arial, sans-serif;
        font-size: 11px;
        font-weight: 600;
    }
    QPushButton:hover { background-color: #FEE2E2; border-color: #F87171; }
    QPushButton:pressed { background-color: #FECACA; }
"""


def type_label(electrode_type: str) -> str:
    """``'CATHODE'`` -> ``'Cathodes'``, the name on the sub tab."""
    key = str(electrode_type).strip().upper()
    return TYPE_LABELS.get(key, key.title())


class QuantityNames:
    """Names and rounding for one layout's columns.

    Built once per electrode type. Everything that has to turn a stored column
    into a header, or a value into text, goes through here, so anodes and
    cathodes stay consistent without sharing a hardcoded list.
    """

    def __init__(self, layout: list[str] | None = None) -> None:
        # Longest base name first, so ``ni_pct`` is never mistaken for a suffix
        # of another quantity's column.
        self._by_column: list[tuple[str, Quantity]] = sorted(
            ((q.column, q) for q in quantities(layout)),
            key=lambda pair: len(pair[0]), reverse=True)

    def split(self, column: str) -> tuple[Quantity, str] | None:
        """``'ni_pct_2'`` -> ``(Quantity('Ni'), '2')``, ``'ni_s_avg'`` ->
        ``(Quantity('Ni:S'), 'avg')``. None when it is not a quantity column."""
        text = str(column)
        for base, quantity in self._by_column:
            if text == base:
                return quantity, ""
            if text.startswith(f"{base}_"):
                return quantity, text[len(base) + 1:]
        return None

    def display_name(self, column: str) -> str:
        """The header an operator sees for one stored column."""
        text = str(column)
        if text in COLUMN_DISPLAY_NAMES:
            return COLUMN_DISPLAY_NAMES[text]
        parts = self.split(text)
        if parts is None:
            return text
        quantity, suffix = parts
        if suffix == ELECTRODE_AVERAGE_SUFFIX:
            # Averaged over every sample of the electrode rather than over the
            # points of this one. The sheet calls it "Ni:S average per
            # electrodes"; the same figure repeats down an electrode's samples.
            return f"{quantity.name} per electrode"
        # A ratio has no point columns, so its average needs no qualifier and
        # the header stays as narrow as the numbers under it.
        if quantity.is_ratio:
            return quantity.name
        if suffix in ("", "avg"):
            return f"{quantity.name} avg" if suffix else quantity.name
        return f"{quantity.name} {suffix}"

    def decimals(self, column: str) -> int | None:
        """How many decimals a column reads to, None when it is not a number."""
        text = str(column)
        if text == str(SemEdxColumns.MEASUREMENT):
            return 0
        parts = self.split(text)
        if parts is None:
            return None
        return RATIO_DECIMALS if parts[0].is_ratio else ELEMENT_DECIMALS

    def is_editable(self, column: str) -> bool:
        """Whether a figure in this column can be corrected by hand.

        A percentage was read off the instrument and can be mistyped. A ratio
        is arithmetic over two of them, so it has no value of its own to
        correct: fixing the percentage fixes the ratio.
        """
        parts = self.split(column)
        return bool(parts and parts[1] == "" and not parts[0].is_ratio)

    def is_average(self, column: str) -> bool:
        """True for the figure an operator reads first, which is set in bold."""
        parts = self.split(column)
        return bool(parts and (parts[1] == "avg" or parts[0].is_ratio))

    def format_frame(self, df: pd.DataFrame) -> pd.DataFrame:
        """Round every numeric column of a frame for display."""
        out = df.copy()
        for column in out.columns:
            decimals = self.decimals(str(column))
            if decimals is not None:
                out[column] = out[column].map(
                    lambda v, d=decimals: format_number(v, d))
        return out

    def to_display_frame(self, df: pd.DataFrame) -> pd.DataFrame:
        """Round the numbers and relabel the headers."""
        out = self.format_frame(df)
        out.columns = [self.display_name(str(c)) for c in out.columns]
        return out


def format_number(value, decimals: int) -> str:
    """A measured value as text, with blanks reading as an em dash."""
    number = pd.to_numeric(value, errors="coerce")
    return EMPTY if pd.isna(number) else f"{float(number):.{decimals}f}"


def _as_number(text: str) -> float | None:
    """A typed figure as a number, or None when it is not one.

    A comma is accepted as the decimal separator: the operators are Danish,
    Hungarian and Polish, and every one of those keyboards produces one.
    """
    cleaned = str(text).strip().replace(",", ".")
    if not cleaned or cleaned == EMPTY:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _describe_types(types: dict[str, str], electrodes: list[str]) -> str:
    """``'Cathode'`` for one folder, ``'B1 cathode, B2 anode'`` for a mixed one.

    Empty when nothing was resolved, so the summary drops the row rather than
    showing a label with nothing after it.
    """
    known = [(e, types.get(e, "")) for e in dict.fromkeys(electrodes)]
    known = [(e, t) for e, t in known if t]
    if not known:
        return ""

    distinct = {t for _e, t in known}
    if len(distinct) == 1:
        return type_label(distinct.pop()).rstrip("s")
    return ", ".join(f"{e} {type_label(t).rstrip('s').lower()}" for e, t in known)


class ImportCancelled(Exception):
    """Raised out of a prompt when the operator stops the import.

    Thrown rather than returned so it unwinds the whole folder: the backend
    writes nothing until every question is answered, so a cancel part way
    through leaves that folder untouched instead of half filed.
    """


class _NoDismiss(QObject):
    """Eats Escape and the window close, so a question has to be answered.

    Closing these used to count as an answer: the capture picker fell back to
    its filename guess and the summary threw the operator away. Now the only
    ways out are answering or the Cancel in the footer, which is explicit.
    """

    def eventFilter(self, _watched, event) -> bool:  # noqa: N802 (Qt naming)
        if event.type() == QEvent.Type.Close:
            event.ignore()
            return True
        return (event.type() == QEvent.Type.KeyPress
                and event.key() == Qt.Key.Key_Escape)


def import_footer(done: int, total: int, on_cancel) -> QWidget:
    """The strip along the bottom of every dialog an import raises.

    Carries the Cancel, always, because nothing else can dismiss these now.
    The count and the bar come along only for a batch: reporting "1 of 1" says
    nothing worth the room.
    """
    strip = QWidget()
    strip.setObjectName("importFooter")
    # Scoped by name, or the rule cascades onto the button inside it.
    strip.setStyleSheet(
        "#importFooter { border-top: 1px solid #E5E7EB; background: transparent; }")
    row = QHBoxLayout(strip)
    row.setSpacing(10)

    if total > 1 and done > 0:
        label = QLabel(f"Importing {done} of {total}")
        label.setStyleSheet(IMPORT_PROGRESS_LABEL_STYLE)
        bar = QProgressBar()
        bar.setFixedHeight(8)
        bar.setTextVisible(False)
        bar.setRange(0, total)
        bar.setValue(done)
        bar.setStyleSheet(IMPORT_PROGRESS_STYLE)
        row.addWidget(label, 0)
        row.addWidget(bar, 1)
    else:
        row.addStretch(1)

    cancel = QPushButton("Cancel import")
    cancel.setMinimumHeight(30)
    cancel.setStyleSheet(SECONDARY_BUTTON_STYLE)
    cancel.clicked.connect(on_cancel)
    row.addWidget(cancel, 0)
    return strip


@contextmanager
def asking():
    """Put the busy cursor away for as long as a dialog is asking something.

    An import runs under a wait cursor, and it calls back into the GUI to ask
    which type an electrode is and which captures belong to a sample. Without
    this the operator reads a question with a spinner on the pointer, which
    reads as a window that has hung rather than one waiting for them.

    The shape is remembered rather than assumed, so this restores whatever was
    actually set instead of forcing a wait cursor onto a caller that had none.
    """
    cursor = QApplication.overrideCursor()
    shape = cursor.shape() if cursor is not None else None
    if shape is not None:
        QApplication.restoreOverrideCursor()
    try:
        yield
    finally:
        if shape is not None:
            QApplication.setOverrideCursor(shape)


def report(parent, title: str, lines: list[str], error: bool = False) -> None:
    """One place for every message this tab shows.

    No count above the rows: they read as a short report, not as a list of
    faults to be tallied.
    """
    ValidationErrorScrollDialog(
        parent=parent, errors=lines or ["Nothing to report."], title=title,
        header_color="#EF4444" if error else "#14B8A6",
        icon="⚠️" if error else "✅", subtitle_noun="").exec()


class ImagePickerDialog(QDialog):
    """Which captures in a folder belong to the sample being imported.

    A list rather than a gallery of thumbnails: one folder can hold every
    position mapped on an electrode, and fifteen previews of the same grey mesh
    take a while to decode and tell you nothing a filename does not. Clicking a
    row anywhere toggles it, and the count is always on screen.
    """

    def __init__(self, parent=None, electrode: str = "", position: str = "",
                 images: list | None = None, preselected=None) -> None:
        super().__init__(parent)
        self._paths = [Path(p) for p in (images or [])]
        self.chosen: list[str] = []

        self.setWindowTitle(f"{electrode}, {position}")
        self.setMinimumSize(560, 480)
        self.setStyleSheet("QDialog { background-color: #FFFFFF; }")

        caption = QLabel(
            f"Which captures belong to <b>{electrode} {position}</b>?")
        caption.setTextFormat(Qt.TextFormat.RichText)
        caption.setStyleSheet("font-size: 13px;")

        self.list = QListWidget()
        # Multi rather than extended: a plain click toggles one row, which is
        # what a picker needs. Extended would clear the rest on every click.
        self.list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        self.list.setStyleSheet(PICKER_LIST_STYLE + SLIM_SCROLLBAR_STYLE)
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setAlternatingRowColors(True)

        # Nothing chosen to begin with. Guessing from the filenames and
        # pre-selecting would be taken as read, and the guess is wrong often
        # enough that it is worth making someone look.
        #
        # An earlier ANSWER is different from a guess. Coming back through
        # "Choose captures again", what was ticked before is ticked again, so
        # correcting one row does not mean picking the other four afresh.
        already = {str(name).strip().lower() for name in (preselected or [])}
        for number, path in enumerate(self._paths, start=1):
            item = QListWidgetItem(f"{number}     {path.name}")
            item.setData(Qt.ItemDataRole.UserRole, path.name)
            item.setSizeHint(QSize(0, 34))
            self.list.addItem(item)
            if path.name.lower() in already:
                item.setSelected(True)

        self.counter = QLabel()
        self.counter.setStyleSheet("color: #6B6B6B; font-size: 12px; font-weight: 600;")
        self.list.itemSelectionChanged.connect(self._update_counter)
        self._update_counter()

        select_all = QPushButton("Select all")
        select_all.setMinimumHeight(38)
        select_all.setStyleSheet(SECONDARY_BUTTON_STYLE)
        select_all.clicked.connect(self.list.selectAll)

        select_none = QPushButton("Select none")
        select_none.setMinimumHeight(38)
        select_none.setStyleSheet(SECONDARY_BUTTON_STYLE)
        select_none.clicked.connect(self.list.clearSelection)

        use_button = QPushButton("Use these")
        use_button.setMinimumHeight(38)
        use_button.setMinimumWidth(130)
        use_button.setStyleSheet(PRIMARY_BUTTON_STYLE)
        apply_modern_shadow(use_button)
        use_button.clicked.connect(self._on_accept)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addWidget(select_none, 0)
        buttons.addWidget(select_all, 0)
        buttons.addStretch(1)
        buttons.addWidget(self.counter, 0)
        buttons.addSpacing(10)
        buttons.addWidget(use_button, 0)

        layout = QVBoxLayout()
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)
        layout.addWidget(caption, 0)
        layout.addWidget(self.list, 1)
        layout.addLayout(buttons, 0)
        self.setLayout(layout)

    def _update_counter(self) -> None:
        chosen = len(self.list.selectedItems())
        self.counter.setText(f"{chosen} of {self.list.count()} chosen")

    def _set_all(self, checked: bool) -> None:
        """Kept for callers that drove the old checkbox gallery."""
        self.list.selectAll() if checked else self.list.clearSelection()

    def _on_accept(self) -> None:
        # In list order rather than click order, so the captures stay in the
        # order they were taken.
        chosen = [
            self.list.item(row).data(Qt.ItemDataRole.UserRole)
            for row in range(self.list.count())
            if self.list.item(row).isSelected()]

        # Nothing ticked is a plausible mistake rather than a plausible answer,
        # since the sample would then be imported with no captures at all and
        # nothing on the tab would say why.
        if not chosen and self.list.count():
            confirm = ConfirmationDialog(
                parent=self, title="No captures chosen",
                message=("You have not chosen any captures. The sample will be "
                         "imported with no images, and the View button will "
                         "have nothing to show.<br><br>Import it anyway?"),
                yes_text="Import with none", no_text="Go back")
            if confirm.exec() != QDialog.DialogCode.Accepted:
                return

        self.chosen = chosen
        self.accept()


class ManualSampleDialog(QDialog):
    """Type in a sample the parser could not read.

    A result folder whose reports are malformed still had a microscope pointed
    at an electrode, and the numbers are on screen in front of the operator.
    Refusing them loses the measurement, not just the file.

    WHICH BOXES APPEAR DEPENDS ON THE ELECTRODE TYPE, the same way the table's
    columns do: Gen 3.1 measures Ni and S, Gen 4.1 adds Co, and its cathodes add
    C and N. Changing the type rebuilds the grid rather than offering boxes for
    quantities that generation never measures.

    RATIOS ARE NOT ASKED FOR. Every ratio is arithmetic over two elements, so a
    typed one could only disagree with them. The sample window does not let one
    be edited either.

    THREE POINTS ARE OFFERED AND THE BLANK ONES ARE DROPPED. Samples run to one,
    two or three points; a row left empty is a point that was never measured and
    is not recorded as one.
    """

    POINTS = 3

    def __init__(self, parent=None, generation: str = "", folder: str = "",
                 electrode: str = "", types: list | None = None,
                 elements_for=None, images: list | None = None,
                 folder_path: str = "", generations: list | None = None,
                 electrode_type: str = "") -> None:
        super().__init__(parent)
        self.saved: dict | None = None
        self._elements_for = elements_for or (lambda _generation, _type: [])
        self._candidates = list(images or [])
        self._chosen_images: list = []
        # Where the chosen images live. The picked folder when they came from
        # it, and wherever they were browsed to otherwise, since the sample's
        # folder is what its filenames are resolved against later.
        self.image_folder: str = folder_path or ""
        self._inputs: dict = {}

        title = "Type in a sample"
        self.setWindowTitle(title)
        self.setMinimumWidth(640)
        self.setStyleSheet("QDialog { background-color: #FFFFFF; }")

        self.electrode_input = self._field(electrode, "B14349")
        self.position_input = self._field("", "where on the electrode")
        self.zoom_input = self._field("", "e.g. 500x")
        self.date_input = self._field("", "yyyy-mm-dd")

        # Which generation, rather than whichever tab the import was started
        # from: a folder picked from the wrong tab is the likeliest reason a
        # sample is being typed in at all.
        self.generation_combo = NoScrollComboBox()
        self.generation_combo.setMinimumHeight(34)
        self.generation_combo.setStyleSheet(COMBO_BOX_STYLE)
        self.generation_combo.addItems(list(generations or [generation]))
        if generation:
            index = self.generation_combo.findText(generation)
            self.generation_combo.setCurrentIndex(max(0, index))
        self.generation_combo.currentIndexChanged.connect(self._rebuild_grid)

        self.type_combo = NoScrollComboBox()
        self.type_combo.setMinimumHeight(34)
        self.type_combo.setStyleSheet(COMBO_BOX_STYLE)
        for value in (types or []):
            self.type_combo.addItem(type_label(value).rstrip("s"), value)
        # Both lists open on what the operator was already looking at, so the
        # boxes in front of them are the ones they came to fill in.
        if electrode_type:
            self.type_combo.setCurrentIndex(
                max(0, self.type_combo.findData(electrode_type)))
        self.type_combo.currentIndexChanged.connect(self._rebuild_grid)

        form = QGridLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(6)
        top = (("Generation", self.generation_combo), ("Type", self.type_combo),
               ("Electrode", self.electrode_input))
        for column, (caption, widget) in enumerate(top):
            form.addWidget(self._caption(caption), 0, column)
            form.addWidget(widget, 1, column)
        bottom = (("Position", self.position_input), ("Zoom", self.zoom_input),
                  ("Measured on", self.date_input))
        for column, (caption, widget) in enumerate(bottom):
            form.addWidget(self._caption(caption), 2, column)
            form.addWidget(widget, 3, column)

        self.grid_host = QWidget()
        self.grid = QGridLayout()
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(10)
        self.grid.setVerticalSpacing(6)
        self.grid_host.setLayout(self.grid)

        self.images_button = QPushButton("Choose images")
        self.images_button.setMinimumHeight(34)
        self.images_button.setStyleSheet(SECONDARY_BUTTON_STYLE)
        self.images_button.clicked.connect(self._on_choose_images)
        self.images_label = QLabel()
        self.images_label.setStyleSheet("color: #6B6B6B; font-size: 12px;")
        self._show_image_count()

        images_row = QHBoxLayout()
        images_row.setContentsMargins(0, 0, 0, 0)
        images_row.setSpacing(8)
        images_row.addWidget(self.images_button, 0)
        images_row.addWidget(self.images_label, 0)
        images_row.addStretch(1)

        cancel = QPushButton("Cancel")
        cancel.setMinimumHeight(38)
        cancel.setStyleSheet(SECONDARY_BUTTON_STYLE)
        cancel.clicked.connect(self.reject)

        self.save_button = QPushButton("Save")
        self.save_button.setMinimumHeight(38)
        self.save_button.setMinimumWidth(110)
        self.save_button.setStyleSheet(PRIMARY_BUTTON_STYLE)
        apply_modern_shadow(self.save_button)
        self.save_button.clicked.connect(self._on_save)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(cancel, 0)
        buttons.addWidget(self.save_button, 0)

        note = QLabel("Leave a point empty if it was not measured.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #6B6B6B; font-size: 12px;")

        body = QVBoxLayout()
        body.setContentsMargins(24, 18, 24, 18)
        body.setSpacing(12)
        body.addWidget(note)
        body.addLayout(form)
        body.addWidget(self.grid_host)
        body.addLayout(images_row)
        body.addLayout(buttons)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(build_header("\u270e", title, "#14B8A6"), 0)
        layout.addLayout(body, 1)
        self.setLayout(layout)
        self._rebuild_grid()

    # ---- building -----------------------------------------------------
    @staticmethod
    def _caption(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("color: #6B6B6B; font-size: 12px; font-weight: 600;")
        return label

    @staticmethod
    def _field(value: str, placeholder: str) -> QLineEdit:
        field = QLineEdit(value)
        field.setMinimumHeight(34)
        field.setPlaceholderText(placeholder)
        field.setStyleSheet(INPUT_FIELD_STYLE)
        return field

    def electrode_type(self) -> str:
        return str(self.type_combo.currentData() or "")

    def generation(self) -> str:
        return str(self.generation_combo.currentText() or "")

    def _rebuild_grid(self) -> None:
        """One column per element this generation measures on this type."""
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # Unparented first: deleteLater only schedules the delete, and
                # until it runs the old boxes are still children of the host
                # and still on screen, under the new ones.
                widget.setParent(None)
                widget.deleteLater()
        self._inputs = {}

        elements = list(self._elements_for(self.generation(), self.electrode_type()))
        if not elements:
            self.grid.addWidget(
                self._caption("Nothing is configured for this type."), 0, 0)
            return

        self.grid.addWidget(self._caption("Point"), 0, 0)
        for column, symbol in enumerate(elements, start=1):
            self.grid.addWidget(self._caption(f"{symbol} %"), 0, column)
        for row in range(1, self.POINTS + 1):
            self.grid.addWidget(self._caption(str(row)), row, 0)
            for column, symbol in enumerate(elements, start=1):
                field = self._field("", "")
                field.setMinimumWidth(70)
                self._inputs[(row, symbol)] = field
                self.grid.addWidget(field, row, column)

    # ---- images -------------------------------------------------------
    def _on_choose_images(self) -> None:
        """Pick from the folder, or go and find them when it held none.

        A folder the parser could make nothing of often holds no captures where
        they were expected either, so there has to be a way to point at them
        rather than losing them with the report.
        """
        if self._candidates:
            dialog = ImagePickerDialog(
                self, electrode=self.electrode_input.text().strip(),
                position=self.position_input.text().strip(),
                images=self._candidates)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self._chosen_images = list(dialog.chosen or [])
                self._show_image_count()
            return

        chosen, _filter = QFileDialog.getOpenFileNames(
            self, "Where are the images?", self.image_folder,
            "Images (*.tif *.tiff *.png *.jpg *.jpeg);;All files (*)")
        if not chosen:
            return
        # The sample's folder is what its filenames are resolved against, so it
        # follows the images rather than the folder that was picked first.
        self.image_folder = str(Path(chosen[0]).parent)
        self._chosen_images = [Path(path).name for path in chosen]
        self._show_image_count()

    def _show_image_count(self) -> None:
        chosen = len(self._chosen_images)
        if not self._candidates:
            self.images_button.setText("Find the images")
            self.images_label.setText(f"{chosen} chosen" if chosen else "")
            return
        self.images_label.setText(
            f"{chosen} of {len(self._candidates)} chosen" if chosen
            else f"{len(self._candidates)} in the folder, none chosen yet")

    # ---- saving -------------------------------------------------------
    def _readings(self):
        """``{point: {symbol: value}}`` and whatever could not be read."""
        readings: dict = {}
        problems: list = []
        for (point, symbol), field in self._inputs.items():
            text = field.text().strip().replace(",", ".")
            if not text or text == EMPTY:
                continue        # never taken, the same as leaving it blank
            try:
                readings.setdefault(point, {})[symbol] = float(text)
            except ValueError:
                problems.append(f"Point {point}, {symbol} is not a number.")
        return readings, problems

    def _on_save(self) -> None:
        readings, problems = self._readings()
        if not self.electrode_input.text().strip():
            problems.append("An electrode name is needed.")
        if not self.position_input.text().strip():
            problems.append("A position is needed.")
        if not readings:
            problems.append("No measurements were typed in.")
        if problems:
            report(self, "That cannot be saved yet", sorted(problems), error=True)
            return

        # Asked rather than blocked: a sample with no captures is a real thing,
        # and forgetting to attach them is easy to do and hard to notice later.
        if not self._chosen_images:
            ask = ConfirmationDialog(
                parent=self, title="No images chosen",
                message="No images were chosen. Save anyway?",
                yes_text="Save anyway", no_text="Go back")
            if ask.exec() != ConfirmationDialog.DialogCode.Accepted:
                return

        self.saved = {
            "generation": self.generation(),
            "electrode": self.electrode_input.text().strip(),
            "electrode_type": self.electrode_type(),
            "position": self.position_input.text().strip(),
            "zoom": self.zoom_input.text().strip(),
            "date": self.date_input.text().strip(),
            "readings": readings,
            "images": list(self._chosen_images),
            "folder": self.image_folder,
        }
        self.accept()


IMPORT_PICKER_TITLE = "Add SEM results"
# One row of the picker names one electrode, so these are singular, unlike
# TYPE_LABELS above which names a tab holding many. Anything else is shown as
# it is stored rather than mangled by a general rule: MM1 is not "Mm1".
ROW_TYPE_LABELS = {"ANODE": "Anode", "CATHODE": "Cathode"}
# Where the type and the spot are kept on a row, beside the folder path
# already on UserRole.
TYPE_ROLE = Qt.ItemDataRole.UserRole + 1
LOCATION_ROLE = Qt.ItemDataRole.UserRole + 2
# The spot exactly as the report named it, which is what the import filters on.
SPOT_ROLE = Qt.ItemDataRole.UserRole + 3


class _RowTypeDelegate(QStyledItemDelegate):
    """Draws the spot and the electrode type along a picker row.

    Two more columns in all but name. Painted rather than folded into the item
    text because the font is proportional, so padding with spaces would leave
    them in a ragged line down the list.
    """

    COLOR = "#64748B"
    INSET = 14
    # Where the spot starts, as a fraction of the row. Markings are short and
    # of similar length, so a fixed stop lines them all up.
    SPOT_AT = 0.42

    def paint(self, painter, option, index) -> None:
        super().paint(painter, option, index)
        spot = str(index.data(LOCATION_ROLE) or "")
        kind = str(index.data(TYPE_ROLE) or "")
        if not spot and not kind:
            return
        painter.save()
        painter.setPen(QColor(self.COLOR))
        if spot:
            painter.drawText(
                option.rect.adjusted(int(option.rect.width() * self.SPOT_AT), 0, 0, 0),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                spot)
        if kind:
            painter.drawText(
                option.rect.adjusted(0, 0, -self.INSET, 0),
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                kind)
        painter.restore()


class ImportPickerDialog(QDialog):
    """Which result folders to import, one tab per generation.

    Instead of a file explorer. The library is found the same way opening a
    capture finds it, so nobody has to know where OneDrive put it, and only
    folders the database holds nothing from are listed: what is left to do is
    the point of the window.

    Ticking rows in both tabs at once is allowed. Each folder is imported
    under the generation whose tab it came from, not the tab the button
    happened to be pressed on.
    """

    def __init__(self, parent=None, generations: list[str] | None = None,
                 candidates: dict[str, list] | None = None,
                 current: str = "") -> None:
        super().__init__(parent)
        self.chosen: list[tuple[str, str]] = []
        self.browse = False
        self._lists: dict[str, QListWidget] = {}
        self._counts: dict[str, QLabel] = {}

        self.setWindowTitle(IMPORT_PICKER_TITLE)
        self.setMinimumSize(640, 580)
        self.setStyleSheet("QDialog { background-color: #FFFFFF; }")

        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(build_header("+", IMPORT_PICKER_TITLE, "#14B8A6"))

        body = QVBoxLayout()
        body.setContentsMargins(20, 14, 20, 18)
        body.setSpacing(10)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search")
        self.search.setStyleSheet(INPUT_FIELD_STYLE)
        self.search.textChanged.connect(self._filter)
        body.addWidget(self.search, 0)

        self.tabs = NoWheelTabWidget(fade_ms=0)
        self.tabs.setStyleSheet(PLOT_TABS_STYLE)
        for generation in (generations or []):
            rows = (candidates or {}).get(generation) or []
            self.tabs.addTab(self._page(generation, rows), generation)
        body.addWidget(self.tabs, 1)

        buttons = QHBoxLayout()
        browse = QPushButton("Browse instead")
        browse.setMinimumHeight(34)
        browse.setStyleSheet(SECONDARY_BUTTON_STYLE)
        browse.clicked.connect(self._on_browse)
        cancel = QPushButton("Cancel")
        cancel.setMinimumHeight(34)
        cancel.setStyleSheet(SECONDARY_BUTTON_STYLE)
        cancel.clicked.connect(self.reject)
        self.import_button = QPushButton("Import")
        self.import_button.setMinimumHeight(34)
        self.import_button.setStyleSheet(PRIMARY_BUTTON_STYLE)
        self.import_button.setEnabled(False)
        self.import_button.clicked.connect(self._on_import)
        buttons.addWidget(browse, 0)
        buttons.addStretch(1)
        buttons.addWidget(cancel, 0)
        buttons.addWidget(self.import_button, 0)
        body.addLayout(buttons)

        outer.addLayout(body, 1)
        self.setLayout(outer)

        # Open on the tab they were already looking at.
        for index in range(self.tabs.count()):
            if self.tabs.tabText(index) == str(current or "").strip():
                self.tabs.setCurrentIndex(index)
                break
        self._update_counts()

    def _page(self, generation: str, rows: list) -> QWidget:
        """One generation's list of folders still to import."""
        page = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(8)

        count = QLabel()
        count.setStyleSheet(
            "font-size: 12px; color: #64748B; padding: 2px 0 0 6px;")

        listing = QListWidget()
        # Multi rather than extended, so a plain click toggles one row instead
        # of clearing every other, the same as the capture picker.
        listing.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        listing.setStyleSheet(PICKER_LIST_STYLE + SLIM_SCROLLBAR_STYLE)
        listing.setFrameShape(QFrame.Shape.NoFrame)
        listing.setAlternatingRowColors(True)
        listing.setItemDelegate(_RowTypeDelegate(listing))
        listing.itemSelectionChanged.connect(self._update_counts)
        for row in rows:
            item = QListWidgetItem(str(row.get("name", "")))
            item.setData(Qt.ItemDataRole.UserRole, str(row.get("path", "")))
            stored = str(row.get("type", "") or "").strip()
            item.setData(TYPE_ROLE, ROW_TYPE_LABELS.get(stored.upper(), stored))
            # Lower cased for the eye only. What the import is asked for is the
            # spot as the report named it, kept on the item below.
            spot = str(row.get("position", "") or "").strip()
            item.setData(LOCATION_ROLE, spot.lower())
            item.setData(SPOT_ROLE, spot)
            item.setSizeHint(QSize(0, 34))
            listing.addItem(item)
        if not rows:
            empty = QListWidgetItem("Everything here is already imported.")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            listing.addItem(empty)

        layout.addWidget(count, 0)
        layout.addWidget(listing, 1)
        page.setLayout(layout)
        self._lists[generation] = listing
        self._counts[generation] = count
        return page

    def _filter(self) -> None:
        """Show only rows matching the search, in every tab."""
        needle = self.search.text().strip().lower()
        for listing in self._lists.values():
            for index in range(listing.count()):
                item = listing.item(index)
                if item.flags() == Qt.ItemFlag.NoItemFlags:
                    continue
                # The type counts as part of the row, so "anode" narrows the
                # list even though it is painted rather than in the text.
                haystack = (f"{item.text()} {item.data(TYPE_ROLE) or ''} "
                            f"{item.data(LOCATION_ROLE) or ''}").lower()
                hide = bool(needle) and needle not in haystack
                # Dropping the selection with the row, so nothing is imported
                # that the operator has filtered out of sight.
                if hide and item.isSelected():
                    item.setSelected(False)
                item.setHidden(hide)

    def _update_counts(self) -> None:
        """Keep each tab's caption and the Import button honest."""
        total = 0
        for generation, listing in self._lists.items():
            rows = sum(1 for index in range(listing.count())
                       if listing.item(index).flags() != Qt.ItemFlag.NoItemFlags)
            picked = len(listing.selectedItems())
            total += picked
            caption = f"{rows} not imported"
            if picked:
                caption += f", {picked} selected"
            self._counts[generation].setText(caption)
        self.import_button.setEnabled(total > 0)
        self.import_button.setText(f"Import {total}" if total else "Import")

    def _on_browse(self) -> None:
        """Fall back to the file explorer, for a folder filed somewhere odd."""
        self.browse = True
        self.accept()

    def _on_import(self) -> None:
        """Every ticked row, from every tab, with the generation it came from."""
        for generation, listing in self._lists.items():
            for item in listing.selectedItems():
                path = str(item.data(Qt.ItemDataRole.UserRole) or "")
                if path:
                    self.chosen.append(
                        (generation, path, str(item.data(SPOT_ROLE) or "")))
        self.accept()


class ImportSummaryDialog(QDialog):
    """What an import did, and the one field it could not fill in.

    Nothing in the export names who ran the microscope, so the operator is
    asked for here rather than left for someone to remember later.
    """

    def __init__(self, parent=None, title: str = "", lines: list[str] | None = None,
                 ask_operator: bool = False, error: bool = False,
                 zoom_hint: str = "", allow_back: bool = False,
                 operator_hint: str = "", note_hint: str = "") -> None:
        super().__init__(parent)
        self.operator: str = ""
        self.zoom: str = ""
        self.note: str = ""
        # Set when the operator wants to answer the capture question again.
        self.back = False

        self.setWindowTitle(title)
        self.setMinimumWidth(560)
        self.setStyleSheet("QDialog { background-color: #FFFFFF; }")

        colour = "#EF4444" if error else "#14B8A6"
        rows = QWidget()
        rows_layout = QVBoxLayout()
        rows_layout.setContentsMargins(0, 0, 0, 0)
        rows_layout.setSpacing(6)
        for line in (lines or ["Nothing to report."]):
            row = QLabel(f"•  {line}")
            row.setWordWrap(True)
            row.setStyleSheet(
                "color: #1F1F1F; font-size: 13px; background-color: #F9FAFB;"
                "border: 1px solid #E4E4E4; border-radius: 6px; padding: 8px 10px;")
            rows_layout.addWidget(row)
        rows_layout.addStretch(1)
        rows.setLayout(rows_layout)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { border: none; }" + SLIM_SCROLLBAR_STYLE)
        scroll.setWidget(rows)
        scroll.setMinimumHeight(110)
        scroll.setMaximumHeight(320)

        self.operator_input: QLineEdit | None = None
        self.zoom_input: QLineEdit | None = None
        self.note_input: QLineEdit | None = None
        body = QVBoxLayout()
        body.setContentsMargins(20, 16, 20, 16)
        body.setSpacing(12)
        body.addWidget(scroll, 1)

        if ask_operator:
            self.operator_input = self._field("", self._on_accept)
            # Kept across a trip back to the captures: retyping a name and a
            # note because one capture was wrong is a poor trade.
            self.operator_input.setText(operator_hint)
            # Filled in with what the import read off the image sidecars, as
            # real text rather than a hint: a placeholder disappears the moment
            # anything is typed, taking the answer with it.
            self.zoom_input = self._field("", self._on_accept)
            self.zoom_input.setText(zoom_hint)
            self.note_input = self._field("", self._on_accept)
            self.note_input.setText(note_hint)

            fields = QGridLayout()
            fields.setContentsMargins(0, 0, 0, 0)
            fields.setHorizontalSpacing(10)
            fields.setVerticalSpacing(4)
            for column, (label, widget) in enumerate((
                    ("Operator name", self.operator_input),
                    ("Zoom", self.zoom_input))):
                fields.addWidget(self._caption(label), 0, column)
                fields.addWidget(widget, 1, column)
            fields.addWidget(self._caption("Note"), 2, 0, 1, 2)
            fields.addWidget(self.note_input, 3, 0, 1, 2)
            body.addLayout(fields)

        self.ok_button = QPushButton("OK")
        self.ok_button.setMinimumHeight(38)
        self.ok_button.setMinimumWidth(120)
        self.ok_button.setStyleSheet(PRIMARY_BUTTON_STYLE)
        apply_modern_shadow(self.ok_button)
        self.ok_button.clicked.connect(self._on_accept)

        if self.operator_input is not None:
            # Held shut until a name is typed: the operator is the one thing
            # the export cannot supply, and it is far easier to enter now than
            # to notice the blank column later.
            # From what is in the box, not simply shut: coming back from the
            # captures the name is already there, and a disabled OK beside a
            # filled-in field reads as the window being stuck.
            self.ok_button.setEnabled(bool(self.operator_input.text().strip()))
            self.ok_button.setToolTip("Enter the operator name first")
            self.operator_input.textChanged.connect(
                lambda text: self.ok_button.setEnabled(bool(text.strip())))

        buttons = QHBoxLayout()
        if allow_back:
            # The captures are chosen several dialogs earlier and only named
            # here, so this is the first place a wrong choice is visible.
            #
            # Left, and away from OK: going back is the opposite direction to
            # finishing, and the two should not sit under the same thumb.
            back_button = QPushButton("Choose captures again")
            back_button.setMinimumHeight(38)
            back_button.setStyleSheet(SECONDARY_BUTTON_STYLE)
            back_button.clicked.connect(self._on_back)
            buttons.addWidget(back_button, 0)
        buttons.addStretch(1)
        buttons.addWidget(self.ok_button, 0)
        body.addLayout(buttons, 0)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(build_header("⚠️" if error else "✅", title, colour), 0)
        layout.addLayout(body, 1)
        self.setLayout(layout)

        if self.operator_input is not None:
            self.operator_input.setFocus()

    def _on_back(self) -> None:
        """Answer nothing, and ask for the folder to be read again.

        What is in the boxes is still taken, so it can be put back when this
        window comes round again. Unlike OK, an empty one is fine here.
        """
        if self.operator_input is not None:
            self.operator = self.operator_input.text().strip()
            self.zoom = self.zoom_input.text().strip() if self.zoom_input else ""
            self.note = self.note_input.text().strip() if self.note_input else ""
        self.back = True
        self.done(QDialog.DialogCode.Rejected)

    @staticmethod
    def _caption(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("color: #6B6B6B; font-size: 12px; font-weight: 600;")
        return label

    @staticmethod
    def _field(placeholder: str, on_return) -> QLineEdit:
        field = QLineEdit()
        field.setMinimumHeight(34)
        field.setPlaceholderText(placeholder)
        field.setStyleSheet(INPUT_FIELD_STYLE)
        field.returnPressed.connect(on_return)
        return field

    def _on_accept(self) -> None:
        if self.operator_input is not None:
            self.operator = self.operator_input.text().strip()
            if not self.operator:
                return          # Enter pressed in an empty field.
            # Both optional: an empty one is not sent, so the zoom the import
            # read stays as it is rather than being overwritten with nothing.
            self.zoom = self.zoom_input.text().strip() if self.zoom_input else ""
            self.note = self.note_input.text().strip() if self.note_input else ""
        self.accept()


class ElectrodeTypeDialog(QDialog):
    """Asks which type an electrode is, when the database has not heard of it.

    Only ever reached for a marking that is not in dbo.MetaData, so it is a
    rare prompt rather than a step in the normal import.
    """

    def __init__(self, parent=None, electrode: str = "",
                 choices: list[str] | None = None) -> None:
        super().__init__(parent)
        self.chosen: str = ""

        self.setWindowTitle(f"Which type is {electrode}?")
        self.setMinimumWidth(420)
        self.setStyleSheet("QDialog { background-color: #FFFFFF; }")

        message = QLabel(
            f"<b>{electrode}</b> is not in the database, so its type is unknown."
            f"<br/>Choose where its measurements belong.")
        message.setTextFormat(Qt.TextFormat.RichText)
        message.setWordWrap(True)
        message.setStyleSheet("font-size: 12px;")

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        cancel_button = QPushButton("Skip this electrode")
        cancel_button.setMinimumHeight(38)
        cancel_button.setStyleSheet(SECONDARY_BUTTON_STYLE)
        cancel_button.clicked.connect(self.reject)
        buttons.addWidget(cancel_button, 0)
        buttons.addStretch(1)

        for electrode_type in (choices or []):
            button = QPushButton(type_label(electrode_type).rstrip("s"))
            button.setMinimumHeight(38)
            button.setMinimumWidth(110)
            button.setStyleSheet(PRIMARY_BUTTON_STYLE)
            apply_modern_shadow(button)
            button.clicked.connect(
                lambda _c=False, value=electrode_type: self._choose(value))
            buttons.addWidget(button, 0)

        layout = QVBoxLayout()
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(14)
        layout.addWidget(message, 0)
        layout.addLayout(buttons, 0)
        self.setLayout(layout)

    def _choose(self, electrode_type: str) -> None:
        self.chosen = electrode_type
        self.accept()


class SemImageViewerDialog(QDialog):
    """Steps through one sample's captures, one at a time.

    The images are 2560x1920 TIFs on a share, so each is loaded when it is
    first shown and then kept, rather than loading all of them up front.
    """

    def __init__(self, parent=None, title: str = "", images: list | None = None) -> None:
        super().__init__(parent)
        self._images = [Path(p) for p in (images or [])]
        self._index = 0
        self._loaded: dict[int, QPixmap] = {}

        self.setWindowTitle(title or "SEM images")
        self.setMinimumSize(760, 640)
        self.setStyleSheet("QDialog { background-color: #FFFFFF; }")

        self.view = QLabel()
        self.view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.view.setMinimumHeight(460)
        self.view.setStyleSheet(
            "QLabel { background-color: #111827; border-radius: 8px; color: #E5E7EB; }")

        self.caption = QLabel()
        self.caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.caption.setStyleSheet("color: #6B6B6B; font-size: 12px;")

        self.previous_button = QPushButton("Previous")
        self.next_button = QPushButton("Next")
        for button, step in ((self.previous_button, -1), (self.next_button, 1)):
            button.setMinimumHeight(38)
            button.setMinimumWidth(110)
            button.setStyleSheet(SECONDARY_BUTTON_STYLE)
            button.clicked.connect(lambda _c, s=step: self._step(s))

        close_button = QPushButton("Close")
        close_button.setMinimumHeight(38)
        close_button.setMinimumWidth(110)
        close_button.setStyleSheet(PRIMARY_BUTTON_STYLE)
        apply_modern_shadow(close_button)
        close_button.clicked.connect(self.accept)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        controls.addWidget(self.previous_button, 0)
        controls.addWidget(self.next_button, 0)
        controls.addStretch(1)
        controls.addWidget(close_button, 0)

        layout = QVBoxLayout()
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        layout.addWidget(self.view, 1)
        layout.addWidget(self.caption, 0)
        layout.addLayout(controls, 0)
        self.setLayout(layout)

        self._show_current()

    def _step(self, step: int) -> None:
        """Move by one, stopping at the ends rather than wrapping.

        Three captures is few enough that wrapping just loses your place; the
        buttons say where you are instead.
        """
        if not self._images:
            return
        self._index = max(0, min(self._index + step, len(self._images) - 1))
        self._show_current()

    def _pixmap(self, index: int) -> QPixmap:
        if index not in self._loaded:
            self._loaded[index] = QPixmap(str(self._images[index]))
        return self._loaded[index]

    def _show_current(self) -> None:
        total = len(self._images)
        self.previous_button.setEnabled(self._index > 0)
        self.next_button.setEnabled(self._index < total - 1)

        if not total:
            self.view.setText("No images were found for this sample")
            self.caption.setText("")
            return

        pixmap = self._pixmap(self._index)
        if pixmap.isNull():
            self.view.setText(f"{self._images[self._index].name} could not be opened")
        else:
            self.view.setPixmap(pixmap.scaled(
                self.view.size(), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
        self.caption.setText(
            f"{self._index + 1} of {total}   ·   {self._images[self._index].name}")

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        """Re-fit the capture when the window changes size."""
        super().resizeEvent(event)
        if self._images:
            self._show_current()


class SemSampleDialog(QDialog):
    """One sample on its own: its measurements, plus the fields no instrument
    records.

    The Word report carries the composition, the operator and the date; the
    magnification comes off the image sidecar and the electrode type out of the
    database. What is left, and what this form is for, is the note, and any of
    the rest that a folder happened not to contain.
    """

    def __init__(self, parent=None, names: QuantityNames | None = None, on_save=None,
                 electrode: str = "", position: str = "", electrode_type: str = "",
                 operator: str = "", zoom: str = "", note: str = "", date: str = "",
                 measurements: pd.DataFrame | None = None) -> None:
        super().__init__(parent)
        self.values: dict[str, str] = {}
        # ``{point: {element column: value or None}}``, the corrected readings.
        self.measurements: dict[int, dict[str, float | None]] = {}
        # Set instead of the two above when Delete was pressed and confirmed.
        self.delete = False
        # ``() -> bool``, the write and the re-read. Given one, this window
        # stays open and busy until it comes back, so the table behind it is
        # already up to date the moment the window goes.
        self.on_save = on_save
        self._electrode, self._position = electrode, position
        names = names or QuantityNames()

        self.setWindowTitle(f"{electrode}, {position}")
        self.setMinimumWidth(600)
        self.setStyleSheet("QDialog { background-color: #FFFFFF; }")

        muted = "<span style='color:#6B6B6B;'>"
        parts = [f"{muted}Electrode type:</span> <b>{electrode_type or EMPTY}</b>",
                 f"{muted}Sample location:</span> <b>{position or EMPTY}</b>"]
        # Only when there is one. The workbooks record no measurement date at
        # all, so on everything imported from them this read "Measured: —".
        if date:
            parts.append(f"{muted}Measured:</span> <b>{date}</b>")

        header = QLabel("     ".join(parts))
        header.setTextFormat(Qt.TextFormat.RichText)
        header.setStyleSheet("font-size: 12px;")

        self.names = names
        self._detail = (measurements if measurements is not None
                        else pd.DataFrame()).reset_index(drop=True)
        self._build_measurement_table()

        self._inputs: dict[str, QLineEdit] = {}
        form = QGridLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        for row, (column, current) in enumerate((
                (str(SemEdxColumns.ZOOM), zoom),
                (str(SemEdxColumns.OPERATOR), operator),
                (str(SemEdxColumns.NOTE), note))):
            caption = QLabel(COLUMN_DISPLAY_NAMES[column])
            caption.setStyleSheet("color: #6B6B6B; font-size: 12px;")
            field = QLineEdit(current)
            field.setMinimumHeight(32)
            field.setStyleSheet(INPUT_FIELD_STYLE)
            self._inputs[column] = field
            form.addWidget(caption, row, 0)
            form.addWidget(field, row, 1)
        form.setColumnStretch(1, 1)

        save_button = QPushButton("Save")
        save_button.setMinimumHeight(38)
        save_button.setMinimumWidth(110)
        save_button.setStyleSheet(PRIMARY_BUTTON_STYLE)
        apply_modern_shadow(save_button)
        save_button.clicked.connect(self._on_save)

        cancel_button = QPushButton("Cancel")
        cancel_button.setMinimumHeight(38)
        cancel_button.setMinimumWidth(110)
        cancel_button.setStyleSheet(SECONDARY_BUTTON_STYLE)
        cancel_button.clicked.connect(self.reject)

        delete_button = QPushButton("Delete")
        delete_button.setMinimumHeight(38)
        delete_button.setMinimumWidth(110)
        delete_button.setStyleSheet(DANGER_BUTTON_STYLE)
        delete_button.setToolTip(
            f"Delete {electrode} {position} and every measurement on it")
        delete_button.clicked.connect(self._on_delete)

        self.spinner = BusySpinner(self)
        self._buttons = (save_button, cancel_button, delete_button)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        # Away from Save, on the other side of the stretch, so the destructive
        # one is never the button next to the one you meant to press.
        buttons.addWidget(delete_button, 0)
        buttons.addStretch(1)
        buttons.addWidget(self.spinner, 0)
        buttons.addWidget(cancel_button, 0)
        buttons.addWidget(save_button, 0)

        layout = QVBoxLayout()
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)
        layout.addWidget(header, 0)
        layout.addWidget(self.table, 1)
        layout.addLayout(form, 0)
        layout.addLayout(buttons, 0)
        self.setLayout(layout)

    # ------------------------------------------------------------------
    def _build_measurement_table(self) -> None:
        """The points, with the measured percentages open for correction.

        Built by hand rather than through the shared table widget, because
        which cell may be edited depends on what the column *is*: a percentage
        was read off the instrument and can be mistyped, a ratio is arithmetic
        over two of them and has no independent value to correct.
        """
        columns = [str(c) for c in self._detail.columns]
        self.table = QTableWidget(len(self._detail), len(columns))
        self.table.setStyleSheet(COMPACT_TABLE_STYLE)
        self.table.setFrameShape(QFrame.Shape.NoFrame)
        self.table.setHorizontalHeaderLabels(
            [self.names.display_name(c) for c in columns])
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(150)
        self.table.setEditTriggers(
            QTableWidget.EditTrigger.DoubleClicked
            | QTableWidget.EditTrigger.EditKeyPressed
            | QTableWidget.EditTrigger.AnyKeyPressed)
        self.table.horizontalHeader().setSectionResizeMode(
            self.table.horizontalHeader().ResizeMode.Stretch)

        for row in range(len(self._detail)):
            for index, column in enumerate(columns):
                decimals = self.names.decimals(column)
                value = self._detail.iloc[row][column]
                item = QTableWidgetItem(
                    format_number(value, decimals) if decimals is not None
                    else str(value))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if self.names.is_editable(column):
                    item.setBackground(QBrush(QColor(EDITABLE_CELL)))
                else:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    item.setForeground(QBrush(QColor(DERIVED_CELL_TEXT)))
                self.table.setItem(row, index, item)

        self.table.itemChanged.connect(self._on_cell_changed)

    def _on_cell_changed(self, item: QTableWidgetItem) -> None:
        """Recompute that row's ratios, so they never contradict the numbers
        they are made of while the window is open."""
        columns = [str(c) for c in self._detail.columns]
        if not self.names.is_editable(columns[item.column()]):
            return

        row = item.row()
        readings = {}
        for index, column in enumerate(columns):
            if not self.names.is_editable(column):
                continue
            parts = self.names.split(column)
            cell = self.table.item(row, index)
            readings[parts[0].name] = _as_number(cell.text() if cell else "")

        self.table.blockSignals(True)
        for index, column in enumerate(columns):
            parts = self.names.split(column)
            if parts is None or not parts[0].is_ratio:
                continue
            top, bottom = (readings.get(s) for s in parts[0].parts)
            ratio = (top / bottom) if top is not None and bottom else None
            cell = self.table.item(row, index)
            if cell is not None:
                cell.setText(EMPTY if ratio is None
                             else format_number(ratio, RATIO_DECIMALS))
        self.table.blockSignals(False)

    def _read_measurements(self) -> tuple[dict, list[str]]:
        """The edited percentages, and anything that is not a number.

        A blank cell is a reading that was never taken, which is different from
        a nonsense one: the first is stored as missing, the second refuses to
        save so nobody loses a figure to a typo.

        THE PLACEHOLDER COUNTS AS BLANK. A reading that was never taken is
        shown as an em dash, so an untouched cell hands back that dash rather
        than an empty string. Read as a typo, it made a sample with any missing
        reading impossible to save at all, including when the only thing being
        changed was the note.
        """
        columns = [str(c) for c in self._detail.columns]
        point_column = str(SemEdxColumns.MEASUREMENT)
        edited: dict[int, dict[str, float | None]] = {}
        problems: list[str] = []

        for row in range(self.table.rowCount()):
            point = int(pd.to_numeric(
                self._detail.iloc[row].get(point_column, row + 1),
                errors="coerce") or row + 1)
            for index, column in enumerate(columns):
                if not self.names.is_editable(column):
                    continue
                cell = self.table.item(row, index)
                text = (cell.text() if cell else "").strip()
                if not text or text == EMPTY:
                    edited.setdefault(point, {})[column] = None
                    continue
                number = _as_number(text)
                if number is None:
                    problems.append(
                        f"Point {point}, {self.names.display_name(column)}: "
                        f"{text!r} is not a number.")
                else:
                    edited.setdefault(point, {})[column] = number
        return edited, problems

    def _on_save(self) -> None:
        """Nothing is saved unless every corrected figure is a number.

        With an ``on_save`` the window stays up and turns its spinner while the
        write and the re-read happen, and closes only once the table behind it
        holds the new figures. Without one it simply closes, and the caller
        does the work afterwards.
        """
        self.measurements, problems = self._read_measurements()
        if problems:
            self.measurements = {}
            report(self, "Those are not numbers", problems, error=True)
            return
        self.values = {column: field.text().strip()
                       for column, field in self._inputs.items()}
        if not callable(self.on_save):
            self.accept()
            return

        self._set_busy(True)
        try:
            outcome = self.on_save()
        except Exception:
            self._set_busy(False)
            raise
        # None means it was only started, and saved() will finish it. That is
        # the case that lets the spinner turn: this thread goes back to the
        # event loop instead of blocking on a database.
        if outcome is None:
            return
        self.saved(bool(outcome))

    def saved(self, ok: bool) -> None:
        """Finish a save started by ``on_save``. Called on the UI thread.

        On failure the window stays open with everything still typed into it,
        and whatever went wrong has already been reported by the caller.
        """
        self._set_busy(False)
        if ok:
            self.accept()

    def _set_busy(self, busy: bool) -> None:
        """Turn the spinner and shut the buttons, or put both back."""
        for button in self._buttons:
            button.setEnabled(not busy)
        self.spinner.start() if busy else self.spinner.stop()
        # One pass so the spinner and the greyed buttons are on screen before
        # anything else happens, whether or not the work blocks this thread.
        QApplication.processEvents()

    def _on_delete(self) -> None:
        """Delete the whole sample, once. There is no undo on this tab, so the
        count of what goes is spelled out rather than left to be discovered."""
        points = len(self._detail)
        confirm = ConfirmationDialog(
            parent=self, title="Delete sample",
            message=(f"Delete {self._electrode} {self._position} and its "
                     f"{points} measurement{'s' if points != 1 else ''}?<br><br>"
                     f"The captures stay in the result folder, only what was "
                     f"read off them is removed. This cannot be undone."),
            yes_text="Delete", no_text="Keep")
        if confirm.exec() != QDialog.DialogCode.Accepted:
            return

        # Nothing else is carried out: a delete is not also an edit.
        self.values, self.measurements = {}, {}
        self.delete = True
        self.accept()


class SemEdxTypeWidget(QWidget):
    """One electrode type's samples: the filtered table and the import button.

    Carries no title or card of its own, it lives inside the SEM EDX tab's sub
    tab strips.
    """

    # An import or an edit changed the stored measurements; whoever owns the
    # backend re-reads and hands the frames back. Carries the generation, so
    # editing a Gen 4.1 sample does not re-read four hundred Gen 3.1 rows that
    # cannot have changed.
    data_changed = pyqtSignal(str)

    def __init__(self, parent=None, generation: str = "", electrode_type: str = "",
                 layout: list[str] | None = None, on_get_measurements=None,
                 on_update_sample=None, on_update_measurements=None,
                 on_save_sample=None,
                 on_delete_sample=None, on_list_images=None,
                 on_images_available=None) -> None:
        super().__init__(parent)
        self.generation = str(generation).strip()
        self.electrode_type = str(electrode_type).strip().upper()
        self.names = QuantityNames(layout)
        # ``(generation, type, electrode, position) -> DataFrame``.
        self.on_get_measurements = on_get_measurements
        # ``(generation, electrode, position, {column: value}) -> bool``, the
        # fields the whole sample shares.
        self.on_update_sample = on_update_sample
        # ``(generation, electrode, position, {point: {column: value}}) -> bool``,
        # a corrected percentage on one point.
        self.on_update_measurements = on_update_measurements
        # The whole save handed over in one piece, so it can run off the UI
        # thread. Absent, the two callables above are used here instead.
        self.on_save_sample = on_save_sample
        # ``(generation, electrode, position) -> bool``, the whole sample gone.
        self.on_delete_sample = on_delete_sample
        # ``folder -> [image path, ...]``, so the tab never walks a directory.
        self.on_list_images = on_list_images
        # ``folder -> bool``, cheap enough to ask per row while drawing.
        self.on_images_available = on_images_available

        self.table: DataTableWidget | None = None
        # Everything loaded, original column names: the truth the filters work
        # from and the row index to sample mapping works against.
        self.df_source: pd.DataFrame | None = None
        # What is currently rendered (df_source after every active filter).
        self.df_visible: pd.DataFrame | None = None

        self.init_ui()

    # ------------------------------------------------------------------
    # UI initialization
    # ------------------------------------------------------------------
    def init_ui(self) -> None:
        """Filter bar, then the table. Importing belongs to the generation, not
        to one of its types: a folder's type is looked up, never chosen."""
        # No date filter: many samples have no date, the workbook records none.
        self.filter_bar = FilterBarWidget(
            [FilterDropdown(FILTER_POSITION, "Location"),
             FilterDropdown(FILTER_OPERATOR, "Operator")],
            with_date=False, search_placeholder="Search", count_noun="samples")
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
        filter_scroll.setFixedHeight(self.filter_bar.sizeHint().height() + 12)
        filter_scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            + SLIM_SCROLLBAR_STYLE)

        self.table = DataTableWidget(
            # Every measured point gets a column, so they are kept as narrow as
            # a four character number and the table scrolls sideways rather
            # than squeezing them.
            min_column_width=34,
            max_column_width=190,
            enable_alternating_colors=True,
            enable_row_coloring_on_add=False,
            # Newest electrode last, so the table opens scrolled to the bottom,
            # which is the useful end here.
            scroll_to_top_on_load=False,
        )
        self.table.setStyleSheet(COMPACT_TABLE_STYLE)
        # Covers the table until the sheets have been read. Narrower columns
        # here than on the Electrodes tab, since this one holds a point per
        # column and reads as a denser grid.
        self._skeleton = SkeletonOverlay(self.table, columns=9)
        self.table.setFrameShape(QFrame.Shape.NoFrame)
        self.table.setSelectionMode(self.table.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.verticalHeader().setVisible(False)
        # The note is the one free-text column, so it is sized to its full
        # content rather than capped like the numbers. It is the last column,
        # so the width it takes only ever costs horizontal scrolling.
        self.table.set_uncapped_columns({COLUMN_DISPLAY_NAMES[str(SemEdxColumns.NOTE)]})
        # Any width left over is shared out, so the table fills the pane
        # without a column ever being squeezed below its content.
        self.table.set_fill_remaining_width(True)
        # A minimum of 0 means "use the size hint", which includes the header's
        # own minimum plus the (deliberately thick) scrollbar, and that becomes
        # the window's width floor. An explicit minimum lets columns compress.
        self.table.setMinimumWidth(160)
        # The same outline the Electrodes tab uses: a soft one under the
        # pointer, a thicker one on the row that was clicked. This table paints
        # its cells the ordinary way, so the delegate only adds the outline.
        self._row_highlight = RowHighlight()
        # Same wash the Electrodes tab uses when a row changes underneath you.
        self._flasher = None
        self._picked_key: tuple = ()
        self.table.setItemDelegate(
            RowHighlightDelegate(self._row_highlight, self.table))
        self._flasher = RowFlasher(self._row_highlight, self.table, self)
        # The electrode and where the sample came from stay put while the
        # measurements scroll past. Transparent to the mouse, so hovering,
        # picking and double-clicking below still reach the table itself.
        self._frozen = FrozenColumns(self.table)
        self.table.setMouseTracking(True)
        self.table.cellEntered.connect(self._on_cell_entered)
        self.table.cellClicked.connect(self._on_cell_clicked)
        self.table.cellDoubleClicked.connect(self._on_row_activated)
        self.table.viewport().installEventFilter(self)

        layout = QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(0)
        layout.addWidget(filter_scroll, 0)
        layout.addSpacing(4)
        layout.addWidget(self.table, 1)
        self.setLayout(layout)

        # Start empty, headers arrive with the processor's columns.
        self.load_data(pd.DataFrame())

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802 (Qt naming)
        """Re-share the spare width on resize, and drop the hover outline when
        the cursor leaves the table."""
        if self.table is not None and obj is self.table.viewport():
            if event.type() == QEvent.Type.Resize and self.table.columnCount():
                self.table.resize_columns_to_contents_constrained()
            elif event.type() == QEvent.Type.Leave:
                self._row_highlight.hover_row = -1
                self._repaint_rows()
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------
    # Row highlight
    # ------------------------------------------------------------------
    def _row_key(self, row: int) -> tuple:
        """What identifies the row, so the outline survives a re-render.

        A sample is its electrode and the spot measured on it, never its
        position in the table: a filter or a reload moves every row.
        """
        if self.df_visible is None or row < 0 or row >= len(self.df_visible):
            return ()
        sample = self.df_visible.iloc[row]
        return (str(sample[str(SemEdxColumns.ELECTRODE)]).strip(),
                str(sample[str(SemEdxColumns.POSITION)]).strip())

    def _repaint_rows(self) -> None:
        """Redraw the rows, both halves of them.

        The pinned columns are a second view, so an outline drawn on one and
        not the other is half an outline.
        """
        self.table.viewport().update()
        self._frozen.repaint_rows()

    def _on_cell_entered(self, row: int, _column: int) -> None:
        """Outline the row under the cursor."""
        if self._row_highlight.hover_row != row:
            self._row_highlight.hover_row = row
            self._repaint_rows()

    def _on_cell_clicked(self, row: int, _column: int) -> None:
        """Pick the row, or let it go if it was already picked."""
        key = self._row_key(row)
        if not key:
            return
        self._picked_key = () if key == self._picked_key else key
        self._sync_picked_row()
        self._repaint_rows()

    def _sync_picked_row(self) -> None:
        """Find the picked sample's row again, after anything redrew."""
        self._row_highlight.last_column = self.table.columnCount() - 1
        self._row_highlight.picked_row = -1
        if not self._picked_key or self.df_visible is None:
            return
        for row in range(len(self.df_visible)):
            if self._row_key(row) == self._picked_key:
                self._row_highlight.picked_row = row
                return

    # ------------------------------------------------------------------
    # Data loading / filtering
    # ------------------------------------------------------------------
    def show_loading(self) -> None:
        """Say the sheets are on their way, until ``load_data`` arrives."""
        self._skeleton.start()

    def hide_loading(self) -> None:
        """Give up on the placeholder, for a read that never answered."""
        self._skeleton.stop()

    def load_data(self, dataframe: pd.DataFrame | None) -> None:
        """Take a new dataset, refresh the filter options and re-render."""
        self._skeleton.stop()
        self.df_source = self._in_production_order(dataframe)
        self._refresh_filter_options()
        self._render()

    @staticmethod
    def _in_production_order(dataframe: pd.DataFrame | None) -> pd.DataFrame | None:
        """Oldest electrode first, by the NUMBER in the marking.

        Sorting the markings as text puts B1039 before B757, so the number is
        pulled out and sorted on. A marking carrying no number at all sorts
        last rather than first, where it would look like the oldest electrode.
        Then position and point, so one electrode's rows read in order.
        """
        if dataframe is None or dataframe.empty:
            return dataframe

        electrode = str(SemEdxColumns.ELECTRODE)
        if electrode not in dataframe.columns:
            return dataframe.copy()

        order = pd.DataFrame(index=dataframe.index)
        order["number"] = [electrode_number(m) for m in dataframe[electrode]]
        order["number"] = order["number"].fillna(float("inf"))
        order["marking"] = dataframe[electrode].astype(str)
        for name, column in (("position", str(SemEdxColumns.POSITION)),
                             ("point", str(SemEdxColumns.MEASUREMENT))):
            order[name] = (dataframe[column] if column in dataframe.columns
                           else "")
        order["point"] = pd.to_numeric(order["point"], errors="coerce").fillna(0)

        ordered = order.sort_values(["number", "marking", "position", "point"])
        return dataframe.loc[ordered.index].reset_index(drop=True)

    def _refresh_filter_options(self) -> None:
        """Offer only the samples and operators the data holds."""
        df = self.df_source
        for key, column in ((FILTER_POSITION, str(SemEdxColumns.POSITION)),
                            (FILTER_OPERATOR, str(SemEdxColumns.OPERATOR))):
            if df is None or df.empty or column not in df.columns:
                self.filter_bar.set_options(key, [])
                continue
            values = sorted({str(v).strip() for v in df[column] if str(v).strip()})
            self.filter_bar.set_options(key, values)

    def _filtered(self) -> pd.DataFrame:
        """df_source narrowed by every active filter; they combine."""
        df = self.df_source
        if df is None or df.empty:
            return pd.DataFrame(columns=list(COLUMN_DISPLAY_NAMES))

        mask = pd.Series(True, index=df.index)
        for key, column in ((FILTER_POSITION, str(SemEdxColumns.POSITION)),
                            (FILTER_OPERATOR, str(SemEdxColumns.OPERATOR))):
            chosen = self.filter_bar.values(key)
            if chosen and column in df.columns:
                mask &= df[column].astype(str).str.strip().isin(set(chosen))

        search = self.filter_bar.search_text().lower()
        if search:
            # Match across every column, so an operator can type a marking, a
            # name or a word from a note without choosing a field first.
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
        self.filter_bar.set_result_count(
            len(self.df_visible), 0 if self.df_source is None else len(self.df_source))

        if self.df_visible.empty:
            self.table.load_data(pd.DataFrame())
            self._sync_picked_row()
            return

        display_df = self.names.to_display_frame(
            self.df_visible.drop(columns=list(HIDDEN_COLUMNS), errors="ignore"))
        images_header = COLUMN_DISPLAY_NAMES[IMAGES_COLUMN]
        if images_header in display_df.columns:
            # The path itself is never shown, a button takes its place.
            display_df[images_header] = ""

        try:
            self.table.load_data(display_df, cell_style_callback=self._cell_style)
            self._place_image_buttons(images_header)
            self._frozen.freeze_through(
                COLUMN_DISPLAY_NAMES[str(SemEdxColumns.POSITION)])
        except Exception as e:
            print(f"Error loading SEM EDX table: {e}")
        self._sync_picked_row()

    def _place_image_buttons(self, images_header: str) -> None:
        """Put a View button in the images column of every row that has a folder.

        Rows imported from the workbook have none, so they read as a dash: the
        measurements are known but the captures were never filed anywhere.
        """
        if self.table is None or self.df_visible is None:
            return
        headers = [self.table.horizontalHeaderItem(c).text()
                   for c in range(self.table.columnCount())]
        if images_header not in headers:
            return
        column = headers.index(images_header)

        # Samples of one electrode share a folder, so the answer is worth
        # keeping for the length of one redraw.
        reachable: dict[str, bool] = {}

        for row in range(self.table.rowCount()):
            folder = str(self.df_visible.iloc[row].get(IMAGES_COLUMN, "")).strip()
            if not folder:
                item = self.table.item(row, column)
                if item is not None:
                    item.setText(EMPTY)
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                continue

            if folder not in reachable:
                reachable[folder] = self._folder_is_reachable(folder)

            button = QPushButton("View")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFixedSize(IMAGE_BUTTON_WIDTH, IMAGE_BUTTON_HEIGHT)
            if reachable[folder]:
                button.setStyleSheet(IMAGE_BUTTON_STYLE)
            else:
                button.setStyleSheet(IMAGE_BUTTON_MISSING_STYLE)
                button.setToolTip(
                    "The captures could not be found on this computer.\n"
                    f"{folder}")
            button.clicked.connect(
                lambda _checked=False, r=row: self._on_show_images(r))

            # Centred inside a holder rather than dropped straight into the
            # cell. A bare cell widget is stretched to that row's height and
            # padding, so a row of a different height, the newest one after an
            # import, drew its button out of line with the rest.
            holder = QWidget()
            # Transparent, or the holder paints the default window colour over
            # the row and the button ends up sitting on a white patch.
            #
            # Scoped to the holder BY NAME. An unqualified "background:
            # transparent" cascades to everything inside, and a tooltip is a
            # child of the widget that raised it, so the tip panel came out
            # transparent and rendered as a black slab.
            holder.setObjectName("semImageCell")
            holder.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            holder.setStyleSheet("#semImageCell { background: transparent; }")
            centred = QHBoxLayout(holder)
            centred.setContentsMargins(0, 0, 0, 0)
            centred.setSpacing(0)
            centred.addWidget(button, 0, Qt.AlignmentFlag.AlignCenter)
            self.table.setCellWidget(row, column, holder)

    def _folder_is_reachable(self, folder: str) -> bool:
        """Whether this machine can open that folder. True when nobody says."""
        if not callable(self.on_images_available):
            return True
        try:
            return bool(self.on_images_available(folder))
        except Exception as e:
            print(f"Could not check the images folder {folder}: {e}")
            return True

    def _cell_style(self, _row_index: int, column_name: str, _value) -> CellStyle | None:
        """Centre the numbers; the text columns stay left aligned. The averages
        are bold, since they are the figures an operator reads first."""
        if self.df_visible is None:
            return None
        for column in self.df_visible.columns:
            if self.names.display_name(str(column)) != column_name:
                continue
            if self.names.decimals(str(column)) is None:
                return None
            return CellStyle(align_center=True,
                             bold=self.names.is_average(str(column)))
        return None

    # ------------------------------------------------------------------
    # Detail
    # ------------------------------------------------------------------
    def _on_row_activated(self, row: int, _column: int) -> None:
        """Open the double clicked sample: its measurements, and a form for the
        fields no instrument records."""
        if self.df_visible is None or row >= len(self.df_visible):
            return
        sample = self.df_visible.iloc[row]
        electrode = str(sample[str(SemEdxColumns.ELECTRODE)]).strip()
        position = str(sample[str(SemEdxColumns.POSITION)]).strip()

        measurements = pd.DataFrame()
        if callable(self.on_get_measurements):
            try:
                measurements = self.on_get_measurements(
                    self.generation, self.electrode_type, electrode, position)
            except Exception as e:
                print(f"Could not read the measurements of {electrode}: {e}")

        def text(column) -> str:
            return str(sample.get(str(column), "")).strip()

        dialog = SemSampleDialog(
            self, names=self.names, electrode=electrode, position=position,
            electrode_type=text(SemEdxColumns.ELECTRODE_TYPE),
            operator=text(SemEdxColumns.OPERATOR),
            zoom=text(SemEdxColumns.ZOOM),
            note=text(SemEdxColumns.NOTE),
            date=text(SemEdxColumns.DATE),
            measurements=measurements,
            # Saved while the window is still up, so it closes onto a table
            # that already holds the new figures rather than a second later.
            on_save=lambda: self._save_sample(electrode, position, dialog))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        if dialog.delete:
            try:
                if callable(self.on_delete_sample) and self.on_delete_sample(
                        self.generation, electrode, position):
                    # Stay where they were. They deleted one row of four
                    # hundred, not asked to be taken somewhere else.
                    with self.table.keep_scroll():
                        self.data_changed.emit(self.generation)
            except Exception as e:
                self._report(f"Deleting {electrode} {position} failed",
                             [str(e)], error=True)

    def _save_sample(self, electrode: str, position: str, dialog):
        """Write one sample and re-read the table.

        True when the window may close, False when it may not, and None when
        the work was handed off and will report back: that last one is what
        keeps the spinner turning, since this thread returns to its event loop
        instead of sitting on a database call.

        A failed write leaves the window open with everything still typed into
        it, which is the point of doing this here rather than after it closes.
        """
        if callable(self.on_save_sample):
            self.on_save_sample(
                self.generation, electrode, position,
                dialog.values, dialog.measurements,
                lambda ok, error: self._sample_saved(
                    dialog, electrode, position, ok, error))
            return None
        try:
            changed = False
            if dialog.values and callable(self.on_update_sample):
                changed |= bool(self.on_update_sample(
                    self.generation, electrode, position, dialog.values))
            if dialog.measurements and callable(self.on_update_measurements):
                changed |= bool(self.on_update_measurements(
                    self.generation, electrode, position, dialog.measurements))
            if changed:
                with self.table.keep_scroll():
                    self.data_changed.emit(self.generation)
                # Light the row, the same as a freshly imported one. A save
                # that takes a moment otherwise looks like nothing happened.
                self.flash_samples([(electrode, position)])
            return True
        except Exception as e:
            self._report(f"Saving {electrode} {position} failed", [str(e)], error=True)
            return False

    def _sample_saved(self, dialog, electrode: str, position: str,
                      ok: bool, error) -> None:
        """The answer to a handed-off save. Back on the UI thread."""
        if ok:
            # Light the row, the same as a freshly imported one. A save that
            # takes a moment otherwise looks like nothing happened.
            self.flash_samples([(electrode, position)])
        else:
            self._report(f"Saving {electrode} {position} failed",
                         [str(error)], error=True)
        dialog.saved(ok)

    def _on_show_images(self, row: int) -> None:
        """Open the captures of one sample, read from its result folder."""
        if self.df_visible is None or row >= len(self.df_visible):
            return
        sample = self.df_visible.iloc[row]
        folder = str(sample.get(IMAGES_COLUMN, "")).strip()
        electrode = str(sample[str(SemEdxColumns.ELECTRODE)]).strip()
        position = str(sample[str(SemEdxColumns.POSITION)]).strip()

        recorded = str(sample.get(str(SemEdxColumns.IMAGES), "")).strip()
        images: list = []
        if callable(self.on_list_images):
            try:
                images = list(self.on_list_images(folder, position, recorded))
            except Exception as e:
                print(f"Could not list the images of {electrode}: {e}")

        if not images:
            self._report(
                "No images to show",
                [f"No captures of {electrode} {position} were found in {folder}.",
                 "The folder may have moved, the sample may have been mapped "
                 "somewhere else, or you may not have access to it."])
            return

        SemImageViewerDialog(
            self, title=f"{electrode}, {position}", images=images).exec()

    def _report(self, title: str, lines: list[str], error: bool = False) -> None:
        """One place for every message this tab shows."""
        report(self, title, lines, error)

    # ------------------------------------------------------------------
    # Showing what just arrived
    # ------------------------------------------------------------------
    def _rows_of(self, samples) -> list[int]:
        """Which rendered rows these samples are on, if any."""
        wanted = {(str(e).strip(), str(p).strip()) for e, p in (samples or [])}
        if not wanted:
            return []
        return [row for row in range(self.table.rowCount())
                if self._sample_at(row) in wanted]

    def flash_samples(self, samples) -> None:
        """Light these rows where they are, without moving the view.

        For a row the operator was already looking at, an edited one. Scrolling
        to it would undo the care taken to leave the view where they left it.
        """
        rows = self._rows_of(samples)
        if rows and self._flasher is not None:
            self._flasher.flash(rows)

    def scroll_to(self, samples) -> None:
        """Bring the first of these samples into view, and light them all.

        An import adds a handful of rows to a table of four hundred, and Gen
        3.1 sorts oldest first, so a new row can be nowhere near the screen.
        Scrolled to AND lit: on a dense table, arriving at the right place
        still leaves you hunting for which line is new.
        """
        rows = self._rows_of(samples)
        if not rows:
            return
        self.table.scrollToItem(self.table.item(rows[0], 0))
        if self._flasher is not None:
            self._flasher.flash(rows)

    def _sample_at(self, row: int) -> tuple[str, str]:
        """The electrode and position of a rendered row."""
        if self.df_visible is None or row >= len(self.df_visible):
            return ("", "")
        sample = self.df_visible.iloc[row]
        return (str(sample[str(SemEdxColumns.ELECTRODE)]).strip(),
                str(sample[str(SemEdxColumns.POSITION)]).strip())

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------
    def get_data(self) -> pd.DataFrame:
        """The full source DataFrame (original column names, unfiltered)."""
        return self.df_source if self.df_source is not None else pd.DataFrame()


class SemEdxGenerationWidget(QWidget):
    """One generation: a sub tab strip with one table per electrode type.

    Importing lives here rather than on a type tab, because a result folder's
    type is looked up rather than chosen: one button serves both. The
    generation, on the other hand, cannot be looked up, so it comes from which
    tab the import was started on.
    """

    data_changed = pyqtSignal(str)
    # ``(generation, [(electrode, position)], {electrode: type})``. Emitted
    # rather than acted on, because the generation tabs belong to the parent
    # and an import started here can land in the other generation.
    reveal_requested = pyqtSignal(str, object, object)

    def __init__(self, parent=None, generation: str = "",
                 generations: list[str] | None = None,
                 layout: dict[str, list[str]] | None = None,
                 on_import_folder=None, on_add_sample=None,
                 on_get_elements=None, on_update_sample=None,
                 on_update_measurements=None,
                 on_list_import_candidates=None, **callables) -> None:
        super().__init__(parent)
        self.generation = str(generation).strip()
        # Every generation there is, so a sample typed in from the wrong tab
        # can be filed under the right one without starting again.
        self.generations: list[str] = [str(g).strip() for g in (generations or [])
                                       ] or [self.generation]
        # ``(generation, folder, ask_type, pick) -> (result, already present)``.
        self.on_import_folder = on_import_folder
        # ``(generation) -> [{name, path}]``, the folders in the library that
        # nothing has been imported from yet. Named here rather than left in
        # **callables so it is not handed on to the type widgets.
        self.on_list_import_candidates = on_list_import_candidates
        # Where a batch has got to, for the footer on every dialog it raises,
        # and whether the operator has asked it to stop.
        self._batch: tuple[int, int] = (0, 0)
        self._cancelled = False
        # Whether the folder being imported was confirmed, refused, or never
        # got as far as asking because it held nothing new.
        self._confirmed = None
        # Set when the confirmation is answered with "Choose captures again".
        self._redo = False
        # What was answered on the way through, so a trip back does not throw
        # it away. Both are cleared when a different folder is started.
        self._remembered_images: dict[tuple[str, str], list[str]] = {}
        self._remembered_fields: dict[str, str] = {}
        # Recording a sample nobody could parse, and which quantities to ask
        # for. Both are injected, the same as everything else here.
        self.on_add_sample = on_add_sample
        self.on_get_elements = on_get_elements
        # Shared with the type widgets: the operator asked for after an import
        # is written the same way as one edited on a row.
        self.on_update_sample = on_update_sample
        self.types: dict[str, SemEdxTypeWidget] = {}
        self.choices: list[str] = [str(t).strip().upper() for t in (layout or {})]

        # Fades like the main strip. The two never compete: clicking a main tab
        # does not change a sub tab, so only one dissolve runs per click.
        # No fade on the sub tabs: switching Anodes to Cathodes swaps one
        # table for another of the same shape, and dissolving between two
        # grids of numbers reads as a glitch rather than a transition.
        self.sub_tabs = NoWheelTabWidget(fade_ms=0)
        self.sub_tabs.setStyleSheet(PLOT_TABS_STYLE)
        for electrode_type, quantity_layout in (layout or {}).items():
            widget = SemEdxTypeWidget(
                generation=self.generation, electrode_type=electrode_type,
                layout=quantity_layout, on_update_sample=on_update_sample,
                on_update_measurements=on_update_measurements, **callables)
            widget.data_changed.connect(self.data_changed.emit)
            self.types[str(electrode_type).strip().upper()] = widget
            self.sub_tabs.addTab(widget, type_label(electrode_type))

        self.add_button = QPushButton("Add SEM result")
        self.add_button.setMinimumHeight(30)
        self.add_button.setStyleSheet(TOOLBAR_BUTTON_STYLE)
        self.add_button.setIcon(qta.icon("fa6s.plus", color="white"))
        apply_modern_shadow(self.add_button)
        self.add_button.clicked.connect(self._on_add_clicked)

        # Its own row above the tab strip rather than the strip's corner slot:
        # that slot is only as tall as the tab bar, so it clipped the button
        # and its shadow whatever the margins were.
        header = QHBoxLayout()
        header.setContentsMargins(0, 8, 2, 8)
        header.setSpacing(0)
        header.addStretch(1)
        header.addWidget(self.add_button, 0)

        box = QVBoxLayout()
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        box.addLayout(header, 0)
        box.addWidget(self.sub_tabs, 1)
        self.setLayout(box)

    # ------------------------------------------------------------------
    # Import
    # ------------------------------------------------------------------
    def _on_add_clicked(self) -> None:
        """Choose result folders from the library, or browse for one."""
        if not callable(self.on_import_folder):
            return
        if not callable(self.on_list_import_candidates):
            self._browse_and_import()       # no listing available, old way
            return

        # Reading the library means a directory walk on a synced share.
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            candidates = {generation: self.on_list_import_candidates(generation)
                          for generation in self.generations}
        except Exception as e:
            report(self, "Could not read the SEM images library", [str(e)],
                   error=True)
            return
        finally:
            QApplication.restoreOverrideCursor()

        dialog = ImportPickerDialog(
            self, generations=list(self.generations), candidates=candidates,
            current=self.generation)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if dialog.browse:
            self._browse_and_import()
            return
        # One at a time, and one report each: an operator answering for thirty
        # folders still needs to see what happened to each of them.
        chosen = list(dialog.chosen)
        self._cancelled = False
        self.add_button.setEnabled(False)
        try:
            for index, (generation, folder, spot) in enumerate(chosen, start=1):
                # Set before the import, so every dialog it raises can show
                # where the batch has got to.
                self._batch = (index, len(chosen))
                # So the previous folder's new rows are on screen, and lit,
                # before the next one blocks the window parsing.
                QApplication.processEvents()
                self._guarded_import(folder, generation, spot)
                if self._cancelled:
                    # Including the one in hand: cancelling unwinds it before
                    # anything is written, so it was not imported either.
                    remaining = len(chosen) - index + 1
                    report(self, "Import stopped",
                           [f"{remaining} of {len(chosen)} folders were "
                            f"not imported."])
                    break
        finally:
            # Whatever happened, the button comes back and the strip goes.
            self._batch = (0, 0)
            self._cancelled = False
            self.add_button.setEnabled(True)

    def _browse_and_import(self) -> None:
        """The file explorer, for a capture filed outside the usual layout."""
        folder = QFileDialog.getExistingDirectory(self, FOLDER_DIALOG_TITLE)
        if folder:
            self._cancelled = False
            self._guarded_import(folder, self.generation)

    def _guarded_import(self, folder: str, generation: str, spot: str = "") -> None:
        """Import one folder, reporting anything that goes wrong.

        An operator picking the wrong folder is normal, and it must never cost
        them the window.
        """
        # A fresh folder starts with a clean slate. Within one folder the
        # answers survive, which is what makes going back cheap.
        self._remembered_images = {}
        self._remembered_fields = {}
        try:
            # More than once when the confirmation is answered with "Choose
            # captures again": nothing was written, so reading the folder from
            # the top simply asks everything over.
            while True:
                self._redo = False
                self._import_folder(folder, generation, spot)
                if not self._redo:
                    break
        except ImportCancelled:
            pass            # they stopped it; nothing was written for this one
        except Exception as e:
            report(self, "Import failed", [str(e)], error=True)

    def _import_folder(self, folder: str, generation: str = "",
                       spot: str = "") -> None:
        """Read one folder, ask before writing it, then show where it landed.

        The generation is passed in rather than taken from this tab: the picker
        lists both, so a Gen 3.1 folder chosen from the Gen 4.1 tab must still
        be filed under Gen 3.1.

        A spot narrows it to one place on the plate. A folder can hold five,
        offered as five rows, and taking one must leave the rest alone.
        """
        generation = str(generation or "").strip() or self.generation
        name = Path(folder).name
        # None until the confirmation runs, then True or False. It only runs
        # when the folder actually has something new in it.
        self._confirmed = None

        # Reading a folder means opening every report in it, and they live on
        # a synced share. Without this the window looks hung rather than busy.
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            result, skipped = self.on_import_folder(
                generation, folder, self._ask_type, self._pick_images,
                lambda parsed, already: self._confirm_import(parsed, already, name),
                [spot] if spot else None)
        except ImportCancelled:
            # Raised by our own prompts, from inside this call. It is a
            # decision, not a failure, so it goes to the caller unreported.
            raise
        except Exception as e:
            report(self, "Import failed", [str(e)], error=True)
            return
        finally:
            QApplication.restoreOverrideCursor()

        if self._confirmed is False:
            return              # refused at the confirmation, nothing written

        if self._confirmed:
            self.data_changed.emit(generation)
            imported = sorted(
                {(m.electrode, m.position) for m in result.measurements
                 if f"{m.electrode} {m.position}" not in skipped})
            self.reveal_requested.emit(
                generation, imported, getattr(result, "electrode_types", {}))
            return

        # The confirmation never ran, so there was nothing new to write. Say
        # why: a folder already in the table looks identical to a failed one.
        if not result.measurements and callable(self.on_add_sample):
            # Straight to typing it in: a failure dialog first only puts a page
            # between the operator and the one thing they can do about it.
            self._offer_manual_entry(folder, result, generation)
            return
        title, lines = self._import_summary(result, skipped, name)
        dialog = ImportSummaryDialog(
            self, title=title, lines=lines, ask_operator=False,
            error=not result.measurements, zoom_hint=self._zoom_of(result))
        self._prepare_dialog(dialog)
        dialog.exec()

    def _confirm_import(self, result, already: list[str], name: str):
        """Show what the folder holds and take the by-hand fields, before the write.

        Returns the fields to stamp on every sample, or None to refuse the
        folder entirely. Asked here rather than after the write because a row
        that appears and only then gets an operator is a row that stays blank
        if the window is answered with Cancel.
        """
        _title, lines = self._import_summary(result, already, name)
        with asking():
            remembered = self._remembered_fields
            dialog = ImportSummaryDialog(
                self, title="Confirm import", lines=lines, ask_operator=True,
                error=False, allow_back=True,
                # What was typed last time round beats what the sidecars read.
                zoom_hint=remembered.get("zoom") or self._zoom_of(result),
                operator_hint=remembered.get("operator", ""),
                note_hint=remembered.get("note", ""))
            self._prepare_dialog(dialog)
            dialog.exec()
        # Taken whichever button was pressed, so a trip back to the captures
        # comes back to the same boxes.
        self._remembered_fields = {"operator": dialog.operator,
                                   "zoom": dialog.zoom, "note": dialog.note}
        if getattr(dialog, "back", False):
            # Refuse this pass. _guarded_import reads the folder again, which
            # puts every question a second time.
            self._redo = True
            self._confirmed = False
            return None
        if self._cancelled:
            self._confirmed = False
            return None

        self._confirmed = True
        # Only what was typed. A blank box means "leave it", not "clear it".
        values = {}
        if dialog.operator:
            values[str(SemEdxColumns.OPERATOR)] = dialog.operator
        if dialog.zoom:
            values[str(SemEdxColumns.ZOOM)] = dialog.zoom
        if dialog.note:
            values[str(SemEdxColumns.NOTE)] = dialog.note
        return values

    def current_type(self) -> str:
        """Which sub tab is open: Anodes or Cathodes."""
        current = self.sub_tabs.currentWidget() if self.sub_tabs else None
        for electrode_type, widget in self.types.items():
            if widget is current:
                return electrode_type
        return ""

    @staticmethod
    def _marking_or_blank(value: str) -> str:
        """The value if it reads as an electrode marking, otherwise nothing."""
        text = str(value or "").strip()
        return text if electrode_number(text) is not None else ""

    def _offer_manual_entry(self, folder: str, result, generation: str = "") -> None:
        """Ask whether to type in what the folder could not be read for."""
        generation = str(generation or "").strip() or self.generation
        if not callable(self.on_add_sample):
            return
        ask = ConfirmationDialog(
            parent=self, title="Manual input",
            message=f"Nothing could be read from <b>{Path(folder).name}</b>.",
            icon="\u270e", header_color="#14B8A6",
            yes_text="Manual input", no_text="Cancel")
        if ask.exec() != ConfirmationDialog.DialogCode.Accepted:
            return

        dialog = ManualSampleDialog(
            self, generation=generation, generations=self.generations,
            folder=Path(folder).name,
            # Only when it really is a marking. The parser names the folder as
            # the electrode, and a pick one level too high is named for the
            # library, which would prefill nonsense.
            electrode=self._marking_or_blank(getattr(result, "electrode", "")),
            folder_path=folder, types=list(self.types),
            electrode_type=self.current_type(),
            elements_for=lambda generation, electrode_type: (
                self.on_get_elements(generation, electrode_type)
                if callable(self.on_get_elements) else []),
            images=[str(image) for image in getattr(result, "images", [])])
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.saved:
            return

        entry = dialog.saved
        generation = entry["generation"] or self.generation
        try:
            electrode_type = self.on_add_sample(
                generation, entry["electrode"], entry["position"],
                entry["readings"], entry["electrode_type"], entry["zoom"],
                entry["date"], entry["folder"] or folder, entry["images"])
        except Exception as e:
            report(self, "The sample could not be saved", [str(e)], error=True)
            return

        self.data_changed.emit(self.generation)
        # Only this generation's tabs are ours to scroll. Another generation's
        # sample is reported instead, saying where it went.
        if generation == self.generation:
            self._reveal([(entry["electrode"], entry["position"])],
                         {entry["electrode"]: electrode_type})
        report(self, "Sample recorded",
               [f"{entry['electrode']} {entry['position']} recorded under "
                f"{generation} {type_label(electrode_type).rstrip('s')}, "
                f"{len(entry['readings'])} point(s)."])

    @staticmethod
    def _zoom_of(result) -> str:
        """The magnification the import read, when every sample agrees on one."""
        found = {str(getattr(m, "zoom", "") or "").strip()
                 for m in getattr(result, "measurements", [])}
        found.discard("")
        return found.pop() if len(found) == 1 else ""

    def reveal(self, imported: list[tuple[str, str]],
               types: dict[str, str]) -> None:
        """Show the tab the samples landed on, and light the rows they added.

        Which tab that is comes from the electrode type, which is looked up
        rather than chosen, so importing a cathode while looking at Anodes
        otherwise leaves you staring at an unchanged table wondering whether
        anything happened.
        """
        by_type: dict[str, list[tuple[str, str]]] = {}
        for electrode, position in imported:
            key = str(types.get(electrode, "")).strip().upper()
            if key in self.types:
                by_type.setdefault(key, []).append((electrode, position))
        if not by_type:
            return

        # More than one type in a folder is rare; the first is where to look.
        first = next(iter(by_type))
        self.sub_tabs.setCurrentWidget(self.types[first])
        for electrode_type, samples in by_type.items():
            self.types[electrode_type].scroll_to(samples)

    def _prepare_dialog(self, dialog) -> None:
        """Seal a question and give it the import footer.

        Sealed because dismissing one used to count as answering it: the
        capture picker fell back to a guess and the summary lost the operator.
        The footer's Cancel is then the only way out, which is the point.
        """
        dialog.setWindowFlags(
            dialog.windowFlags() & ~Qt.WindowType.WindowCloseButtonHint)
        # Held by attribute rather than by Qt parent, so it lives as long as
        # the dialog without requiring one that Qt will accept as a parent.
        dialog._no_dismiss = _NoDismiss()
        dialog.installEventFilter(dialog._no_dismiss)

        layout = dialog.layout()
        if layout is None:
            return
        done, total = self._batch
        footer = import_footer(done, total, lambda: self._cancel_batch(dialog))
        # Line the strip up with the dialog's content rather than its frame:
        # some of these carry their own inset and some do not.
        side = 0 if layout.contentsMargins().left() else 18
        footer.layout().setContentsMargins(side, 10, side, side and 14)
        layout.addWidget(footer)

    def _cancel_batch(self, dialog) -> None:
        """Stop the import from a dialog that nothing else can dismiss."""
        self._cancelled = True
        # done() rather than close(), which the guard deliberately blocks.
        dialog.done(QDialog.DialogCode.Rejected)

    def _ask_type(self, electrode: str, choices: list[str]) -> str:
        """Which type an electrode is, asked only when the database is silent."""
        with asking():
            dialog = ElectrodeTypeDialog(
                self, electrode=electrode, choices=choices or self.choices)
            self._prepare_dialog(dialog)
            chosen = dialog.chosen if dialog.exec() == QDialog.DialogCode.Accepted else ""
        # Sealed, so the only way it came back rejected is the Cancel.
        if self._cancelled:
            raise ImportCancelled
        return chosen

    def _pick_images(self, electrode: str, position: str, candidates: list,
                     _suggested: list) -> list | None:
        """Which captures belong to one sample.

        The suggestion is deliberately not shown as a pre-selection: it is a
        guess from the filenames, and a pre-ticked guess gets accepted without
        being read. It used to be what the backend fell back to when this
        window was closed rather than answered, which meant dismissing the
        question quietly accepted the guess. The window cannot be dismissed
        now, so the only ways out are choosing or cancelling the import.
        """
        key = (electrode, position)
        with asking():
            dialog = ImagePickerDialog(
                self, electrode=electrode, position=position, images=candidates,
                preselected=self._remembered_images.get(key))
            self._prepare_dialog(dialog)
            chosen = dialog.chosen if dialog.exec() == QDialog.DialogCode.Accepted else None
        if self._cancelled:
            raise ImportCancelled
        if chosen is not None:
            self._remembered_images[key] = list(chosen)
        return chosen

    @staticmethod
    def _import_summary(result, skipped: list[str], name: str) -> tuple[str, list[str]]:
        """What to tell the operator about one import.

        A folder is never silently half-read: whatever was missing is listed
        alongside whatever was taken. An element a report did not carry is not
        mentioned, it reads as a blank cell in the table.
        """
        imported = [m for m in result.measurements
                    if f"{m.electrode} {m.position}" not in skipped]
        lines: list[str] = []

        if imported:
            # Which electrode and which spot on it. No count of measurements:
            # this window is asked before the write now, so what it has to say
            # is WHAT is about to be written, not how much of it.
            samples = sorted({(m.electrode, m.position) for m in imported})
            lines.append(
                ("Sample: " if len(samples) == 1 else "Samples: ")
                + "; ".join(", ".join(part for part in sample if part)
                            for sample in samples))
            # A row of its own: the type is looked up rather than chosen, so it
            # is also the answer to "which tab did that land on".
            described = _describe_types(
                getattr(result, "electrode_types", {}), [e for e, _ in samples])
            if described:
                lines.append(f"Electrode type: {described}")
        elif result.measurements:
            lines.append("Nothing was added.")
        else:
            lines.append(f"No measurements could be read from {name}.")

        if skipped:
            lines.append(f"Already in the table, so left alone: {', '.join(skipped)}.")
        lines.extend(result.problems)

        return ("Imported" if imported else "Nothing imported"), lines

    def show_loading(self) -> None:
        """Every electrode type of this generation is still being read."""
        for widget in self.types.values():
            widget.show_loading()

    def hide_loading(self) -> None:
        """Take every placeholder of this generation away."""
        for widget in self.types.values():
            widget.hide_loading()

    def load_data(self, dataframe: pd.DataFrame | None, electrode_type: str) -> None:
        """Hand one electrode type's samples to its sub tab."""
        widget = self.types.get(str(electrode_type).strip().upper())
        if widget is not None:
            widget.load_data(dataframe)

    def get_data(self, electrode_type: str) -> pd.DataFrame:
        """One electrode type's full source DataFrame, unfiltered."""
        widget = self.types.get(str(electrode_type).strip().upper())
        return widget.get_data() if widget else pd.DataFrame()


class SemEdxWidget(QWidget):
    """The SEM EDX tab: a generation, then an electrode type within it.

    Two axes because the data has two. The generation picks the file, and they
    do not measure the same things: Gen 3.1 records Ni and S, Gen 4.1 adds Co,
    and its cathodes add C and N on top. The electrode type then picks the
    sheet, which differ again. Each of the four tables builds its columns from
    the layout it was handed, so none of that is written down here.
    """

    data_changed = pyqtSignal(str)

    def apply_saved(self, generation: str, frames: dict) -> None:
        """Render frames a background save fetched, without moving the view.

        Same as a poll: the operator did not ask to be taken anywhere, they
        asked for one row to change.
        """
        widget = self.generations.get(str(generation or "").strip())
        for electrode_type, frame in (frames or {}).items():
            target = (widget.types.get(str(electrode_type).strip().upper())
                      if widget else None)
            if target is None:
                self.load_data(frame, electrode_type, generation)
                continue
            with target.table.keep_scroll():
                self.load_data(frame, electrode_type, generation)

    def _on_reveal(self, generation: str, imported, types) -> None:
        """Open the tab an import landed on, both axes of it.

        The generation comes from the folder, so a Gen 3.1 cathode imported
        from the Gen 4.1 tab opens Gen 3.1 and then Cathodes within it. Only
        this widget owns the generation strip, which is why the tab that ran
        the import asks rather than doing it.
        """
        widget = self.generations.get(str(generation or "").strip())
        if widget is None:
            return
        self.generation_tabs.setCurrentWidget(widget)
        widget.reveal(imported, types)

    def __init__(self, parent=None,
                 layouts: dict[str, dict[str, list[str]]] | None = None,
                 **callables) -> None:
        super().__init__(parent)
        self.generations: dict[str, SemEdxGenerationWidget] = {}
        self.init_ui(layouts or {}, callables)

    def init_ui(self, layouts: dict[str, dict[str, list[str]]],
                callables: dict) -> None:
        # Polling belongs to the whole tab, not to one generation, so it is
        # taken out before the rest is handed down.
        self.on_check_for_updates = callables.pop("on_check_for_updates", None)
        main_layout, content_container, content_layout = create_styled_container_layout()

        title = QLabel("SEM EDX")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setMinimumHeight(25)
        title.setMaximumHeight(40)
        title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        # Same reasoning as the type sub tabs, and a generation switch also
        # changes the inner tab, so two dissolves ran over one click.
        self.generation_tabs = NoWheelTabWidget(fade_ms=0)
        self.generation_tabs.setStyleSheet(PLOT_TABS_STYLE)

        # In the order given, which is oldest first.
        for generation, layout in layouts.items():
            widget = SemEdxGenerationWidget(
                generation=generation, generations=list(layouts),
                layout=layout, **callables)
            widget.data_changed.connect(self.data_changed.emit)
            widget.reveal_requested.connect(self._on_reveal)
            self.generations[generation] = widget
            self.generation_tabs.addTab(widget, generation)

        content_layout.addWidget(title, 0)
        content_layout.addSpacing(4)
        content_layout.addWidget(self.generation_tabs, 1)
        content_container.setLayout(content_layout)

        main_layout.addWidget(content_container, 1)
        self.setLayout(main_layout)

    def generation_names(self) -> list[str]:
        """The generations on screen, in tab order, oldest first."""
        return list(self.generations)

    def _generation(self, generation: str) -> SemEdxGenerationWidget | None:
        """One generation's widget; the first when none is named."""
        if not generation:
            return next(iter(self.generations.values()), None)
        return self.generations.get(generation)

    def show_loading(self) -> None:
        """Every sheet is still being read, so every sub tab says so."""
        for widget in self.generations.values():
            widget.show_loading()

    def hide_loading(self) -> None:
        """Take every placeholder away, for a read that never answered."""
        for widget in self.generations.values():
            widget.hide_loading()

    def load_data(self, dataframe: pd.DataFrame | None, electrode_type: str = "",
                  generation: str = "") -> None:
        """Hand one electrode type of one generation its samples."""
        widget = self._generation(generation)
        if widget is not None:
            widget.load_data(dataframe, electrode_type)

    def get_data(self, electrode_type: str = "",
                 generation: str = "") -> pd.DataFrame:
        """One sub tab's full source DataFrame, unfiltered."""
        widget = self._generation(generation)
        return widget.get_data(electrode_type) if widget else pd.DataFrame()

    def apply_external_updates(self, result) -> bool:
        """Render the answer to a poll. Runs on the UI thread.

        The payload is ``{generation: {electrode type: frame}}``, since one
        import changes exactly one of the four tables and the tab has no way of
        knowing which.
        """
        if not result or not result.get("has_updates"):
            return False
        for generation, frames in (result.get("data") or {}).items():
            for electrode_type, frame in (frames or {}).items():
                # Nobody here asked for this. Somebody else's import must not
                # move the view of whoever is reading the table, so every one
                # of them keeps its place rather than landing on the last row.
                widget = self.generations.get(generation)
                target = (widget.types.get(str(electrode_type).strip().upper())
                          if widget else None)
                if target is None:
                    self.load_data(frame, electrode_type, generation)
                    continue
                with target.table.keep_scroll():
                    self.load_data(frame, electrode_type, generation)
        return True

    def check_for_external_updates(self) -> bool:
        """Poll and apply in one go, for callers with no worker thread."""
        if not callable(self.on_check_for_updates):
            return False
        return self.apply_external_updates(self.on_check_for_updates())
