"""Inputs that ignore the mouse wheel.

A combo box or a date field inside a scroll area is a trap: reaching for the
scrollbar and brushing the wheel over it silently changes the value, and on a
tablet the gesture is even easier to make by accident. Swallowing the event
costs nothing, since both still open on click.

The same reasoning as ``no_wheel_tabs``, applied to a control rather than a tab
strip.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QComboBox, QDateEdit
from PyQt6.QtCore import QEvent


class NoScrollComboBox(QComboBox):
    """A QComboBox that ignores mouse wheel scrolling."""

    def wheelEvent(self, event: QEvent) -> None:  # noqa: N802 (Qt naming)
        event.ignore()


class NoScrollDateEdit(QDateEdit):
    """A QDateEdit that ignores mouse wheel scrolling."""

    def wheelEvent(self, event: QEvent) -> None:  # noqa: N802 (Qt naming)
        event.ignore()
