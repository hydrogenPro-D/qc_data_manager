"""Set where the QC Sampling tab's range boxes start.

Each end is either a fixed marking or one of the two moving edges, the oldest
or newest electrode on record. The edges are stored as words rather than as the
number they resolve to today, because "up to the newest electrode" has to stay
true tomorrow.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QButtonGroup, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QRadioButton, QVBoxLayout, QWidget,
)

from src.data_access import app_config
from ..styles.button_styles import (
    PRIMARY_BUTTON_STYLE, SECONDARY_BUTTON_STYLE, apply_modern_shadow,
)
from ..styles.input_styles import INPUT_FIELD_STYLE
from .error_dialogs import build_header

TITLE = "Default range"
ACCENT = "#14B8A6"

# Every state spelled out. Setting a size on ::indicator and nothing else
# switches Qt from drawing the native control to drawing the stylesheet's, and
# an unstyled state then draws NOTHING: the checked option had no dot at all.
#
# The two states are deliberately the same total size, 16 plus a 1px border
# either side, so the row does not jump as the choice moves.
_RADIO_STYLE = f"""
    QRadioButton {{ color: #1F1F1F; font-size: 13px; spacing: 9px;
                    background: transparent; border: none; }}
    QRadioButton::indicator {{ width: 16px; height: 16px; border-radius: 9px;
                               border: 1px solid #C7CDD1;
                               background: #FFFFFF; }}
    QRadioButton::indicator:hover {{ border: 1px solid {ACCENT}; }}
    QRadioButton::indicator:checked {{ border: 1px solid {ACCENT};
                                       background: {ACCENT}; }}
"""
# Transparent on purpose. The dialog opens as a child of the sampling tab,
# whose own "background" rule cascades into every plain widget in here, which
# painted a grey band behind each label.
_CAPTION_STYLE = ("color: #1F1F1F; font-size: 12px; font-weight: 700;"
                  "background: transparent; border: none;")
_PROBLEM_STYLE = ("color: #C81E1E; font-size: 12px; font-weight: 600;"
                  "background: transparent; border: none;")
NEEDS_A_MARKING = "Type a marking, or pick the electrode on record instead."
BACKWARDS = "The end of the range cannot be earlier than the start."


class _End(QWidget):
    """One end of the range: an edge, or a marking typed in."""

    def __init__(self, caption: str, edge_label: str, edge_value: str,
                 value, marking=None) -> None:
        super().__init__()
        self.edge_value = edge_value
        # Scoped, so it cannot reach a tooltip belonging to anything in here:
        # a bare selector matches the tip window too, which then paints nothing
        # and comes out black on Windows.
        self.setObjectName("rangeEnd")
        self.setStyleSheet("QWidget#rangeEnd { background: transparent; }")

        self.edge = QRadioButton(edge_label)
        self.fixed = QRadioButton("This marking")
        for button in (self.edge, self.fixed):
            button.setStyleSheet(_RADIO_STYLE)
        # Grouped explicitly. Two ends in one dialog means two groups, and left
        # to Qt they would share the parent's and pick one radio between them.
        self.group = QButtonGroup(self)
        self.group.addButton(self.edge)
        self.group.addButton(self.fixed)

        self.field = QLineEdit()
        self.field.setStyleSheet(INPUT_FIELD_STYLE)
        self.field.setFixedWidth(130)
        self.field.setMinimumHeight(32)
        self.field.setPlaceholderText("B14000")
        # Enabled only for the option it belongs to, so which one is in force
        # is visible rather than inferred from a dot.
        self.fixed.toggled.connect(self.field.setEnabled)
        # Anything the reader changes is an answer to the complaint, so the
        # complaint goes rather than sitting there contradicting the screen.
        self.changed = self.field.textEdited

        line = QHBoxLayout()
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(10)
        line.addWidget(self.fixed, 0)
        line.addWidget(self.field, 0)
        line.addStretch(1)

        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(7)
        box.addWidget(_caption(caption))
        box.addWidget(self.edge, 0)
        box.addLayout(line)
        self.set_value(value, marking)

    def set_value(self, value, marking=None) -> None:
        """Show one stored end: an edge word, or a number.

        ``marking`` is what the tab's own range box says right now, and it wins
        for the text. Opening this on a range you have just set and finding a
        different number in the box is the surprise worth avoiding: the button
        sits beside those boxes, so it reads as being about them.
        """
        shown = marking if marking is not None else value
        self.field.setText(f"B{shown}" if isinstance(shown, int) else "")
        self.edge.setChecked(value == self.edge_value)
        self.fixed.setChecked(value != self.edge_value)
        # toggled only fires on a CHANGE, and the first call here is often not
        # one, so the field would open enabled whatever was chosen.
        self.field.setEnabled(self.fixed.isChecked())

    def value(self):
        """The chosen end, or None when a marking was asked for and not given."""
        if self.edge.isChecked():
            return self.edge_value
        digits = "".join(c for c in self.field.text() if c.isdigit())
        return int(digits) if digits else None


def _backwards(start, end) -> bool:
    """True if the range ends before it starts.

    Only two fixed markings can be judged here. Anything involving an edge is
    fine by construction: the oldest electrode cannot be after another one, and
    the newest cannot be before one, whatever the numbers turn out to be on the
    day.
    """
    return isinstance(start, int) and isinstance(end, int) and end < start


def _caption(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet(_CAPTION_STYLE)
    return label


class SamplingRangeDialog(QDialog):
    """Pick the two ends the tab opens on."""

    def __init__(self, parent=None, default=(13900, app_config.NEWEST),
                 on_screen=(None, None)) -> None:
        super().__init__(parent)
        self.chosen: tuple | None = None
        self.setWindowTitle(TITLE)
        self.setMinimumWidth(400)
        self.setStyleSheet("QDialog { background-color: #FFFFFF; }")

        self.start = _End("From", "The oldest electrode on record",
                          app_config.OLDEST, default[0], on_screen[0])
        self.end = _End("To", "The newest electrode on record",
                        app_config.NEWEST, default[1], on_screen[1])

        self.problem = QLabel()
        self.problem.setWordWrap(True)
        self.problem.setStyleSheet(_PROBLEM_STYLE)
        self.problem.hide()
        for end in (self.start, self.end):
            end.changed.connect(lambda *_: self.problem.hide())
            end.fixed.toggled.connect(lambda *_: self.problem.hide())

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

        body = QVBoxLayout()
        body.setContentsMargins(24, 18, 24, 20)
        body.setSpacing(16)
        body.addWidget(self.start)
        body.addWidget(self.end)
        body.addWidget(self.problem)
        body.addSpacing(2)
        body.addLayout(buttons)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(build_header("↔", TITLE, ACCENT))
        layout.addLayout(body, 1)
        self.setLayout(layout)

    def _on_save(self) -> None:
        start, end = self.start.value(), self.end.value()
        if start is None or end is None:
            # Nothing typed in a box whose own option is chosen. Said out loud
            # rather than a fallback being saved quietly.
            self._complain(NEEDS_A_MARKING,
                           self.start if start is None else self.end)
            return
        if _backwards(start, end):
            self._complain(BACKWARDS, self.end)
            return
        self.chosen = (start, end)
        self.accept()

    def _complain(self, text: str, end: _End) -> None:
        """Say what is wrong, and put the caret where it can be fixed."""
        self.problem.setText(text)
        self.problem.show()
        end.fixed.setChecked(True)
        end.field.setFocus()
        end.field.selectAll()
