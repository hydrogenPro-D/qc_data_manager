"""The white card every panel on the screen is built inside.

One class rather than a stylesheet copied into five widgets: the radius, the
edge, the shadow and the heading row are decided here, so restyling the screen
is one edit. Same reasoning as the app's shared widgets.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)

from .. import palette as p


def soft_shadow(widget: QWidget, blur: int = 34, drop: int = 8,
                colour: str = p.TEAL_DEEP, alpha: int = 34) -> None:
    """Lift a card off the backdrop with a tinted shadow.

    Tinted rather than grey, and barely there. A grey shadow under a card on a
    teal-washed backdrop reads as dirt; the same shadow in the backdrop's own
    hue reads as depth.
    """
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setXOffset(0)
    effect.setYOffset(drop)
    shade = QColor(colour)
    shade.setAlpha(alpha)
    effect.setColor(shade)
    widget.setGraphicsEffect(effect)


class Card(QFrame):
    """A titled white panel. Subclasses fill ``self.body``.

    ``accent`` draws a coloured rule along the top edge, which is what keeps a
    grid of white rectangles from reading as one grey mass.
    """

    def __init__(self, title: str = "", accent: str = "", parent=None,
                 subtitle: str = "") -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.setStyleSheet(p.card_style(accent))
        soft_shadow(self)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(p.CARD_PADDING, p.CARD_PADDING - 4,
                                 p.CARD_PADDING, p.CARD_PADDING - 4)
        outer.setSpacing(10)

        self._title = QLabel(title.upper())
        self._title.setStyleSheet(p.title_style(p.CARD_TITLE_PT, p.INK_MUTED,
                                                700, "1.4px"))
        self._subtitle = QLabel(subtitle)
        self._subtitle.setStyleSheet(p.title_style(p.CARD_TITLE_PT - 2,
                                                   p.INK_FAINT, 600, "0.3px"))
        self._subtitle.setVisible(bool(subtitle))

        heading = QHBoxLayout()
        heading.setContentsMargins(0, 0, 0, 0)
        heading.setSpacing(10)
        heading.addWidget(self._title, 0)
        heading.addStretch(1)
        heading.addWidget(self._subtitle, 0)
        if title or subtitle:
            outer.addLayout(heading, 0)

        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(8)
        outer.addLayout(self.body, 1)

    def set_title(self, text: str) -> None:
        """Rename the card, for one that describes what it is showing."""
        self._title.setText(text.upper())

    def set_subtitle(self, text: str) -> None:
        """The small grey note in the heading's right corner."""
        self._subtitle.setText(text)
        self._subtitle.setVisible(bool(text))


def label(text: str, size: int = p.BODY_PT, colour: str = p.INK,
          weight: int = 500, align=None) -> QLabel:
    """A plain text label in the screen's type, since every panel needs several."""
    widget = QLabel(text)
    font = QFont()
    font.setPointSize(size)
    font.setWeight(QFont.Weight.Bold if weight >= 700 else QFont.Weight.Medium)
    widget.setFont(font)
    widget.setStyleSheet(f"color: {colour}; background: transparent;")
    if align is not None:
        widget.setAlignment(align | Qt.AlignmentFlag.AlignVCenter)
    return widget


def grouped(value) -> str:
    """A number with thin spaces for thousands, the way the report writes them."""
    return f"{int(round(value)):,}".replace(",", " ")
