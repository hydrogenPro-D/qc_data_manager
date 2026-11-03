"""Whether automatic sampling is on, read from and written to the database.

    reads   qc_requests.vw_sampling_switch
    writes  qc_requests.usp_sampling_switch_set

IT HAS TO BE IN THE DATABASE. Both applications call
``usp_raise_due_requests`` every 60 seconds, so a switch held in this one would
only have switched off this one. The procedure reads the same row and raises
nothing while it is off.

FEATURE BY PRESENCE, like the intervals beside it. On a database without
migration 016 the read answers None, which the tab reads as "there is no switch
here" and shows nothing rather than a control that cannot work.
"""

from __future__ import annotations

SCHEMA = "qc_requests"


def load_switch(session) -> bool | None:
    """True if automatic sampling is on, None if the switch cannot be read."""
    try:
        rows = session.rows(
            f"SELECT [is_on] FROM [{SCHEMA}].[vw_sampling_switch]")
    except Exception as e:
        print(f"Could not read the sampling switch: {e}")
        return None
    for row in rows:
        value = row.get("is_on")
        return None if value is None else bool(value)
    return None


def set_switch(session, on: bool, start_now: bool = False) -> bool:
    """Throw the switch. False when it could not be written.

    ``start_now`` is only read on the way on: it says the next electrode each
    vat makes is the one to ask for, rather than counting a full interval from
    where the vat stands now.
    """
    try:
        session.call(f"[{SCHEMA}].[usp_sampling_switch_set]",
                     on=1 if on else 0, start_now=1 if start_now else 0)
        return True
    except Exception as e:
        print(f"Could not change the sampling switch: {e}")
        return False
