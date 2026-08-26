"""Export electrodes and their QC status to a CSV, for analysis elsewhere.

Read only, and not part of the pipeline. It exists so the question can be
answered outside this application.

It is not one query, because a status is computed from three layers: the base in
qc_data.electrode_status, verdicts frozen into qc_data.qc_board_archive, and
decisions still on the board, which sweep onto the two electrodes either side.
Only the sweep is not a plain column, so the script reads all three and applies
the same function the application does rather than reimplementing the rule.

The sweep is computed over every electrode and the result narrowed afterwards: a
decision on B13860 reaches B13862, so starting at the cutoff would lose it.

Statuses are exported as stored, so 'Tested and Released' rather than the tab's
'Tested, passed'.

Run:
    venv\\Scripts\\python.exe tools\\export_electrode_statuses.py
    venv\\Scripts\\python.exe tools\\export_electrode_statuses.py --from B13862 --output out.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.data_access.electrodes.electrode_columns import (  # noqa: E402
    ElectrodeColumns,
    ElectrodeStatus,
    normalize_status,
)
from src.data_access.electrodes.status_propagation import (  # noqa: E402
    electrode_number,
    propagate,
)
from src.data_access.qc_board.qc_board_columns import (  # noqa: E402
    QcStage,
    STAGE_STATUS,
)

CONNECTION_FILE = PROJECT_ROOT / "config" / "db_connection.json"
KNOWN_DATABASES = ("dataloggingTest", "dataloggingDev")

# The application's own filter, so the export holds the same electrodes the tab
# does: A-numbered and test-recipe rows are not QC.
METADATA_FILTER = ("[Marking] LIKE 'B%' "
                   "AND TRIM([Full_name]) IN ('CATHODE', 'ANODE', 'MM1')")

FIELDS = ["marking", "date", "electrode_type", "status"]


def connect(database: str):
    """The application's own read only credential is enough for all of this."""
    import pyodbc

    from src.data_access.sql_session import SQL_DATETIMEOFFSET, handle_datetimeoffset

    if not CONNECTION_FILE.exists():
        raise SystemExit(f"No connection settings at {CONNECTION_FILE}.")
    settings = json.loads(CONNECTION_FILE.read_text(encoding="utf-8"))

    connection = pyodbc.connect(
        f"DRIVER={{{settings['DB_DRIVER']}}};"
        f"SERVER={settings['DB_SERVER']};DATABASE={database};"
        f"UID={settings['DB_USERNAME']};PWD={settings['DB_PASSWORD']};"
        f"Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;")
    # dbo.MetaData.[Date] is a DATETIMEOFFSET, which the driver cannot fetch
    # without this.
    connection.add_output_converter(SQL_DATETIMEOFFSET, handle_datetimeoffset)
    print(f"Reading {database} as {settings['DB_USERNAME']}")
    return connection


def rows(cursor, sql: str) -> list[dict]:
    cursor.execute(sql)
    columns = [c[0] for c in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from", dest="first", default="B13862",
                        help="lowest marking to export (default: B13862)")
    parser.add_argument("--to", dest="last", default="",
                        help="highest marking to export (default: no limit)")
    parser.add_argument("--database", default=KNOWN_DATABASES[0],
                        choices=KNOWN_DATABASES)
    parser.add_argument("--output", default="",
                        help="where to write (default: electrodes_from_<marking>.csv)")
    args = parser.parse_args()

    low = electrode_number(args.first)
    high = electrode_number(args.last) if args.last else None
    if low is None:
        raise SystemExit(f"Cannot read a number out of {args.first!r}.")

    connection = connect(args.database)
    try:
        cursor = connection.cursor()
        columns = ", ".join(f"[{c}]" for c in
                            (ElectrodeColumns.DATE, ElectrodeColumns.MARKING,
                             ElectrodeColumns.FULL_NAME))
        produced = rows(cursor,
                        f"SELECT {columns} FROM [dbo].[MetaData] "
                        f"WHERE {METADATA_FILTER}")
        stored = rows(cursor, "SELECT electrode, status "
                              "FROM qc_data.vw_electrode_status")
        archived = rows(cursor, "SELECT electrode, status "
                                "FROM qc_data.vw_qc_board_archive")
        board = rows(cursor, "SELECT electrode, stage FROM qc_data.vw_qc_board")
    finally:
        connection.close()

    marking_of = str(ElectrodeColumns.MARKING)
    markings = [str(r[marking_of]).strip() for r in produced]
    print(f"   {len(markings)} electrodes, {len(stored)} stored statuses, "
          f"{len(archived)} archived, {len(board)} on the board")

    # The base layer, then the frozen verdicts on top of it. Both are settled,
    # neither sweeps any further.
    base = {str(r["electrode"]).strip(): normalize_status(r["status"])
            for r in (stored + archived)}

    # Live decisions. Only the two terminal stages carry a verdict, an electrode
    # halfway through QC has nothing to say yet.
    decisions = {}
    for row in board:
        stage = str(row["stage"]).strip()
        if stage in {str(s) for s in QcStage} and QcStage(stage) in STAGE_STATUS:
            decisions[str(row["electrode"]).strip()] = STAGE_STATUS[QcStage(stage)]

    effective = propagate(markings, base, decisions)

    not_tested = str(ElectrodeStatus.NOT_TESTED)
    exported = []
    for row in produced:
        marking = str(row[marking_of]).strip()
        number = electrode_number(marking)
        if number is None or number < low or (high is not None and number > high):
            continue
        exported.append({
            "marking": marking,
            "date": row[str(ElectrodeColumns.DATE)],
            "electrode_type": str(row[str(ElectrodeColumns.FULL_NAME)] or "").strip(),
            "status": effective.get(marking, base.get(marking, not_tested)),
        })

    exported.sort(key=lambda r: electrode_number(r["marking"]) or 0)
    output = Path(args.output or f"electrodes_from_{args.first}.csv")
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(exported)

    print(f"\nWrote {len(exported)} electrodes to {output}")
    counts: dict[str, int] = {}
    for row in exported:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    for status, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"   {status:22} {count:5}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
