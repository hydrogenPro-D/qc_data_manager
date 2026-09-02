"""Electrodes off the line per hour or per day, cathodes and anodes stacked.

Painted by hand rather than with matplotlib. The deck redraws three of these
every minute and slides them across the screen while it does; a matplotlib
canvas is a raster that has to be re-rendered on every resize, and the whole
chart here is a row of rectangles. Doing it in ``QPainter`` also lets the bars
grow when the window arrives, which is what gives the screen its bit of life.

Every day in the window gets a bar, including the empty ones. A chart that
skips an idle Sunday shows a flat week where there was a stoppage.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QRectF, QVariantAnimation, QEasingCurve
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QHBoxLayout, QSizePolicy, QWidget

from .. import palette as p
from .card import Card, label

GROW_MS = 700
# Room under the bars for the date labels, and over them for the peak figure.
AXIS_H = 26
HEAD_H = 20
BAR_GAP = 4
MIN_BAR_W = 3
# And a ceiling. Seven bars across a wide card with no cap become slabs
# that touch, which reads as a filled area rather than as seven days.
MAX_BAR_W = 86
# Breathing room at each end. Without it the first and last bars sit hard
# against the card's inner edge and their date labels are clipped.
SIDE_PAD = 16
# Below this many bars every one gets a date under it; above it they are
# thinned, because thirty dates in that space is a grey smear.
DENSE_LABELS = 10


class _Bars(QWidget):
    """The plot itself: one stacked bar per day, growing from the baseline."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(150)
        self._rows: list = []
        self._progress = 1.0

        self._grow = QVariantAnimation(self)
        self._grow.setDuration(GROW_MS)
        self._grow.setStartValue(0.0)
        self._grow.setEndValue(1.0)
        self._grow.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._grow.valueChanged.connect(self._on_frame)

    def set_rows(self, rows) -> None:
        """Adopt a list of ``dashboard_stats.DayRow`` and grow into it."""
        self._rows = list(rows or [])
        self._grow.stop()
        self._progress = 0.0
        self._grow.start()

    def _on_frame(self, value) -> None:
        self._progress = float(value)
        self.update()

    # ------------------------------------------------------------------
    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        try:
            self._paint(painter)
        finally:
            painter.end()

    def _paint(self, painter: QPainter) -> None:
        width, height = self.width(), self.height()
        floor = height - AXIS_H
        peak = max((row.total for row in self._rows), default=0)

        # The baseline is drawn whatever the data, so an empty window reads as
        # an empty chart rather than as a panel that failed to load.
        painter.setPen(QPen(QColor(p.CARD_EDGE), 2))
        painter.drawLine(0, floor, width, floor)
        if not self._rows or peak <= 0:
            painter.setPen(QPen(QColor(p.INK_FAINT)))
            painter.drawText(QRectF(0, 0, width, floor),
                             Qt.AlignmentFlag.AlignCenter, "Nothing produced")
            return

        count = len(self._rows)
        plot_w = max(1.0, width - SIDE_PAD * 2)
        slot = plot_w / count
        bar_w = min(MAX_BAR_W, max(MIN_BAR_W, slot - BAR_GAP))
        usable = floor - HEAD_H
        # Headroom over the tallest bar, so the peak label is never clipped and
        # the chart does not look like it is pressing against the card.
        scale = usable / (peak * 1.08)

        font = QFont()
        font.setPointSize(9)
        painter.setFont(font)

        for index, row in enumerate(self._rows):
            left = SIDE_PAD + index * slot + (slot - bar_w) / 2
            cathode_h = row.cathodes * scale * self._progress
            anode_h = row.anodes * scale * self._progress
            bottom = floor

            for value_h, colour in ((cathode_h, p.CATHODE), (anode_h, p.ANODE)):
                if value_h <= 0.4:
                    continue
                rect = QRectF(left, bottom - value_h, bar_w, value_h)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(colour))
                # Rounded only when the bar is wide enough for a radius to
                # read; on a thirty day window it would just blur the top.
                radius = min(4.0, bar_w / 3)
                painter.drawRoundedRect(rect, radius, radius)
                bottom -= value_h

            self._paint_labels(painter, row, index, count, left, bar_w,
                               floor, bottom, peak)

    def _paint_labels(self, painter: QPainter, row, index: int, count: int,
                      left: float, bar_w: float, floor: float, top: float,
                      peak: int) -> None:
        """The date under a bar, and the count over the tallest one.

        Only the peak is labelled. Numbering every bar is what turns a chart
        into a table nobody can read from a distance, and the peak is the one
        figure that gives the rest of the bars a scale.
        """
        every = 1 if count <= DENSE_LABELS else max(1, count // 8)
        if index % every == 0 or index == count - 1:
            painter.setPen(QPen(QColor(p.INK_FAINT)))
            painter.drawText(QRectF(left - 14, floor + 4, bar_w + 28, AXIS_H - 6),
                             Qt.AlignmentFlag.AlignHCenter
                             | Qt.AlignmentFlag.AlignTop, row.label)
        if row.total == peak and row.total > 0:
            painter.setPen(QPen(QColor(p.INK)))
            bold = painter.font()
            bold.setBold(True)
            painter.setFont(bold)
            painter.drawText(QRectF(left - 16, top - HEAD_H, bar_w + 32, HEAD_H),
                             Qt.AlignmentFlag.AlignHCenter
                             | Qt.AlignmentFlag.AlignBottom, str(row.total))
            bold.setBold(False)
            painter.setFont(bold)


def _key(colour: str, text: str) -> QWidget:
    """A swatch and a word, for the two things the line makes."""
    swatch = QWidget()
    swatch.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    swatch.setFixedSize(14, 14)
    swatch.setStyleSheet(f"background: {colour}; border-radius: 4px;")

    holder = QWidget()
    line = QHBoxLayout(holder)
    line.setContentsMargins(0, 0, 0, 0)
    line.setSpacing(7)
    line.addWidget(swatch, 0)
    line.addWidget(label(text, p.KPI_CAPTION_PT, p.INK_MUTED, 600), 0)
    return holder


class ThroughputCard(Card):
    """Daily output over the window, with a cathode/anode key."""

    def __init__(self, parent=None) -> None:
        super().__init__(title="Production per day", accent=p.INDIGO, parent=parent)
        self._bars = _Bars()
        self.body.addWidget(self._bars, 1)

        key = QWidget()
        key_line = QHBoxLayout(key)
        key_line.setContentsMargins(0, 0, 0, 0)
        key_line.setSpacing(18)
        key_line.addWidget(_key(p.CATHODE, "Cathode"), 0)
        key_line.addWidget(_key(p.ANODE, "Anode"), 0)
        key_line.addStretch(1)
        self.body.addWidget(key, 0)

    def set_series(self, rows, bucket: str = "day") -> None:
        """Redraw from a list of ``dashboard_stats.BucketRow``.

        ``bucket`` is the word for one bar, so the card retitles itself rather
        than claiming "per day" over a chart of hours.
        """
        rows = list(rows or [])
        self._bars.set_rows(rows)
        self.set_title(f"Production per {bucket}")
        busiest = max((row.total for row in rows), default=0)
        article = "an" if bucket == "hour" else "a"
        self.set_subtitle(f"peak {busiest} {article} {bucket}" if busiest else "")
