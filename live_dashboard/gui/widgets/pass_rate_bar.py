"""Passed against failed, as one bar the width of the card.

The QC block is only three lines, which left most of its card empty, and empty
space on a wall screen reads as a panel that failed to load. This fills it with
the one comparison those three lines are actually about: of everything that was
tested, how much passed.

A single split bar rather than two, because the question is a proportion. Two
bars beside each other make the reader do the division themselves.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QRectF, QVariantAnimation, QEasingCurve
from PyQt6.QtGui import QColor, QFont, QFontMetricsF, QPainter
from PyQt6.QtWidgets import QSizePolicy, QWidget

from src.data_access.electrodes.electrode_columns import ElectrodeStatus

from .. import palette as p

GROW_MS = 700
BAR_H = 40
RADIUS = 10
# Below this share a segment has no room for its number inside, so the label is
# dropped rather than drawn over the edge of the other colour.
MIN_INSIDE = 0.13


class PassRateBar(QWidget):
    """One bar split by pass and fail, each segment labelled with its count."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(BAR_H + 30)
        self._passed = 0
        self._failed = 0
        self._progress = 1.0

        self._grow = QVariantAnimation(self)
        self._grow.setDuration(GROW_MS)
        self._grow.setStartValue(0.0)
        self._grow.setEndValue(1.0)
        self._grow.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._grow.valueChanged.connect(self._on_frame)

    def set_counts(self, passed: int, failed: int) -> None:
        self._passed, self._failed = int(passed), int(failed)
        self._grow.stop()
        self._progress = 0.0
        self._grow.start()

    def _on_frame(self, value) -> None:
        self._progress = float(value)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        try:
            self._paint(painter)
        finally:
            painter.end()

    def _paint(self, painter: QPainter) -> None:
        width = float(self.width())
        total = self._passed + self._failed

        caption = QFont()
        caption.setPointSize(p.KPI_CAPTION_PT - 1)
        caption.setBold(True)
        painter.setFont(caption)
        painter.setPen(QColor(p.INK_FAINT))
        painter.drawText(QRectF(0, 0, width, 20),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         "PASSED AGAINST FAILED")

        top = 24.0
        painter.setPen(Qt.PenStyle.NoPen)
        if total <= 0:
            painter.setBrush(QColor(p.CARD_EDGE))
            painter.drawRoundedRect(QRectF(0, top, width, BAR_H), RADIUS, RADIUS)
            painter.setPen(QColor(p.INK_FAINT))
            painter.drawText(QRectF(0, top, width, BAR_H),
                             Qt.AlignmentFlag.AlignCenter, "Nothing tested yet")
            return

        share = self._passed / total
        # Both ends are drawn as one rounded bar first, then the failed part is
        # painted over its right end. Rounding each segment separately leaves a
        # notch where they meet, which reads as a gap in the data.
        painter.setBrush(QColor(p.STATUS_ACCENTS[
            str(ElectrodeStatus.TESTED_AND_RELEASED)]))
        painter.drawRoundedRect(QRectF(0, top, width, BAR_H), RADIUS, RADIUS)

        fail_w = width * (1 - share) * self._progress
        if fail_w > 1:
            painter.save()
            painter.setClipRect(QRectF(width - fail_w, top, fail_w, BAR_H))
            painter.setBrush(QColor(p.CORAL))
            painter.drawRoundedRect(QRectF(0, top, width, BAR_H), RADIUS, RADIUS)
            painter.restore()

        self._segment_label(painter, 0, width - fail_w, top, self._passed,
                            share, "passed", Qt.AlignmentFlag.AlignLeft)
        self._segment_label(painter, width - fail_w, fail_w, top, self._failed,
                            1 - share, "failed", Qt.AlignmentFlag.AlignRight)

    def _segment_label(self, painter: QPainter, left: float, span: float,
                       top: float, count: int, share: float, word: str,
                       align) -> None:
        """The most of the label that actually fits inside one segment.

        MEASURED, not guessed from the share. A segment can be a quarter of a
        wide bar and still be too narrow for "1 failed 25%", and Qt does not
        drop text it cannot fit, it clips it: the screen read "AILED 25%".
        Three lengths are tried and the first that fits wins; if none does, the
        segment carries its colour alone and the row above says the numbers.
        """
        if share < MIN_INSIDE:
            return
        font = QFont()
        font.setPointSize(p.KPI_CAPTION_PT)
        font.setBold(True)
        painter.setFont(font)
        metrics = QFontMetricsF(font)

        pad = 14.0
        room = span - pad * 2
        percent = f"{share * 100:.0f}%"
        for text in (f"{count} {word}   {percent}", f"{count} {word}", percent):
            if metrics.horizontalAdvance(text) <= room:
                painter.setPen(QColor("#FFFFFF"))
                painter.drawText(QRectF(left + pad, top, room, BAR_H),
                                 align | Qt.AlignmentFlag.AlignVCenter, text)
                return
