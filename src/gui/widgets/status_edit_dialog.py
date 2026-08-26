"""Set one electrode's status by hand.

A deliberate correction to a single electrode — it never sweeps to neighbours,
unlike a QC-board decision. Clearing the override hands the electrode back to
the normal rules.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
)

from ..styles.button_styles import (
    PRIMARY_BUTTON_STYLE,
    SECONDARY_BUTTON_STYLE,
    apply_modern_shadow,
)
from .error_dialogs import build_header
from .no_scroll_inputs import NoScrollComboBox

_COMBO_STYLE = """
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
    QComboBox::down-arrow {
        width: 0px; height: 0px;
        border-left: 5px solid transparent;
        border-right: 5px solid transparent;
        border-top: 6px solid #6B7280;
    }
    QComboBox QAbstractItemView {
        border: 1px solid #E1E1E1;
        background-color: #FFFFFF;
        selection-background-color: #E6F6F6;
        selection-color: #1F1F1F;
        outline: none;
    }
"""


class StatusEditDialog(QDialog):
    """Pick a status for one electrode.

    There is no way to clear one. The status it replaces cannot be worked out
    again: one inherited from the tested list is not stored anywhere the app
    can reach, so "back to normal" would silently mean "back to whatever the
    ranges say", which for a tested electrode is wrong. To change a hand set
    status you set another one.
    """

    def __init__(self, parent=None, marking: str = "", current: str = "",
                 statuses: list[str] | None = None) -> None:
        super().__init__(parent)
        self.chosen: str | None = None

        self.setWindowTitle(f"Set status — {marking}")
        self.setMinimumWidth(420)
        self.setStyleSheet("QDialog { background-color: #FFFFFF; }")

        self.combo = NoScrollComboBox()
        self.combo.setMinimumHeight(38)
        self.combo.setStyleSheet(_COMBO_STYLE)
        self.combo.addItems(statuses or [])
        index = self.combo.findText(current)
        if index >= 0:
            self.combo.setCurrentIndex(index)
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
        body.setContentsMargins(24, 20, 24, 20)
        body.setSpacing(8)
        body.addWidget(self._caption(f"Status for {marking}"))
        body.addWidget(self.combo)
        body.addSpacing(6)
        body.addLayout(buttons)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(build_header("✎", f"Set status — {marking}", "#14B8A6"))
        layout.addLayout(body, 1)
        self.setLayout(layout)

    @staticmethod
    def _caption(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("color: #1F1F1F; font-size: 12px; font-weight: 600;")
        return label

    def _on_save(self) -> None:
        self.chosen = self.combo.currentText().strip()
        self.accept()
