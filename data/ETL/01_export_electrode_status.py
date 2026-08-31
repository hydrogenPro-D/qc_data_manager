"""01 — Export one QC status per electrode.

Takes the hand-curated ranges in ``data/csv/electrode_ranges.csv`` and the
electrodes the app actually shows (dbo.MetaData, B-markings on the
CATHODE/ANODE recipes), and flattens them into one row per electrode:

    electrode,status
    B14174,Not Tested
    B13835,Released

Anything no range covers is exported as "Not Tested" — including electrodes
produced since the ranges were last updated.

The ranges file is hand-maintained, so it is validated rather than trusted:
unparseable rows, inverted ranges, unknown statuses and overlaps are all
reported. Overlaps are fatal, because the status of an electrode covered twice
would depend on row order.

Run:
    venv\\Scripts\\python.exe data\\ETL\\01_export_electrode_status.py
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from bisect import bisect_right
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from etl_databases import KNOWN_DATABASES, ask_database  # noqa: E402

from src.data_access.db_manager import DbManager  # noqa: E402
from src.data_access.electrodes.electrode_columns import (  # noqa: E402
    ElectrodeColumns,
    ElectrodeStatus,
)

RANGES_CSV = PROJECT_ROOT / "data" / "csv" / "electrode_ranges.csv"
OUTPUT_CSV = PROJECT_ROOT / "data" / "csv" / "electrode_status.csv"

NOT_TESTED = str(ElectrodeStatus.NOT_TESTED)
# Test ongoing is deliberately not one of them: it is read off the QC board at
# render time and has no row in qc_data.electrode_status_kind, so a range naming
# it would load right up to a foreign key violation.
KNOWN_STATUSES = {str(s) for s in ElectrodeStatus
                  if s is not ElectrodeStatus.TEST_ONGOING}


def electrode_number(marking: str) -> int | None:
    """'B14174' → 14174. None when the marking has no numeric part."""
    match = re.fullmatch(r"\s*[A-Za-z]*\s*(\d+)\s*", str(marking))
    return int(match.group(1)) if match else None


def load_ranges(path: Path) -> list[tuple[int, int, str]]:
    """Read the curated ranges, validating as we go.

    Returns them sorted by start. Raises on problems that would make a lookup
    ambiguous; merely odd rows are reported and skipped.
    """
    if not path.exists():
        raise FileNotFoundError(f"Ranges file not found: {path}")

    ranges: list[tuple[int, int, str]] = []
    problems: list[str] = []

    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        for line_no, row in enumerate(csv.DictReader(handle), start=2):
            start = electrode_number(row.get("start", ""))
            end = electrode_number(row.get("end", ""))
            status = (row.get("status") or "").strip()

            if start is None or end is None:
                problems.append(f"line {line_no}: cannot read range "
                                f"{row.get('start')!r}-{row.get('end')!r}")
                continue
            if start > end:
                problems.append(f"line {line_no}: start B{start} is after end B{end}")
                continue
            if status not in KNOWN_STATUSES:
                problems.append(f"line {line_no}: unknown status {status!r} "
                                f"(expected one of {sorted(KNOWN_STATUSES)})")
                continue
            ranges.append((start, end, status))

    for problem in problems:
        print(f"  SKIPPED  {problem}")

    ranges.sort(key=lambda r: r[0])
    overlaps = [
        f"B{a_start}-B{a_end} ({a_status}) overlaps B{b_start}-B{b_end} ({b_status})"
        for (a_start, a_end, a_status), (b_start, b_end, b_status)
        in zip(ranges, ranges[1:]) if b_start <= a_end
    ]
    if overlaps:
        raise ValueError(
            "Overlapping ranges — an electrode would have two statuses:\n  "
            + "\n  ".join(overlaps))

    return ranges


def build_lookup(ranges: list[tuple[int, int, str]]):
    """Return a ``number -> status`` function over non-overlapping ranges."""
    starts = [start for start, _, _ in ranges]

    def lookup(number: int) -> str:
        index = bisect_right(starts, number) - 1
        if index < 0:
            return NOT_TESTED
        start, end, status = ranges[index]
        return status if start <= number <= end else NOT_TESTED

    return lookup


def report_gaps(ranges: list[tuple[int, int, str]]) -> None:
    """Print the holes between ranges — everything there exports as Not Tested."""
    gaps = [(a_end + 1, b_start - 1) for (_, a_end, _), (b_start, _, _)
            in zip(ranges, ranges[1:]) if b_start > a_end + 1]
    if not gaps:
        print("  no gaps between ranges")
        return
    print(f"  {len(gaps)} gap(s) between ranges:")
    for start, end in gaps:
        span = end - start + 1
        print(f"    B{start}-B{end}  ({span} electrode{'s' if span != 1 else ''})")


def connect(database: str) -> DbManager:
    """The backend, pointed at whichever database was chosen.

    A clearer failure than the driver's own: reaching a database the migrations
    have not run on fails while reading a view that is not there yet, which
    says nothing about what to do next.
    """
    try:
        return DbManager(database=database)
    except Exception as e:
        raise SystemExit(
            f"Could not read {database}: {e}\n"
            f"If this is a database the migrations have not run on yet, run "
            f"sql/migrations 001 to 014 against it first.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database", default="", choices=KNOWN_DATABASES,
                        help="skip the database prompt")
    args = parser.parse_args()

    print(f"Reading ranges from {RANGES_CSV.name}")
    ranges = load_ranges(RANGES_CSV)
    print(f"  {len(ranges)} ranges, B{ranges[0][0]}–B{ranges[-1][1]}")
    report_gaps(ranges)

    settings = json.loads(
        (PROJECT_ROOT / "config" / "db_connection.json").read_text(encoding="utf-8")
    ) if (PROJECT_ROOT / "config" / "db_connection.json").exists() else {}
    database = args.database or ask_database(
        str(settings.get("DB_DATABASE", "")), action="exported")

    print(f"\nReading electrodes from {database}")
    backend = connect(database)
    try:
        electrodes = backend.get_metadata()
    finally:
        backend.close()

    marking_column = str(ElectrodeColumns.MARKING)
    markings = [str(m).strip() for m in electrodes[marking_column] if str(m).strip()]
    print(f"  {len(markings)} electrodes")

    lookup = build_lookup(ranges)
    rows, unreadable = [], []
    for marking in markings:
        number = electrode_number(marking)
        if number is None:
            unreadable.append(marking)
            rows.append((marking, NOT_TESTED))
            continue
        rows.append((marking, lookup(number)))

    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_CSV, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["electrode", "status"])
        writer.writerows(rows)

    print(f"\nWrote {len(rows)} rows → {OUTPUT_CSV}")
    for status, count in Counter(status for _, status in rows).most_common():
        print(f"  {status:<12} {count:>6}  ({count / len(rows):.1%})")

    if unreadable:
        print(f"\n{len(unreadable)} marking(s) with no number, exported as "
              f"{NOT_TESTED}: {', '.join(unreadable[:10])}")

    beyond = [m for m in markings
              if (n := electrode_number(m)) is not None and n > ranges[-1][1]]
    if beyond:
        print(f"\n{len(beyond)} electrode(s) past the last range (B{ranges[-1][1]}) — "
              f"these need a QC range analysis run")


if __name__ == "__main__":
    main()
