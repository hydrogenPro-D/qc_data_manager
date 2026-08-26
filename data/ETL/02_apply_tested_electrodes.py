"""02 — Mark the electrodes that were actually tested.

``01_export_electrode_status.py`` derives a status for every electrode from the
curated ranges, but those are all *inferred*: nothing in the ranges says which
electrodes were physically put through SEM or IC. This overlays that fact.

Reads ``data/csv/tested_electrodes.csv``:

    electrode,status
    B13876,Scrapped
    B13881,Released

and rewrites the matching lines of ``data/csv/electrode_status.csv`` to the
tested variant, so ``Released`` becomes ``Tested and Released``. Everything
else in that file is left exactly as it was.

Run it after 01, since 01 regenerates the whole status file from the ranges and
would otherwise drop these marks:

    venv\\Scripts\\python.exe data\\ETL\\01_export_electrode_status.py
    venv\\Scripts\\python.exe data\\ETL\\02_apply_tested_electrodes.py

Running it twice is harmless: an already tested line is left alone.
"""

from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from src.data_access.electrodes.electrode_columns import ElectrodeStatus  # noqa: E402

TESTED_CSV = PROJECT_ROOT / "data" / "csv" / "tested_electrodes.csv"
STATUS_CSV = PROJECT_ROOT / "data" / "csv" / "electrode_status.csv"

# What an operator may write in the tested list, and the status it produces.
# Both the plain and the already tested spelling are accepted, so the file can
# be written either way round.
TESTED_BY_INPUT: dict[str, str] = {
    str(ElectrodeStatus.RELEASED).lower(): str(ElectrodeStatus.TESTED_AND_RELEASED),
    str(ElectrodeStatus.TESTED_AND_RELEASED).lower(): str(ElectrodeStatus.TESTED_AND_RELEASED),
    str(ElectrodeStatus.SCRAPPED).lower(): str(ElectrodeStatus.TESTED_AND_SCRAPPED),
    str(ElectrodeStatus.TESTED_AND_SCRAPPED).lower(): str(ElectrodeStatus.TESTED_AND_SCRAPPED),
}


def read_tested(path: Path) -> tuple[dict[str, str], list[str]]:
    """The tested list as ``{marking: tested status}``, plus any problems."""
    if not path.exists():
        raise FileNotFoundError(
            f"Tested list not found: {path}\n"
            f"Create it with the columns: electrode,status")

    tested: dict[str, str] = {}
    problems: list[str] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        for line_no, row in enumerate(csv.DictReader(handle), start=2):
            marking = (row.get("electrode") or "").strip().upper()
            written = (row.get("status") or "").strip()
            if not marking:
                continue
            status = TESTED_BY_INPUT.get(written.lower())
            if status is None:
                problems.append(
                    f"line {line_no}: {marking} has status {written!r}, expected "
                    f"Released or Scrapped")
                continue
            if marking in tested and tested[marking] != status:
                problems.append(
                    f"line {line_no}: {marking} listed twice with different "
                    f"statuses, keeping {tested[marking]!r}")
                continue
            tested[marking] = status
    return tested, problems


def read_statuses(path: Path) -> list[tuple[str, str]]:
    """The generated status file, in its original order."""
    if not path.exists():
        raise FileNotFoundError(
            f"Status file not found: {path}\n"
            f"Run data/ETL/01_export_electrode_status.py first.")
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        return [((row.get("electrode") or "").strip(),
                 (row.get("status") or "").strip())
                for row in csv.DictReader(handle)
                if (row.get("electrode") or "").strip()]


def main() -> None:
    tested, problems = read_tested(TESTED_CSV)
    for problem in problems:
        print(f"  SKIPPED  {problem}")
    print(f"Read {len(tested)} tested electrode(s) from {TESTED_CSV.name}")

    rows = read_statuses(STATUS_CSV)
    print(f"Read {len(rows)} rows from {STATUS_CSV.name}")

    updated: list[tuple[str, str]] = []
    changes = Counter()
    seen: set[str] = set()

    for marking, status in rows:
        wanted = tested.get(marking.upper())
        if wanted is None:
            updated.append((marking, status))
            continue
        seen.add(marking.upper())
        if status == wanted:
            changes["already marked"] += 1
        else:
            changes[f"{status} -> {wanted}"] += 1
        updated.append((marking, wanted))

    with open(STATUS_CSV, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["electrode", "status"])
        writer.writerows(updated)

    print(f"\nWrote {len(updated)} rows back to {STATUS_CSV.name}")
    for change, count in sorted(changes.items()):
        print(f"  {change:<40} {count:>5}")

    missing = sorted(set(tested) - seen)
    if missing:
        print(f"\n{len(missing)} tested electrode(s) are not in {STATUS_CSV.name} "
              f"and were ignored:")
        print("  " + ", ".join(missing[:20]) + (" ..." if len(missing) > 20 else ""))


if __name__ == "__main__":
    main()
