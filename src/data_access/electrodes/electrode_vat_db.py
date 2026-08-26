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

# One row per marking, the multi-vat cases comma joined in vat order.
_VAT_SQL = """
    SELECT [Marking],
           STRING_AGG(CAST([Vat] AS VARCHAR(2)), ', ')
               WITHIN GROUP (ORDER BY [Vat]) AS [Vat]
      FROM (SELECT DISTINCT [Marking], [Proces] AS [Vat]
              FROM [dbo].[ProcessData]
             WHERE [Proces] IN (28, 29, 30) AND [Marking] LIKE 'B%') t
     GROUP BY [Marking]
"""


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

    vats: dict[str, str] = {}
    for row in rows:
        marking = str(row.get("Marking") or "").strip()
        vat = str(row.get("Vat") or "").strip()
        if marking and vat:
            vats[marking] = vat
    if not vats:
        print("No vat numbers matched in dbo.ProcessData, the Vat column will "
              "not be shown.")
    return vats
