"""The SEM EDX measurements, read from and written to the database.

Hands back the wide frame the rest of the application expects: one row per
measured point, with a ``<symbol>_pct`` column per element, which is what
:class:`SemEdxProcessor` and the tab are built on.

THE PIVOT HAPPENS HERE, not in SQL. The database stores one row per element
reading, because which elements exist differs by generation and has already
changed once. A view cannot grow a column per element without dynamic SQL, and
the application already knows how to name those columns, so the long rows come
up as they are and are widened in :func:`load_sem_edx`.

A point is not stored anywhere of its own: it exists because a reading names
it. So the import sends the points nested in its payload and the readings carry
the point number, while the frame here groups them back into one row per point.

Which quantities a tab *shows* is not here either, it is configuration:
``sem_edx.layout`` in config/config.json, read through ``app_config``.

Rows come back ordered by production number, then position, then point, so the
table reads oldest electrode first whatever order things were imported in.
"""

from __future__ import annotations

import json

import pandas as pd

from src.data_access.electrodes.electrode_columns import normalize_marking
from src.data_access.sem_edx.sem_edx_columns import (
    SemEdxColumns,
    FIXED_COLUMNS,
    element_column,
    join_images,
    split_images,
)
from src.data_access.sem_edx.sem_edx_source import GENERATIONS

SCHEMA = "qc_data"

# The order the table is built in. TRY_CONVERT rather than CONVERT so an odd
# marking sorts last instead of failing the whole query.
_ORDER_BY = ("TRY_CONVERT(BIGINT, SUBSTRING(LTRIM(RTRIM([electrode])), 2, 20)), "
             "[electrode], [position], [point]")


def load_generations(session) -> list[str]:
    """The generations there is data for, oldest first, one tab each.

    The order is the application's, from ``GENERATIONS``, since it is a fact
    about the product line rather than about the data. One the code has not
    heard of still gets a tab, built from the configured layout, and goes last
    rather than being dropped.
    """
    known = list(GENERATIONS)
    names = [str(row.get("generation") or "").strip()
             for row in session.rows(
                 f"SELECT [generation] FROM [{SCHEMA}].[sem_generation]")
             if str(row.get("generation") or "").strip()]
    return sorted(names, key=lambda name: (known.index(name)
                                           if name in known else len(known), name))


def load_sem_edx(session, generation: str) -> pd.DataFrame:
    """One generation's measurements, one row per point, elements as columns."""
    rows = session.rows(
        f"SELECT * FROM [{SCHEMA}].[vw_sem_measurement] "
        f"WHERE [generation] = ? ORDER BY {_ORDER_BY}",
        (str(generation).strip(),))
    if not rows:
        return pd.DataFrame(columns=list(FIXED_COLUMNS))

    # One entry per point, in the order the query returned them, gathering the
    # element readings of that point as it goes.
    wide: dict[tuple, dict] = {}
    for row in rows:
        position = str(row.get("position") or "")
        point = row.get("point")
        key = (str(row.get("electrode") or ""), position, point)
        record = wide.get(key)
        if record is None:
            record = wide[key] = _fixed_fields(row, point)
        symbol = str(row.get("symbol") or "").strip()
        if symbol:
            record[element_column(symbol)] = row.get("value")

    df = pd.DataFrame(list(wide.values()))
    for column in FIXED_COLUMNS:
        if column not in df.columns:
            df[column] = ""
    elements = [c for c in df.columns if c not in FIXED_COLUMNS]
    return df[[*FIXED_COLUMNS, *sorted(elements)]]


def _fixed_fields(row: dict, point) -> dict:
    """The per sample columns of one row, spelled the way the CSV spells them."""
    return {
        str(SemEdxColumns.ELECTRODE): str(row.get("electrode") or ""),
        str(SemEdxColumns.ELECTRODE_TYPE): str(row.get("electrode_type") or ""),
        str(SemEdxColumns.POSITION): str(row.get("position") or ""),
        str(SemEdxColumns.ZOOM): str(row.get("zoom") or ""),
        # A sample recorded with no points at all still gets a row, so it shows
        # up on the tab as something to fill in rather than disappearing.
        str(SemEdxColumns.MEASUREMENT): int(point) if point is not None else 0,
        str(SemEdxColumns.OPERATOR): str(row.get("operator") or ""),
        str(SemEdxColumns.NOTE): str(row.get("note") or ""),
        str(SemEdxColumns.DATE): str(row.get("measured_on") or ""),
        str(SemEdxColumns.FOLDER): str(row.get("folder") or ""),
        str(SemEdxColumns.IMAGES): join_images(split_images(row.get("images"))),
    }


# ---- writes ---------------------------------------------------------------
def import_measurements(session, generation: str, electrode: str,
                        electrode_type: str, folder: str, measurements,
                        images: dict[tuple[str, str], list[str]]) -> tuple[int, int]:
    """Write one electrode's parsed measurements. Returns (imported, skipped).

    One call per electrode rather than per reading: a folder can hold five
    samples of three points of ten elements, and an operator should not wait
    for a hundred and fifty round trips.
    """
    samples: dict[str, dict] = {}
    for measurement in measurements:
        position = str(measurement.position).strip()
        sample = samples.get(position)
        if sample is None:
            sample = samples[position] = {
                "position": position,
                "zoom": str(getattr(measurement, "zoom", "") or ""),
                # Nothing in an export names who ran the microscope, so these
                # two are filled in afterwards from the sample's own window.
                "operator": "",
                "note": "",
                "measured_on": str(getattr(measurement, "date", "") or ""),
                "images": list(images.get((measurement.electrode, position), [])),
                "points": [],
            }
        sample["points"].append({
            "point": int(measurement.point),
            "readings": [{"symbol": symbol, "value": value}
                         for symbol, value in (measurement.elements or {}).items()],
        })

    payload = {
        "generation": str(generation).strip(),
        "electrode": normalize_marking(electrode),
        "electrode_type": str(electrode_type).strip().upper(),
        "folder": str(folder or ""),
        "samples": list(samples.values()),
    }
    result = session.call(f"[{SCHEMA}].[usp_sem_sample_import]",
                          payload=json.dumps(payload))
    first = result[0] if result else {}
    return int(first.get("imported") or 0), int(first.get("skipped") or 0)


def update_sample(session, generation: str, electrode: str, position: str,
                  zoom: str = "", operator: str = "", note: str = "") -> bool:
    """Set the by-hand fields on one sample. False if there is no such sample."""
    result = session.call(
        f"[{SCHEMA}].[usp_sem_sample_update]",
        generation=str(generation).strip(), electrode=str(electrode).strip(),
        position=str(position).strip(), zoom=zoom, operator=operator, note=note)
    return bool(result and result[0].get("changed"))


def delete_sample(session, generation: str, electrode: str,
                  position: str) -> bool:
    """Remove one sample and everything measured at it.

    Its readings and its recorded filenames go with it through the cascades.
    The captures themselves stay in the shared library, which is not this
    application's to tidy.
    """
    result = session.call(
        f"[{SCHEMA}].[usp_sem_sample_delete]",
        generation=str(generation).strip(), electrode=str(electrode).strip(),
        position=str(position).strip())
    return bool(result and result[0].get("deleted"))


def set_reading(session, generation: str, electrode: str, position: str,
                point: int, symbol: str, value) -> None:
    """Correct one measured percentage. ``None`` is a reading never taken."""
    session.call(
        f"[{SCHEMA}].[usp_sem_reading_set]",
        generation=str(generation).strip(), electrode=str(electrode).strip(),
        position=str(position).strip(), point=int(point), symbol=str(symbol),
        value=None if value is None else float(value))
