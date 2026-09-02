"""One screen of the deck: everything about a single time window.

Three of these exist at once, one per window, and the deck slides between them.
They are built once and refilled on every refresh rather than rebuilt, so a
refresh landing mid slide cannot pull a card out from under the animation.

Layout is a bento grid, which is the shape that suits a wall: cards of
different weights, the heaviest reading first. Top row the headline figures,
middle row what QC decided, bottom row how the line ran.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from src.gui.electrodes_gui import status_label

from ..data.dashboard_stats import (
    ALL_PRODUCED,
    ALL_TESTED,
    SCRAP_FAULTS,
    SCRAP_TESTING,
    SCRAPPED_TOTAL,
    WindowStats,
)
from . import palette as p
from .widgets.donut_card import DonutCard
from .widgets.kpi_tile import KpiTile
from .widgets.pass_rate_bar import PassRateBar
from .widgets.summary_table import SummaryCard, SummaryRow
from .widgets.throughput_chart import ThroughputCard
from .widgets.vat_table import VatCard

# The composite rows are not statuses, so they get their own colours: grey for
# the two arithmetic lines, and the colour of what they are made of otherwise.
COMPOSITE_ACCENTS: dict[str, tuple[str, str]] = {
    ALL_PRODUCED: (p.INK, "#EDF0F1"),
    ALL_TESTED: (p.INK, "#EDF0F1"),
    SCRAPPED_TOTAL: (p.CORAL, p.CORAL_WASH),
    SCRAP_FAULTS: (p.CORAL, p.CORAL_WASH),
    SCRAP_TESTING: (p.TEAL_MID, p.TEAL_WASH),
}


def _summary_rows(rows: list[tuple[str, int, int]]) -> list[SummaryRow]:
    """Turn the stats layer's ``(label, count, depth)`` into drawable rows.

    The labels arrive as STORED status values where the row is a status, so
    this is where the app's wording and colours are applied. That split is what
    lets the stats module stay free of Qt and of display text.
    """
    drawable = []
    for text, count, depth in rows:
        accent = p.STATUS_ACCENTS.get(text, "")
        tint = p.STATUS_TINTS.get(text, "")
        if not accent:
            accent, tint = COMPOSITE_ACCENTS.get(text, (p.INK, "#EDF0F1"))
        drawable.append(SummaryRow(label=status_label(text), count=count,
                                   depth=depth, accent=accent, tint=tint))
    return drawable


class DashboardPage(QWidget):
    """The cards for one window. Built once, refilled by ``show_stats``."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._tiles = {
            "produced": KpiTile("Electrodes produced", p.TEAL_DEEP),
            "pass": KpiTile("QC pass rate", p.TEAL_MID, suffix="%"),
            "faults": KpiTile("Fault rate", p.CORAL, suffix="%"),
            "board": KpiTile("On the QC board", p.INDIGO),
        }
        self.donut = DonutCard()
        self.production = SummaryCard("Production", p.TEAL_DEEP)
        self.qc = SummaryCard("QC results", p.TEAL_MID)
        # Three lines left most of that card empty, and empty space on a wall
        # reads as a panel that failed to load rather than as restraint.
        self.pass_bar = PassRateBar()
        self.qc.attach(self.pass_bar)
        self.throughput = ThroughputCard()
        self.vats = VatCard()

        tiles = QHBoxLayout()
        tiles.setContentsMargins(0, 0, 0, 0)
        tiles.setSpacing(p.GRID_GAP)
        for tile in self._tiles.values():
            tiles.addWidget(tile, 1)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(p.GRID_GAP)
        grid.setVerticalSpacing(p.GRID_GAP)
        grid.addWidget(self.donut, 0, 0)
        grid.addWidget(self.production, 0, 1)
        grid.addWidget(self.qc, 0, 2)
        grid.addWidget(self.throughput, 1, 0, 1, 2)
        grid.addWidget(self.vats, 1, 1 + 1)
        # The ring is squarer than the two blocks beside it, and the chart is
        # the widest thing on the screen, so the columns are not equal.
        grid.setColumnStretch(0, 4)
        grid.setColumnStretch(1, 4)
        grid.setColumnStretch(2, 5)
        grid.setRowStretch(0, 6)
        grid.setRowStretch(1, 5)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(p.GRID_GAP)
        layout.addLayout(tiles, 0)
        layout.addLayout(grid, 1)

    # ------------------------------------------------------------------
    def show_stats(self, stats: WindowStats) -> None:
        """Put one window's figures on the cards."""
        self._tiles["produced"].set_value(
            stats.produced, f"{stats.per_day:,.0f} a day on average"
            .replace(",", " ") if stats.days > 1 else "")
        self._tiles["pass"].set_value(
            stats.pass_rate, f"{stats.passed} of {stats.tested} tested",
            decimals=1)
        self._tiles["faults"].set_value(
            stats.fault_rate, f"{stats.faults} will not ship", decimals=1)
        self._tiles["board"].set_value(stats.on_board, "being tested now")

        # The fault tile is the one that changes colour, because it is the one
        # figure on the screen that is either fine or not. Recolouring several
        # would leave nothing standing out.
        self._tiles["faults"].set_accent(p.fault_colour(stats.fault_rate))

        self.donut.set_counts(stats.status_counts)
        self.production.set_rows(_summary_rows(stats.production),
                                 total=stats.produced)
        # Shares of what was TESTED, not of everything produced, so a pass rate
        # on this card reads as a pass rate.
        self.qc.set_rows(_summary_rows(stats.qc), total=stats.tested,
                         subtitle="share of tested")
        self.pass_bar.set_counts(stats.passed, stats.failed)
        self.throughput.set_series(stats.series, stats.bucket)
        self.vats.set_vats(stats.vats)
