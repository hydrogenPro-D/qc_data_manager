"""Donut chart of a category breakdown, with a labelled legend.

Part-to-whole at a glance, kept to a handful of segments. Colour is never the
only signal: the total sits in the middle, the larger slices are labelled
directly, and every category is listed underneath with its count — so the chart
survives colour-vision deficiency, greyscale printing and a bad projector.

Domain-neutral — the owning tab supplies the counts and their colours.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame
from PyQt6.QtGui import QFont
import qtawesome as qta

import matplotlib
matplotlib.use("QtAgg", force=False)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
import matplotlib.patheffects as path_effects

# Slices smaller than this are left unlabelled on the ring — the legend carries
# them instead. Labelling every slice is what makes small charts unreadable.
MIN_LABELLED_SHARE = 0.08

# How the ring gets from one shape to the next. Long enough to be seen as
# movement, short enough that nobody waits for it: filtering should feel like
# the chart answered, not like it reloaded.
TWEEN_MS = 220
TWEEN_FRAMES = 8

_DARK_LUMINANCE = 0.0129        # relative luminance of #1F1F1F


def ink_on(background: str) -> str:
    """Black or white, whichever is readable on that fill.

    The slices are no longer all dark, so one colour for every label does not
    work: white on the pale mint or the amber is barely there. WCAG relative
    luminance, with the usual 0.55 split.
    """
    hex_value = str(background).lstrip("#")
    if len(hex_value) != 6:
        return "#FFFFFF"

    def channel(pair: str) -> float:
        c = int(pair, 16) / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    luminance = (0.2126 * channel(hex_value[0:2])
                 + 0.7152 * channel(hex_value[2:4])
                 + 0.0722 * channel(hex_value[4:6]))
    # Whichever actually contrasts more, rather than a guess at where the
    # crossover sits. On the amber that is the difference between 2.4:1 and
    # 6.9:1, and 2.4 is not readable at any size.
    on_white = (luminance + 0.05) / (_DARK_LUMINANCE + 0.05)
    on_dark = 1.05 / (luminance + 0.05)
    return "#1F1F1F" if on_white >= on_dark else "#FFFFFF"


_TEXT = "#1F1F1F"
_MUTED = "#6B6B6B"
# The ring before there is anything to put in it. The same grey the table
# placeholders use, so the whole tab reads as one thing loading.
_PLACEHOLDER = "#E3E7EB"


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
        # In axes fractions, not data coordinates. With no pie drawn there is
        # nothing to set the limits, so they stay at 0 to 1 and (0, 0) puts the
        # text in the bottom left corner, half of it outside the canvas.
        ax.text(0.5, 0.5, "No data", ha="center", va="center",
                transform=ax.transAxes, color=_MUTED, fontsize=pct_fontsize)
        return 0

    def label(share: float) -> str:
        return f"{share:.0f}%" if share >= MIN_LABELLED_SHARE * 100 else ""

    fills = [colors.get(name, "#9CA3AF") for name, _ in present]
    _wedges, _names, percents = ax.pie(
        [value for _, value in present],
        colors=fills,
        startangle=90,
        counterclock=False,
        autopct=label,
        # Centred in the band: it runs from 0.62 to 1.0, so 0.81 is its middle.
        pctdistance=0.81,
        textprops={"fontsize": pct_fontsize, "fontweight": "bold"},
        # Thin ring, with a surface-coloured gap so adjacent slices separate.
        wedgeprops={"width": 0.38, "edgecolor": "#FFFFFF", "linewidth": 2},
    )
    # Per slice, not one colour for the lot, and haloed in its own fill so a
    # label wider than the band is still readable where it crosses the edge.
    for text, fill in zip(percents, fills):
        text.set_color(ink_on(fill))
        text.set_path_effects([
            path_effects.withStroke(linewidth=2.4, foreground=fill)])
    ax.text(0, 0.09, f"{round(total):,}".replace(",", " "), ha="center", va="center",
            color=_TEXT, fontsize=total_fontsize, fontweight="bold")
    ax.text(0, -0.17, unit_label, ha="center", va="center",
            color=_MUTED, fontsize=caption_fontsize)
    return total


def render_donut_png(counts: dict[str, int], colors: dict[str, str],
                     unit_label: str = "total", size_px: int = 460,
                     dpi: int = 200) -> bytes:
    """The same ring as a PNG, for the printed report.

    Drawn at print resolution rather than grabbed off the screen: the report
    goes out as a PDF, and a 100dpi screenshot of a widget looks it.
    """
    import io

    figure = Figure(figsize=(size_px / 100, size_px / 100), dpi=dpi)
    figure.patch.set_alpha(0.0)
    ax = figure.add_subplot(111)
    figure.subplots_adjust(left=0, right=1, top=1, bottom=0)
    draw_donut(ax, counts, colors, unit_label)

    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", transparent=True, dpi=dpi)
    return buffer.getvalue()


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

        # Hidden until somebody calls set_info. The chart is domain-neutral, so
        # what the categories mean has to come from the tab that filled it.
        self._info = QLabel()
        self._info.setPixmap(qta.icon("fa5s.info-circle", color=_MUTED).pixmap(15, 15))
        self._info.setCursor(Qt.CursorShape.WhatsThisCursor)
        self._info.hide()

        heading_row = QHBoxLayout()
        heading_row.setContentsMargins(0, 0, 0, 0)
        heading_row.setSpacing(6)
        heading_row.addWidget(heading, 0)
        heading_row.addStretch(1)
        heading_row.addWidget(self._info, 0)
        layout.addLayout(heading_row, 0)

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
        # Tight: the rows are one line each and read as a block, so the gap
        # only has to keep them from touching.
        self._legend.setSpacing(2)
        legend_host = QWidget()
        legend_host.setLayout(self._legend)
        layout.addWidget(legend_host, 0)

        layout.addStretch(1)
        self.setLayout(layout)

        # The shape currently on screen, which is not the shape asked for while
        # the ring is easing between the two.
        self._shown: dict[str, float] = {}
        self._start: dict[str, float] = {}
        self._target: dict[str, float] = {}
        self._colors: dict[str, str] = {}
        self._frame = 0
        self._tween = QTimer(self)
        self._tween.setInterval(max(1, TWEEN_MS // TWEEN_FRAMES))
        self._tween.timeout.connect(self._advance)

        self._loading = False
        self.update_data({}, {})

    # ------------------------------------------------------------------
    def set_info(self, entries) -> None:
        """Show an info icon explaining the categories, as ``(term, meaning)``.

        A tooltip rather than anything on the chart itself: the meanings are
        looked up once and then never again, and putting them on screen
        permanently would cost more room than the chart.
        """
        rows = "".join(
            f"<tr><td valign='top'><b>{term}</b>&nbsp;&nbsp;&nbsp;</td>"
            f"<td valign='top'>{meaning}</td></tr>"
            for term, meaning in (entries or []))
        if not rows:
            self._info.hide()
            return
        self._info.setToolTip(f"<table cellspacing='0' cellpadding='3'>{rows}</table>")
        self._info.show()

    def set_loading(self, loading: bool = True) -> None:
        """Show a blank ring while the first read is on its way.

        Otherwise the chart spends the first second showing its "No data"
        state with every category at zero, which says the filters matched
        nothing rather than that the numbers have not arrived.

        Deliberately still, where the tables shimmer: redrawing a matplotlib
        canvas eleven times a second for the sake of a two second wait is a lot
        of work to say something this ring can say by just sitting there.
        """
        self._loading = bool(loading)
        if not self._loading:
            return
        self._tween.stop()
        self._shown, self._target, self._start = {}, {}, {}
        self._rebuild_legend({}, {}, 0)
        self._ax.clear()
        self._ax.set_aspect("equal")
        self._ax.axis("off")
        self._ax.pie([1], colors=[_PLACEHOLDER], startangle=90,
                     wedgeprops={"width": 0.38, "edgecolor": "#FFFFFF",
                                 "linewidth": 2})
        self._canvas.draw_idle()

    def update_data(self, counts: dict[str, int], colors: dict[str, str]) -> None:
        """Redraw from ``{category: count}`` and ``{category: hex}``.

        The ring eases into its new shape; the legend does not. Numbers are
        read rather than watched, and animating them would only make the figure
        somebody is trying to note down harder to read.
        """
        # Real numbers, so whatever the placeholder was showing is over. They
        # land at once rather than growing out of the blank ring.
        self._loading = False
        total = sum(value for value in counts.values() if value > 0)
        self._rebuild_legend(counts, colors, total)

        self._colors = dict(colors)
        self._target = {name: float(value) for name, value in counts.items()}
        # Categories the old shape had and the new one does not still have to
        # shrink to nothing rather than vanish, so both sides carry every key.
        for name in self._target:
            self._shown.setdefault(name, 0.0)
        for name in list(self._shown):
            self._target.setdefault(name, 0.0)

        # Nothing on screen to ease from: the first counts, or a filter that
        # emptied the ring. Those land at once rather than growing in.
        if not any(self._shown.values()) or self._shown == self._target:
            self._tween.stop()
            self._shown = dict(self._target)
            self._draw_ring(self._shown, self._colors)
            return
        # Eased from where it started, not from wherever the last frame left
        # it: compounding the step lands on the target a third of the way in.
        self._start = dict(self._shown)
        self._frame = 0
        self._tween.start()

    def _advance(self) -> None:
        """One frame of the ease, and stop cleanly on the last one."""
        self._frame += 1
        if self._frame >= TWEEN_FRAMES:
            self._tween.stop()
            self._shown = dict(self._target)
        else:
            # Ease out: most of the movement happens early, so the ring reads
            # as settling rather than as sliding at a constant speed.
            progress = 1 - (1 - self._frame / TWEEN_FRAMES) ** 3
            self._shown = {
                name: start + (self._target[name] - start) * progress
                for name, start in self._start.items()}
        self._draw_ring(self._shown, self._colors)

    def _draw_ring(self, counts: dict, colors: dict[str, str]) -> None:
        # Below a whole electrode a slice is not worth a wedge, and leaving it
        # in makes a category that reached zero flicker on the last frame.
        drawn = {name: value for name, value in counts.items() if value >= 0.5}
        draw_donut(self._ax, drawn, colors, self._unit_label,
                   pct_fontsize=10, total_fontsize=22, caption_fontsize=10)
        self._canvas.draw_idle()

    def _rebuild_legend(self, counts: dict[str, int],
                        colors: dict[str, str], total: int) -> None:
        """One row per category — including the zero ones, so a category
        disappearing from the ring is visibly zero rather than just gone."""
        while self._legend.count():
            item = self._legend.takeAt(0)
            widget = item.widget()
            if widget:
                # Off the parent before deleteLater, or it keeps painting where
                # it was until the event loop gets round to it. Invisible while
                # rows are replaced, since the new ones cover it, but clearing
                # to none left the old legend on screen under an empty ring.
                widget.setParent(None)
                widget.deleteLater()

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
