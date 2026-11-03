"""A pill switch: green and over to the right for on, red and left for off.

Qt has no such control. A checkbox says the same thing, but this one is read
across a room and tapped with a thumb, which is what the rest of this
application is built for.

IT DOES NOT TOGGLE ITSELF. A click emits :attr:`ToggleSwitch.requested` with
the state the reader is asking for, and the owner calls :meth:`set_state` once
the database has agreed. Throwing the switch asks a question and writes to a
table shared with another application, and a control that had already moved
would be telling the room something that had not happened yet.
"""

from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QRectF, QSize, Qt, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QAbstractButton

ON_FILL = "#16A34A"
OFF_FILL = "#E45A52"
KNOB = "#FFFFFF"
# The knob carries a hairline so it stays a disc on the green, which is close
# enough in value to white that the edge otherwise disappears at a glance.
KNOB_EDGE = "#00000022"
WIDTH = 58
HEIGHT = 30
# Room around the knob inside the pill, each side.
INSET = 3
SLIDE_MS = 150


class ToggleSwitch(QAbstractButton):
    """On or off, asked for rather than taken."""

    requested = pyqtSignal(bool)

    def __init__(self, parent=None, on: bool = False) -> None:
        super().__init__(parent)
        self._on = bool(on)
        # Where the knob is, 0 at the left and 1 at the right. Animated rather
        # than snapped, so a switch that was thrown from another machine and a
        # switch somebody just tapped look the same.
        self._travel = 1.0 if self._on else 0.0
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(WIDTH, HEIGHT)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._slide = QVariantAnimation(self)
        self._slide.setDuration(SLIDE_MS)
        self._slide.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._slide.valueChanged.connect(self._on_slide)

        self.clicked.connect(lambda: self.requested.emit(not self._on))

    def is_on(self) -> bool:
        return self._on

    def set_state(self, on: bool, animate: bool = True) -> None:
        """Show this state. Does not emit anything, so it cannot loop."""
        on = bool(on)
        if on == self._on:
            return
        self._on = on
        target = 1.0 if on else 0.0
        self._slide.stop()
        if not animate:
            self._travel = target
            self.update()
            return
        self._slide.setStartValue(self._travel)
        self._slide.setEndValue(target)
        self._slide.start()

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt naming)
        return QSize(WIDTH, HEIGHT)

    def _on_slide(self, value) -> None:
        self._travel = float(value)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # The fill crossfades with the slide, so the colour and the knob arrive
        # together rather than the colour flipping at the start.
        fill = QColor(OFF_FILL)
        fill = _blend(fill, QColor(ON_FILL), self._travel)
        if self.isDown():
            fill = _blend(fill, QColor("#000000"), 0.12)

        pill = QRectF(0, 0, self.width(), self.height())
        radius = self.height() / 2
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(pill, radius, radius)

        size = self.height() - INSET * 2
        left = INSET + self._travel * (self.width() - size - INSET * 2)
        painter.setBrush(QColor(KNOB))
        painter.setPen(QPen(QColor(KNOB_EDGE), 1))
        painter.drawEllipse(QRectF(left, INSET, size, size))
        painter.end()


def _blend(base: QColor, towards: QColor, amount: float) -> QColor:
    """``base`` moved this far towards ``towards``, 0 to 1."""
    amount = min(1.0, max(0.0, amount))
    return QColor(
        round(base.red() + (towards.red() - base.red()) * amount),
        round(base.green() + (towards.green() - base.green()) * amount),
        round(base.blue() + (towards.blue() - base.blue()) * amount))
