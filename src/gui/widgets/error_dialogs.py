"""Consistently styled dialogs: warnings, confirmations, validation-error lists.

Every screen surfaces problems through these instead of a bare QMessageBox, so
headers, colors and button sizes stay identical across the app.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QWidget,
    QFrame,
)
from PyQt6.QtCore import Qt

from ..styles.button_styles import (
    DANGER_BUTTON_STYLE,
    PRIMARY_BUTTON_STYLE,
    SECONDARY_BUTTON_STYLE,
    apply_modern_shadow,
)
from ..styles.table_styles import SCROLLBAR_STYLE


def build_header(icon: str, title: str, color: str) -> QWidget:
    """The colored header band every dialog in this module wears."""
    header = QWidget()
    header.setStyleSheet(f"background-color: {color};")
    header_layout = QHBoxLayout()
    header_layout.setContentsMargins(20, 14, 20, 14)
    header_layout.setSpacing(10)

    icon_label = QLabel(icon)
    icon_label.setStyleSheet("font-size: 20px; color: #FFFFFF; background: transparent;")
    title_label = QLabel(title)
    title_label.setStyleSheet(
        "color: #FFFFFF; font-size: 15px; font-weight: 700; background: transparent;"
    )
    header_layout.addWidget(icon_label, 0)
    header_layout.addWidget(title_label, 1)
    header.setLayout(header_layout)
    return header


class WarningDialog(QDialog):
    """A titled message dialog with a colored header band and a single OK."""

    def __init__(
        self,
        parent=None,
        title: str = "Warning",
        message: str = "",
        icon: str = "⚠️",
        header_color: str = "#FF9800",
        button_color: str | None = None,
        ok_text: str = "OK",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(460)
        self.setStyleSheet("QDialog { background-color: #FFFFFF; }")

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(build_header(icon, title, header_color))

        body = QVBoxLayout()
        body.setContentsMargins(24, 20, 24, 20)
        body.setSpacing(16)
        body.addWidget(self._body_widget(message), 1)
        body.addLayout(self._buttons(button_color or header_color, ok_text))
        layout.addLayout(body, 1)

        self.setLayout(layout)

    def _body_widget(self, message: str) -> QWidget:
        label = QLabel(message)
        label.setWordWrap(True)
        label.setStyleSheet("color: #1F1F1F; font-size: 13px;")
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        return label

    def _buttons(self, color: str, ok_text: str) -> QHBoxLayout:
        self.ok_button = QPushButton(ok_text)
        self.ok_button.setMinimumHeight(40)
        self.ok_button.setMinimumWidth(120)
        self.ok_button.setStyleSheet(PRIMARY_BUTTON_STYLE)
        apply_modern_shadow(self.ok_button)
        self.ok_button.clicked.connect(self.accept)

        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.ok_button, 0)
        return row


class ConfirmationDialog(QDialog):
    """Yes/No confirmation with the same header band as WarningDialog."""

    def __init__(
        self,
        parent=None,
        title: str = "Confirm",
        message: str = "",
        icon: str = "⚠️",
        header_color: str = "#FF5252",
        yes_text: str = "Yes",
        no_text: str = "No",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(460)
        self.setStyleSheet("QDialog { background-color: #FFFFFF; }")

        header = build_header(icon, title, header_color)

        message_label = QLabel(message)
        message_label.setWordWrap(True)
        message_label.setStyleSheet("color: #1F1F1F; font-size: 13px;")

        self.no_button = QPushButton(no_text)
        self.no_button.setMinimumHeight(40)
        self.no_button.setMinimumWidth(120)
        self.no_button.setStyleSheet(SECONDARY_BUTTON_STYLE)
        self.no_button.clicked.connect(self.reject)

        self.yes_button = QPushButton(yes_text)
        self.yes_button.setMinimumHeight(40)
        self.yes_button.setMinimumWidth(120)
        self.yes_button.setStyleSheet(PRIMARY_BUTTON_STYLE)
        apply_modern_shadow(self.yes_button)
        self.yes_button.clicked.connect(self.accept)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.no_button, 0)
        buttons.addWidget(self.yes_button, 0)

        body = QVBoxLayout()
        body.setContentsMargins(24, 20, 24, 20)
        body.setSpacing(16)
        body.addWidget(message_label, 1)
        body.addLayout(buttons)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(header)
        layout.addLayout(body, 1)
        self.setLayout(layout)


class ValidationErrorScrollDialog(WarningDialog):
    """Scrollable list of validation errors (or warnings) as bulleted rows."""

    def __init__(
        self,
        parent=None,
        errors: list[str] | None = None,
        title: str = "Validation Errors",
        header_color: str = "#EF5350",
        icon: str = "❌",
        subtitle_noun: str = "error",
    ) -> None:
        """Args:
            subtitle_noun: What to call the rows in the count above them. Pass
                an empty string to drop that line, for a dialog whose rows read
                as a report rather than as a list of faults.
        """
        self._errors = errors or []
        self._subtitle_noun = subtitle_noun
        super().__init__(
            parent=parent, title=title, message="", icon=icon,
            header_color=header_color, button_color=header_color,
        )
        self.setMinimumWidth(520)

    def _body_widget(self, message: str) -> QWidget:
        count = len(self._errors)
        plural = "" if count == 1 else "s"
        subtitle = QLabel(f"{count} {self._subtitle_noun}{plural} found:"
                          if self._subtitle_noun else "")
        subtitle.setVisible(bool(self._subtitle_noun))
        subtitle.setStyleSheet("color: #6B6B6B; font-size: 12px; font-weight: 600;")

        items = QWidget()
        items_layout = QVBoxLayout()
        items_layout.setContentsMargins(0, 0, 0, 0)
        items_layout.setSpacing(6)
        for error in self._errors:
            row = QLabel(f"•  {error}")
            row.setWordWrap(True)
            row.setStyleSheet(
                "color: #1F1F1F; font-size: 13px; background-color: #F9FAFB;"
                "border: 1px solid #E4E4E4; border-radius: 6px; padding: 8px 10px;"
            )
            items_layout.addWidget(row)
        items_layout.addStretch(1)
        items.setLayout(items_layout)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { border: none; }" + SCROLLBAR_STYLE)
        scroll.setWidget(items)
        scroll.setMinimumHeight(120)
        scroll.setMaximumHeight(360)

        container = QWidget()
        container_layout = QVBoxLayout()
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(10)
        container_layout.addWidget(subtitle, 0)
        container_layout.addWidget(scroll, 1)
        container.setLayout(container_layout)
        return container


class ErrorSavingDialog(WarningDialog):
    """The write failed — show the backend's message verbatim."""

    def __init__(self, parent=None, error: str = "") -> None:
        super().__init__(
            parent=parent,
            title="Error Saving Data",
            message=(
                "The entry could not be saved:\n\n"
                f"{error}\n\n"
                "Nothing was written. Try again, and contact the administrator if "
                "the problem persists."
            ),
            icon="❌",
            header_color="#EF5350",
        )




class ConfirmDialog(QDialog):
    """A yes or no, wearing the same header band as the rest of them.

    QMessageBox is the obvious thing to reach for and it looks like nothing
    else in this application: a system icon, a system font and buttons in the
    platform's order. This asks the same question in the application's own
    clothes.

    THE CONFIRMING BUTTON SAYS WHAT IT DOES, "Request" rather than "Yes", so
    an operator reading only the buttons still knows what they are agreeing
    to. Cancel is the default, so a stray Enter does nothing.
    """

    def __init__(self, parent=None, title: str = "Confirm",
                 question: str = "", confirm_text: str = "Yes",
                 icon: str = "?", header_color: str = "#14B8A6",
                 danger: bool = False, extra: QWidget | None = None) -> None:
        """``extra`` is a widget placed under the question, for a confirmation
        that also has to ask something: the coupons a request wants, say. The
        caller keeps the widget and reads it after exec()."""
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(420)
        self.setStyleSheet("QDialog { background-color: #FFFFFF; }")

        prompt = QLabel(question)
        prompt.setWordWrap(True)
        prompt.setStyleSheet("color: #1F1F1F; font-size: 14px; font-weight: 600;")

        cancel = QPushButton("Cancel")
        cancel.setMinimumHeight(38)
        cancel.setStyleSheet(SECONDARY_BUTTON_STYLE)
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        cancel.setDefault(True)

        confirm = QPushButton(confirm_text)
        confirm.setMinimumHeight(38)
        confirm.setMinimumWidth(120)
        confirm.setStyleSheet(DANGER_BUTTON_STYLE if danger
                              else PRIMARY_BUTTON_STYLE)
        confirm.setCursor(Qt.CursorShape.PointingHandCursor)
        apply_modern_shadow(confirm)
        confirm.clicked.connect(self.accept)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(cancel, 0)
        buttons.addWidget(confirm, 0)

        body = QVBoxLayout()
        body.setContentsMargins(24, 22, 24, 20)
        body.setSpacing(18)
        body.addWidget(prompt, 1)
        if extra is not None:
            body.addWidget(extra, 0)
        body.addLayout(buttons)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(build_header(icon, title, header_color))
        layout.addLayout(body, 1)
        self.setLayout(layout)


def ask(parent, title: str, question: str, confirm_text: str = "Yes",
        icon: str = "?", danger: bool = False) -> bool:
    """Put the question and return whether it was agreed to."""
    dialog = ConfirmDialog(parent, title=title, question=question,
                           confirm_text=confirm_text, icon=icon, danger=danger)
    return dialog.exec() == QDialog.DialogCode.Accepted
