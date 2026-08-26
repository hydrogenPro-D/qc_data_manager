"""Donut chart of a category breakdown, with a labelled legend.

Part-to-whole at a glance, kept to a handful of segments. Colour is never the
only signal: the total sits in the middle, the larger slices are labelled
directly, and every category is listed underneath with its count — so the chart
survives colour-vision deficiency, greyscale printing and a bad projector.

Domain-neutral — the owning tab supplies the counts and their colours.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame
from PyQt6.QtGui import QFont

import matplotlib
matplotlib.use("QtAgg", force=False)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

# Slices smaller than this are left unlabelled on the ring — the legend carries
# them instead. Labelling every slice is what makes small charts unreadable.
MIN_LABELLED_SHARE = 0.08

_TEXT = "#1F1F1F"
_MUTED = "#6B6B6B"


def draw_donut(ax, counts: dict[str, int], colors: dict[str, str],
               unit_label: str = "total", *, pct_fontsize: int = 13,
               total_fontsize: int = 30, caption_fontsize: int = 13) -> int:
    """Draw the ring onto a matplotlib axes; returns the total.

    Shared by the on-screen chart and the printed report so the two can't drift
    apart — only the type sizes differ.
    """
    present = [(name, value) for name, value in counts.items() if value > 0]
    total = sum(value for _, value in present)

    ax.clear()
    ax.set_aspect("equal")
    ax.axis("off")

    if not present:
        ax.text(0, 0, "No data", ha="center", va="center",
                color=_MUTED, fontsize=pct_fontsize)
        return 0

    def label(share: float) -> str:
        return f"{share:.0f}%" if share >= MIN_LABELLED_SHARE * 100 else ""

    ax.pie(
        [value for _, value in present],
        colors=[colors.get(name, "#9CA3AF") for name, _ in present],
        startangle=90,
        counterclock=False,
        autopct=label,
        pctdistance=0.80,
        textprops={"color": "#FFFFFF", "fontsize": pct_fontsize, "fontweight": "bold"},
        # Thin ring, with a surface-coloured gap so adjacent slices separate.
        wedgeprops={"width": 0.38, "edgecolor": "#FFFFFF", "linewidth": 2},
    )
    ax.text(0, 0.09, f"{total:,}".replace(",", " "), ha="center", va="center",
            color=_TEXT, fontsize=total_fontsize, fontweight="bold")
    ax.text(0, -0.17, unit_label, ha="center", va="center",
            color=_MUTED, fontsize=caption_fontsize)
    return total


class _NoWheelCanvas(FigureCanvasQTAgg):
    """A chart that lets the wheel through to whatever scrolls behind it.

    Matplotlib accepts wheel events so a plot can be zoomed. This one is a
    static ring, so all that achieved was a dead patch in the middle of the
    panel where scrolling stopped working.
    """

    def wheelEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        event.ignore()


class StatusDonutWidget(QWidget):
    """A ring chart plus a swatch/label/count legend."""

    def __init__(self, parent=None, title: str = "Breakdown",
                 diameter: int = 250, unit_label: str = "total") -> None:
        super().__init__(parent)
        self._unit_label = unit_label
        self._legend_rows: list[QWidget] = []

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        heading = QLabel(title)
        heading_font = QFont()
        heading_font.setPointSize(11)
        heading_font.setBold(True)
        heading.setFont(heading_font)
        heading.setStyleSheet(f"color: {_TEXT};")
        layout.addWidget(heading, 0)

        self._figure = Figure(figsize=(diameter / 100, diameter / 100), dpi=100)
        self._figure.patch.set_alpha(0.0)
        self._canvas = _NoWheelCanvas(self._figure)
        self._canvas.setStyleSheet("background: transparent;")
        self._canvas.setFixedSize(diameter, diameter)
        self._ax = self._figure.add_subplot(111)
        # Matplotlib reserves room for axes furniture we don't draw; without
        # this the ring sits in the middle of a large empty square.
        self._figure.subplots_adjust(left=0, right=1, top=1, bottom=0)
        layout.addWidget(self._canvas, 0, Qt.AlignmentFlag.AlignHCenter)

        self._legend = QVBoxLayout()
        self._legend.setContentsMargins(0, 0, 0, 0)
        self._legend.setSpacing(6)
        legend_host = QWidget()
        legend_host.setLayout(self._legend)
        layout.addWidget(legend_host, 0)

        layout.addStretch(1)
        self.setLayout(layout)

        self.update_data({}, {})

    # ------------------------------------------------------------------
    def update_data(self, counts: dict[str, int], colors: dict[str, str]) -> None:
        """Redraw from ``{category: count}`` and ``{category: hex}``."""
        total = sum(value for value in counts.values() if value > 0)
        self._draw_ring(counts, colors)
        self._rebuild_legend(counts, colors, total)

    def _draw_ring(self, counts: dict[str, int], colors: dict[str, str]) -> None:
        draw_donut(self._ax, counts, colors, self._unit_label,
                   pct_fontsize=10, total_fontsize=22, caption_fontsize=10)
        self._canvas.draw_idle()

    def _rebuild_legend(self, counts: dict[str, int],
                        colors: dict[str, str], total: int) -> None:
        """One row per category — including the zero ones, so a category
        disappearing from the ring is visibly zero rather than just gone."""
        while self._legend.count():
            item = self._legend.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for name, value in counts.items():
            share = (value / total * 100) if total else 0.0
            self._legend.addWidget(self._legend_row(name, value, share, colors))

    @staticmethod
    def _legend_row(name: str, value: int, share: float, colors: dict[str, str]) -> QWidget:
        swatch = QFrame()
        swatch.setFixedSize(10, 10)
        swatch.setStyleSheet(
            f"background-color: {colors.get(name, '#9CA3AF')}; border-radius: 3px;")

        label = QLabel(name)
        label.setStyleSheet(f"color: {_TEXT}; font-size: 12px;")

        value_label = QLabel(f"{value:,}".replace(",", " "))
        value_label.setStyleSheet(f"color: {_TEXT}; font-size: 12px; font-weight: 600;")

        share_label = QLabel(f"{share:.0f}%")
        share_label.setStyleSheet(f"color: {_MUTED}; font-size: 11px;")
        share_label.setFixedWidth(34)
        share_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        row = QWidget()
        row_layout = QHBoxLayout()
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)
        row_layout.addWidget(swatch, 0)
        row_layout.addWidget(label, 1)
        row_layout.addWidget(value_label, 0)
        row_layout.addWidget(share_label, 0)
        row.setLayout(row_layout)
        return row
