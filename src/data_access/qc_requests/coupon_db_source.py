"""The coupon settings, read from and written to the shared schema.

    reads   qc_requests.vw_coupon_setting
    writes  qc_requests.usp_set_coupon_setting, usp_request_set_coupons

FEATURE BY PRESENCE, like the sampling intervals beside it. A database without
008 has none of these objects, and the application falls back to the values the
migration seeds, which are what QC does today. The settings dialog is the only
part that notices, and it says so rather than offering to write somewhere that
cannot take it.
"""

from __future__ import annotations

from src.data_access.qc_requests.coupon_columns import (
    REASONS,
    normalize,
    with_defaults,
)

SCHEMA = "qc_requests"


def load_settings(session) -> dict[str, str]:
    """``{reason: coupons}`` for all three, whatever the database holds.

    Always the full three. A caller deciding what a failure is worth should not
    have to know which rows happen to exist, and the defaults are the same ones
    008 seeds, so a missing row and a fresh database behave alike.
    """
    try:
        rows = session.rows(
            f"SELECT [reason], [coupons] FROM [{SCHEMA}].[vw_coupon_setting]")
    except Exception as e:
        print(f"Could not read the coupon settings: {e}")
        return with_defaults(None)

    stored = {}
    for row in rows:
        reason = str(row.get("reason") or "").strip()
        if reason in REASONS:
            stored[reason] = normalize(row.get("coupons"))
    return with_defaults(stored)


def set_setting(session, reason: str, coupons: str) -> bool:
    """Record one setting. False when nothing was written.

    False also when the value was already that, which is not a failure and is
    why the caller is told how many rows moved rather than being thrown at.
    """
    return _changed(session, "usp_set_coupon_setting",
                    reason=str(reason).strip(), coupons=normalize(coupons))


def set_request_coupons(session, electrode: str, coupons: str) -> bool:
    """Say what QC wants from an electrode it has agreed to.

    For an offer, which arrives with nothing recorded because production does
    not decide this. A sent or declined request is left alone by the procedure.
    """
    return _changed(session, "usp_request_set_coupons",
                    electrode=str(electrode).strip(),
                    coupons=normalize(coupons))


def _changed(session, procedure: str, **params) -> bool:
    try:
        rows = session.call(f"[{SCHEMA}].[{procedure}]", **params)
    except Exception as e:
        print(f"{procedure} failed: {e}")
        return False
    try:
        return bool(int((rows or [{}])[0].get("changed") or 0))
    except (AttributeError, IndexError, TypeError, ValueError):
        return False
