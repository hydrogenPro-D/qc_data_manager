"""A placeholder that pulses over a table whose data has not arrived yet.

The window now opens before the reads finish, so for a moment the tables have
nothing in them. Blank tables read as "there is nothing here"; a pulsing
skeleton reads as "this is coming", which is the true one.

It paints over the table rather than filling it with blank rows. Fake rows
would reach the filter bars, the row styling and the image buttons, none of
which should ever see a row that does not exist.

The pulse lives here so everything that shows a placeholder breathes at the
same rate, including the Vat column's own bars in the Electrodes tab.
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QObject, QRectF, Qt, QTimer
from PyQt6.QtGui import QBrush, QColor, QPainter
from PyQt6.QtWidgets import QWidget

# Slow enough to read as waiting rather than as working. The reads take about
# a second, so a racing placeholder would suggest the window is busy.
SHIMMER_TICK_MS = 90
SHIMMER_PERIOD = 12          # ticks in one full breath

BAR_COLOR = "#94A3B8"
SURFACE = "#FFFFFF"
_MIN_ALPHA = 0.16
_ALPHA_RANGE = 0.24


def pulse_strength(step: int) -> float:
    """Where in the breath this tick is, 0 to 1 and back.

    A triangle wave rather than a sine: it brightens and dims at one steady
    rate, which is what stops it looking like a heartbeat.
    """
    half = SHIMMER_PERIOD / 2
    position = step % SHIMMER_PERIOD
    return position / half if position < half else 2 - position / half


def bar_color(strength: float) -> QColor:
    """The placeholder grey at this point in the breath."""
    colour = QColor(BAR_COLOR)
    colour.setAlphaF(_MIN_ALPHA + _ALPHA_RANGE * min(1.0, max(0.0, strength)))
    return colour


class SkeletonOverlay(QWidget):
    """Rows of pulsing bars, sized to cover the widget it is a child of."""

    ROW_HEIGHT = 34
    HEADER_HEIGHT = 40
    BAR_HEIGHT = 9
    MARGIN = 14
    GAP = 26
    # Column widths as fractions, so the skeleton reads as a table rather than
    # as a block. Cycled, and shifted by row, so no two rows line up exactly.
    WIDTHS = (0.62, 0.86, 0.44, 0.74, 0.55, 0.68, 0.5)

    def __init__(self, parent: QWidget, columns: int = 6) -> None:
        super().__init__(parent)
        self._columns = max(1, int(columns))
        self._step = 0
        self._timer = QTimer(self)
        self._timer.setInterval(SHIMMER_TICK_MS)
        self._timer.timeout.connect(self._advance)
        # Clicks belong to whatever is underneath: this is a picture of a table
        # loading, not something to interact with.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        parent.installEventFilter(self)
        self.setGeometry(parent.rect())
        self.hide()

    def start(self) -> None:
        """Show the placeholder and begin the pulse."""
        if self.parent() is not None:
            self.setGeometry(self.parent().rect())
        self.show()
        self.raise_()
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        """Take it away. Safe to call when it was never shown."""
        self._timer.stop()
        self.hide()

    @property
    def running(self) -> bool:
        return self._timer.isActive()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        """Track the size of the widget underneath, so it stays covered."""
        if watched is self.parent() and event.type() == QEvent.Type.Resize:
            self.setGeometry(self.parent().rect())
        return False

    def _advance(self) -> None:
        self._step += 1
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(SURFACE))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        width = self.width() - 2 * self.MARGIN
        if width <= 0:
            return
        column_width = (width - self.GAP * (self._columns - 1)) / self._columns
        if column_width <= 0:
            return

        # The header sits a shade darker than the rows, the way a real one does.
        strength = pulse_strength(self._step)
        self._paint_row(painter, self.MARGIN // 2, column_width,
                        bar_color(min(1.0, strength + 0.35)), 0)

        top = self.HEADER_HEIGHT
        row = 0
        while top < self.height():
            self._paint_row(painter, top, column_width, bar_color(strength), row + 1)
            top += self.ROW_HEIGHT
            row += 1
        painter.end()

    def _paint_row(self, painter: QPainter, top: int, column_width: float,
                   colour: QColor, row: int) -> None:
        painter.setBrush(QBrush(colour))
        for column in range(self._columns):
            fraction = self.WIDTHS[(row + column * 3) % len(self.WIDTHS)]
            left = self.MARGIN + column * (column_width + self.GAP)
            painter.drawRoundedRect(
                QRectF(left, top + (self.ROW_HEIGHT - self.BAR_HEIGHT) / 2,
                       column_width * fraction, self.BAR_HEIGHT), 4, 4)
