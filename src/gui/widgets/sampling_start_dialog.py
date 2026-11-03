"""Where the count starts when automatic sampling is switched on.

The rule counts from the last electrode requested in a vat, or the last one
tested in it. While the switch was off production carried on, so that anchor is
behind: switching on without moving it would ask for a run of electrodes made
weeks ago, one a minute, none of them fetchable.

So switching on moves each vat's anchor to the newest electrode it has, and
this dialog asks what happens next. Either answer is reasonable, which is why
it is a question rather than a rule: after a shutdown QC usually wants to see
the first electrode off the line, and after a pause in the middle of a run they
usually do not.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QButtonGroup, QDialog, QHBoxLayout, QLabel, QPushButton, QRadioButton,
    QVBoxLayout,
)

from ..styles.button_styles import (
    PRIMARY_BUTTON_STYLE, SECONDARY_BUTTON_STYLE, apply_modern_shadow,
)
from .error_dialogs import build_header

TITLE = "Switch sampling on"
ACCENT = "#16A34A"
QUESTION = "Which electrode should QC be asked for first?"
FIRST_LABEL = "The very next electrode made"
INTERVAL_LABEL = "Only after a full interval"

_RADIO_STYLE = f"""
    QRadioButton {{ color: #1F1F1F; font-size: 13px; font-weight: 600;
                    spacing: 9px; background: transparent; border: none; }}
    QRadioButton::indicator {{ width: 16px; height: 16px; border-radius: 9px;
                               border: 1px solid #C7CDD1;
                               background: #FFFFFF; }}
    QRadioButton::indicator:hover {{ border: 1px solid {ACCENT}; }}
    QRadioButton::indicator:checked {{ border: 1px solid {ACCENT};
                                       background: {ACCENT}; }}
"""
_QUESTION_STYLE = ("color: #1F1F1F; font-size: 13px; font-weight: 700;"
                   "background: transparent; border: none;")


def interval_suffix(intervals: dict) -> str:
    """How far a full interval is, for the end of that option's own line.

    In the label rather than under it: a reader choosing between the two wants
    the actual figure, and there are three of them at most.
    """
    figures = {str(vat): int(value) for vat, value in (intervals or {}).items()}
    if not figures:
        return ""
    distinct = set(figures.values())
    if len(distinct) == 1:
        return f" (one in every {distinct.pop()})"
    listed = ", ".join(f"{vat} every {figures[vat]}" for vat in sorted(figures))
    return f" ({listed})"


class SamplingStartDialog(QDialog):
    """Ask where the count starts, and answer with ``start_now``."""

    def __init__(self, parent=None, intervals: dict | None = None) -> None:
        super().__init__(parent)
        self.start_now: bool | None = None
        self.setWindowTitle(TITLE)
        self.setMinimumWidth(430)
        self.setStyleSheet("QDialog { background-color: #FFFFFF; }")

        self.first = QRadioButton(FIRST_LABEL)
        self.after = QRadioButton(INTERVAL_LABEL + interval_suffix(intervals))
        for button in (self.first, self.after):
            button.setStyleSheet(_RADIO_STYLE)
        # Grouped explicitly, for the reason the range dialog gives: left to
        # Qt the group is the parent widget's, which is not always this one.
        self.group = QButtonGroup(self)
        self.group.addButton(self.first)
        self.group.addButton(self.after)
        self.first.setChecked(True)

        cancel = QPushButton("Cancel")
        cancel.setMinimumHeight(38)
        cancel.setStyleSheet(SECONDARY_BUTTON_STYLE)
        cancel.clicked.connect(self.reject)

        switch_on = QPushButton("Switch on")
        switch_on.setMinimumHeight(38)
        switch_on.setMinimumWidth(120)
        switch_on.setStyleSheet(PRIMARY_BUTTON_STYLE)
        apply_modern_shadow(switch_on)
        switch_on.clicked.connect(self._on_accept)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(cancel, 0)
        buttons.addWidget(switch_on, 0)

        body = QVBoxLayout()
        body.setContentsMargins(24, 18, 24, 20)
        body.setSpacing(12)
        body.addWidget(_caption(QUESTION, _QUESTION_STYLE))
        body.addWidget(self.first)
        body.addWidget(self.after)
        body.addSpacing(4)
        body.addLayout(buttons)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(build_header("⏱", TITLE, ACCENT))
        layout.addLayout(body, 1)
        self.setLayout(layout)

    def _on_accept(self) -> None:
        self.start_now = self.first.isChecked()
        self.accept()


def _caption(text: str, style: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(style)
    return label
