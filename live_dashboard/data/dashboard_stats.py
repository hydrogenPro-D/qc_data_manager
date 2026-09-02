"""Cut the electrode frame down to a time window and reduce it to figures.

Pure ``DataFrame`` in, plain numbers out. No Qt and no database, so every
figure the wall screen shows can be checked in a unit test, which matters more
here than anywhere else in the codebase: nobody is sitting in front of this
program to notice that a number went wrong.

The GUI never counts anything itself. It asks for a ``WindowStats`` and draws
it.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import pandas as pd

from src.data_access.electrodes.electrode_columns import (
    ElectrodeColumns,
    ElectrodeStatus,
    STATUS_COLUMN,
    VAT_COLUMN,
)

# The three windows the deck rotates through, as ``(title, days)``. Order is
# the rotation order.
WINDOWS: tuple[tuple[str, int], ...] = (
    ("Last 24 hours", 1),
    ("Last 7 days", 7),
    ("Last 30 days", 30),
)

# The production block, in the wording the printed report uses. Repeated here
# rather than imported because the report's copy lives in a Qt module and this
# one must not. A test asserts the two still agree, so they cannot drift.
ALL_PRODUCED = "All electrodes"
SCRAPPED_TOTAL = "Scrapped"
SCRAP_FAULTS = "Production faults"
SCRAP_TESTING = "Consumed by QC testing"
ALL_TESTED = "All tested electrodes"

# What an electrode reads when the process log has no vat for it. Grouped under
# one heading rather than dropped: a hundred electrodes nobody can attribute is
# itself worth seeing on the screen.
NO_VAT = "Unknown"

_MARKING = str(ElectrodeColumns.MARKING)
_DATE = str(ElectrodeColumns.DATE)
_TYPE = str(ElectrodeColumns.FULL_NAME)


@dataclass(frozen=True)
class VatRow:
    """One plating vat's showing over the window."""

    vat: str
    produced: int
    faults: int

    @property
    def fault_rate(self) -> float:
        """Share of this vat's output that will not ship, as a percentage."""
        return (self.faults / self.produced * 100) if self.produced else 0.0


@dataclass(frozen=True)
class BucketRow:
    """One step of the throughput series, split by what was on the line.

    Carries its own axis label. A 24 hour window is bucketed by HOUR and a
    longer one by day, so the chart cannot work the wording out for itself, and
    a chart that has to know about calendars is a chart with a date bug in it.
    """

    label: str
    cathodes: int
    anodes: int

    @property
    def total(self) -> int:
        return self.cathodes + self.anodes


@dataclass(frozen=True)
class WindowStats:
    """Everything one screen of the deck needs, worked out in one pass."""

    title: str
    days: int
    produced: int = 0
    # ``(label, count, depth)``, the shape the report's blocks already use.
    production: list[tuple[str, int, int]] = field(default_factory=list)
    qc: list[tuple[str, int, int]] = field(default_factory=list)
    # Keyed by the STORED status, so the GUI owns the wording and the colours.
    status_counts: dict[str, int] = field(default_factory=dict)
    vats: list[VatRow] = field(default_factory=list)
    series: list[BucketRow] = field(default_factory=list)
    # What one step of ``series`` covers, for the chart's own heading.
    bucket: str = "day"
    tested: int = 0
    passed: int = 0
    failed: int = 0
    on_board: int = 0
    faults: int = 0

    @property
    def pass_rate(self) -> float:
        """Share of tested electrodes that passed. Zero tests reads as zero."""
        return (self.passed / self.tested * 100) if self.tested else 0.0

    @property
    def fault_rate(self) -> float:
        """Share of everything produced that will not ship."""
        return (self.faults / self.produced * 100) if self.produced else 0.0

    @property
    def per_day(self) -> float:
        """Average daily output over the window."""
        return self.produced / self.days if self.days else 0.0


def _dates(df: pd.DataFrame) -> pd.Series:
    """The production date of every row, as UTC timestamps.

    Anything unparseable becomes NaT and drops out of every window. A row with
    no date cannot be placed in time, and guessing would put it in the 24 hour
    window, which is the one people actually act on.
    """
    return pd.to_datetime(df[_DATE], errors="coerce", utc=True)


def _moment(now: dt.datetime | None) -> pd.Timestamp:
    """The instant the windows are measured back from, always tz aware."""
    stamp = pd.Timestamp(now or dt.datetime.now(dt.timezone.utc))
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp


def window_frame(df: pd.DataFrame | None, days: int,
                 now: dt.datetime | None = None) -> pd.DataFrame:
    """The rows produced within ``days`` of ``now``.

    ``now`` is injectable so the tests do not depend on the clock. It defaults
    to the real one, which is what the screen uses.
    """
    if df is None or df.empty or _DATE not in df.columns:
        return pd.DataFrame(columns=list(df.columns) if df is not None else [])
    stamps = _dates(df)
    return df[stamps.notna() & (stamps > _moment(now) - pd.Timedelta(days=days))]


def _counts(df: pd.DataFrame) -> dict[str, int]:
    """``{stored status: how many}``, every status present or not.

    Zeroes are kept. A category vanishing from a legend reads as a bug in the
    screen; a category sitting at zero reads as the news that it is at zero.
    """
    counted: dict[str, int] = {}
    if STATUS_COLUMN in df.columns:
        raw = df[STATUS_COLUMN].astype(str).str.strip().value_counts()
        counted = {str(name): int(value) for name, value in raw.items()}
    return {str(status): counted.get(str(status), 0) for status in ElectrodeStatus}


def _blocks(counts: dict[str, int], produced: int
            ) -> tuple[list[tuple[str, int, int]], list[tuple[str, int, int]]]:
    """The production and QC blocks, exactly as the printed report builds them.

    ``(label, count, depth)``: depth 0 is the line the rest are measured
    against, 1 a row of the block, 2 a breakdown of the row above it. The four
    indented production rows add up to the first, so anyone reading the screen
    can see that nothing has been dropped.
    """
    def count(status) -> int:
        return counts.get(str(status), 0)

    rejected = count(ElectrodeStatus.SCRAPPED)
    passed = count(ElectrodeStatus.TESTED_AND_RELEASED)
    failed = count(ElectrodeStatus.TESTED_AND_SCRAPPED)
    consumed = passed + failed

    production = [
        (ALL_PRODUCED, produced, 0),
        (str(ElectrodeStatus.RELEASED), count(ElectrodeStatus.RELEASED), 1),
        (SCRAPPED_TOTAL, rejected + consumed, 1),
        (SCRAP_FAULTS, rejected + failed, 2),
        (SCRAP_TESTING, passed, 2),
        (str(ElectrodeStatus.TEST_ONGOING), count(ElectrodeStatus.TEST_ONGOING), 1),
        (str(ElectrodeStatus.NEEDS_TESTING), count(ElectrodeStatus.NEEDS_TESTING), 1),
        (str(ElectrodeStatus.NOT_TESTED), count(ElectrodeStatus.NOT_TESTED), 1),
    ]
    # What QC itself did. Needs testing and Not Tested are deliberately absent:
    # they are in the production block, where they are what makes it add up,
    # and the same two numbers twice on one screen read as a mistake.
    qc = [
        (str(ElectrodeStatus.TESTED_AND_RELEASED), passed, 1),
        (str(ElectrodeStatus.TESTED_AND_SCRAPPED), failed, 1),
        (ALL_TESTED, consumed, 0),
    ]
    return production, qc


def _vat_league(df: pd.DataFrame, limit: int) -> list[VatRow]:
    """The vats with the most to answer for, worst fault rate first.

    Sorted by rate and then by volume, so of two vats at the same rate the
    busier one is listed first. Vats with nothing in the window are absent
    entirely, which is what stops a thirty vat plant filling the card with
    zeroes on the 24 hour screen.
    """
    if VAT_COLUMN not in df.columns or df.empty:
        return []
    faulty = {str(ElectrodeStatus.SCRAPPED), str(ElectrodeStatus.TESTED_AND_SCRAPPED)}
    vats = (df[VAT_COLUMN].astype(str).str.strip()
            .replace("", NO_VAT).fillna(NO_VAT))
    statuses = (df[STATUS_COLUMN].astype(str).str.strip()
                if STATUS_COLUMN in df.columns else pd.Series("", index=df.index))

    rows = [VatRow(vat=str(name), produced=int(len(group)),
                   faults=int(statuses.loc[group.index].isin(faulty).sum()))
            for name, group in df.groupby(vats, sort=False)]
    rows.sort(key=lambda row: (-row.fault_rate, -row.produced, row.vat))
    return rows[:max(1, limit)]


def _bucket_plan(days: int, moment) -> tuple[list, str]:
    """The steps of the series and the word for one of them.

    Hourly on the 24 hour window, daily on the others. A single day plotted as
    one bar is a rectangle the width of the card, which says nothing at all;
    twenty-four hourly bars over the same rows show the shifts.
    """
    if days <= 1:
        top = moment.floor("h")
        return [top - pd.Timedelta(hours=n) for n in range(23, -1, -1)], "hour"
    top = moment.normalize()
    return [top - pd.Timedelta(days=n) for n in range(days - 1, -1, -1)], "day"


def _series(df: pd.DataFrame, days: int,
            now: dt.datetime | None = None) -> tuple[list[BucketRow], str]:
    """The throughput series, oldest step first, and what one step covers.

    EVERY step is present, including the ones that produced nothing. A chart
    that silently skips an idle Sunday, or a quiet night shift, shows a flat
    stretch where there was a stoppage, which is the opposite of the truth.
    """
    steps, unit = _bucket_plan(days, _moment(now))
    hourly = unit == "hour"

    cathodes: dict = {}
    anodes: dict = {}
    if not df.empty and _DATE in df.columns:
        kinds = (df[_TYPE].astype(str).str.strip().str.upper()
                 if _TYPE in df.columns else pd.Series("", index=df.index))
        for stamp, kind in zip(_dates(df), kinds):
            if pd.isna(stamp):
                continue
            slot = stamp.floor("h") if hourly else stamp.normalize()
            bucket = anodes if str(kind).startswith("ANODE") else cathodes
            bucket[slot] = bucket.get(slot, 0) + 1

    stamp_format = "%H" if hourly else "%d.%m"
    return ([BucketRow(label=step.strftime(stamp_format),
                       cathodes=cathodes.get(step, 0),
                       anodes=anodes.get(step, 0)) for step in steps], unit)


def summarise(df: pd.DataFrame | None, title: str, days: int,
              vat_limit: int = 6, now: dt.datetime | None = None) -> WindowStats:
    """Everything one screen shows, from the processed electrode frame.

    One pass over the window rather than one per card: the deck rebuilds all
    three screens on every refresh, over about twelve thousand rows.
    """
    window = window_frame(df, days, now)
    counts = _counts(window)
    produced = int(len(window))
    production, qc = _blocks(counts, produced)

    passed = counts.get(str(ElectrodeStatus.TESTED_AND_RELEASED), 0)
    failed = counts.get(str(ElectrodeStatus.TESTED_AND_SCRAPPED), 0)
    series, bucket = _series(window, days, now)
    return WindowStats(
        title=title,
        days=days,
        produced=produced,
        production=production,
        qc=qc,
        status_counts=counts,
        vats=_vat_league(window, vat_limit),
        series=series,
        bucket=bucket,
        tested=passed + failed,
        passed=passed,
        failed=failed,
        on_board=counts.get(str(ElectrodeStatus.TEST_ONGOING), 0),
        faults=counts.get(str(ElectrodeStatus.SCRAPPED), 0) + failed,
    )


def summarise_all(df: pd.DataFrame | None, vat_limit: int = 6,
                  now: dt.datetime | None = None) -> list[WindowStats]:
    """One ``WindowStats`` per screen of the deck, in rotation order."""
    return [summarise(df, title, days, vat_limit, now) for title, days in WINDOWS]
