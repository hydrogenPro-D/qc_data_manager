"""Turn the hand-curated failure list into one upload-ready CSV.

Input is ``data/findings_backfill/failure_findings.csv``, which was put
together by hand from QC's own records and is never written to by this script.
Output is the same electrodes in the shape of a ``qc_data.qc_board_archive``
row, so the upload is one pass over one file.

WHY THE ARCHIVE AND NOT ``electrode_status``: the badge on the Electrodes tab
comes from ``qc_board_db_store.findings``, which looks at the live card and
then at the archive. An electrode with no archive row shows no reason however
it failed, so that is the table a reason has to land in.

Only 64 of the 189 are already in the archive. The other 125 are from the ETL
import of 26 August 2026 and have never been on the board, so most of this file
is new rows rather than corrections. Run with --check to see that split.

Undecided electrodes are left out entirely: a row carrying neither finding says
nothing the status does not already say.

Run:
    venv\\Scripts\\python.exe sql\\maintenance\\build_findings_upload.py
    venv\\Scripts\\python.exe sql\\maintenance\\build_findings_upload.py --check
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from src.data_access.electrodes.electrode_columns import (  # noqa: E402
    ElectrodeStatus,
)
from src.data_access.electrodes.status_propagation import (  # noqa: E402
    electrode_number,
)
from src.data_access.qc_board.qc_board_columns import (  # noqa: E402
    IC_FAILED,
    IC_RESULT,
    SEM_CRACKED,
    SEM_RESULT,
)

DEFAULT_SOURCE = PROJECT_ROOT / "data" / "findings_backfill" / "failure_findings.csv"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "findings_backfill" / "findings_upload.csv"

# The curated file's own vocabulary, which is QC's and not the database's.
SEM_FAILED = "SEM"
IC_FAILED_TYPE = "IC"
UNDECIDED = "undecided"

# The archive's columns, in the order a row is written.
OUTPUT_COLUMNS = ("electrode", "status", "caused_by", "test_first",
                  IC_RESULT, SEM_RESULT)

# Every one of these was tested on a rig, so each is the cause of its own
# verdict. A swept neighbour is never in this file, by construction.
TEST_FIRST = "0"


def transform(rows: list[dict]) -> tuple[list[dict], dict[str, int]]:
    """Curated rows in, archive rows out, with a tally of what was skipped."""
    out: list[dict] = []
    counts = {SEM_FAILED: 0, IC_FAILED_TYPE: 0, UNDECIDED: 0, "unknown": 0}
    status = str(ElectrodeStatus.TESTED_AND_SCRAPPED)

    for row in rows:
        marking = str(row.get("electrode name", "")).strip()
        verdict = str(row.get("failed type", "")).strip()
        if not marking:
            continue
        if verdict not in counts:
            counts["unknown"] += 1
            continue
        counts[verdict] += 1
        if verdict == UNDECIDED:
            continue
        out.append({
            "electrode": marking,
            "status": status,
            "caused_by": marking,
            "test_first": TEST_FIRST,
            IC_RESULT: IC_FAILED if verdict == IC_FAILED_TYPE else "",
            SEM_RESULT: SEM_CRACKED if verdict == SEM_FAILED else "",
        })

    out.sort(key=lambda r: (electrode_number(r["electrode"]) or 0, r["electrode"]))
    return out, counts


def check_against_database(markings: list[str]) -> None:
    """Say how many of these are corrections and how many are new rows.

    Read only, and on production, because that is where the upload lands.
    """
    import pyodbc  # Imported here so the plain transform needs no driver.

    cfg = json.loads((PROJECT_ROOT / "config" / "db_connection.json")
                     .read_text(encoding="utf-8"))
    connection = (f"DRIVER={{{cfg['DB_DRIVER']}}};SERVER={cfg['DB_SERVER']};"
                  f"DATABASE=dataloggingDev;UID={cfg['DB_USERNAME']};"
                  f"PWD={cfg['DB_PASSWORD']};Encrypt=yes;"
                  "TrustServerCertificate=no;Connection Timeout=30")
    cursor = pyodbc.connect(connection).cursor()

    cursor.execute("SELECT electrode, ic_result, sem_result "
                   "FROM qc_data.vw_qc_board_archive")
    archived = {str(r[0]).strip(): (r[1], r[2]) for r in cursor.fetchall()}

    present = [m for m in markings if m in archived]
    occupied = [m for m in present if any(archived[m])]
    print(f"\non dataloggingDev: {len(present)} already in the archive "
          f"(an update), {len(markings) - len(present)} not (a new row)")
    if occupied:
        print(f"  CAREFUL, {len(occupied)} already carry a finding: {occupied}")
    else:
        print("  none of them carry a finding yet, so nothing is overwritten")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true",
                        help="ask production which rows are new (read only)")
    args = parser.parse_args()

    with args.source.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    out, counts = transform(rows)
    if not out:
        print(f"Nothing to upload from {args.source}.")
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(OUTPUT_COLUMNS))
        writer.writeheader()
        writer.writerows(out)

    print(f"read  {len(rows)} rows from {args.source}")
    print(f"wrote {len(out)} rows to {args.output}")
    print(f"  {SEM_RESULT} = {SEM_CRACKED}: {counts[SEM_FAILED]}")
    print(f"  {IC_RESULT} = {IC_FAILED}: {counts[IC_FAILED_TYPE]}")
    print(f"  skipped as {UNDECIDED}: {counts[UNDECIDED]}")
    if counts["unknown"]:
        print(f"  skipped, unrecognised verdict: {counts['unknown']}")

    if args.check:
        check_against_database([r["electrode"] for r in out])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
