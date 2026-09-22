"""How long the good and bad runs actually are, measured from the archive.

The one number the sampling question hangs on is how long a bad run lasts
once it starts. Not how often they happen: how LONG. This works it out by
walking each vat's electrodes in production order and measuring the unbroken
stretches.

Per vat, because a chemistry excursion belongs to a vat. Walking the plant-wide
sequence instead would break every stretch up with the other vats' electrodes
running alongside, and report bad runs far shorter than they are.

Pure ``DataFrame`` in, numbers out. No Qt, no database.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

import pandas as pd

from src.data_access.electrodes.electrode_columns import (
    ElectrodeColumns,
    ElectrodeStatus,
    STATUS_COLUMN,
    VAT_COLUMN,
    normalize_marking,
)
from src.data_access.electrodes.status_propagation import electrode_number

# What each verdict says about the PROCESS at that point on the line, which is
# not the same as what it says about the electrode. A tested electrode is
# destroyed either way, but a pass is still evidence the chemistry was right
# just then, and that is what a run is made of.
GOOD_STATUSES = {str(ElectrodeStatus.RELEASED),
                 str(ElectrodeStatus.TESTED_AND_RELEASED)}
BAD_STATUSES = {str(ElectrodeStatus.SCRAPPED),
                str(ElectrodeStatus.TESTED_AND_SCRAPPED)}

GOOD, BAD, UNKNOWN = "good", "bad", "unknown"

# The vats in service. Everything else the process log names is left out of
# the analysis entirely: retired vats, blank entries, and the rows that give
# two vats for one electrode. None of them can answer the question and all of
# them crowd the table. Edit this tuple when a vat comes or goes.
KEPT_VATS: tuple[str, ...] = ("28", "29", "30")

ALL_VATS = "All vats"

_MARKING = str(ElectrodeColumns.MARKING)


@dataclass(frozen=True)
class RunStats:
    """The spread of one kind of run, in electrodes."""

    lengths: list[int] = field(default_factory=list)

    @property
    def count(self) -> int:
        """How many separate runs there were."""
        return len(self.lengths)

    @property
    def total(self) -> int:
        """How many electrodes they cover between them."""
        return sum(self.lengths)

    @property
    def mean(self) -> float:
        return statistics.fmean(self.lengths) if self.lengths else 0.0

    @property
    def median(self) -> float:
        return statistics.median(self.lengths) if self.lengths else 0.0

    @property
    def stdev(self) -> float:
        """Sample standard deviation. One run has no spread, so it reads 0."""
        return statistics.stdev(self.lengths) if len(self.lengths) > 1 else 0.0

    @property
    def longest(self) -> int:
        return max(self.lengths) if self.lengths else 0

    @property
    def shortest(self) -> int:
        return min(self.lengths) if self.lengths else 0


@dataclass(frozen=True)
class VatStats:
    """One vat's showing over the region, or the pooled figure for all of them."""

    vat: str
    # Electrodes carrying one of the four verdicts. NOT everything the vat
    # made: an electrode nobody has ruled on yet says nothing about the
    # chemistry, takes no part in a run, and is left out of the count so that
    # electrodes = bad + good on every row with nothing to reconcile.
    electrodes: int
    bad: RunStats
    good: RunStats

    @property
    def bad_share(self) -> float:
        """Share of this vat's electrodes that will not ship.

        The two shares add up to 100, which is what lets each block carry its
        own.
        """
        return (self.bad.total / self.electrodes * 100) if self.electrodes else 0.0

    @property
    def good_share(self) -> float:
        """Share of this vat's electrodes that can be sold."""
        return (self.good.total / self.electrodes * 100) if self.electrodes else 0.0

    @property
    def bad_fraction(self) -> float:
        """The same as ``bad_share`` but as 0..1, which is what the sampling
        arithmetic takes. Kept apart from the percentage so neither has to be
        divided or multiplied by a hundred at the point of use."""
        return (self.bad.total / self.electrodes) if self.electrodes else 0.0


@dataclass(frozen=True)
class Analysis:
    """Everything the window draws, for one range of markings."""

    first: int = 0
    last: int = 0
    # Electrodes counted: in range, in a working vat, and carrying a verdict.
    matched: int = 0
    # In range and in a working vat, but with no verdict yet, so left out of
    # every figure. Not shown; kept because a range that is mostly untested is
    # worth being able to check for.
    skipped: int = 0
    # Electrodes inside the marking range but not in one of KEPT_VATS.
    excluded: int = 0
    per_vat: list[VatStats] = field(default_factory=list)
    overall: VatStats = field(
        default_factory=lambda: VatStats(ALL_VATS, 0, RunStats(), RunStats()))

    @property
    def skipped_share(self) -> float:
        """Share of the electrodes in range that had no verdict to count."""
        seen = self.matched + self.skipped
        return (self.skipped / seen * 100) if seen else 0.0

    @property
    def empty(self) -> bool:
        return self.matched == 0


def parse_marking(text: str) -> int | None:
    """``'B14174'``, ``'b 14174'`` or ``'14174'`` to ``14174``. None if unusable."""
    return electrode_number(normalize_marking(str(text).strip()))


def family(status: str) -> str:
    """What one electrode says about the process: good, bad, or nothing."""
    text = str(status).strip()
    if text in GOOD_STATUSES:
        return GOOD
    if text in BAD_STATUSES:
        return BAD
    return UNKNOWN


def _segments(families: list[str]) -> list[tuple[str, int]]:
    """The sequence as alternating ``(good|bad, length)`` stretches.

    An undecided electrode is STEPPED OVER, joining the stretches either side
    rather than ending the run it interrupts. It is a gap in what has been
    looked at, not evidence that the chemistry changed at that point.
    """
    out: list[tuple[str, int]] = []
    for value in families:
        if value == UNKNOWN:
            continue
        if out and out[-1][0] == value:
            out[-1] = (value, out[-1][1] + 1)
        else:
            out.append((value, 1))
    return out


def bridge(segments: list[tuple[str, int]], gap: int) -> list[tuple[str, int]]:
    """Join two bad stretches separated by at most ``gap`` good electrodes.

    The bridged electrodes are ABSORBED, so the merged run spans from the first
    bad electrode to the last, the few good ones in the middle included. That is
    what the merge is claiming: one bad zone, with an anomaly inside it, rather
    than two zones that happen to be near each other.

    Cascades, so three short stretches separated by two small gaps become one.

    ``gap = 0`` changes nothing, which is the default for a reason. Merging
    makes bad runs LONGER, longer runs are easier to catch, and the tool then
    recommends testing LESS often. Every other choice in this tool errs toward
    more testing; this is the only one that errs the other way, so it is opt in
    and the operator moves the slider themselves.
    """
    if gap <= 0:
        return list(segments)
    out: list[tuple[str, int]] = []
    for kind, length in segments:
        joins = (kind == BAD and len(out) >= 2
                 and out[-1][0] == GOOD and out[-1][1] <= gap
                 and out[-2][0] == BAD)
        if joins:
            between = out.pop()[1]
            before = out.pop()[1]
            out.append((BAD, before + between + length))
        else:
            out.append((kind, length))
    return out


def runs_in(families: list[str], gap: int = 0
            ) -> tuple[list[int], list[int]]:
    """``(bad lengths, good lengths)`` from one vat's sequence of verdicts.

    ``gap`` is the run of good electrodes a bad zone is allowed to contain, see
    ``bridge``.
    """
    merged = bridge(_segments(families), gap)
    return ([length for kind, length in merged if kind == BAD],
            [length for kind, length in merged if kind == GOOD])


def _ordered(frame: pd.DataFrame) -> list[str]:
    """One vat's verdicts in production order, oldest electrode first.

    By the marking's NUMBER, not by row order. The rows arrive newest first and
    the numbering has gaps, and a stretch walked in the wrong order is not a
    stretch at all.
    """
    numbers = frame[_MARKING].map(parse_marking)
    ordered = frame.assign(_n=numbers).dropna(subset=["_n"]).sort_values("_n")
    return [family(status) for status in ordered[STATUS_COLUMN].astype(str)]


def analyse(df: pd.DataFrame | None, first: int, last: int,
            gap: int = 0) -> Analysis:
    """Measure every vat's runs over the electrodes between two markings.

    The range is inclusive and order does not matter, so somebody who types the
    newest electrode into the first box still gets an answer.
    """
    low, high = (first, last) if first <= last else (last, first)
    if df is None or df.empty or STATUS_COLUMN not in df.columns:
        return Analysis(first=low, last=high)

    numbers = df[_MARKING].map(parse_marking)
    window = df[numbers.notna() & numbers.between(low, high)].copy()
    if window.empty:
        return Analysis(first=low, last=high)

    vats = (window[VAT_COLUMN].astype(str).str.strip()
            if VAT_COLUMN in window.columns
            else pd.Series("", index=window.index))
    # Dropped before anything is measured, so the pooled figures on the right
    # of the window are the same electrodes as the per vat rows on the left.
    # Counted first, because a range where half the electrodes fall outside the
    # working vats is worth knowing about rather than silently shrinking.
    keep = vats.isin(KEPT_VATS)
    excluded = int((~keep).sum())
    window, vats = window[keep], vats[keep]
    if window.empty:
        return Analysis(first=low, last=high, excluded=excluded)

    families = window[STATUS_COLUMN].astype(str).map(family)

    per_vat: list[VatStats] = []
    all_bad: list[int] = []
    all_good: list[int] = []
    for name, group in window.groupby(vats, sort=False):
        bad, good = runs_in(_ordered(group), gap)
        # POOLED FROM THE PER VAT RUNS, never re-measured across the plant
        # sequence: a run belongs to a vat, and the combined figure has to be
        # the same runs counted once, not a different measurement.
        all_bad.extend(bad)
        all_good.extend(good)
        # A vat with nothing decided in this range is left out rather than
        # listed as a row of zeroes. It contributes no runs, no rate and no
        # curve, and a whole tab of nothing is worse than not offering it.
        if bad or good:
            per_vat.append(VatStats(vat=str(name), electrodes=sum(bad) + sum(good),
                                    bad=RunStats(bad), good=RunStats(good)))

    # In KEPT_VATS order, so the table reads 28, 29, 30 every time. Sorting by
    # how bad each vat looked reshuffled the rows on every change of range,
    # which makes two runs of the tool tedious to compare; with only three vats
    # the worst one is easy enough to find by eye.
    rank = {vat: index for index, vat in enumerate(KEPT_VATS)}
    per_vat.sort(key=lambda row: (rank.get(row.vat, len(rank)), row.vat))
    return Analysis(
        first=low, last=high,
        matched=sum(all_bad) + sum(all_good),
        skipped=int((families == UNKNOWN).sum()),
        excluded=excluded,
        per_vat=per_vat,
        overall=VatStats(ALL_VATS, sum(all_bad) + sum(all_good),
                         RunStats(all_bad), RunStats(all_good)))


# ---------------------------------------------------------------------------
# What it all means for the testing rate
# ---------------------------------------------------------------------------
def hit_chance(run_length: int, sampling_interval: int) -> float:
    """Odds that testing every ``sampling_interval``-th electrode lands in one run.

    ``min(1, run_length / sampling_interval)``: the test either falls inside the
    run or it does not, and over many runs that is how often it does. A run at
    least as long as the interval is caught every time.
    """
    if sampling_interval < 1:
        return 0.0
    return min(1.0, run_length / sampling_interval)


def detection(run_lengths: list[int], sampling_interval: int) -> float:
    """Share of the bad ELECTRODES that testing at this interval would catch.

    Weighted by how long each run is, which is the whole point. Counting runs
    instead treats a stub of 3 and an excursion of 25 as the same event, and
    they are not: missing the stub costs three electrodes, missing the
    excursion costs twenty-five. Since a short run is also the hard one to
    catch, counting runs equally made the tool demand a punishing rate to
    chase the cases that matter least.

    Every electrode in a run counts as caught when the run is caught, because
    finding it is what condemns the whole stretch.
    """
    total = sum(run_lengths)
    if not total or sampling_interval < 1:
        return 0.0
    return sum(run_length * hit_chance(run_length, sampling_interval)
               for run_length in run_lengths) / total


# Past this an "interval" is a statement that nobody is testing at all, and
# the search stops rather than doubling forever on a catch rate near zero.
_INTERVAL_CEILING = 100_000


def interval_for(run_lengths: list[int], catch_rate: float) -> int:
    """The widest testing interval that still catches ``catch_rate`` of them.

    Widest, because every electrode tested is an electrode destroyed. Detection
    only falls as the interval grows, so this walks out until it drops below the
    target and takes the last interval that held.
    """
    if not run_lengths or catch_rate <= 0:
        return 0
    # Binary search, not a walk. Detection only ever falls as the interval
    # grows, so the answer can be bracketed. A walk needed a ceiling to stop
    # at, and any ceiling low enough to be quick was also low enough to be hit:
    # every catch rate below what that interval happened to achieve came back
    # with the same answer, which drew as a flat line across the chart.
    #
    # Testing every electrode catches everything, so 1 always qualifies.
    low = 1
    high = max(2, max(run_lengths))
    while detection(run_lengths, high) >= catch_rate:
        high *= 2
        if high > _INTERVAL_CEILING:
            return _INTERVAL_CEILING
    while low < high - 1:
        middle = (low + high) // 2
        if detection(run_lengths, middle) >= catch_rate:
            low = middle
        else:
            high = middle
    return low


def payback(bad_share: float, sampling_interval: int, catch_rate: float,
            bad_efficiency: float = 0.0) -> float:
    """Bad electrodes stopped for each GOOD electrode testing costs you.

    The figure that says whether a testing rate is worth running at all, and
    the one thing the catch rate on its own cannot tell you. Catching 90% of a
    2% problem and 90% of a 44% problem are the same row in the table and
    wildly different trades.

    Per 1000 electrodes::

        bad stopped    = 1000 * bad_share * catch_rate
        tests done     = 1000 / sampling_interval
        good lost      = tests * (1 - bad_share)

    The batch size cancels, since it is on both sides, leaving::

        bad_share * sampling_interval * catch_rate / (1 - bad_share)

    NOT every destroyed electrode is a loss. A test lands on a random
    electrode, so ``bad_share`` of the ones you destroy were bad anyway and
    cost nothing; only the good ones are a real loss. Dividing by the tests
    instead of by the good ones lost understated the value of testing, badly
    on a vat where much of the output is bad to begin with.

    ``bad_efficiency`` is how much of a good electrode a bad one still
    delivers if it ships. At 0 a bad electrode is worthless and stopping one is
    worth a whole electrode. At 0.5 it still does half the job, so stopping it
    only saves you the half it was missing, and the payback halves with it. At
    1.0 a "bad" electrode is as good as a good one and stopping it is worth
    nothing at all.

    Above 1.0 you stop more than you destroy. Where the line really sits is a
    business call about what an escape costs beyond its lost efficiency, and
    that judgement is deliberately not in here.
    """
    good_fraction = 1.0 - bad_share
    worth_stopping = 1.0 - bad_efficiency
    if sampling_interval < 1 or bad_share <= 0 or worth_stopping <= 0:
        return 0.0
    if good_fraction <= 0:
        # Everything this vat makes is bad, so testing costs no good
        # electrodes at all. Not a number, and saying so beats printing one.
        return float("inf")
    return bad_share * sampling_interval * catch_rate * worth_stopping / good_fraction


def stopped_and_lost(bad_share: float, sampling_interval: int,
                     catch_rate: float,
                     bad_efficiency: float = 0.0) -> tuple[float, float]:
    """``(bad stopped, good lost)``, both as a percentage of production.

    The two halves of the net, kept as their own figures so a table can show
    the subtraction rather than only its answer. Nobody can then take the net
    and the cost of testing and difference them, which they cannot: some of the
    electrodes testing destroys were bad anyway and are not a loss.

    ``bad stopped`` is already discounted by ``bad_efficiency``, so it is what
    stopping them is WORTH, not how many there were. That is what keeps
    ``net = stopped - lost`` true at every setting of the slider.
    """
    if sampling_interval < 1:
        return 0.0, 0.0
    stopped = 100.0 * bad_share * catch_rate * (1.0 - bad_efficiency)
    lost = (100.0 / sampling_interval) * (1.0 - bad_share)
    return stopped, lost


def net_saved(bad_share: float, sampling_interval: int, catch_rate: float,
              bad_efficiency: float = 0.0, batch: int = 1000) -> float:
    """Electrodes ahead per ``batch`` produced, testing at this interval.

    The same two quantities ``payback`` divides, subtracted instead::

        stopped   = batch * bad_share * catch_rate * (1 - bad_efficiency)
        good lost = batch / sampling_interval * (1 - bad_share)

    Dividing them says whether a rate pays for itself. Subtracting them says by
    how much, and that difference has a PEAK: wide intervals leave most of the
    bad running, tight ones destroy more than they are worth, and somewhere
    between is the best this vat can do. The peak is a recommendation the tool
    can make on its own, where a ratio always needs a threshold from somebody.
    """
    stopped, lost = stopped_and_lost(bad_share, sampling_interval, catch_rate,
                                     bad_efficiency)
    return (stopped - lost) * batch / 100.0


@dataclass(frozen=True)
class SamplingOption:
    """One row of the recommendation: a target, and what meeting it takes."""

    # What was asked for, as 0..1.
    target: float
    # Test one electrode in every this many. Zero when there is nothing to
    # size a rate against, which is not the same as needing no testing.
    sampling_interval: int
    # What that interval actually achieves, which is at least the target and
    # usually a little more, since the interval is a whole number.
    catch_rate: float
    # Share of everything produced that testing destroys, as a percentage.
    production_destroyed: float
    # Bad electrodes stopped per good electrode lost, scaled by how much a
    # bad one is still worth. See payback.
    value: float
    # Net electrodes ahead per 1000 produced: what testing stops, less the
    # good ones it destroys. Payback says whether a rate pays; this says how
    # much it pays, and unlike payback it has a peak. That peak is the answer
    # to "how often should we test" without anybody having to pick a threshold.
    net_saved: float = 0.0
    # The two halves of it, each as a percentage of production, so a table can
    # show the subtraction rather than only its answer.
    bad_stopped: float = 0.0
    good_lost: float = 0.0

    @property
    def net_percent(self) -> float:
        """The net as a share of production, which is the same number per 100."""
        return self.bad_stopped - self.good_lost

    @property
    def worth_it(self) -> bool:
        """Whether it stops more than it destroys, at one for one."""
        return self.value >= 1.0


def sampling_table(run_lengths: list[int], bad_share: float = 0.0,
                   targets=(0.5, 0.8, 0.9, 0.95),
                   bad_efficiency: float = 0.0) -> list[SamplingOption]:
    """One ``SamplingOption`` per target, hardest last.

    ``bad_share`` is the share of that vat's electrodes that are bad, as 0..1.
    Neither it nor ``bad_efficiency`` affects how often to test: that comes
    from the run lengths alone. They only decide what the testing is worth.
    """
    rows = []
    for target in targets:
        interval = interval_for(run_lengths, target)
        achieved = detection(run_lengths, interval) if interval else 0.0
        stopped, lost = stopped_and_lost(bad_share, interval, achieved,
                                         bad_efficiency)
        rows.append(SamplingOption(
            target=target,
            sampling_interval=interval,
            catch_rate=achieved,
            production_destroyed=(100.0 / interval) if interval else 0.0,
            value=payback(bad_share, interval, achieved, bad_efficiency),
            net_saved=net_saved(bad_share, interval, achieved, bad_efficiency),
            bad_stopped=stopped, good_lost=lost))
    return rows


def best_option(options):
    """The best interval on the curve, or None if nothing pays for itself.

    The strict peak, and on an exact tie the SHORTEST interval, which errs
    towards catching more.

    A tolerance band was tried here, treating anything within a couple of
    percent of the peak as tied so the answer stopped wandering as the worth
    slider moved. It was dropped on purpose: the recommendation should be the
    best interval, not a cautious neighbour of it. The top of the curve really
    is flat, and that is shown rather than smoothed away, by the shaded band on
    the plot.
    """
    if not options:
        return None
    ceiling = max(option.net_percent for option in options)
    if ceiling <= 0:
        return None
    return min((option for option in options
                if option.net_percent == ceiling),
               key=lambda option: option.sampling_interval)


# How far below the best a gain can fall and still be a choice somebody could
# defend, in percentage points of production, the same units the Gain column
# prints. Absolute rather than a share of the peak, because that column is
# where it is read off: a row saying +8.56% beside a best of +8.60% is the same
# decision whatever the peak happens to be.
#
# This does NOT move the recommendation. best_option is still the strict peak.
# It only says which other rows are as good, which is what stops the answer
# looking unstable when the peak slides along a level ridge.
ACCEPTABLE_GAIN = 0.2


def acceptable(options, best=None) -> list:
    """Every option scoring within ACCEPTABLE_GAIN of the best, the best included.

    Empty when nothing pays for itself, which is not the same as everything
    being acceptable.
    """
    if not options:
        return []
    best = best or best_option(options)
    if best is None:
        return []
    floor = best.net_percent - ACCEPTABLE_GAIN
    return [option for option in options if option.net_percent >= floor]


def acceptable_band(options, best=None) -> tuple[int, int] | None:
    """The unbroken stretch of acceptable intervals around the best.

    Unbroken, because it is drawn as one shaded region. A stray interval far
    out that happens to score inside the band is not part of the choice.
    """
    best = best or best_option(options)
    if best is None:
        return None
    floor = best.net_percent - ACCEPTABLE_GAIN
    order = sorted(options, key=lambda option: option.sampling_interval)
    index = order.index(best)
    low = high = index
    while low and order[low - 1].net_percent >= floor:
        low -= 1
    while high < len(order) - 1 and order[high + 1].net_percent >= floor:
        high += 1
    return order[low].sampling_interval, order[high].sampling_interval


# The catch rates the curve is drawn over. From 20%, because below that the
# interval runs off into the hundreds and squashes the part of the plot anyone
# is choosing between, and stopping at 99% because 100% needs an interval of one.
CURVE_RATES = tuple(rate / 100 for rate in range(20, 100))


def sampling_curve(run_lengths: list[int], bad_share: float = 0.0,
                   bad_efficiency: float = 0.0) -> list[SamplingOption]:
    """The same thing as ``sampling_table`` but at every catch rate."""
    return sampling_table(run_lengths, bad_share, CURVE_RATES, bad_efficiency)


def interval_curve(run_lengths: list[int], bad_share: float = 0.0,
                   bad_efficiency: float = 0.0) -> list[SamplingOption]:
    """One option per sampling INTERVAL, for plotting against the interval.

    Swept over the interval rather than over the catch rate, because that is
    what the x axis shows. Sweeping catch rates and reading the interval off
    each gives the same curve with duplicates and gaps in it, since several
    catch rates land on the same whole number of electrodes.
    """
    if not run_lengths:
        return []
    # Far enough out that the curve is flat and obviously past its peak.
    ceiling = min(400, max(30, max(run_lengths) * 6))
    out = []
    for interval in range(1, ceiling + 1):
        achieved = detection(run_lengths, interval)
        stopped, lost = stopped_and_lost(bad_share, interval, achieved,
                                         bad_efficiency)
        out.append(SamplingOption(
            target=achieved,
            sampling_interval=interval,
            catch_rate=achieved,
            production_destroyed=100.0 / interval,
            value=payback(bad_share, interval, achieved, bad_efficiency),
            net_saved=net_saved(bad_share, interval, achieved, bad_efficiency),
            bad_stopped=stopped, good_lost=lost))
    return out
