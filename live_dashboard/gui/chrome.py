"""The furniture around the deck: the brand bar and the rotation strip.

Both exist to answer questions a person walking past would otherwise have to
ask. The header says whose screen this is and which window is on it; the strip
says that the board is rotating on its own, how far through it is, and how many
screens there are. Without that last part a static-looking board is impossible
to tell from a frozen one.
"""

from __future__ import annotations

import datetime as dt

from PyQt6.QtCore import Qt, QRectF, QTimer, QVariantAnimation, QEasingCurve
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from . import palette as p
from .widgets.card import soft_shadow

# How long the window title takes to settle after a rotation. It arrives a beat
# behind the slide, which is what makes the header feel attached to the deck
# rather than switching independently of it.
TITLE_FADE_MS = 320
DOT_SIZE = 10
DOT_GAP = 9


def _white(text: str, size: int, weight: int = 700,
           spacing: str = "0.5px", alpha: str = "255") -> QLabel:
    label = QLabel(text)
    label.setStyleSheet(
        f"color: rgba(255, 255, 255, {alpha}); font-family: {p.FONT};"
        f"font-size: {size}pt; font-weight: {weight};"
        f"letter-spacing: {spacing}; background: transparent;")
    return label


class HeaderBar(QFrame):
    """The teal bar: wordmark on the left, window title and clock on the right."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("header")
        self.setStyleSheet(p.header_style())
        self.setFixedHeight(84)
        soft_shadow(self, blur=26, drop=6, alpha=60)

        wordmark = _white("HYDROGEN", 23, 300, "2.6px")
        strong = _white("PRO", 23, 800, "2.6px")
        name = QHBoxLayout()
        name.setContentsMargins(0, 0, 0, 0)
        name.setSpacing(0)
        name.addWidget(wordmark, 0)
        name.addWidget(strong, 0)
        name_holder = QWidget()
        name_holder.setLayout(name)

        # A pill rather than more words in the same line: it separates what the
        # screen is from who it belongs to, at a glance.
        badge = QLabel("QC LIVE")
        badge.setStyleSheet(
            f"color: {p.TEAL_DEEP}; background: rgba(255,255,255,0.92);"
            f"font-family: {p.FONT}; font-size: 11pt; font-weight: 800;"
            f"letter-spacing: 1.8px; border-radius: 11px; padding: 4px 13px;")

        self._title = _white("", 27, 800, "1.6px")
        self._title.setAlignment(Qt.AlignmentFlag.AlignRight
                                 | Qt.AlignmentFlag.AlignVCenter)
        self._clock = _white("", 14, 600, "0.6px", alpha="215")
        self._clock.setAlignment(Qt.AlignmentFlag.AlignRight
                                 | Qt.AlignmentFlag.AlignVCenter)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        right.addWidget(self._title, 0)
        right.addWidget(self._clock, 0)
        right_holder = QWidget()
        right_holder.setLayout(right)

        line = QHBoxLayout(self)
        line.setContentsMargins(26, 0, 26, 0)
        line.setSpacing(16)
        line.addWidget(name_holder, 0)
        line.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)
        line.addStretch(1)
        line.addWidget(right_holder, 0)

        self._ticking = QTimer(self)
        self._ticking.setInterval(1000)
        self._ticking.timeout.connect(self._tick)
        self._ticking.start()
        self._note = ""
        self._tick()

    def set_title(self, title: str) -> None:
        self._title.setText(title.upper())

    def set_note(self, note: str) -> None:
        """A short warning beside the clock, or "" for the normal state.

        This is how an unattended screen admits it is showing stale numbers.
        Without it a database that went away at 02:00 looks exactly like a
        quiet night shift.
        """
        self._note = note
        self._tick()

    def _tick(self) -> None:
        stamp = dt.datetime.now().strftime("%a %d %b  %H:%M:%S")
        self._clock.setText(f"{self._note}   ·   {stamp}" if self._note else stamp)


class RotationStrip(QWidget):
    """A progress line and one dot per screen, so the rotation is visible."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(20)
        self._progress = 0.0
        self._count = 0
        self._index = 0

        self._sweep = QVariantAnimation(self)
        self._sweep.setStartValue(0.0)
        self._sweep.setEndValue(1.0)
        # Linear on purpose. This is a clock, and an eased clock lies about how
        # much time is left.
        self._sweep.setEasingCurve(QEasingCurve.Type.Linear)
        self._sweep.valueChanged.connect(self._on_frame)

    def set_pages(self, count: int, index: int) -> None:
        self._count, self._index = count, index
        self.update()

    def restart(self, milliseconds: int) -> None:
        """Run the line from empty to full over one rotation."""
        self._sweep.stop()
        self._sweep.setDuration(max(1, milliseconds))
        self._sweep.start()

    def _on_frame(self, value) -> None:
        self._progress = float(value)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        try:
            width, height = self.width(), self.height()
            dots_w = self._count * DOT_SIZE + max(0, self._count - 1) * DOT_GAP
            track_w = max(0, width - dots_w - 24)

            # QRectF throughout. The int overload silently fails to match on
            # a float, and PyQt6 turns an exception raised inside a paintEvent
            # into an abort, so a wall screen would simply vanish.
            painter.setPen(Qt.PenStyle.NoPen)
            top = height / 2 - 2
            painter.setBrush(QColor(p.CARD_EDGE))
            painter.drawRoundedRect(QRectF(0.0, top, track_w, 4.0), 2.0, 2.0)
            painter.setBrush(QColor(p.TEAL_MID))
            painter.drawRoundedRect(
                QRectF(0.0, top, track_w * self._progress, 4.0), 2.0, 2.0)

            left = width - dots_w
            for index in range(self._count):
                painter.setBrush(QColor(p.TEAL_DEEP if index == self._index
                                        else p.CARD_EDGE))
                painter.drawEllipse(int(left + index * (DOT_SIZE + DOT_GAP)),
                                    int(height / 2 - DOT_SIZE / 2),
                                    DOT_SIZE, DOT_SIZE)
        finally:
            painter.end()
