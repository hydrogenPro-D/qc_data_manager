"""The headline numbers along the top of every screen.

One figure each, large enough to read from the far side of the room, with a
caption underneath saying what it is and a footnote saying what it is out of.
The number counts up to its value rather than appearing, which is the one bit
of movement on the screen that carries information: it tells anyone glancing
over that the board is live and has just been refreshed.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QVariantAnimation, QEasingCurve
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .. import palette as p
from .card import Card, grouped

# Long enough to be seen as counting, short enough to be finished well before
# the deck rotates. At four seconds a screen this has to be comfortably under
# a second or the number is still moving when the slide leaves.
COUNT_MS = 620


class KpiTile(Card):
    """One big number, its caption, and an optional footnote.

    ``suffix`` is drawn smaller and tucked against the number, so "97" and "%"
    read as one figure rather than as a number beside a symbol.
    """

    def __init__(self, caption: str, accent: str = p.TEAL_MID,
                 suffix: str = "", parent=None) -> None:
        super().__init__(accent=accent, parent=parent)
        self._accent = accent
        self._shown = 0.0
        # What it is counting towards, which is not what the label reads
        # while the count is still running.
        self._target = 0.0
        self._decimals = 0

        self._number = QLabel("0")
        number_font = QFont(p.NUMERAL_FAMILY)
        number_font.setPointSize(p.KPI_NUMBER_PT)
        number_font.setWeight(QFont.Weight.Bold)
        self._number.setFont(number_font)
        self._number.setStyleSheet(
            f"color: {accent}; background: transparent;")

        self._suffix = QLabel(suffix)
        suffix_font = QFont(p.NUMERAL_FAMILY)
        suffix_font.setPointSize(p.KPI_NUMBER_PT // 2)
        suffix_font.setWeight(QFont.Weight.Bold)
        self._suffix.setFont(suffix_font)
        self._suffix.setStyleSheet(f"color: {accent}; background: transparent;")
        self._suffix.setVisible(bool(suffix))

        figure = QHBoxLayout()
        figure.setContentsMargins(0, 0, 0, 0)
        figure.setSpacing(3)
        figure.addWidget(self._number, 0, Qt.AlignmentFlag.AlignBottom)
        figure.addWidget(self._suffix, 0, Qt.AlignmentFlag.AlignBottom)
        figure.addStretch(1)

        self._caption = QLabel(caption.upper())
        self._caption.setStyleSheet(
            p.title_style(p.KPI_CAPTION_PT, p.INK_MUTED, 700, "1.6px"))

        self._note = QLabel("")
        self._note.setStyleSheet(
            p.title_style(p.KPI_CAPTION_PT - 1, p.INK_FAINT, 500, "0.2px"))
        self._note.setVisible(False)

        holder = QVBoxLayout()
        holder.setContentsMargins(0, 0, 0, 0)
        holder.setSpacing(2)
        holder.addLayout(figure, 0)
        holder.addWidget(self._caption, 0)
        holder.addWidget(self._note, 0)
        wrapper = QWidget()
        wrapper.setLayout(holder)
        self.body.addWidget(wrapper, 1)

        # One animation reused for the life of the tile. Building a new one per
        # refresh leaves the old one running when a refresh lands mid count,
        # and two animations driving one label fight over it.
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(COUNT_MS)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._render)

    # ------------------------------------------------------------------
    def set_value(self, value: float, note: str = "", decimals: int = 0) -> None:
        """Count from whatever is on screen to ``value``.

        From the current value rather than from zero: a refresh that nudges
        1 284 to 1 290 should look like a nudge, not like the board reloading.
        """
        self._decimals = decimals
        self._note.setText(note)
        self._note.setVisible(bool(note))

        target = self._target = float(value)
        self._animation.stop()
        if abs(target - self._shown) < 10 ** -(decimals + 1):
            self._render(target)
            return
        self._animation.setStartValue(self._shown)
        self._animation.setEndValue(target)
        self._animation.start()

    def value(self) -> float:
        """The figure the tile stands for, settled or still counting."""
        return self._target

    def set_accent(self, accent: str) -> None:
        """Recolour the tile, for a figure that has crossed a threshold."""
        if accent == self._accent:
            return
        self._accent = accent
        self.setStyleSheet(p.card_style(accent))
        for widget in (self._number, self._suffix):
            widget.setStyleSheet(f"color: {accent}; background: transparent;")

    def _render(self, value) -> None:
        self._shown = float(value)
        self._number.setText(
            f"{self._shown:.{self._decimals}f}" if self._decimals
            else grouped(self._shown))
