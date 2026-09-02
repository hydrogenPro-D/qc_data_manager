"""A small turning ring, for the moment a dialog is doing something.

Hand painted rather than an animated GIF or a qtawesome spin, for the same
reason the loading skeleton is: it is a timer and an arc, and owning the tick
means the rest of the app can share its rhythm and its colour.
"""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QWidget

TICK_MS = 60
# Degrees per tick. A full turn in about a second and a half, which reads as
# working rather than as racing.
STEP = 24
# How much of the ring is drawn. A gap is what makes the turning visible.
ARC_DEGREES = 280


class BusySpinner(QWidget):
    """Turns while it is shown, and takes no space when it is not."""

    SIZE = 18
    THICKNESS = 2
    COLOR = "#14B8A6"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._advance)
        self.hide()

    def start(self) -> None:
        """Show it and begin turning."""
        self.show()
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        """Stop and take it away. Safe when it was never started."""
        self._timer.stop()
        self.hide()

    @property
    def running(self) -> bool:
        return self._timer.isActive()

    def _advance(self) -> None:
        self._angle = (self._angle + STEP) % 360
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor(self.COLOR))
        pen.setWidth(self.THICKNESS)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        inset = self.THICKNESS
        # Qt counts arcs in sixteenths of a degree, anticlockwise from 3
        # o'clock, so the angle is negated to turn the way a clock does.
        painter.drawArc(
            QRectF(inset, inset,
                   self.width() - 2 * inset, self.height() - 2 * inset),
            int(-self._angle * 16), int(ARC_DEGREES * 16))
        painter.end()
