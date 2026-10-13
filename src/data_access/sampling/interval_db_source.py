"""Each vat's sampling interval, read from and written to the database.

    reads   qc_requests.vw_vat_sampling_interval
    writes  qc_requests.usp_vat_interval_set

FEATURE BY PRESENCE. A database that has not had the qc_requests migrations run
on it has neither object, and the application still opens: the intervals read
as empty, every vat shows the default, and a change says it could not be saved
rather than appearing to work. That matters more here than elsewhere, because
the schema is shared with another application and the two are not deployed
together.
"""

from __future__ import annotations

SCHEMA = "qc_requests"


def load_intervals(session) -> dict[str, int]:
    """``{vat: electrodes between samples}``, empty where nothing is set yet."""
    try:
        rows = session.rows(
            f"SELECT [vat], [electrodes_between] "
            f"FROM [{SCHEMA}].[vw_vat_sampling_interval]")
    except Exception as e:
        print(f"Could not read the sampling intervals: {e}")
        return {}

    intervals: dict[str, int] = {}
    for row in rows:
        vat = str(row.get("vat") or "").strip()
        try:
            if vat:
                intervals[vat] = int(row.get("electrodes_between"))
        except (TypeError, ValueError):
            continue
    return intervals


def set_interval(session, vat: str, electrodes_between: int) -> bool:
    """Record one vat's interval. False when it could not be written.

    False rather than an exception: the tab has a spin box, and an operator
    turning it is not a place to put a traceback. The caller says so on screen
    and leaves the figure as the database still has it.
    """
    try:
        session.call(f"[{SCHEMA}].[usp_vat_interval_set]",
                     vat=str(vat).strip(),
                     electrodes_between=int(electrodes_between))
        return True
    except Exception as e:
        print(f"Could not save the interval for vat {vat}: {e}")
        return False
