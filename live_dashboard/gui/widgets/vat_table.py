"""The plating vats, worst fault rate first.

The one panel on the screen that names something somebody can go and act on.
Everything else says how the plant is doing; this says which vat to look at.

Colour is the point of it, so it is never the only signal: the rate is written
out beside the bar, and the row order already says which is worst.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QWidget

from .. import palette as p
from .card import Card, label, grouped

HEADINGS = ("Vat", "Out", "Faults", "Fault rate")
# Each heading aligned the way its column is, or the two right-hand ones
# collide into one unreadable phrase.
HEAD_ALIGN = (Qt.AlignmentFlag.AlignLeft, Qt.AlignmentFlag.AlignRight,
              Qt.AlignmentFlag.AlignRight, Qt.AlignmentFlag.AlignRight)
# Column widths, in the proportion the four headings need. The vat is a short
# identifier and the rate carries a bar, so they are not equal.
STRETCH = (2, 2, 2, 5)


def _bar(rate: float, colour: str) -> QWidget:
    """The rate as a length as well as a number.

    Scaled against twice the "bad" threshold rather than against 100%. A vat
    losing 8% of its output is a serious problem and would be a nearly invisible
    sliver on a 0 to 100 scale, so the axis is chosen to make the range that
    actually occurs readable.
    """
    ceiling = p.FAULT_BAD_PCT * 2
    share = max(0.0, min(1.0, rate / ceiling)) if ceiling else 0.0
    track = QWidget()
    track.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    track.setFixedHeight(12)
    track.setStyleSheet(
        f"background: qlineargradient(x1:0, y1:0, x2:1, y2:0,"
        f"stop:0 {colour}, stop:{max(share, 0.0001):.4f} {colour},"
        f"stop:{min(1.0, share + 0.002):.4f} {p.CARD_EDGE},"
        f"stop:1 {p.CARD_EDGE}); border-radius: 6px;")
    return track


class VatCard(Card):
    """A league table of vats: output, faults and the rate, worst first."""

    def __init__(self, parent=None) -> None:
        super().__init__(title="Vat performance", accent=p.AMBER, parent=parent)
        self._built: list[QWidget] = []
        self.body.setSpacing(6)

    def _clear(self) -> None:
        while self.body.count():
            item = self.body.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()
        self._built = []

    def _heading_row(self) -> QWidget:
        row = QWidget()
        line = QHBoxLayout(row)
        line.setContentsMargins(10, 0, 12, 2)
        line.setSpacing(12)
        for index, text in enumerate(HEADINGS):
            cell = label(text.upper(), p.KPI_CAPTION_PT - 1, p.INK_FAINT, 700,
                         HEAD_ALIGN[index])
            line.addWidget(cell, STRETCH[index])
        return row

    def set_vats(self, vats) -> None:
        """Redraw from a list of ``dashboard_stats.VatRow``."""
        self._clear()
        if not vats:
            # Said plainly. A blank card on a wall reads as a broken screen,
            # and on the 24 hour window an empty vat table is normal news.
            self.body.addWidget(
                label("No vat data in this window", p.BODY_PT, p.INK_FAINT, 500,
                      Qt.AlignmentFlag.AlignHCenter), 0)
            self.body.addStretch(1)
            return

        self.body.addWidget(self._heading_row(), 0)
        for vat in vats:
            colour = p.fault_colour(vat.fault_rate)
            row = QWidget()
            row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            row.setStyleSheet(
                f"background: {p.TEAL_WASH if vat is vats[0] else 'transparent'};"
                f"border-radius: 8px;")

            line = QHBoxLayout(row)
            line.setContentsMargins(10, 6, 12, 6)
            line.setSpacing(12)
            line.addWidget(label(vat.vat, p.ROW_PT, p.INK, 700), STRETCH[0])
            line.addWidget(label(grouped(vat.produced), p.ROW_PT, p.INK, 500,
                                 Qt.AlignmentFlag.AlignRight), STRETCH[1])
            line.addWidget(label(grouped(vat.faults), p.ROW_PT, colour, 700,
                                 Qt.AlignmentFlag.AlignRight), STRETCH[2])

            rate = QWidget()
            rate_line = QHBoxLayout(rate)
            rate_line.setContentsMargins(0, 0, 0, 0)
            rate_line.setSpacing(10)
            rate_line.addWidget(_bar(vat.fault_rate, colour), 1)
            number = label(f"{vat.fault_rate:.1f} %", p.ROW_PT, colour, 700,
                           Qt.AlignmentFlag.AlignRight)
            number.setMinimumWidth(78)
            rate_line.addWidget(number, 0)
            line.addWidget(rate, STRETCH[3])

            self._built.append(row)
            self.body.addWidget(row, 0)
        self.body.addStretch(1)
