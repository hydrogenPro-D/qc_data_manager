"""Which frozen rows no longer say what the app shows, and what they should say.

A finalise writes one archive row per electrode in a card's stretch, as the
status stood that second, and nothing rewrites it afterwards. A row frozen as a
placeholder therefore stays one even once a later verdict has released the
stretch it sits in. The tab re-runs the sweeps on every render and is right;
the table keeps the old answer, and every other reader of the database sees
that one.

This works out the difference. Pure data in, data out, no Qt and no database,
so the rules can be tested directly and the maintenance script and the app can
share them.
"""

from __future__ import annotations

from src.data_access.electrodes.electrode_columns import (
    DECIDED,
    NEVER_STORED,
    ElectrodeStatus,
)

# More than this and something is wrong with the inputs rather than with the
# table. The whole archive is about two thousand rows and a normal correction
# is a handful, so a plan this size is a bug asking to be written to
# production.
REPAIR_LIMIT = 500

# The words the archive is allowed to hold. The rest are worked out at render
# time and belong nowhere near a write.
STORED = {str(status) for status in ElectrodeStatus if status not in NEVER_STORED}
FINAL = {str(status) for status in DECIDED}


def stale_rows(shown: dict[str, str], held: dict[str, str],
               archived: dict[str, dict], sources: dict[str, str],
               on_board: set[str], by_hand: set[str]) -> list[dict]:
    """``[{electrode, was, status, caused_by, archived_at_utc}]``, oldest first.

    ``was`` is what the row must still say for the write to touch it, and None
    on an electrode that has no row at all.

    Only an electrode that has settled is ever written: a placeholder is the
    absence of an answer, and one placeholder replacing another is churn.

    Args:
        shown: ``{marking: status}`` as the Electrodes tab is displaying it.
        held: ``{marking: the live card driving it}``, empty where none does.
        archived: the frozen rows, ``{marking: {status, from, at}}``.
        sources: ``{marking: the decision that claims it}`` from the
            propagation, which is what an unrecorded electrode is recorded as
            having been condemned by.
        on_board: electrodes with a card on the board.
        by_hand: electrodes whose status an operator set, which live in
            electrode_status and are not the archive's to answer for.
    """
    rows = []
    for marking, status in shown.items():
        if status not in STORED or held.get(marking):
            continue
        if marking in on_board or marking in by_hand:
            continue
        # ONLY EVER AN OUTCOME. Not Tested and Needs testing are the absence of
        # an answer, and which of the two an electrode reads depends on the
        # queueing rules, which differ between databases and move when a coupon
        # setting does. Writing those down made 470 rows on the test database
        # and would have churned on every startup. The rows that matter, and
        # the only ones any reader counts, are the ones that have settled.
        if status not in FINAL:
            continue
        row = archived.get(marking)
        source = sources.get(marking, "")
        if row is None:
            # Never recorded anywhere: no reader can see it, not even as a
            # placeholder. Only worth writing where a decision claims it.
            if source:
                rows.append(_row(marking, None, status, source,
                                 _stamp(archived.get(source))))
            continue
        frozen = str(row.get("status") or "").strip()
        caused_by = str(row.get("from") or marking)
        # Never an electrode's own verdict. A real measurement is not the
        # sweeps' to rewrite, whatever they make of it.
        if frozen == status or caused_by == marking:
            continue
        rows.append(_row(marking, frozen, status, caused_by, _stamp(row)))
    return sorted(rows, key=lambda row: _number(row["electrode"]))


def _row(marking: str, was: str | None, status: str, caused_by: str,
         at: str) -> dict:
    """One row of the plan, in the shape the procedure reads."""
    return {"electrode": marking, "was": was, "status": status,
            "caused_by": caused_by, "archived_at_utc": at or None}


def _stamp(row: dict | None) -> str:
    """When a row settled. The decision's own date, never today: an electrode
    swept by a verdict settled when that verdict did."""
    return str((row or {}).get("at") or "")


def _number(marking: str) -> int:
    """'B14174' to 14174, so a plan reads in production order."""
    digits = "".join(ch for ch in str(marking) if ch.isdigit())
    return int(digits) if digits else 0
