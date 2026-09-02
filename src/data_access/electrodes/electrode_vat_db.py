"""Which plating vat each electrode went through, read from dbo.ProcessData.

NOT A COLUMN OF dbo.MetaData. The vat is joined in from the process log at
display time, keyed by marking, and nothing is written back. See
VAT_COLUMN_HOWTO.md.

WHY ProcessData AND NOT ProductionData. ProductionData.Kar is the vat actually
used, ground truth, and it is enormous. ProcessData.Proces is the plan and is
small enough to read in one go. The cost of the plan is that a handful of
electrodes list more than one vat, which are joined with a comma rather than
picked between.

B LINE ONLY. Only the production line numbers its plating vats 28, 29 and 30.
On the pilot line those same numbers mean Activation and Rinse, so without the
marking filter every A electrode would claim to have used all three. An
A marking has no vat here and reads empty.
"""

from __future__ import annotations

# The raw pairs, with duplicates; load_vat_by_marking below groups them.
#
# THIS QUERY LEANS ON AN INDEX: IX_ProcessData_Vat on (Proces, Marking),
# filtered WHERE Proces IN (28, 29, 30). That filter must list exactly the
# vats below. If they ever diverge the optimiser silently ignores the index
# and falls back to scanning all 327k rows, with no error, just 125ms
# becoming 835ms. Changing a vat number here means changing the index too.
#
# DISTINCT and STRING_AGG stay in Python on purpose: grouping 36k rows down
# to 12k costs the server more than sending them costs us (171ms to 125ms).
_VAT_SQL = """
    SELECT [Marking], [Proces] AS [Vat]
      FROM [dbo].[ProcessData]
     WHERE [Proces] IN (28, 29, 30) AND [Marking] LIKE 'B%'
"""


def _vat_order(vat: str):
    """Vats sort as numbers, so 9 comes before 28 rather than after it."""
    return (0, int(vat)) if vat.isdigit() else (1, 0)


def load_vat_by_marking(session) -> dict[str, str]:
    """``{'B14349': '29', 'B13980': '28, 30', ...}`` from ``dbo.ProcessData``.

    An empty map when the table is not there or is not readable, which is what
    a database holding only the MetaData snapshot looks like. The column is
    then left off the table entirely rather than shown blank for everything.
    """
    try:
        rows = session.rows(_VAT_SQL)
    except Exception as e:
        print(f"No vat numbers: dbo.ProcessData could not be read ({e}). "
              f"The Vat column will not be shown.")
        return {}

    found: dict[str, set[str]] = {}
    for row in rows:
        marking = str(row.get("Marking") or "").strip()
        vat = str(row.get("Vat") or "").strip()
        # Only B-numbers: the server was not asked to filter them out.
        if marking[:1].upper() == "B" and vat:
            found.setdefault(marking, set()).add(vat)
    vats = {marking: ", ".join(sorted(seen, key=_vat_order))
            for marking, seen in found.items()}
    if not vats:
        print("No vat numbers matched in dbo.ProcessData, the Vat column will "
              "not be shown.")
    return vats
