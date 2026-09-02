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


def runs_in(families: list[str]) -> tuple[list[int], list[int]]:
    """``(bad lengths, good lengths)`` from one vat's sequence of verdicts.

    An undecided electrode is STEPPED OVER, joining the stretches either side
    rather than ending the run it interrupts. It is a gap in what has been
    looked at, not evidence that the chemistry changed at that point, and
    ending the run there chopped genuine ones into several short pieces. That
    is the one error worth avoiding here, because short runs are exactly what
    drives the recommended testing rate down.
    """
    bad: list[int] = []
    good: list[int] = []
    current = ""
    length = 0

    def close() -> None:
        nonlocal current, length
        if length:
            (bad if current == BAD else good).append(length)
        current, length = "", 0

    for value in families:
        if value == UNKNOWN:
            continue
        if value != current:
            close()
            current = value
        length += 1
    close()
    return bad, good


def _ordered(frame: pd.DataFrame) -> list[str]:
    """One vat's verdicts in production order, oldest electrode first.

    By the marking's NUMBER, not by row order. The rows arrive newest first and
    the numbering has gaps, and a stretch walked in the wrong order is not a
    stretch at all.
    """
    numbers = frame[_MARKING].map(parse_marking)
    ordered = frame.assign(_n=numbers).dropna(subset=["_n"]).sort_values("_n")
    return [family(status) for status in ordered[STATUS_COLUMN].astype(str)]


def analyse(df: pd.DataFrame | None, first: int, last: int) -> Analysis:
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
        bad, good = runs_in(_ordered(group))
        # POOLED FROM THE PER VAT RUNS, never re-measured across the plant
        # sequence: a run belongs to a vat, and the combined figure has to be
        # the same runs counted once, not a different measurement.
        all_bad.extend(bad)
        all_good.extend(good)
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
def detection(lengths: list[int], spacing: int) -> float:
    """Share of the observed bad runs a test every ``spacing`` would find.

    A run of length L is caught with probability ``min(1, L / spacing)``: the
    test either lands inside it or it does not, and over many runs that is
    how often it does. Averaged over the runs actually measured rather than
    over an assumed distribution, so the answer is about this plant.
    """
    if not lengths or spacing < 1:
        return 0.0
    return statistics.fmean(min(1.0, length / spacing) for length in lengths)


def spacing_for(lengths: list[int], target: float) -> int:
    """The widest testing gap that still catches ``target`` of the bad runs.

    Widest, because every electrode tested is an electrode destroyed. Detection
    only falls as the gap widens, so this walks out until it drops below the
    target and takes the last gap that held.
    """
    if not lengths or target <= 0:
        return 0
    ceiling = max(lengths) * 4 + 2
    best = 1
    for spacing in range(1, ceiling):
        if detection(lengths, spacing) < target:
            break
        best = spacing
    return best


def sampling_table(lengths: list[int],
                   targets=(0.5, 0.8, 0.9, 0.95)) -> list[tuple[float, int, float]]:
    """``(target, test every k, share of production destroyed)`` per target."""
    rows = []
    for target in targets:
        spacing = spacing_for(lengths, target)
        rows.append((target, spacing, (100.0 / spacing) if spacing else 0.0))
    return rows
