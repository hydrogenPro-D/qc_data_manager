"""QC requests, read from and written to the shared schema.

    reads   qc_requests.vw_qc_request
    writes  qc_requests.usp_request_raise, usp_request_qc_approve,
            usp_request_qc_decline, usp_raise_due_requests

ONLY THIS APPLICATION'S HALF. Production Data Manager has its own procedures
for the moves that belong to it, and neither side is granted the other's. The
ones missing here are missing on purpose, see sql/qc_requests/004.

FEATURE BY PRESENCE, like the sampling intervals. A database without the
qc_requests migrations has none of these objects and the application still
opens: every electrode reads as having no request and the column stays empty.
The schema is shared with another application and the two are not deployed
together, so this is the normal state of affairs rather than an error.
"""

from __future__ import annotations

SCHEMA = "qc_requests"


def load_requests(session) -> dict[str, str]:
    """``{electrode: state}``, empty where nothing has been asked for."""
    try:
        rows = session.rows(
            f"SELECT [electrode], [state] FROM [{SCHEMA}].[vw_qc_request]")
    except Exception as e:
        print(f"Could not read the QC requests: {e}")
        return {}

    requests: dict[str, str] = {}
    for row in rows:
        electrode = str(row.get("electrode") or "").strip()
        state = str(row.get("state") or "").strip()
        if electrode and state:
            requests[electrode] = state
    return requests


def raise_request(session, electrode: str, kind: str) -> bool:
    """Ask production for one electrode. False when nothing was written.

    False rather than an exception, and it covers two cases the caller treats
    the same way: the database refused, and the electrode already has a
    request. One row per electrode ever, so asking twice is not an error, it
    simply does not happen.
    """
    return _changed(session, "usp_request_raise",
                    electrode=str(electrode).strip(), kind=str(kind))


def approve(session, electrode: str, expected_state: str) -> bool:
    """Accept an electrode a technician offered."""
    return _changed(session, "usp_request_qc_approve",
                    electrode=str(electrode).strip(),
                    expected_state=str(expected_state))


def decline(session, electrode: str, expected_state: str) -> bool:
    """Turn down an electrode a technician offered."""
    return _changed(session, "usp_request_qc_decline",
                    electrode=str(electrode).strip(),
                    expected_state=str(expected_state))


def raise_due_requests(session) -> int:
    """Run the sampling rule. How many requests it raised, 0 on any failure.

    Safe to call as often as anybody likes: it raises what is due and nothing
    else. The other application calls the same procedure, so whichever of the
    two is open keeps the sampling moving.
    """
    try:
        rows = session.call(f"[{SCHEMA}].[usp_raise_due_requests]")
    except Exception as e:
        print(f"Could not raise the due QC requests: {e}")
        return 0
    try:
        return int((rows or [{}])[0].get("raised") or 0)
    except (AttributeError, IndexError, TypeError, ValueError):
        return 0


def _changed(session, procedure: str, **params) -> bool:
    """Run one write and report whether it moved anything."""
    try:
        rows = session.call(f"[{SCHEMA}].[{procedure}]", **params)
    except Exception as e:
        print(f"{procedure} failed: {e}")
        return False
    try:
        return bool(int((rows or [{}])[0].get("changed") or 0))
    except (AttributeError, IndexError, TypeError, ValueError):
        return False
