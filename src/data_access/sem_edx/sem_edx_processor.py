"""Turns raw SEM EDX measurements into the frames the tab renders.

Pure ``DataFrame -> DataFrame``: no Qt, no file access, no database. Two shapes
come out of it:

* :meth:`SemEdxProcessor.process` — one row per *sample*, laid out like the
  workbook: each element's individual points followed by their average, and
  each ratio's average in the place the sheet keeps it.
* :meth:`SemEdxProcessor.measurements` — the individual measurements behind one
  sample, for the detail view.

Which quantities exist is not decided here. The processor is handed a layout,
``['Ni', 'S', 'Ni:S', 'Co', ...]``, read from whatever the workbook turned out
to hold, so anodes and cathodes produce different frames from the same code.

Averages follow the workbook exactly, including the ratio averages: a ratio is
worked out for each measurement and those are averaged. Averaging the
percentages first and dividing once would be the more defensible statistic, but
matching the sheet operators already read matters more, and on this data the two
differ by well under a percent.
"""

from __future__ import annotations

import pandas as pd

from src.data_access.sem_edx.sem_edx_columns import (
    SemEdxColumns,
    POINT_COUNT,
    SAMPLE_KEY,
    Quantity,
    average_column,
    electrode_average_column,
    element_column,
    first_ratio,
    point_column,
    quantities,
)

# Where a measurement sits within its sample, 1 based. Internal to the widening.
_POINT = "_point"


def _ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    """Element-wise division with a zero or missing denominator giving NaN."""
    denominator = pd.to_numeric(denominator, errors="coerce").replace(0, pd.NA)
    return pd.to_numeric(numerator, errors="coerce") / denominator


class SemEdxProcessor:
    """Reshapes SEM EDX measurements for display.

    Args:
        layout: The quantities to produce, in the order the sheet holds them.
            ``['Ni', 'S', 'Ni:S', 'Co', 'Ni:Co']`` for an anode.
    """

    # Per sample columns carried through untouched: they are recorded once for
    # the whole sample, so the first measurement's value speaks for all of them.
    CARRIED = (str(SemEdxColumns.ELECTRODE_TYPE), str(SemEdxColumns.ZOOM),
               str(SemEdxColumns.OPERATOR), str(SemEdxColumns.NOTE),
               str(SemEdxColumns.DATE), str(SemEdxColumns.FOLDER),
               str(SemEdxColumns.IMAGES))

    def __init__(self, layout: list[str] | None = None) -> None:
        self.quantities: list[Quantity] = quantities(layout)

    def summary_columns(self, points: int = POINT_COUNT) -> list[str]:
        """Column order of the per sample frame, following the workbook.

        Two departures from the sheet: electrode type is added, since the sheet
        splits anodes and cathodes into two tabs; and a ratio carries only its
        average, because a ratio is read as one number per sample.
        """
        measured: list[str] = []
        for quantity in self.quantities:
            if not quantity.is_ratio:
                measured += [point_column(quantity.column, p)
                             for p in range(1, points + 1)]
            measured.append(average_column(quantity.column))

        # One figure covering the whole electrode rather than this spot on it.
        # Placed where the workbook puts it, after the operator and before the
        # note, since it belongs to neither the quantity block nor the sample.
        ratio = first_ratio([q.name for q in self.quantities])
        electrode_average = ([electrode_average_column(ratio.column)]
                             if ratio is not None else [])

        # The workbook's own block first, then who measured it, when, and where
        # the captures live.
        return [str(SemEdxColumns.ELECTRODE), str(SemEdxColumns.ELECTRODE_TYPE),
                str(SemEdxColumns.POSITION), str(SemEdxColumns.ZOOM),
                *measured,
                str(SemEdxColumns.OPERATOR), *electrode_average,
                str(SemEdxColumns.DATE),
                str(SemEdxColumns.FOLDER), str(SemEdxColumns.IMAGES),
                str(SemEdxColumns.NOTE)]

    def detail_columns(self) -> list[str]:
        """Column order of the per measurement frame."""
        return [str(SemEdxColumns.MEASUREMENT),
                *(q.column for q in self.quantities)]

    def process(self, df: pd.DataFrame | None) -> pd.DataFrame:
        """Measurements -> one row per sample, points beside their average.

        An empty input still yields the full column set, so the table keeps its
        headers.
        """
        if df is None or df.empty:
            return pd.DataFrame(columns=self.summary_columns())

        keys = list(SAMPLE_KEY)
        df = self._add_ratios(df.copy())
        # sort=False keeps the workbook's own order while grouping.
        grouped = df.groupby(keys, sort=False, dropna=False)
        df[_POINT] = grouped.cumcount() + 1
        # A sample with more than the usual three points gets more columns
        # rather than quietly losing the extras.
        points = max(POINT_COUNT, int(df[_POINT].max()))

        carried = [c for c in self.CARRIED if c in df.columns]
        out = grouped[carried].first().reset_index()
        out = out.join(self._widen(df, keys, points, out))
        self._add_electrode_average(df, out)

        # Oldest production first, so the newest sample lands at the bottom and
        # the tab opens scrolled to it. Markings sort by their number, not as
        # the strings they render as.
        out = out.sort_values(
            str(SemEdxColumns.ELECTRODE),
            key=lambda s: s.map(marking_number), kind="stable")
        return out.reindex(columns=self.summary_columns(points)).reset_index(drop=True)

    def measurements(self, df: pd.DataFrame | None, electrode: str,
                     position: str) -> pd.DataFrame:
        """The individual measurements of one sample, with their own ratios."""
        columns = self.detail_columns()
        if df is None or df.empty:
            return pd.DataFrame(columns=columns)

        electrode_column, position_column = SAMPLE_KEY
        match = ((df[electrode_column].astype(str).str.strip() == str(electrode).strip())
                 & (df[position_column].astype(str).str.strip() == str(position).strip()))
        rows = df[match]
        if rows.empty:
            return pd.DataFrame(columns=columns)

        return self._add_ratios(rows.copy()).reindex(columns=columns).reset_index(drop=True)

    def _widen(self, df: pd.DataFrame, keys: list[str], points: int,
               order: pd.DataFrame) -> pd.DataFrame:
        """One column per (element, point), plus each quantity's average.

        ``order`` fixes the row order: pivoting sorts the keys, so the result is
        put back into the order the samples appear in.
        """
        present = [q for q in self.quantities if q.column in df.columns]
        if not present:
            return pd.DataFrame(index=order.index)

        names = [q.column for q in present]
        index = pd.MultiIndex.from_frame(order[keys])
        wide = df.pivot(index=keys, columns=_POINT, values=names).reindex(index)
        averages = df.groupby(keys, sort=False, dropna=False)[names].mean()

        columns: dict[str, list] = {}
        for quantity in present:
            for point in (range(1, points + 1) if not quantity.is_ratio else ()):
                key = (quantity.column, point)
                columns[point_column(quantity.column, point)] = (
                    list(wide[key]) if key in wide.columns else [pd.NA] * len(order))
            # The workbook averages the per measurement ratios rather than
            # dividing the averaged percentages, so this matches the sheet.
            columns[average_column(quantity.column)] = list(averages[quantity.column])
        return pd.DataFrame(columns, index=order.index)

    def _add_electrode_average(self, measurements: pd.DataFrame,
                               out: pd.DataFrame) -> None:
        """One ratio averaged over every measurement of the whole electrode.

        Across all of its samples, not just the one on the row, so an electrode
        mapped at five spots shows the same figure on each of the five. That is
        the point of it: it says what the electrode reads, next to what this
        spot on it reads.
        """
        ratio = first_ratio([q.name for q in self.quantities])
        if ratio is None or ratio.column not in measurements.columns:
            return

        electrode = str(SemEdxColumns.ELECTRODE)
        averages = measurements.groupby(electrode, sort=False)[ratio.column].mean()
        out[electrode_average_column(ratio.column)] = out[electrode].map(averages)

    def _add_ratios(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add every ratio the layout asks for, per measurement."""
        for quantity in self.quantities:
            if not quantity.is_ratio:
                continue
            numerator, denominator = (element_column(s) for s in quantity.parts)
            if numerator in df.columns and denominator in df.columns:
                df[quantity.column] = _ratio(df[numerator], df[denominator])
        return df


def marking_number(marking) -> int:
    """'B13951' -> 13951, so markings sort numerically. Unparseable ones sort
    last by reading as -1."""
    digits = "".join(c for c in str(marking) if c.isdigit())
    return int(digits) if digits else -1
