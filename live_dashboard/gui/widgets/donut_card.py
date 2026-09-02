"""The QC status ring, reusing the app's chart rather than drawing another one.

``StatusDonutWidget`` already eases between shapes, labels the big slices,
writes the total in the middle and lists every category underneath with its
count. That is exactly what the wall needs, so this is a frame around it and a
translation of stored statuses into the wording and colours the app uses.

Keeping the app's widget also keeps the two honest: if somebody restyles the
ring in QC Data Manager, the screen in the office restyles with it.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QWidget

from src.gui.electrodes_gui import CHART_ORDER, ROW_ACCENTS, chart_label
from src.gui.widgets.status_donut_widget import StatusDonutWidget

from .. import palette as p
from .card import Card

# Bigger than the app's 250. This one is read from across a room, and the ring
# is the panel that carries the whole screen's headline.
DIAMETER = 300


def chart_split(status_counts: dict[str, int]) -> tuple[dict, dict]:
    """``(counts, colours)`` keyed by the wording the chart uses.

    Ordered by ``CHART_ORDER`` rather than by size, so a category does not jump
    around the legend between one window and the next. Zeroes are kept, because
    a category vanishing reads as a broken panel.
    """
    counts = {chart_label(status): int(status_counts.get(str(status), 0))
              for status in CHART_ORDER}
    return counts, {chart_label(status): colour
                    for status, colour in ROW_ACCENTS.items()}


class DonutCard(Card):
    """The status breakdown of everything produced in the window."""

    def __init__(self, parent=None) -> None:
        super().__init__(title="QC status", accent=p.TEAL_MID, parent=parent)
        # No title of its own: the card already has one, and two headings
        # stacked on one panel is the look of a widget dropped in by accident.
        self._chart = StatusDonutWidget(title="", diameter=DIAMETER,
                                        unit_label="electrodes")
        self._chart.setStyleSheet("background: transparent;")

        centred = QWidget()
        line = QHBoxLayout(centred)
        line.setContentsMargins(0, 0, 0, 0)
        line.addStretch(1)
        line.addWidget(self._chart, 0, Qt.AlignmentFlag.AlignTop)
        line.addStretch(1)
        self.body.addWidget(centred, 1)

    def set_counts(self, status_counts: dict[str, int]) -> None:
        """Redraw the ring from ``{stored status: count}``."""
        self._chart.update_data(*chart_split(status_counts))
