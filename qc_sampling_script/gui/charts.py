"""The two plots, drawn with matplotlib in a Qt canvas like the rest of the app.

Both answer the same question from different angles: the spread plot says which
vat has the long bad runs and how consistent it is, the histogram says how long
a bad run is across the plant. The histogram is the one that feeds the sampling
rate, since the whole calculation turns on the SHAPE of that distribution and
not on its average.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QSizePolicy

# Matching the app, so a bad stretch is the same colour here as on the
# Electrodes tab. Teal and crimson rather than green and red, which is the axis
# colour blindness leaves intact.
BAD = "#E45A52"
GOOD = "#0D9488"
INK = "#12232B"
MUTED = "#66787F"
GRID = "#E3EAEC"

# How far the two series sit either side of a vat's line.
OFFSET = 0.17


def _canvas(width: float = 5.0, height: float = 3.4):
    """A blank figure in a widget. Imported here rather than at module load.

    Matplotlib costs about 400ms to import and nothing needs it until there are
    numbers to plot, which is after the window is already up.
    """
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

    figure = Figure(figsize=(width, height), dpi=100)
    figure.patch.set_facecolor("#FFFFFF")
    canvas = FigureCanvasQTAgg(figure)
    canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    return figure, canvas


def _dress(axes, title: str, xlabel: str = "", ylabel: str = "") -> None:
    """The furniture every plot here shares."""
    axes.set_title(title, color=INK, fontsize=11, fontweight="bold", pad=10)
    axes.set_xlabel(xlabel, color=MUTED, fontsize=9)
    axes.set_ylabel(ylabel, color=MUTED, fontsize=9)
    axes.tick_params(colors=MUTED, labelsize=9)
    for edge in ("top", "right"):
        axes.spines[edge].set_visible(False)
    for edge in ("left", "bottom"):
        axes.spines[edge].set_color(GRID)
    axes.set_facecolor("#FFFFFF")


def _spread(stats) -> tuple[float, list[float]]:
    """``(mean, [reach below, reach above])`` for one vat's runs.

    The lower reach is clipped at the mean, because a run cannot be shorter
    than nothing and a whisker crossing zero says the vat sometimes produced a
    negative number of electrodes in a row.
    """
    return stats.mean, [min(stats.stdev, stats.mean), stats.stdev]


class VatSpreadCanvas:
    """A dot at each vat's average run length, with one standard deviation
    either side.

    A dot and a range rather than a bar. A bar draws a filled block from zero to
    the average, which invites the eye to compare areas when the only thing that
    matters is where the dot sits and how far the line reaches. The line IS the
    consistency of the vat: a short one means the runs are all much the same
    length, a long one means the average is not telling you much.
    """

    def __init__(self) -> None:
        self.figure, self.widget = _canvas()
        self._axes = self.figure.add_subplot(111)
        self.draw([])

    def draw(self, per_vat) -> None:
        axes = self._axes
        axes.clear()
        _dress(axes, "Average run length per vat, with one standard deviation",
               "electrodes in a row")

        rows = [row for row in per_vat if row.bad.count or row.good.count]
        if not rows:
            axes.text(0.5, 0.5, "No decided electrodes in this range",
                      ha="center", va="center", transform=axes.transAxes,
                      color=MUTED, fontsize=10)
            axes.set_xticks([])
            axes.set_yticks([])
            self.figure.tight_layout()
            self.widget.draw_idle()
            return

        positions = list(range(len(rows)))
        for offset, colour, label, pick in (
                (OFFSET, BAD, "Bad run", lambda row: row.bad),
                (-OFFSET, GOOD, "Good run", lambda row: row.good)):
            spreads = [_spread(pick(row)) for row in rows]
            axes.errorbar(
                [mean for mean, _reach in spreads],
                [p + offset for p in positions],
                xerr=[[reach[0] for _m, reach in spreads],
                      [reach[1] for _m, reach in spreads]],
                fmt="o", markersize=8, color=colour, ecolor=colour,
                elinewidth=2, capsize=5, capthick=2, linestyle="none",
                label=label, zorder=3)

        axes.set_yticks(positions)
        axes.set_yticklabels([f"Vat {row.vat}" for row in rows], fontsize=9)
        axes.invert_yaxis()          # worst vat at the top, as the table lists it
        axes.set_ylim(len(rows) - 0.5, -0.5)
        axes.set_xlim(left=0)
        # Horizontal rules only. The dots are read against the x axis, and a
        # grid in both directions competes with the ranges themselves.
        axes.grid(axis="x", color=GRID, linewidth=0.8)
        axes.set_axisbelow(True)
        axes.legend(frameon=False, fontsize=9, loc="lower right")
        self.figure.tight_layout()
        self.widget.draw_idle()


class LengthHistogramCanvas:
    """How long a bad run is, across every vat pooled.

    The distribution rather than the average, because the sampling rate is
    decided by the SHORT runs: those are the ones a wide testing gap steps
    over, and an average hides them behind the long ones.
    """

    def __init__(self) -> None:
        self.figure, self.widget = _canvas(5.0, 3.0)
        self._axes = self.figure.add_subplot(111)
        self.draw([], [])

    def draw(self, bad_lengths, good_lengths) -> None:
        axes = self._axes
        axes.clear()
        _dress(axes, "How long a run lasts, all vats",
               "electrodes in a row", "number of runs")

        if not bad_lengths and not good_lengths:
            axes.text(0.5, 0.5, "No runs to measure", ha="center", va="center",
                      transform=axes.transAxes, color=MUTED, fontsize=10)
            axes.set_xticks([])
            axes.set_yticks([])
            self.figure.tight_layout()
            self.widget.draw_idle()
            return

        longest = max(bad_lengths + good_lengths)
        # One bin per electrode while the runs are short, so a run of three is
        # visibly not a run of four. Only pooled once that would be a comb.
        bins = min(30, max(longest, 1))
        if bad_lengths:
            axes.hist(bad_lengths, bins=bins, range=(0.5, longest + 0.5),
                      color=BAD, alpha=0.85, label="Bad runs")
        if good_lengths:
            axes.hist(good_lengths, bins=bins, range=(0.5, longest + 0.5),
                      color=GOOD, alpha=0.55, label="Good runs")
        axes.grid(axis="y", color=GRID, linewidth=0.8)
        axes.set_axisbelow(True)
        axes.legend(frameon=False, fontsize=9)
        self.figure.tight_layout()
        self.widget.draw_idle()
