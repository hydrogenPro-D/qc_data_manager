"""The per-electrode QC status, read from the database.

``data/ETL/01_export_electrode_status.py`` derives these from the hand curated
ranges and ``04_load_to_database.py`` loads them; the app only reads them.

Missing is still Not Tested: an electrode the table has never heard of degrades
to "we do not know yet" rather than to an error.
"""

from __future__ import annotations

from src.data_access.electrodes.electrode_columns import (
    ElectrodeStatus,
    normalize_status,
)

SCHEMA = "qc_data"


def load_status_by_marking(session) -> dict[str, str]:
    """``{'B14174': 'Released', ...}`` from ``qc_data.vw_electrode_status``.

    Unknown status values are dropped rather than shown, so a row that got in
    before the lookup existed cannot put an unstyled value in the Status column.
    """
    known = {str(s) for s in ElectrodeStatus}
    statuses: dict[str, str] = {}
    unknown: set[str] = set()

    for row in session.rows(f"SELECT [electrode], [status] "
                            f"FROM [{SCHEMA}].[vw_electrode_status]"):
        marking = str(row.get("electrode") or "").strip()
        status = normalize_status(row.get("status") or "")
        if not marking:
            continue
        if status in known:
            statuses[marking] = status
        else:
            unknown.add(status)

    if unknown:
        print(f"Ignored unknown status value(s) in "
              f"{SCHEMA}.vw_electrode_status: {sorted(unknown)}")
    if not statuses:
        print(f"No rows in {SCHEMA}.vw_electrode_status, every electrode reads "
              f"'{ElectrodeStatus.NOT_TESTED}'. Run data/ETL/04_load_to_database.py.")
    return statuses


def load_manual_edit_times(session) -> dict[str, str]:
    """``{'B14116': '2026-08-19 11:48:40.588261', ...}`` for hand set statuses.

    Only the corrections, and only when each was made. A frozen board verdict
    covers the same electrode from ``qc_board_archive``, so the two have to be
    ordered to know which one the operator meant last, see
    ``DbManager._settled_statuses``.
    """
    times: dict[str, str] = {}
    for row in session.rows(f"SELECT [electrode], [updated_at_utc] "
                            f"FROM [{SCHEMA}].[vw_electrode_status] "
                            f"WHERE [source] = N'manual'"):
        marking = str(row.get("electrode") or "").strip()
        if marking:
            times[marking] = str(row.get("updated_at_utc") or "")
    return times
