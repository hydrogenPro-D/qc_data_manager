"""The gain curve on the QC Sampling tab, drawn with pyqtgraph.

One plot per vat, beside that vat's recommendation table. The x axis is the
sampling interval, test one electrode in every this many, and the y axis is
what that leaves you ahead per 1000 electrodes produced.

Only this one curve, because it is the only one here with a peak. Payback is a
ratio and climbs for ever as the interval widens, so on its own it always
points at testing almost nothing; net saved is the same two quantities
subtracted rather than divided, so it turns over. **The peak is the
recommendation**, and it is marked.
"""

from __future__ import annotations

import pyqtgraph as pg
from PyQt6.QtCore import Qt

from src.data_access.sampling.run_lengths import (
    ACCEPTABLE_GAIN, acceptable_band, best_option,
)

# Matching the app, so the colours mean the same thing here as on the
# Electrodes tab.
BAD = "#E45A52"
GOOD = "#0D9488"
INK = "#12232B"
MUTED = "#66787F"
GRID = "#E3EAEC"

pg.setConfigOptions(antialias=True, background="w", foreground=INK)

# How far out to draw, as a multiple of the best interval. Scaled to the peak
# rather than fixed, because the peak is what the plot is for: this puts it a
# quarter of the way across whatever the vat's numbers turn out to be, with the
# rise to its left and a real fall to its right. A fixed span cannot do both,
# it is a wall at the left edge on one vat and a plateau on the next.
WINDOW_MULTIPLE = 4
MIN_INTERVAL_SHOWN = 30
MAX_INTERVAL_SHOWN = 400
# Fallback only, for a vat where no interval ever pays: draw until the gain has
# fallen back to this much of its peak.
TAIL_SHARE = 0.45
# The shaded region is the acceptable band from run_lengths, so the plot and
# the bold rows in the table can never disagree about which intervals are a
# real choice.
BAND_FILL = (13, 148, 136, 34)
# The y axis is fitted to the stretch from half the best interval to double
# it, not to the whole curve. Everything outside that is worse by so much that
# including it flattens the part being decided into a straight line, and the
# curve leaving the frame says "far worse" perfectly well.
#
# A ratio rather than a fixed number of electrodes either side. Ten either side
# is the same idea, but on a vat whose best is 9 it reaches interval 1, where
# the gain is about minus fifty percent, and the zoom does nothing at all.
ZOOM_RATIO = 2
# Only for a vat with no best to zoom around: testing every electrode scores
# about minus fifty percent by construction, so cut off below this multiple of
# the peak rather than scaling to it.
FLOOR_OF_PEAK = 1.15


def _window(options) -> list:
    """The stretch of the curve worth drawing, sized from the best interval."""
    points = list(options or [])
    if not points:
        return []
    best = best_option(points)
    limit = (best.sampling_interval * WINDOW_MULTIPLE if best
             else _tail_limit(points))
    limit = max(MIN_INTERVAL_SHOWN, min(MAX_INTERVAL_SHOWN, limit))
    return [option for option in points if option.sampling_interval <= limit]


def _tail_limit(points) -> int:
    """Where the gain has dropped back to TAIL_SHARE of its peak.

    Only reached on a vat with no worthwhile interval at all, which has no peak
    to scale a window to.
    """
    peak = max(option.net_percent for option in points)
    if peak <= 0:
        return MIN_INTERVAL_SHOWN
    past = [option.sampling_interval for option in points
            if option.net_percent <= peak * TAIL_SHARE]
    return min(MAX_INTERVAL_SHOWN, int((past[0] if past else 1) * 1.2))


def _anchor(x: float, xs) -> float:
    """Which end of a label to pin to a point, so it stays on the plot.

    Pinned by its left edge near the left of the view, by its right edge near
    the right, and centred in between. A label anchored centre at the peak ran
    off the edge on every vat whose best interval was a small number.
    """
    left, right = xs[0], xs[-1]
    where = (x - left) / (right - left) if right > left else 0.5
    if where < 0.25:
        return 0.0
    return 1.0 if where > 0.75 else 0.5


def _y_range(points) -> tuple[float, float]:
    """Top and bottom of the view, fitted to the intervals around the best.

    Zoomed in on purpose. Fitted to the whole curve, the part actually being
    decided occupied a couple of pixels of height and read as dead flat, which
    is the opposite of what the plot is there to show.
    """
    best = best_option(points)
    if best is None:
        gains = [option.net_percent for option in points]
        high = max(gains)
        low = (max(min(gains), -abs(high) * FLOOR_OF_PEAK) if high > 0
               else min(gains))
    else:
        inner = best.sampling_interval / ZOOM_RATIO
        outer = best.sampling_interval * ZOOM_RATIO
        near = [option.net_percent for option in points
                if inner <= option.sampling_interval <= outer]
        low, high = min(near), max(near)
    span = (high - low) or 1.0
    # Headroom mostly at the top, because the peak label sits above the peak
    # and the peak is by definition at the top of the curve.
    return low - span * 0.08, high + span * 0.26


class NetSavedCanvas:
    """One vat's net saved against the sampling interval."""

    def __init__(self, parent=None) -> None:
        # Parented, for the same reason the tables are: an unparented widget is
        # a top-level window until something adopts it.
        self.widget = pg.PlotWidget(parent)
        self.widget.setBackground("w")
        plot = self.widget.getPlotItem()
        plot.setMenuEnabled(False)
        # Fixed. Nothing here rewards panning, and a stray drag that scrolled
        # the peak off the edge would hide the one thing the plot is for.
        plot.setMouseEnabled(x=False, y=False)
        # The little "A" in the corner is the auto range button. Nothing here
        # pans or zooms, so it does nothing but sit in the plot.
        plot.hideButtons()
        plot.showGrid(x=True, y=True, alpha=0.12)
        plot.setLabel("bottom", "sampling interval", color=MUTED)
        plot.setLabel("left", "Gain, % of production", color=GOOD)
        for edge in ("left", "bottom"):
            plot.getAxis(edge).setPen(pg.mkPen(GRID))
        plot.getAxis("left").setTextPen(pg.mkPen(GOOD))
        plot.getAxis("bottom").setTextPen(pg.mkPen(MUTED))
        self._plot = plot
        self._points: list = []
        self._xs: list = []
        self._marker = None
        self._readout = None
        self._y_view = (0.0, 1.0)
        # Scene-level, because the signal that carries the cursor belongs to
        # the scene rather than to the plot inside it.
        self.widget.scene().sigMouseMoved.connect(self._on_hover)
        self.draw([])

    # ------------------------------------------------------------------
    def draw(self, options) -> None:
        """Redraw from one vat's ``SamplingOption`` per interval."""
        self._plot.clear()
        points = _window(options)
        if not points:
            text = pg.TextItem("No bad runs to size a rate against",
                               color=MUTED, anchor=(0.5, 0.5))
            self._plot.addItem(text)
            text.setPos(0.5, 0.5)
            self._plot.setXRange(0, 1, padding=0)
            self._plot.setYRange(0, 1, padding=0)
            return

        xs = [option.sampling_interval for option in points]
        ys = [option.net_percent for option in points]
        self._plot.addLine(y=0.0, pen=pg.mkPen(MUTED, width=1,
                                               style=Qt.PenStyle.DotLine))
        self._plot.plot(xs, ys, pen=pg.mkPen(GOOD, width=2))
        self._mark_peak(xs, ys, points)
        self._add_readout(points, xs)
        self._plot.setXRange(1, max(xs), padding=0.02)
        # Set explicitly. The empty state pins the view to 0..1 to place its
        # message, and that pinning survives into the next real draw, which
        # left the curve off screen entirely.
        self._y_view = _y_range(points)
        self._plot.setYRange(*self._y_view, padding=0)

    def _add_readout(self, points, xs) -> None:
        """The crosshair and label that follow the cursor.

        Rebuilt on every draw, because clearing the plot throws them away with
        everything else. Hidden until the cursor is actually over the plot.
        """
        self._points, self._xs = points, xs
        self._marker = pg.InfiniteLine(angle=90, movable=False,
                                       pen=pg.mkPen(MUTED, width=1,
                                                    style=Qt.PenStyle.DashLine))
        self._readout = pg.TextItem(color=INK, anchor=(0, 1),
                                    fill=pg.mkBrush(255, 255, 255, 220))
        for item in (self._marker, self._readout):
            item.setVisible(False)
            item.setZValue(10)
            self._plot.addItem(item, ignoreBounds=True)

    def _on_hover(self, position) -> None:
        """Snap to the nearest interval and show what it costs and buys."""
        if not self._points or self._marker is None:
            return
        if not self._plot.sceneBoundingRect().contains(position):
            self._marker.setVisible(False)
            self._readout.setVisible(False)
            return

        where = self._plot.vb.mapSceneToView(position)
        # Nearest by interval, so the readout is always a row that exists
        # rather than an interpolated point between two of them.
        index = min(range(len(self._xs)),
                    key=lambda i: abs(self._xs[i] - where.x()))
        option = self._points[index]
        self._marker.setPos(self._xs[index])
        # No catch rate here, and none in the table either. It counts a whole
        # run as caught the moment one electrode in it is tested, which reads
        # as a promise to recover the run when most of it is already made.
        self._readout.setText(
            f"1 in {option.sampling_interval}   "
            f"destroys {option.production_destroyed:.1f}%   "
            f"gain {option.net_percent:+.2f}%")
        # Flipped so the label never runs off either edge.
        self._readout.setAnchor((_anchor(self._xs[index], self._xs), 1))
        # Clamped into the view. The y axis is zoomed to the intervals around
        # the best, so the curve leaves the frame further out and a readout
        # placed on it would leave with it.
        low, high = self._y_view
        edge = (high - low) * 0.08
        self._readout.setPos(
            self._xs[index],
            min(max(option.net_percent, low + edge), high - edge))
        self._marker.setVisible(True)
        self._readout.setVisible(True)

    def _mark_peak(self, xs, ys, points) -> None:
        """The best this vat can do, and how much room there is around it.

        Chosen by the same function the table uses, so the marked peak and the
        tinted row are always the same interval.
        """
        best = best_option(points)
        if best is None:
            return
        index = points.index(best)
        span = acceptable_band(points, best)
        if span:
            band = pg.LinearRegionItem(list(span), movable=False,
                                       brush=pg.mkBrush(*BAND_FILL),
                                       pen=pg.mkPen(None))
            # Behind the curve, so it reads as ground rather than another line.
            band.setZValue(-10)
            self._plot.addItem(band, ignoreBounds=True)

        self._plot.plot([xs[index]], [ys[index]], pen=None, symbol="o",
                        symbolSize=11, symbolBrush=GOOD,
                        symbolPen=pg.mkPen("w", width=2))
        label = pg.TextItem(
            f"best: 1 in {best.sampling_interval}, "
            f"gain {best.net_percent:+.2f}%",
            color=GOOD, anchor=(_anchor(xs[index], xs), 1.35))
        label.setPos(xs[index], ys[index])
        self._plot.addItem(label)
