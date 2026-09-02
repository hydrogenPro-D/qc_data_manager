"""QC tab — the kanban board mirroring the QC progress overview workbook.

Each stage is a card holding two lists, Anodes and Cathodes. Electrodes are
dragged between stages; a drag is only accepted by a list of the *same*
electrode type, so a cathode can never land in an anode column.

The tab owns no data: the board lives in an injected board store and the
electrode index (which markings exist, their type and status) is handed in by
MainWindow.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QStyle,
    QStyledItemDelegate,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFrame,
    QPushButton,
    QScrollArea,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QAbstractItemView,
    QDialog,
    QComboBox,
    QStyledItemDelegate,
)
from PyQt6.QtCore import Qt, QEvent, QObject, QSize, QTimer, pyqtSignal
from PyQt6.QtGui import (QFont, QBrush, QColor, QCursor, QDrag, QPainter,
                         QPalette, QPen, QPixmap)
import pandas as pd
import qtawesome as qta

from .styles.button_styles import (
    PRIMARY_BUTTON_STYLE,
    TOOLBAR_BUTTON_STYLE,
    SECONDARY_BUTTON_STYLE,
    apply_modern_shadow,
)
from .styles.tab_styles import create_styled_container_layout
from .styles.table_styles import SCROLLBAR_STYLE, SLIM_SCROLLBAR_STYLE
from .styles.input_styles import INPUT_FIELD_STYLE
from .widgets.error_dialogs import ValidationErrorScrollDialog, ConfirmationDialog, build_header
from .widgets.no_scroll_inputs import NoScrollComboBox
from .widgets.table_delegates import TickDelegate
from src.data_access.electrodes.electrode_columns import (
    ElectrodeColumns,
    ElectrodeStatus,
    STATUS_COLUMN,
)
from src.data_access.qc_board.qc_board_columns import (
    ElectrodeType,
    QcStage,
    STAGE_ORDER,
    STAGE_STATUS,
    SEM_RESULT,
    SEM_ACCEPTED,
    SEM_CRACKED,
    SEM_PENDING,
    IC_RESULT,
    IC_PENDING,
    IC_PASSED,
    IC_FAILED,
    IC_DONE,
)

# Display text for each stage — relabel here, never in the enum.
STAGE_LABELS: dict[QcStage, str] = {
    QcStage.WAITING: "Electrodes waiting for QC",
    QcStage.IC_DONE: "In progress, IC started",
    QcStage.SEM_DONE: "In progress, SEM done",
    QcStage.WAITING_APPROVAL: "Waiting for approval",
    QcStage.WAITING_FOR_RETEST: "Waiting for retest",
    QcStage.RETEST_ONGOING: "Retest ongoing",
    # These two say what the TEST found, because that is what a card landing
    # here records: Tested and Released, or Tested and Scrapped. Released and
    # Rejected are deliberately not used, those are the Electrodes tab's words
    # for a neighbour that was never tested and is only presumed one way or
    # the other. One word, one meaning.
    QcStage.RELEASE: "Passed",
    QcStage.SCRAPPED: "Failed",
}

# Card text colour, by what has been found about the electrode. Checked in
# order, so the worst finding wins.
IC_FAILED_COLOUR = "#DC2626"
CRACKED_PASSED_COLOUR = "#E0A80A"
CRACKED_COLOUR = "#F5C518"


def card_colour(findings: dict) -> str | None:
    """Colour for one electrode's card, or None to leave it as normal text."""
    if findings.get(IC_RESULT) == IC_FAILED:
        return IC_FAILED_COLOUR
    if findings.get(SEM_RESULT) == SEM_CRACKED:
        # Cracked but IC passed is the awkward middle case, so it gets its own
        # darker shade rather than reading as a plain crack.
        return (CRACKED_PASSED_COLOUR if findings.get(IC_RESULT) == IC_PASSED
                else CRACKED_COLOUR)
    return None

# The two terminal stages are colour-coded like the workbook; the rest stay
# neutral so the board doesn't read as a traffic light.
STAGE_HEADERS: dict[QcStage, tuple[str, str]] = {
    QcStage.RELEASE: ("#16A34A", "#FFFFFF"),
    QcStage.SCRAPPED: ("#DC2626", "#FFFFFF"),
}
DEFAULT_HEADER = ("#F5F5F5", "#1F1F1F")

# How far the two verdict columns sit away from the workflow, on each side of
# the divider between them. Wide enough to read as a break in the board rather
# than as one more gap between cards.
OUTCOME_GAP = 18

# The column is a soft drop-zone; each electrode is a card floating in it.
_LIST_STYLE = """
    QListWidget {
        background-color: #FAFAFB;
        border: 1px dashed #E5E7EB;
        border-radius: 3px;
        font-family: 'Segoe UI', Arial, sans-serif;
        font-size: 11px;
        padding: 2px;
        outline: none;
    }
    QListWidget::item {
        background-color: #FFFFFF;
        border: 1px solid #E5E7EB;
        border-radius: 7px;
        margin: 2px 0px;
        padding: 3px 2px;
        font-size: 11px;
        font-weight: 600;
    }
    /* No colour here on purpose: a stylesheet colour would override the
       per item foreground that marks cracked and IC failed electrodes. */
    QListWidget::item:hover {
        background-color: #F0FDFA;
        border: 1px solid #99E0DA;
    }
    QListWidget::item:selected {
        background-color: #E6F6F6;
        border: 1.5px solid #55BDBD;
    }
"""

# The app's standard scrollbar is 35px, half the width of one of these columns,
# so board lists take the slim one and the marking still fits beside it.
_LIST_STYLE += SLIM_SCROLLBAR_STYLE


class StageChoiceDialog(QDialog):
    """Pick the column an electrode should sit in.

    The same move a drag makes, for when the target column is scrolled off
    screen or the pointer is not the tool for the job.
    """

    def __init__(self, parent=None, marking: str = "", current=None) -> None:
        super().__init__(parent)
        self.chosen = None

        title = f"Change status, {marking}"
        self.setWindowTitle(title)
        self.setMinimumWidth(400)
        self.setStyleSheet("QDialog { background-color: #FFFFFF; }")

        self.combo = NoScrollComboBox()
        self.combo.setMinimumHeight(38)
        self.combo.setStyleSheet(_DIALOG_COMBO_STYLE)
        for stage in STAGE_ORDER:
            self.combo.addItem(STAGE_LABELS[stage], stage)
        if current is not None:
            self.combo.setCurrentIndex(max(0, self.combo.findData(current)))

        cancel = QPushButton("Cancel")
        cancel.setMinimumHeight(38)
        cancel.setStyleSheet(SECONDARY_BUTTON_STYLE)
        cancel.clicked.connect(self.reject)

        save = QPushButton("Save")
        save.setMinimumHeight(38)
        save.setMinimumWidth(110)
        save.setStyleSheet(PRIMARY_BUTTON_STYLE)
        apply_modern_shadow(save)
        save.clicked.connect(self._on_save)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(cancel, 0)
        buttons.addWidget(save, 0)

        caption = QLabel(f"Status for {marking}")
        caption.setStyleSheet("color: #1F1F1F; font-size: 12px; font-weight: 600;")

        body = QVBoxLayout()
        body.setContentsMargins(24, 20, 24, 20)
        body.setSpacing(8)
        body.addWidget(caption)
        body.addWidget(self.combo)
        body.addSpacing(6)
        body.addLayout(buttons)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(build_header("⬡", title, "#14B8A6"))
        layout.addLayout(body, 1)
        self.setLayout(layout)

    def _on_save(self) -> None:
        self.chosen = self.combo.currentData()
        self.accept()


class ElectrodeDetailDialog(QDialog):
    """What you can do with one electrode on the board.

    Opened by double clicking it: record what was found, or take the card off
    the board. Freezing a verdict is not offered here, it is "mark all as done"
    on the toolbar, which finalises the whole board at once.
    """

    # In the order the work happens: not run, run, and then the two answers,
    # Failed first because it is the one that stops the electrode.
    IC_CHOICES = ((IC_PENDING, "Not tested"), (IC_DONE, "Test done"),
                  (IC_FAILED, "Failed"), (IC_PASSED, "Passed"))
    SEM_CHOICES = ((SEM_PENDING, "Not tested"), (SEM_CRACKED, "Cracked"),
                   (SEM_ACCEPTED, "Accepted"))

    def __init__(self, parent=None, marking: str = "", stage_label: str = "",
                 electrode_type: str = "", findings: dict | None = None,
                 stage=None) -> None:
        super().__init__(parent)
        self.action: str | None = None
        # Where "Change status" left the electrode, read by the board when the
        # dialog closes with that action.
        self.chosen_stage = None
        self._marking = marking
        self._stage = stage
        findings = findings or {}

        self.setWindowTitle(marking)
        self.setMinimumWidth(440)
        self.setStyleSheet("QDialog { background-color: #FFFFFF; }")

        stage = QLabel(
            f"<span style='color:#6B6B6B;'>Status:</span> <b>{stage_label}</b><br/>"
            f"<span style='color:#6B6B6B;'>Electrode type:</span> "
            f"<b>{electrode_type}</b>")
        stage.setTextFormat(Qt.TextFormat.RichText)
        stage.setStyleSheet("font-size: 12px;")

        # One row per instrument, both reading the same way: which test on the
        # left, what it found on the right.
        self.sem_combo = self._finding_combo(
            self.SEM_CHOICES, findings.get(SEM_RESULT, SEM_PENDING))
        self.ic_combo = self._finding_combo(
            self.IC_CHOICES, findings.get(IC_RESULT, IC_PENDING))

        findings_row = QVBoxLayout()
        findings_row.setContentsMargins(0, 0, 0, 0)
        findings_row.setSpacing(10)
        for caption, combo in (("SEM result", self.sem_combo),
                               ("IC result", self.ic_combo)):
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            row.addWidget(self._finding_caption(caption), 0)
            row.addWidget(combo, 0)
            row.addStretch(1)
            findings_row.addLayout(row)

        change_button = QPushButton("Change status")
        change_button.setMinimumHeight(38)
        change_button.setStyleSheet(SECONDARY_BUTTON_STYLE)
        change_button.clicked.connect(self._on_change_status)

        delete_button = QPushButton("Delete from board")
        delete_button.setMinimumHeight(38)
        delete_button.setStyleSheet(SECONDARY_BUTTON_STYLE)
        delete_button.clicked.connect(lambda: self._choose("delete"))

        close_button = QPushButton("OK")
        close_button.setMinimumHeight(38)
        close_button.setMinimumWidth(110)
        close_button.setStyleSheet(PRIMARY_BUTTON_STYLE)
        apply_modern_shadow(close_button)
        close_button.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addWidget(change_button, 0)
        buttons.addWidget(delete_button, 0)
        buttons.addStretch(1)
        buttons.addWidget(close_button, 0)

        body = QVBoxLayout()
        body.setContentsMargins(24, 20, 24, 20)
        body.setSpacing(14)
        body.addWidget(stage)
        body.addLayout(findings_row)
        body.addLayout(buttons)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(build_header("⬡", marking, "#14B8A6"))
        layout.addLayout(body, 1)
        self.setLayout(layout)

    @staticmethod
    def _finding_caption(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("color: #6B6B6B; font-size: 12px;")
        # A fixed width so the two lists start at the same edge: "SEM result"
        # and "IC result" are not the same length, and left aligned rows would
        # otherwise step in and out.
        label.setFixedWidth(72)
        return label

    def _finding_combo(self, choices, current) -> NoScrollComboBox:
        """One finding's list, on the value it already holds."""
        combo = NoScrollComboBox()
        combo.setMinimumHeight(34)
        combo.setMinimumWidth(118)
        combo.setStyleSheet(_DIALOG_COMBO_STYLE)
        for value, label in choices:
            combo.addItem(label, value)
        combo.setCurrentIndex(max(0, combo.findData(str(current or ""))))
        return combo

    def findings(self) -> dict:
        """What the operator has recorded, read whichever way the dialog closed."""
        return {
            IC_RESULT: str(self.ic_combo.currentData() or IC_PENDING),
            SEM_RESULT: str(self.sem_combo.currentData() or SEM_PENDING),
        }

    def _on_change_status(self) -> None:
        """Choose a column from a list rather than dragging the card to it.

        Closes this dialog on a real change, so the findings are saved and the
        move lands through the one path the board already has for it.
        """
        dialog = StageChoiceDialog(self, marking=self._marking, current=self._stage)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.chosen is None:
            return
        if dialog.chosen == self._stage:
            return  # nothing to do, and a write for nothing moves the watermark
        self.chosen_stage = dialog.chosen
        self._choose("move")

    def _choose(self, action: str) -> None:
        self.action = action
        self.accept()


# How a card that has just arrived settles into place. On a tablet the finger
# covers the target, so the drop is confirmed after the hand has moved away
# rather than while it is still in the way.
SETTLE_MS = 260
SETTLE_TICK_MS = 30
SETTLE_LIFT = 10          # pixels above its place that it eases down from


class CardTextDelegate(QStyledItemDelegate):
    """Keeps a card's text colour when it is selected, and eases arrivals in.

    A selected item is painted with the palette's HighlightedText, which
    ignores the per item foreground and comes out white. This feeds the card's
    own colour back in, so a cracked or IC failed marking stays yellow or red
    while it is picked up, and an ordinary one stays dark instead of vanishing.
    """

    DEFAULT_TEXT = "#1F1F1F"

    def initStyleOption(self, option, index) -> None:  # noqa: N802 (Qt naming)
        super().initStyleOption(option, index)
        brush = index.data(Qt.ItemDataRole.ForegroundRole)
        colour = (brush.color() if isinstance(brush, QBrush)
                  else QColor(brush) if brush is not None
                  else QColor(self.DEFAULT_TEXT))
        option.palette.setColor(QPalette.ColorRole.HighlightedText, colour)

    def paint(self, painter, option, index) -> None:  # noqa: N802 (Qt naming)
        """Draw the card, lifted while it is still arriving.

        The offset comes from the list it belongs to, which the board keeps in
        step: a delegate is shared by every row, so it cannot hold the state of
        one card itself.
        """
        offset = 0
        lift = getattr(self.parent(), "settle_offset", None)
        if callable(lift):
            offset = lift(str(index.data() or ""))
        if not offset:
            super().paint(painter, option, index)
            return
        painter.save()
        painter.translate(0, -offset)
        super().paint(painter, option, index)
        painter.restore()


# The dragged marking, carried in the drag itself. Reading it back off the
# source list at drop time is not safe: a refresh clears every list, and a
# background poll can land while a card is being held, leaving the source with
# no current item and throwing the drop away.
MARKING_MIME = "application/x-qc-electrode"
# Several cards can travel in one drag, so the payload is a list.
MARKING_SEPARATOR = "\n"


class ElectrodeDropList(QListWidget):
    """A stage/type cell. Accepts drops only from lists of the same type."""

    electrodes_dropped = pyqtSignal(list, object, object)  # markings, stage, type
    drag_finished = pyqtSignal()
    # A plain click here owns the highlight; every other cell has to drop its
    # own, which no single list can do for itself.
    selection_claimed = pyqtSignal(object)

    # How many cards are being dragged anywhere on the board, so the tab knows
    # not to rebuild itself under the pointer.
    _drags_in_progress = 0

    def __init__(self, stage: QcStage, electrode_type: ElectrodeType, parent=None) -> None:
        super().__init__(parent)
        self.stage = stage
        self.electrode_type = electrode_type

        # Filled in by the board: everything highlighted anywhere, since a
        # selection can span cells and a list cannot see its siblings.
        self.selected_cards_provider = None
        # And how far a card that is still arriving should be lifted. The board
        # owns the animation; a list only draws what it is told.
        self.settle_offset = None

        self.setStyleSheet(_LIST_STYLE)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setItemDelegate(CardTextDelegate(self))
        # A marking is ~7 characters; anything wider is wasted board space.
        self.setMinimumWidth(66)

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        """Shift and Ctrl add to the highlight, a plain click starts it over."""
        adding = event.modifiers() & (Qt.KeyboardModifier.ShiftModifier
                                      | Qt.KeyboardModifier.ControlModifier)
        if not adding:
            self.selection_claimed.emit(self)
        super().mousePressEvent(event)

    @classmethod
    def drag_in_progress(cls) -> bool:
        """True while a card is being held anywhere on the board."""
        return cls._drags_in_progress > 0

    def startDrag(self, supported_actions) -> None:  # noqa: N802 (Qt naming)
        """Lift the card out of the list for the length of the drag.

        Built by hand rather than through the default implementation, for three
        reasons. The card is hidden while it is in the air, so it reads as
        picked up rather than copied. Its picture has to be grabbed before it
        is hidden, or there would be nothing to drag. And the default deletes
        the row itself on a move, while this board is drawn from the store
        rather than from whatever Qt did to the list.

        Everything highlighted travels together, wherever on the board it is
        highlighted. Qt has already made the pressed card the selection unless
        a modifier was held, so a plain drag still carries one.

        Qt blocks in ``exec`` for the whole drag, so this brackets it.
        """
        item = self.currentItem()
        if item is None:
            return

        cards = list(self.selected_cards_provider() if self.selected_cards_provider
                     else [(self, i) for i in self.selectedItems()])
        if (self, item) not in cards:
            cards = [(self, item)]

        # Built from this list's items so Qt's own formats stay valid, then the
        # payload is widened to every card in the air, siblings included.
        data = self.mimeData([card for owner, card in cards if owner is self])
        markings = [card.text().strip() for _owner, card in cards if card.text().strip()]
        data.setData(MARKING_MIME, MARKING_SEPARATOR.join(markings).encode("utf-8"))

        rect = self.visualItemRect(item)
        drag = QDrag(self)
        drag.setMimeData(data)
        drag.setPixmap(self._drag_pixmap(cards, item))
        drag.setHotSpot(self.viewport().mapFromGlobal(QCursor.pos()) - rect.topLeft())

        ElectrodeDropList._drags_in_progress += 1
        for _owner, card in cards:
            card.setHidden(True)
        try:
            drag.exec(Qt.DropAction.MoveAction)
        finally:
            ElectrodeDropList._drags_in_progress -= 1
            # Back where they started, or the drag was abandoned. A drop that
            # went through is redrawn from the store a moment later anyway.
            for _owner, card in cards:
                try:
                    card.setHidden(False)
                except RuntimeError:
                    pass  # Qt disposed of it
            self.drag_finished.emit()

    def _drag_pixmap(self, cards, pressed) -> QPixmap:
        """The picture under the pointer: the pressed card, with the rest of
        the highlighted ones fanned out behind it so the count is visible."""
        rect = self.visualItemRect(pressed)
        face = self.viewport().grab(rect)
        others = min(len(cards) - 1, 3)
        if others <= 0:
            return face

        step = 4
        canvas = QPixmap(face.width() + others * step, face.height() + others * step)
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        for depth in range(others, 0, -1):
            painter.setOpacity(0.35)
            painter.drawPixmap(depth * step, depth * step, face)
        painter.setOpacity(1.0)
        painter.drawPixmap(0, 0, face)
        painter.end()
        return canvas

    def mimeData(self, items):  # noqa: N802 (Qt naming)
        """Put the markings in the drag, so the drop does not have to find them."""
        data = super().mimeData(items)
        markings = [i.text().strip() for i in items if i.text().strip()]
        if markings:
            data.setData(MARKING_MIME, MARKING_SEPARATOR.join(markings).encode("utf-8"))
        return data

    @staticmethod
    def _dragged_markings(event) -> list[str]:
        """The markings being dragged, from the drag's own payload."""
        raw = event.mimeData().data(MARKING_MIME)
        if not raw:
            return []
        return [m for m in bytes(raw).decode("utf-8").split(MARKING_SEPARATOR) if m.strip()]

    def _refusal(self, source) -> str:
        """Why a drop from ``source`` is not allowed here, or '' if it is."""
        if not isinstance(source, ElectrodeDropList):
            return f"source is not a board cell ({type(source).__name__})"
        if source.stage == self.stage:
            return f"already at {self.stage}"
        return ""

    def _accepts(self, source) -> bool:
        """A different stage, that is the whole rule.

        Deliberately not the electrode type. Which half of a column a card
        belongs in is a fact about the electrode, not a choice the operator
        makes with the pointer, so a cathode dropped anywhere on a column is
        moved to that column and drawn in its cathode half. Aiming at the right
        half of a narrow column was work with no decision in it.
        """
        return not self._refusal(source)

    def dragEnterEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        event.acceptProposedAction() if self._accepts(event.source()) else event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        event.acceptProposedAction() if self._accepts(event.source()) else event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if not self._accepts(event.source()):
            event.ignore()
            return

        # Read from the drag, not from the source list: a refresh can clear and
        # rebuild that list while the cards are in the air, leaving nothing to
        # ask.
        markings = self._dragged_markings(event)
        if not markings:
            event.ignore()
            return

        # The store is the source of truth; the tab re-renders both lists from
        # it rather than letting Qt shuffle rows behind our back. Only the stage
        # is acted on: the type travels with the electrode and decides which
        # half it reappears in, whichever half was dropped on.
        event.accept()
        self.electrodes_dropped.emit(markings, self.stage, self.electrode_type)


class StageDropZone(QWidget):
    """A whole stage column as a drop target: gaps, header and captions too.

    Aiming at the strip between the Anodes and Cathodes lists used to show the
    no-entry cursor, because that strip belongs to the card and the card took
    no drops. Since which half a card lands in is decided by the electrode and
    not by the pointer, the entire column can accept it.

    The space BETWEEN two columns still refuses, deliberately: there is no one
    stage it could mean.
    """

    electrodes_dropped = pyqtSignal(list, object, object)  # markings, stage, type

    def __init__(self, stage: QcStage, parent=None) -> None:
        super().__init__(parent)
        self.stage = stage
        self.setAcceptDrops(True)

    def _accepts(self, source) -> bool:
        return isinstance(source, ElectrodeDropList) and source.stage != self.stage

    def dragEnterEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        event.acceptProposedAction() if self._accepts(event.source()) else event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        event.acceptProposedAction() if self._accepts(event.source()) else event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        markings = ElectrodeDropList._dragged_markings(event)
        if not self._accepts(event.source()) or not markings:
            event.ignore()
            return
        event.accept()
        self.electrodes_dropped.emit(markings, self.stage, None)


_DIALOG_COMBO_STYLE = """
    QComboBox {
        background-color: #FFFFFF;
        border: 1px solid #D4D4D4;
        border-radius: 6px;
        padding: 8px 10px;
        font-size: 13px;
        color: #1F1F1F;
    }
    QComboBox:focus { border: 1.5px solid #55BDBD; }
    QComboBox::drop-down {
        subcontrol-origin: padding;
        subcontrol-position: center right;
        width: 28px;
        border-left: 1px solid #E1E1E1;
        background-color: #F5F6F7;
        border-top-right-radius: 5px;
        border-bottom-right-radius: 5px;
    }
    QComboBox::drop-down:hover { background-color: #EBEDEF; }
    /* No arrow asset ships with the app, so draw one out of borders. */
    QComboBox::down-arrow {
        width: 0px; height: 0px;
        border-left: 5px solid transparent;
        border-right: 5px solid transparent;
        border-top: 6px solid #6B7280;
    }
    QComboBox::down-arrow:on { top: 1px; }
    QComboBox QAbstractItemView {
        border: 1px solid #E1E1E1;
        background-color: #FFFFFF;
        selection-background-color: #E6F6F6;
        selection-color: #1F1F1F;
        outline: none;
    }
"""


# Only the frame. Every row is painted by TickDelegate, so there are
# deliberately no ::item rules here to argue with it.
_PICK_LIST_STYLE = """
    QListWidget {
        background-color: #FFFFFF;
        border: 1px solid #D4D4D4;
        border-radius: 6px;
        font-size: 13px;
        outline: none;
    }
"""


class AddElectrodeDialog(QDialog):
    """Pick electrodes to put on the board.

    Two lists: the untested ones (the normal case) and the ones that already
    have a verdict (a re-test). Which list a marking is in is the answer to
    "has this been tested", so the search leaves them where they are and filters
    both rather than merging them into one list that would have to say so in
    words.

    TICK BOXES RATHER THAN A DROPDOWN. A day's testing is a handful of
    electrodes, and adding them one at a time meant reopening this window for
    each. Both lists are checked into one basket and added together.
    """

    def __init__(self, parent=None, not_tested: list[str] | None = None,
                 tested: list[str] | None = None) -> None:
        super().__init__(parent)
        self.markings: list[str] = []
        # Built once: a fresh icon per row is twelve thousand renders.
        self._UNTICKED = qta.icon("fa6.square", color="#C9CDD3")
        self._TICKED = qta.icon("fa6s.square-check", color="#16A34A")
        self.setWindowTitle("Add electrodes to QC")
        self.setMinimumWidth(520)
        self.setStyleSheet("QDialog { background-color: #FFFFFF; }")

        self.search = QLineEdit()
        self.search.setMinimumHeight(36)
        self.search.setPlaceholderText("Search for an electrode")
        self.search.setStyleSheet(INPUT_FIELD_STYLE)
        self.search.textChanged.connect(self._on_search)

        self.new_list, self.new_caption = self._build_list(not_tested or [])
        self.retest_list, self.retest_caption = self._build_list(tested or [])
        self._totals = {id(self.new_list): len(not_tested or []),
                        id(self.retest_list): len(tested or [])}
        self._labels = {id(self.new_list): "Not tested",
                        id(self.retest_list): "Already tested"}
        for widget in (self.new_list, self.retest_list):
            widget.itemChanged.connect(self._on_ticked)
        self._show_counts()

        cancel = QPushButton("Cancel")
        cancel.setMinimumHeight(38)
        cancel.setStyleSheet(SECONDARY_BUTTON_STYLE)
        cancel.clicked.connect(self.reject)

        self.add_button = QPushButton("Add")
        self.add_button.setMinimumHeight(38)
        self.add_button.setMinimumWidth(110)
        self.add_button.setStyleSheet(PRIMARY_BUTTON_STYLE)
        self.add_button.setEnabled(False)
        apply_modern_shadow(self.add_button)
        self.add_button.clicked.connect(self._on_accept)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(cancel, 0)
        buttons.addWidget(self.add_button, 0)

        body = QVBoxLayout()
        body.setContentsMargins(24, 20, 24, 20)
        body.setSpacing(8)
        body.addWidget(self.search)
        body.addSpacing(4)
        body.addWidget(self.new_caption)
        body.addWidget(self.new_list, 1)
        body.addSpacing(4)
        body.addWidget(self.retest_caption)
        body.addWidget(self.retest_list, 1)
        body.addSpacing(6)
        body.addLayout(buttons)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(build_header("\u2795", "Add electrodes to QC", "#14B8A6"))
        layout.addLayout(body, 1)
        self.setLayout(layout)
        self.search.setFocus()

    # ---- building -----------------------------------------------------
    @staticmethod
    def _caption(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("color: #1F1F1F; font-size: 12px; font-weight: 600;")
        return label

    def _build_list(self, markings: list) -> tuple:
        """A tickable list of markings, and the caption above it.

        The whole row toggles, not a small box at its edge: this is a tablet
        first window and a 15 pixel target is not one.
        """
        widget = QListWidget()
        widget.setStyleSheet(_PICK_LIST_STYLE + SCROLLBAR_STYLE)
        widget.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        widget.setMinimumHeight(120)
        widget.setUniformItemSizes(True)     # 12k rows, all the same height
        widget.setItemDelegate(TickDelegate(self._TICKED, self._UNTICKED, widget))
        widget.setMouseTracking(True)        # so the hover tint follows
        for marking in markings:
            item = QListWidgetItem(str(marking))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            widget.addItem(item)
        widget.setEnabled(bool(markings))
        widget.itemClicked.connect(self._toggle)
        return widget, self._caption("")

    @staticmethod
    def _toggle(item) -> None:
        """Clicking anywhere on a row ticks it, or unticks it."""
        item.setCheckState(
            Qt.CheckState.Unchecked
            if item.checkState() == Qt.CheckState.Checked
            else Qt.CheckState.Checked)

    # ---- searching ----------------------------------------------------
    def _on_search(self, text: str) -> None:
        """Filter both lists. A ticked electrode stays ticked while hidden."""
        needle = str(text).strip().upper()
        for widget in (self.new_list, self.retest_list):
            for row in range(widget.count()):
                item = widget.item(row)
                item.setHidden(bool(needle) and needle not in item.text().upper())
        self._show_counts()

    def _visible(self, widget) -> int:
        return sum(1 for row in range(widget.count())
                   if not widget.item(row).isHidden())

    def _show_counts(self) -> None:
        """Each caption says how many are on offer, and how many are ticked.

        The caption is also the answer to whether an electrode has been tested:
        a search leaves each marking in its own list rather than merging them.
        """
        for widget, caption in ((self.new_list, self.new_caption),
                                (self.retest_list, self.retest_caption)):
            total = self._totals[id(widget)]
            visible = self._visible(widget)
            ticked = len(self._checked(widget))
            shown = f"{visible:,} of {total:,}" if visible != total else f"{total:,}"
            text = f"{self._labels[id(widget)]}  ({shown})".replace(",", " ")
            caption.setText(f"{text}   {ticked} chosen" if ticked else text)

    # ---- choosing -----------------------------------------------------
    @staticmethod
    def _checked(widget) -> list:
        return [widget.item(row).text() for row in range(widget.count())
                if widget.item(row).checkState() == Qt.CheckState.Checked]

    def _on_ticked(self, item) -> None:
        # Nothing to set: the delegate reads the check state when it paints.
        item.listWidget().viewport().update()

        chosen = self._checked(self.new_list) + self._checked(self.retest_list)
        self.add_button.setEnabled(bool(chosen))
        self.add_button.setText(f"Add {len(chosen)}" if len(chosen) > 1 else "Add")
        self._show_counts()

    def _on_accept(self) -> None:
        self.markings = self._checked(self.new_list) + self._checked(self.retest_list)
        if self.markings:
            self.accept()


class QcBoardWidget(QWidget):
    """The QC board: stages across, electrode types within each stage."""

    board_changed = pyqtSignal()

    def __init__(self, parent=None, board_store=None,
                 on_preview_finalise=None, on_finalise=None, on_undo=None,
                 on_check_for_updates=None) -> None:
        super().__init__(parent)
        self.store = board_store
        # Injected: the propagation maths lives in the backend, not here.
        self.on_preview_finalise = on_preview_finalise
        self.on_finalise = on_finalise
        self.on_undo = on_undo
        self.on_check_for_updates = on_check_for_updates
        # marking -> {"type": ..., "status": ...}, rebuilt whenever electrodes load.
        self._index: dict[str, dict] = {}
        self._lists: dict[tuple[QcStage, ElectrodeType], ElectrodeDropList] = {}
        # ``{marking: how far through its arrival it is}``, 0 to 1.
        self._settling: dict[str, float] = {}
        self._settle_timer = QTimer(self)
        self._settle_timer.setInterval(SETTLE_TICK_MS)
        self._settle_timer.timeout.connect(self._advance_settle)
        self._counts: dict[QcStage, QLabel] = {}
        # A redraw asked for while a card was in the air, applied when it lands.
        self._refresh_pending = False

        self.init_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def init_ui(self) -> None:
        main_layout, content_container, content_layout = create_styled_container_layout()

        title = QLabel("QC")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setMinimumHeight(25)
        title.setMaximumHeight(40)

        self.add_button = QPushButton("Add electrode")
        self.add_button.setMinimumHeight(30)
        self.add_button.setStyleSheet(TOOLBAR_BUTTON_STYLE)
        self.add_button.setIcon(qta.icon("fa6s.plus", color="white"))
        apply_modern_shadow(self.add_button)
        self.add_button.clicked.connect(self._on_add_clicked)


        self.undo_button = QPushButton("Undo")
        self.undo_button.setMinimumHeight(30)
        self.undo_button.setStyleSheet(TOOLBAR_BUTTON_STYLE)
        self.undo_button.setIcon(qta.icon("fa6s.rotate-left", color="white"))
        apply_modern_shadow(self.undo_button)
        self.undo_button.clicked.connect(self._on_undo_clicked)

        self.finalise_button = QPushButton("Mark all as done")
        self.finalise_button.setMinimumHeight(30)
        self.finalise_button.setStyleSheet(TOOLBAR_BUTTON_STYLE)
        self.finalise_button.setIcon(qta.icon("fa6s.check", color="white"))
        apply_modern_shadow(self.finalise_button)
        self.finalise_button.clicked.connect(self._on_finalise_clicked)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(6)
        toolbar.addWidget(title, 1)
        toolbar.addWidget(self.undo_button, 0)
        toolbar.addWidget(self.finalise_button, 0)
        toolbar.addWidget(self.add_button, 0)

        # Stages sit side by side; the row scrolls on narrow screens rather
        # than squeezing seven cards into the viewport.
        stages_row = QHBoxLayout()
        stages_row.setContentsMargins(0, 0, 0, 0)
        stages_row.setSpacing(8)
        # The two terminal stages are set apart: everything left of the divider
        # is work in progress, everything right of it is a verdict.
        separated = False
        for stage in STAGE_ORDER:
            # Two breaks in the row: after the stages an electrode can reach
            # before its tests report, and before the two verdicts.
            if stage in STAGE_STATUS and not separated:
                stages_row.addSpacing(OUTCOME_GAP)
                stages_row.addWidget(self._build_outcome_divider(), 0)
                stages_row.addSpacing(OUTCOME_GAP)
                separated = True
            elif STAGE_ORDER.index(stage) == STAGE_ORDER.index(
                    self.LAST_STAGE_WITHOUT_RESULTS) + 1:
                stages_row.addSpacing(OUTCOME_GAP)
                stages_row.addWidget(self._build_outcome_divider(), 0)
                stages_row.addSpacing(OUTCOME_GAP)
            # Stretch 0 so the cards keep their width instead of spreading to
            # fill the window; the trailing stretch absorbs the leftover space.
            stages_row.addWidget(self._build_stage_card(stage), 0)
        stages_row.addStretch(1)

        stages_host = QWidget()
        stages_host.setLayout(stages_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }" + SCROLLBAR_STYLE)
        scroll.setWidget(stages_host)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # Without an explicit minimum, the scroll area reports the full width of
        # all seven cards upward and the window can't be narrowed past it. An
        # explicit minimum wins over that hint, so the board scrolls instead.
        scroll.setMinimumWidth(200)

        # A click on bare board, beside the columns or below them, drops the
        # highlight. Filtered rather than overridden: these are plain containers
        # and the press never reaches this widget through them.
        for surface in (stages_host, scroll.viewport(), content_container, self):
            surface.installEventFilter(self)

        content_layout.addLayout(toolbar, 0)
        content_layout.addSpacing(8)
        content_layout.addWidget(scroll, 1)
        content_container.setLayout(content_layout)

        main_layout.addWidget(content_container, 1)
        self.setLayout(main_layout)

    @staticmethod
    def _build_outcome_divider() -> QWidget:
        """The line between the workflow and the two verdicts."""
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.VLine)
        divider.setFixedWidth(1)
        divider.setStyleSheet("background-color: #E5E7EB; border: none;")
        return divider

    def _build_stage_card(self, stage: QcStage) -> QWidget:
        """One stage: a coloured header over an Anodes and a Cathodes list."""
        background, foreground = STAGE_HEADERS.get(stage, DEFAULT_HEADER)

        header = QLabel(STAGE_LABELS[stage])
        header.setWordWrap(True)
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.setMinimumHeight(42)
        header.setStyleSheet(
            f"background-color: {background}; color: {foreground};"
            f"border: 1px solid #E5E7EB; border-radius: 3px;"
            f"font-size: 12px; font-weight: 700; padding: 6px;")

        count = QLabel("0")
        count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        count.setStyleSheet("color: #6B6B6B; font-size: 11px;")
        self._counts[stage] = count

        lists_row = QHBoxLayout()
        lists_row.setContentsMargins(0, 0, 0, 0)
        lists_row.setSpacing(6)
        for electrode_type in (ElectrodeType.ANODE, ElectrodeType.CATHODE):
            lists_row.addLayout(self._build_list_column(stage, electrode_type), 1)

        card_layout = QVBoxLayout()
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(6)
        card_layout.addWidget(header, 0)
        card_layout.addWidget(count, 0)
        card_layout.addLayout(lists_row, 1)

        card = StageDropZone(stage)
        card.setFixedWidth(150)
        card.setLayout(card_layout)
        card.electrodes_dropped.connect(self._on_electrodes_dropped)
        return card

    def _build_list_column(self, stage: QcStage, electrode_type: ElectrodeType) -> QVBoxLayout:
        caption = QLabel("Anodes" if electrode_type == ElectrodeType.ANODE else "Cathodes")
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        caption.setStyleSheet("color: #6B6B6B; font-size: 11px; font-weight: 600;")

        drop_list = ElectrodeDropList(stage, electrode_type)
        drop_list.selection_claimed.connect(self._clear_selection_except)
        drop_list.selected_cards_provider = self._selected_cards
        drop_list.settle_offset = self._settle_offset
        drop_list.electrodes_dropped.connect(self._on_electrodes_dropped)
        drop_list.drag_finished.connect(self._on_drag_finished)
        drop_list.itemDoubleClicked.connect(
            lambda item, s=stage, t=electrode_type: self._on_electrode_opened(item, s, t))
        self._lists[(stage, electrode_type)] = drop_list

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(3)
        column.addWidget(caption, 0)
        column.addWidget(drop_list, 1)
        return column

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------
    def load_electrodes(self, dataframe: pd.DataFrame | None) -> None:
        """Take the electrode list the Electrodes tab uses, as the source of
        truth for what exists, its type and its status."""
        self._index = {}
        if dataframe is None or dataframe.empty:
            self.refresh()
            return

        # Column-wise, not iterrows() — at ~12k rows that difference is seconds.
        markings = dataframe[str(ElectrodeColumns.MARKING)].astype(str).str.strip().str.upper()
        types = dataframe[str(ElectrodeColumns.FULL_NAME)].astype(str).str.strip().str.upper()
        statuses = dataframe[STATUS_COLUMN].astype(str).str.strip()
        self._index = {
            marking: {"type": electrode_type, "status": status}
            for marking, electrode_type, status in zip(markings, types, statuses)
            if marking
        }
        self.refresh()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802 (Qt naming)
        """A press on anything that is not a card clears the highlight."""
        if event.type() == QEvent.Type.MouseButtonPress:
            self._clear_selection_except(None)
        return super().eventFilter(obj, event)

    def _selected_cards(self) -> list:
        """``[(cell, card)]`` for every highlighted electrode on the board.

        Board order, so a multi-card drag moves them in the order they are
        read rather than in the order they happened to be clicked.
        """
        return [(widget, widget.item(row))
                for widget in self._lists.values()
                for row in range(widget.count())
                if widget.item(row).isSelected()]

    def _clear_selection_except(self, keep=None) -> None:
        """One highlight across the whole board, not one per cell.

        Each cell is its own list widget, so Qt would happily leave a card lit
        in every one of the sixteen. Shift and Ctrl skip this, which is what
        makes picking several possible.
        """
        for widget in self._lists.values():
            if widget is not keep:
                widget.clearSelection()

    def _settle(self, markings) -> None:
        """Start the arrival animation for the cards that just moved."""
        wanted = {str(m).strip() for m in markings if str(m).strip()}
        if not wanted:
            return
        self._settling = {marking: 0.0 for marking in wanted}
        self._settle_timer.start()
        self._repaint_cards()

    def _advance_settle(self) -> None:
        """One step of the ease, stopping when the last card has landed."""
        step = SETTLE_TICK_MS / SETTLE_MS
        self._settling = {marking: progress + step
                          for marking, progress in self._settling.items()
                          if progress + step < 1.0}
        if not self._settling:
            self._settle_timer.stop()
        self._repaint_cards()

    def _repaint_cards(self) -> None:
        for widget in self._lists.values():
            widget.viewport().update()

    def _settle_offset(self, marking: str) -> int:
        """How far above its place a card still is, in pixels."""
        progress = self._settling.get(str(marking).strip())
        if progress is None:
            return 0
        # Ease out: it drops in quickly and comes to rest gently.
        return round(SETTLE_LIFT * (1 - progress) ** 2)

    def refresh(self) -> None:
        """Re-render every cell from the store.

        Deferred while a card is being dragged. Rebuilding the lists then makes
        the card vanish from under the pointer, and used to lose the drop with
        it. The pending redraw happens the moment the drag ends.
        """
        if self.store is None:
            return
        if ElectrodeDropList.drag_in_progress():
            self._refresh_pending = True
            return
        self._refresh_pending = False
        for (stage, electrode_type), widget in self._lists.items():
            widget.blockSignals(True)
            widget.clear()
            for marking in self.store.markings(stage, electrode_type):
                item = QListWidgetItem(marking)
                item.setSizeHint(QSize(0, 28))  # even card heights
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                colour = card_colour(self.store.findings(marking))
                if colour:
                    item.setForeground(QBrush(QColor(colour)))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                widget.addItem(item)
            widget.blockSignals(False)

        for stage, label in self._counts.items():
            total = sum(len(self.store.markings(stage, t)) for t in ElectrodeType)
            label.setText(f"{total} electrode{'s' if total != 1 else ''}")

        decided = len(self.store.status_overrides())
        self.finalise_button.setEnabled(decided > 0)
        can_undo = self.store.can_undo()
        self.undo_button.setEnabled(can_undo)
        self.undo_button.setToolTip(f"Undo {self.store.undo_label()}"
                                    if can_undo else "Nothing to undo")

    def _on_drag_finished(self) -> None:
        """A card was let go: catch up on any redraw held back during it."""
        if getattr(self, "_refresh_pending", False):
            self.refresh()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    # As far as a card can go while a result is still outstanding. Everything
    # from Waiting for approval onwards is a statement about tests that have
    # been run, so it cannot be reached before they have.
    LAST_STAGE_WITHOUT_RESULTS = QcStage.IC_DONE

    # What is missing, said as the operator would say it.
    MISSING_LABELS = ((SEM_RESULT, SEM_PENDING, "The SEM result"),
                      (IC_RESULT, IC_PENDING, "The IC result"))

    def _missing_results(self, marking: str) -> list[str]:
        """Which of the two tests this electrode has no result for.

        A crack settles it on its own: the electrode can be sent anywhere
        without waiting for the IC result, since no IC result would release a
        cracked electrode. Nothing moves it by itself, that stays a decision.
        """
        if self.store is None:
            return []
        findings = self.store.findings(marking)
        if str(findings.get(SEM_RESULT) or "") == SEM_CRACKED:
            return []
        return [label for field, pending, label in self.MISSING_LABELS
                if str(findings.get(field) or "") == pending]

    def _too_far(self, markings: list, stage: QcStage) -> dict:
        """``{marking: what is missing}`` for the cards that cannot go there."""
        order = list(STAGE_ORDER)
        if stage not in order:
            return {}
        if order.index(stage) <= order.index(self.LAST_STAGE_WITHOUT_RESULTS):
            return {}
        blocked = {}
        for marking in markings:
            missing = self._missing_results(str(marking))
            if missing:
                blocked[str(marking)] = missing
        return blocked

    def _refuse_move(self, blocked: dict, stage: QcStage) -> None:
        """Say which test each card is still waiting on."""
        lines = []
        for marking, missing in blocked.items():
            names = " and ".join(missing).replace("The IC result", "the IC result")
            lines.append(f"{marking}: {names} is not recorded yet."
                         if len(missing) == 1
                         else f"{marking}: {names} are not recorded yet.")
        ValidationErrorScrollDialog(
            parent=self, errors=lines,
            title=f"Not ready for {STAGE_LABELS.get(stage, stage)}").exec()

    def _on_electrodes_dropped(self, markings: list, stage: QcStage, _type) -> None:
        """Move everything that was dropped, then redraw once.

        One refresh and one board_changed for the whole handful: each of those
        re-processes 12k electrodes, and doing it per card would make moving
        five of them five times the work for the same result.

        A card whose tests have not both reported is refused rather than moved,
        and told which one is outstanding.
        """
        if self.store is None:
            return

        blocked = self._too_far(list(markings), stage)
        if blocked:
            self._refuse_move(blocked, stage)
            markings = [m for m in markings if str(m) not in blocked]
            if not markings:
                return
        # Every move runs: any() would stop at the first one that worked and
        # leave the rest of the handful behind.
        moved = [m for m in markings if self.store.move(str(m), stage)]
        if moved:
            self.refresh()
            self._settle(moved)
            self.board_changed.emit()

    def _on_add_clicked(self) -> None:
        """Offer the untested and already-tested electrodes, then place the
        chosen one in the right half of Waiting."""
        on_board = set()
        if self.store is not None:
            on_board = {m for m in self._index if self.store.contains(m)}

        # Split by whether the electrode still awaits a verdict. Needs testing
        # and Not Tested are the ones you would actually put through QC;
        # anything already Released or Scrapped only comes up for a re-test.
        awaiting = {str(ElectrodeStatus.NOT_TESTED),
                    str(ElectrodeStatus.NEEDS_TESTING)}

        not_tested, tested = [], []
        for marking, entry in self._index.items():
            if marking in on_board:
                continue  # already in the workflow, offering it again is noise
            (not_tested if entry["status"] in awaiting else tested).append(marking)

        dialog = AddElectrodeDialog(self, not_tested=not_tested, tested=tested)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.markings:
            return

        # Every one that can go on goes on, and the rest are reported together.
        # Stopping at the first problem would throw away the others' ticks.
        errors: list[str] = []
        added = 0
        for marking in dialog.markings:
            problems = self._add_errors(marking)
            if problems:
                errors.extend(problems)
                continue
            self.store.add(marking, ElectrodeType(self._index[marking]["type"]))
            added += 1

        if added:
            self.refresh()
            self.board_changed.emit()
        if errors:
            ValidationErrorScrollDialog(
                parent=self, errors=errors,
                title="Not every electrode could be added").exec()

    def _add_errors(self, marking: str) -> list[str]:
        """Everything that would stop this electrode entering the workflow."""
        errors: list[str] = []
        entry = self._index.get(marking)

        if entry is None:
            errors.append(f"{marking}: no such electrode in the database")
            return errors

        if entry["type"] not in {str(t) for t in ElectrodeType}:
            errors.append(f"{marking}: unknown electrode type {entry['type']!r} — "
                          f"expected ANODE or CATHODE")

        # An already-tested electrode is allowed in deliberately — that's what
        # the re-test list is for. Only the type and duplication rules bind.
        if self.store is not None and self.store.contains(marking):
            errors.append(f"{marking}: already on the board")

        return errors

    def _on_electrode_opened(self, item, stage: QcStage, electrode_type) -> None:
        """Double click on a card: show what can be done with that electrode."""
        if item is None or self.store is None:
            return
        marking = item.text()

        dialog = ElectrodeDetailDialog(
            self, marking=marking, stage_label=STAGE_LABELS[stage],
            electrode_type=str(electrode_type),
            findings=self.store.findings(marking), stage=stage)
        result = dialog.exec()

        # Findings are saved however the dialog closed, since ticking a box is
        # a record of what was seen rather than something to confirm.
        recorded = dialog.findings()
        if self.store.set_findings(marking, recorded):
            self.refresh()
            # The Electrodes tab is told either way. A crack recorded against a
            # scrapped electrode changes which run its doubt travels along, so
            # up to five statuses move without any card moving.
            if not self._follow_findings(marking, stage, recorded):
                self.board_changed.emit()

        if result != QDialog.DialogCode.Accepted:
            return

        if dialog.action == "delete":
            self._remove_electrode(marking)
        elif dialog.action == "move" and dialog.chosen_stage is not None:
            self._on_electrodes_dropped([marking], dialog.chosen_stage, None)

    # Where a pair of results puts a card. Both instruments have to have
    # reported: one result on its own says nothing about where the electrode
    # belongs, and the operator is often waiting on the other.
    FINDING_MOVES = {
        # The IC has run and the SEM is clean, so the testing work is finished
        # and the card queues for somebody to look at it.
        (IC_DONE, SEM_ACCEPTED): QcStage.WAITING_APPROVAL,
        # Both instruments say yes, but releasing is a person's call: the card
        # queues for approval rather than deciding the electrode itself.
        (IC_PASSED, SEM_ACCEPTED): QcStage.WAITING_APPROVAL,
        (IC_FAILED, SEM_ACCEPTED): QcStage.WAITING_FOR_RETEST,
        (IC_PASSED, SEM_CRACKED): QcStage.WAITING_FOR_RETEST,
        # Both instruments say no, so there is nothing left to retest.
        (IC_FAILED, SEM_CRACKED): QcStage.SCRAPPED,
    }

    # Where a pair means something different because of where the card already
    # is. A pass on a card already queued for approval IS the approval, so it
    # releases; the same pass earlier in the flow only queues it.
    FINDING_MOVES_FROM = {
        QcStage.WAITING_APPROVAL: {(IC_PASSED, SEM_ACCEPTED): QcStage.RELEASE},
    }

    def _follow_findings(self, marking: str, stage: QcStage,
                         recorded: dict) -> bool:
        """Move the card to wherever the two results put it.

        True when it moved, and the caller then leaves the announcing to this,
        so one edit is one repaint of the Electrodes tab rather than two.

        Fires wherever the card currently is, a finished retest included: the
        results are the answer, and a card sitting somewhere they contradict is
        the thing worth correcting. Where the card is can also change what the
        results mean, see FINDING_MOVES_FROM.
        """
        pair = (str(recorded.get(IC_RESULT) or ""),
                str(recorded.get(SEM_RESULT) or ""))
        wanted = self.FINDING_MOVES_FROM.get(stage, {}).get(
            pair, self.FINDING_MOVES.get(pair))
        if wanted is None or stage == wanted:
            return False
        if self.store is None or not self.store.move(marking, wanted):
            return False
        self.refresh()
        self.board_changed.emit()
        return True

    def _remove_electrode(self, marking: str) -> None:
        """Take one electrode off the board entirely, once confirmed.

        Asked because the card is the only record that this electrode was in
        QC at all: its stage and its findings go with it, and nothing on the
        Electrodes tab shows that it was ever there.
        """
        if self.store is None:
            return

        findings = self.store.findings(marking)
        recorded = [label for value, label in
                    ((findings.get(SEM_RESULT), f"SEM {findings.get(SEM_RESULT)}"),
                     (findings.get(IC_RESULT), f"IC {findings.get(IC_RESULT)}"))
                    if value]
        noted = (f"<br><br>What has been recorded against it "
                 f"({', '.join(recorded)}) goes with it.") if recorded else ""

        dialog = ConfirmationDialog(
            parent=self, title="Delete from board",
            message=(f"Take {marking} off the board?{noted}<br><br>"),
            yes_text="Delete", no_text="Keep")
        if dialog.exec() != ConfirmationDialog.DialogCode.Accepted:
            return

        if self.store.remove(marking):
            self.refresh()
            self.board_changed.emit()

    def _on_finalise_clicked(self) -> None:
        """Freeze every decided electrode, after a confirmation."""
        if not callable(self.on_preview_finalise) or not callable(self.on_finalise):
            return
        decided, _affected = self.on_preview_finalise()
        if not decided:
            return

        message = (f"Are you sure you want to mark all {decided} electrodes as done?"
                   if decided != 1
                   else "Are you sure you want to mark this electrode as done?")
        dialog = ConfirmationDialog(
            parent=self, title="Mark as done", message=message,
            icon="✓", header_color="#16A34A",
            yes_text="Yes, mark as done", no_text="Cancel")
        if dialog.exec() != ConfirmationDialog.DialogCode.Accepted:
            return

        self.on_finalise()
        self.refresh()
        self.board_changed.emit()

    def _on_undo_clicked(self) -> None:
        """Reverse the last board change. No confirmation: undo is itself the
        correction, and one step back is cheap to redo by hand."""
        if not callable(self.on_undo) or self.store is None:
            return
        if not self.on_undo():
            return

        self.refresh()
        self.board_changed.emit()

    def apply_external_updates(self, result) -> bool:
        """Adopt a board another operator changed. Runs on the UI thread.

        The payload is the whole board, read on the worker thread. It is handed
        to the store here rather than there, so a render can never catch the
        store half filled.
        """
        if not result or not result.get("has_updates"):
            return False
        if self.store is not None and result.get("data") is not None:
            # A snapshot older than our own last write would put a card the
            # operator just dragged back where it came from.
            if not self.store.apply(result["data"]):
                return False

        self.refresh()
        self.board_changed.emit()
        return True

    def check_for_external_updates(self) -> bool:
        """Poll and apply in one go, for callers with no worker thread."""
        if not callable(self.on_check_for_updates):
            return False
        return self.apply_external_updates(self.on_check_for_updates())
