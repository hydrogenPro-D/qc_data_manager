"""Why the electrodes the board never saw were scrapped.

The findings on a card follow it into qc_board_archive when it is frozen, so
anything the board judged carries its own reason. The failures imported by the
ETL in August 2026 were judged before the board existed and have no card and no
archive row, so their reason is recorded in a table of its own. See migration
018.

DELIBERATELY NOT THE ARCHIVE. A row there is read as a decision the propagation
sweeps from, so putting these in it condemns their neighbours all over again.
Nothing reads this table as a decision, which is what makes it safe, and that
is a property of where it sits rather than of how it is filled.

Read only. The application never writes here; the rows are a one-off correction
written by an admin.
"""

from __future__ import annotations

from src.data_access.qc_board.qc_board_columns import FINDING_FIELDS

SCHEMA = "qc_data"
VIEW = "vw_electrode_failure_reason"


def load_failure_reasons(session) -> dict[str, dict]:
    """``{marking: {ic_result, sem_result}}``, empty where 018 has not run.

    Feature by presence, like every other optional source: a database without
    the view returns nothing and every electrode reads as no reason recorded,
    which is what it was before this table existed.
    """
    columns = ", ".join(f"[{field}]" for field in FINDING_FIELDS)
    try:
        rows = session.rows(
            f"SELECT [electrode], {columns} FROM [{SCHEMA}].[{VIEW}]")
    except Exception as error:
        print(f"No historic failure reasons: {error}")
        return {}

    reasons: dict[str, dict] = {}
    for row in rows:
        marking = str(row.get("electrode") or "").strip()
        if not marking:
            continue
        found = {field: str(row.get(field) or "").strip()
                 for field in FINDING_FIELDS}
        if any(found.values()):
            reasons[marking] = found
    return reasons
