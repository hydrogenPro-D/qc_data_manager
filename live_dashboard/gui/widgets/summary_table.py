"""The production and QC blocks, as they appear in the printed report.

Domain-neutral: the page hands over already-labelled rows and the colour each
should carry, so this widget knows nothing about electrodes. Same contract the
app's shared widgets use.

Each row is backed by a bar showing its share of the block's total. On paper
the reader has time to compare two columns of digits; on a wall they have about
two seconds, and a bar is what makes "most of these are released" land in that
time. The digits are still there for anyone who wants them.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from .. import palette as p
from .card import Card, label, grouped

# How far each depth is stepped in. Depth 0 is the line the others are measured
# against, so it is not indented at all.
INDENT_PX = (0, 18, 38)
# The share bar never fills the row completely: a bar that reaches the edge is
# indistinguishable from a row that is simply tinted.
BAR_MAX = 0.94


@dataclass(frozen=True)
class SummaryRow:
    """One line of a block, ready to draw."""

    label: str
    count: int
    depth: int = 1
    # The row's own colour. Empty for a subtotal, which is deliberately grey:
    # it is an arithmetic line rather than a category.
    accent: str = ""
    tint: str = ""


def _row_widget(row: SummaryRow, total: int) -> QWidget:
    """One line: an indented label, a share bar behind it, and the count."""
    share = (row.count / total) if total else 0.0
    stop = max(0.0, min(BAR_MAX, share * BAR_MAX))
    fill = row.tint or p.TEAL_WASH

    holder = QWidget()
    holder.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    # Two hard stops rather than a fade: the bar has to read as a measured
    # length, and a gradient that trails off has no readable end.
    holder.setStyleSheet(
        f"background: qlineargradient(x1:0, y1:0, x2:1, y2:0,"
        f"stop:0 {fill}, stop:{stop:.4f} {fill},"
        f"stop:{min(1.0, stop + 0.0015):.4f} rgba(0,0,0,0),"
        f"stop:1 rgba(0,0,0,0)); border-radius: 7px;")

    text = label(row.label, p.ROW_PT, p.INK if row.depth == 0 else p.INK,
                 700 if row.depth == 0 else 500)
    value = label(grouped(row.count), p.ROW_PT, row.accent or p.INK, 700,
                  Qt.AlignmentFlag.AlignRight)
    value.setMinimumWidth(96)

    line = QHBoxLayout(holder)
    line.setContentsMargins(INDENT_PX[min(row.depth, 2)] + 10, 7, 12, 7)
    line.setSpacing(10)
    line.addWidget(text, 1)
    line.addWidget(value, 0)
    return holder


class SummaryCard(Card):
    """A titled block of ``SummaryRow``, with a share bar behind each line."""

    def __init__(self, title: str, accent: str = p.TEAL_MID, parent=None) -> None:
        super().__init__(title=title, accent=accent, parent=parent)
        self._rows: list[QWidget] = []
        # The rows live in their own box so rebuilding them cannot take a
        # footer with them. Without this the pass-rate bar was deleted on
        # every refresh and the card emptied itself after a minute.
        self._rows_box = QVBoxLayout()
        self._rows_box.setContentsMargins(0, 0, 0, 0)
        self._rows_box.setSpacing(6)
        host = QWidget()
        host.setLayout(self._rows_box)
        self.body.addWidget(host, 0)
        self.body.addStretch(1)
        self._footer: QWidget | None = None

    def attach(self, widget: QWidget) -> None:
        """Put a widget under the rows, kept across every ``set_rows``.

        Inserted BEFORE the trailing stretch, so it sits against the rows and
        the spare room falls below it. Anchored to the bottom of the card
        instead, a three row block left a hand's width of white across the
        middle, which reads as a panel that half loaded.
        """
        self._footer = widget
        self.body.insertWidget(self.body.count() - 1, widget, 0)

    def set_rows(self, rows: list[SummaryRow], total: int | None = None,
                 subtitle: str = "") -> None:
        """Replace the block.

        ``total`` is what the bars are a share of. It defaults to the depth-0
        line, which is the number the block is measured against, so a QC block
        showing a pass rate does that against tests rather than against
        everything the line produced.
        """
        self.set_subtitle(subtitle)
        while self._rows_box.count():
            item = self._rows_box.takeAt(0)
            widget = item.widget()
            if widget:
                # Off the parent first. Left attached it keeps painting where it
                # was until the event loop catches up, which on a screen that
                # rebuilds every minute is a visible ghost.
                widget.setParent(None)
                widget.deleteLater()
        self._rows = []

        if total is None:
            roots = [row.count for row in rows if row.depth == 0]
            total = max(roots) if roots else max((row.count for row in rows),
                                                 default=0)
        for row in rows:
            widget = _row_widget(row, total)
            self._rows.append(widget)
            self._rows_box.addWidget(widget, 0)
