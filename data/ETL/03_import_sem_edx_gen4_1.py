"""03 — Flatten the SEM EDX workbooks into one row per measurement.

One script for every generation, because the workbooks share a shape even
though they do not share columns. Each writes
``data/csv/sem_edx_<generation>.csv``, one row per measurement, which
``04_load_to_database.py`` then loads.

    Gen 3.1   Line 1 - production QC.xlsx      Ni, S, Ni:S
    Gen 4.1   Gen 4.1 Production QC.xlsx       and Co, Ni:Co, plus C and N on
                                               the cathodes sheet

It also PRINTS the layout it found, which quantities each sheet holds and in
what order. That is not stored here: which of them a tab shows is presentation
and lives in config/config.json under sem_edx.layout. Discovering it is still
worth doing, since a workbook gaining a column is exactly when somebody needs
to know what changed, and the printed block can be pasted straight in.

Only measured percentages are exported. Every ratio and average in the workbook
is arithmetic over them, so storing those too would only let the copy drift
from the source: the app recomputes them (see ``sem_edx_processor``).

Nothing about the columns is hardcoded. Headers follow a strict pattern::

    <Symbol> atomic %      <Symbol> average
    <A>:<B> ratio          <A>:<B> ratio average

so the elements and ratios are discovered by reading them. That matters: the
sheets do not share a layout and they do not keep one. Cathodes gained ten
carbon and nitrogen columns in August 2026, which moved Operator from column 14
to 24. The previous version read positions literally and wrote a carbon
percentage into the operator column of every cathode row.

The R&D Line Cathodes sheet is deliberately skipped, its A-markings are not in
dbo.MetaData, so those electrodes have no production date, type or status.

Other quirks the workbooks have, and this script absorbs:

* the electrode and the per sample fields are merged across each block of
  measurements, so they are carried down until the next block starts,
* the sample name is a formula, ``=A2 & " top right"``, so the position is
  recovered by stripping the marking off the front. Gen 3.1 pads that marking
  to no fixed width, B897 against B000897, so any leading marking goes rather
  than only the one in the electrode column,
* markings are padded inconsistently in the electrode column too, so they are
  normalized: B0897 and B000897 are both B897, which is what dbo.MetaData and
  the status file call it,
* zoom is written both ``x100`` and ``100x``; both normalize to ``x100``,
* rows with no numbers at all are the workbook's empty template tail.

How many points a sample carries is deliberately not checked. Three is the
Gen 4.1 habit, not a rule: over half the Gen 3.1 samples hold one or two.

Run:
    venv\\Scripts\\python.exe data\\ETL\\03_import_sem_edx_gen4_1.py
    venv\\Scripts\\python.exe data\\ETL\\03_import_sem_edx_gen4_1.py --generation Gen3.1
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path

from openpyxl import load_workbook

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from src.data_access import app_config  # noqa: E402
from src.data_access.sem_edx.sem_edx_columns import (  # noqa: E402
    FIXED_COLUMNS,
    element_column,
    join_images,
)
from src.data_access.sem_edx.sem_edx_result_parser import (  # noqa: E402
    candidate_images,
    reports_directories,
    select_images,
)
from src.data_access.electrodes.electrode_columns import (  # noqa: E402
    normalize_marking,
)
from src.data_access.sem_edx.sem_edx_source import (  # noqa: E402
    portable_folder,
    resolve_folder,
    sem_edx_csv_path,
)

EXCEL_DIR = PROJECT_ROOT / "data" / "excel"

# Which workbook holds which generation. Oldest first, matching the tab order.
WORKBOOKS = {
    "Gen3.1": EXCEL_DIR / "Line 1 - production QC.xlsx",
    "Gen4.1": EXCEL_DIR / "Gen 4.1 Production QC.xlsx",
}

# Sheet name -> the electrode type it holds. R&D Line Cathodes is left out: its
# A-markings are not in dbo.MetaData, so those electrodes have no production
# date, type or status.
SHEETS = {"Anodes": "ANODE", "Cathodes": "CATHODE"}

# The fixed columns, found by header rather than by position.
COLUMN_HEADERS = {
    "electrode": ("electrode",),
    "sample": ("sample name",),
    "zoom": ("zoom",),
    "operator": ("operator",),
    "note": ("notes", "note"),
}
# Without these there is no measurement to speak of; the rest degrade to blank.
REQUIRED_COLUMNS = ("electrode", "sample")

# The two header patterns that carry data. The "average" twins are ignored,
# being arithmetic over the columns these match.
ELEMENT_HEADER = re.compile(r"^([A-Z][a-z]?) atomic %$")
RATIO_HEADER = re.compile(r"^([A-Z][a-z]?):([A-Z][a-z]?) ratio$")

# A production marking names a folder in the SEM images library. Deliberately
# strict: a looser pattern would read "Gen3" as an electrode and stop the walk
# from ever reaching the folders inside it.
MARKING_FOLDER = re.compile(r"^[Bb]\d+$")
# What tells an EDX result folder apart from the other studies filed beside it.
REPORTS_DIR = "reports"
# The library nests by line and generation, so the electrode folders sit a few
# levels down. Bounded so a stray deep tree cannot make this crawl.
MAX_SCAN_DEPTH = 5


def header_key(value) -> str:
    """A header as something comparable: trimmed, collapsed, lowercase.

    The sheets spell them inconsistently, ' Sample name' with a leading space,
    'Ni:S ratio ' with a trailing one, 'Notes' on anodes and 'Note' on cathodes.
    """
    return " ".join(str(value or "").split()).casefold()


def header_text(value) -> str:
    """A header with its whitespace tidied but its case kept, so a chemical
    symbol survives: 'Ni:S ratio ' -> 'Ni:S ratio'."""
    return " ".join(str(value or "").split())


def resolve_columns(worksheet) -> tuple[dict[str, int], list[str]]:
    """Map each fixed field to its 1-based column, plus what is missing."""
    headers = {header_key(worksheet.cell(1, col).value): col
               for col in range(1, worksheet.max_column + 1)
               if worksheet.cell(1, col).value is not None}

    found: dict[str, int] = {}
    for field, candidates in COLUMN_HEADERS.items():
        for candidate in candidates:
            if candidate in headers:
                found[field] = headers[candidate]
                break
    return found, [field for field in COLUMN_HEADERS if field not in found]


def resolve_quantities(worksheet) -> tuple[list[str], dict[str, int]]:
    """Which quantities this sheet holds, in its own left to right order.

    Returns the layout, ``['Ni', 'S', 'Ni:S', ...]``, and where to read each
    element from. Ratios are listed for the layout but never read: they are
    recomputed from the elements.
    """
    layout: list[str] = []
    element_at: dict[str, int] = {}

    for col in range(1, worksheet.max_column + 1):
        header = header_text(worksheet.cell(1, col).value)

        element = ELEMENT_HEADER.match(header)
        if element:
            symbol = element.group(1)
            if symbol not in layout:
                layout.append(symbol)
                element_at[symbol] = col
            continue

        ratio = RATIO_HEADER.match(header)
        if ratio:
            name = f"{ratio.group(1)}:{ratio.group(2)}"
            if name not in layout:
                layout.append(name)

    return layout, element_at


def find_image_folders() -> tuple[dict[str, str], list[str]]:
    """``{marking: stored folder}`` for every electrode folder in the library.

    The workbook records no path, so the captures of a sample typed in by hand
    would otherwise be unreachable from the app. They are found by walking the
    shared library instead, and stored the same portable way an import does, as
    a tail under the configured root (see ``sem_edx_image_roots``).

    Returns the index and any markings found in more than one place.
    """
    found: dict[str, Path] = {}
    duplicates: list[str] = []

    def claim(marking: str, entry: Path) -> None:
        """Keep the folder that looks like an EDX export.

        The same electrode turns up under the coating thickness and post mortem
        studies too. Those are different work, and only the EDX export carries
        a reports folder, so that is what decides rather than whichever the
        walk happened to reach first.
        """
        previous = found.get(marking)
        if previous is None:
            found[marking] = entry
            return
        if is_edx_export(entry) and not is_edx_export(previous):
            duplicates.append(f"{marking}: preferred {entry}, ignored {previous}")
            found[marking] = entry
        else:
            duplicates.append(f"{marking}: kept {previous}, ignored {entry}")

    def walk(directory: Path, depth: int) -> None:
        if depth > MAX_SCAN_DEPTH:
            return
        try:
            entries = sorted(directory.iterdir(), key=lambda p: p.name)
        except OSError:
            return                      # unreadable or still syncing
        for entry in entries:
            if not entry.is_dir():
                continue
            if MARKING_FOLDER.match(entry.name.strip()):
                claim(entry.name.strip().upper(), entry)
                continue                # an electrode folder, do not descend
            walk(entry, depth + 1)

    for root in app_config.sem_edx_image_roots():
        if root.is_dir():
            walk(root, 0)
    return {marking: portable_folder(path) for marking, path in found.items()}, duplicates


def is_edx_export(folder: Path) -> bool:
    """Whether a folder holds an EDX result, which is what a reports folder means.

    Searched rather than checked one level down: the microscope nests a folder
    inside the one it creates, and a plate mapped at several positions nests
    one of those per position.
    """
    return bool(reports_directories(folder))


def cell_text(value) -> str:
    """A worksheet cell as trimmed text, with blanks and errors as ''."""
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.startswith("#") else text


def number(value) -> str:
    """A measured value as text, or '' when the cell holds no number."""
    if value is None or isinstance(value, str):
        return ""
    return f"{float(value):g}"


def position_of(sample: str, electrode: str) -> str:
    """``'B13951 top right'`` -> ``'top right'``.

    Any leading marking goes, not only the one the electrode column holds:
    Gen 3.1 writes B897 in one column and B000897 in the sample name, and
    matching on the exact string left a fifth of that sheet with the marking
    stuck to the front of its position. Internal runs of spaces are collapsed
    for the same reason, "B000897  center" being one of them.
    """
    text = re.sub(r"^\s*[A-Za-z]+\d+\s*", "", str(sample).strip())
    return " ".join(text.split()) or "top right"


def normalize_zoom(text: str) -> str:
    """'100x' and 'x100' are the same magnification written two ways."""
    match = re.fullmatch(r"\s*x?\s*(\d+)\s*x?\s*", text, flags=re.IGNORECASE)
    return f"x{match.group(1)}" if match else text


def open_workbook(path: Path):
    """Load the workbook, working around a lock held by Excel or OneDrive by
    reading a copy instead of failing the whole import."""
    try:
        return load_workbook(path, data_only=True)
    except PermissionError:
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as handle:
            copy = Path(handle.name)
        shutil.copy2(path, copy)
        print(f"{path.name} is locked, read from a temporary copy instead")
        try:
            return load_workbook(copy, data_only=True)
        finally:
            copy.unlink(missing_ok=True)


def read_sheet(worksheet, electrode_type: str, columns: dict[str, int],
               element_at: dict[str, int]) -> tuple[list[dict], list[str]]:
    """One dict per measurement, in sheet order, plus any repeated labels.

    A sample is a *block* of rows under one sample-name cell, not a name. The
    two are usually the same thing, but B14175 carries two separate blocks both
    called "top right", and averaging six points across two spots on the plate
    describes neither. A repeated label therefore gets a number appended, so
    the blocks stay apart until someone renames one in the sheet.
    """
    rows: list[dict] = []
    repeated: list[str] = []
    occurrences: dict[tuple[str, str], int] = {}
    electrode = position = zoom = operator = note = ""
    measurement = 0

    def value(field: str, row: int):
        """A cell by field name; None when this sheet has no such column."""
        column = columns.get(field)
        return worksheet.cell(row, column).value if column else None

    for index in range(2, worksheet.max_row + 1):
        marking = cell_text(value("electrode", index))
        if marking:
            electrode = normalize_marking(marking)

        sample = cell_text(value("sample", index))
        if sample:
            # A new sample block: everything merged across it restarts here.
            label = position_of(sample, electrode)
            seen = occurrences.get((electrode, label), 0) + 1
            occurrences[(electrode, label)] = seen
            position = label if seen == 1 else f"{label} ({seen})"
            if seen > 1:
                repeated.append(f"{electrode} {label}, block {seen} on row {index}")
            zoom = normalize_zoom(cell_text(value("zoom", index)))
            operator = cell_text(value("operator", index))
            note = " ".join(cell_text(value("note", index)).split())
            measurement = 0

        measured = {element_column(symbol): number(worksheet.cell(index, col).value)
                    for symbol, col in element_at.items()}
        if not electrode or not any(measured.values()):
            continue

        measurement += 1
        rows.append({
            "electrode": electrode, "electrode_type": electrode_type,
            "position": position, "zoom": zoom, "measurement": measurement,
            "operator": operator, "note": note, "date": "", "folder": "",
            "images": "",
            **measured,
        })
    return rows, repeated


def export(generation: str, workbook_path: Path,
           folders: dict[str, str], duplicates: list[str]) -> int:
    """Read one generation's workbook and write its two files."""
    print("=" * 72)
    print(f"{generation}: {workbook_path.name}")
    if not workbook_path.exists():
        print(f"   no workbook at {workbook_path}, skipped")
        return 0

    workbook = open_workbook(workbook_path)
    rows: list[dict] = []
    layouts: dict[str, list[str]] = {}

    for sheet_name, electrode_type in SHEETS.items():
        if sheet_name not in workbook.sheetnames:
            print(f"Sheet {sheet_name!r} is missing, skipped")
            continue

        worksheet = workbook[sheet_name]
        columns, missing = resolve_columns(worksheet)
        blocking = [field for field in missing if field in REQUIRED_COLUMNS]
        if blocking:
            print(f"{sheet_name}: no column headed {', '.join(blocking)}, skipped")
            continue
        if missing:
            print(f"{sheet_name}: no column headed {', '.join(missing)}, "
                  f"those values will be blank")

        layout, element_at = resolve_quantities(worksheet)
        if not element_at:
            print(f"{sheet_name}: no '<Symbol> atomic %' column, skipped")
            continue
        layouts[electrode_type] = layout
        print(f"{sheet_name:10} quantities: {', '.join(layout)}")

        sheet_rows, repeated = read_sheet(
            worksheet, electrode_type, columns, element_at)
        samples = {(r["electrode"], r["position"]) for r in sheet_rows}
        electrodes = {r["electrode"] for r in sheet_rows}
        print(f"{sheet_name:10} {len(electrodes):3} electrodes, "
              f"{len(samples):3} samples, {len(sheet_rows):4} measurements")
        for line in repeated:
            print(f"{sheet_name:10} repeated sample name, numbered instead: {line}")
        rows.extend(sheet_rows)

    if not rows:
        print("   nothing to export")
        return 0

    if not folders:
        print("\nNo SEM images library found on this machine, so no image "
              "folders were recorded. Check sem_edx.image_roots in config.json.")
    else:
        # Each sample gets its own captures, not the whole electrode's. A plate
        # mapped at five positions files them under five subfolders, and the
        # names say which is which, so B13853 bottom left gets its three rather
        # than all fifteen.
        #
        # The folder is listed once and the samples picked out of that listing:
        # these live on a synced share, and walking it five times per electrode
        # is five times the wait for the same answer.
        in_folder: dict[str, list] = {}
        listed: dict[tuple[str, str], str] = {}
        for row in rows:
            stored = folders.get(row["electrode"].upper(), "")
            row["folder"] = stored
            if not stored:
                row["images"] = ""
                continue

            if stored not in in_folder:
                in_folder[stored] = candidate_images(resolve_folder(stored))
            key = (stored, row["position"])
            if key not in listed:
                listed[key] = join_images(
                    p.name for p in select_images(
                        in_folder[stored], row["electrode"], row["position"]))
            row["images"] = listed[key]
        with_images = {r["electrode"] for r in rows if r["folder"]}
        electrodes = {r["electrode"] for r in rows}
        print(f"\nFound image folders for {len(with_images)} of "
              f"{len(electrodes)} electrodes ({len(folders)} in the library)")
        missing = sorted(electrodes - with_images)
        if missing:
            print(f"   no folder for: {', '.join(missing)}")
        if duplicates:
            print(f"   {len(duplicates)} electrodes are filed under more than "
                  f"one study:")
            for line in duplicates:
                print(f"      {line}")

    # Every element any sheet mentioned, so both types share one file shape.
    element_fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in FIXED_COLUMNS and key not in element_fields:
                element_fields.append(key)
    fields = [*FIXED_COLUMNS, *sorted(element_fields)]

    output_csv = sem_edx_csv_path(generation)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(output_csv, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, restval="")
        writer.writeheader()
        writer.writerows(rows)

    points = Counter(Counter((r["electrode"], r["position"])
                             for r in rows).values())
    print(f"\n   points per sample: {dict(sorted(points.items()))}")
    print(f"   wrote {len(rows)} measurements to {output_csv.name}")

    # Printed, never written. Which quantities a tab shows is presentation and
    # lives in config/config.json under sem_edx.layout; a second copy beside the
    # measurements would only drift from it.
    print("\n   the layout, for config.json under sem_edx.layout:")
    block = json.dumps({generation.replace(" ", ""): layouts},
                       indent=2, ensure_ascii=False)
    for line in block.splitlines():
        print(f"     {line}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--generation", choices=sorted(WORKBOOKS), default=None,
                        help="just this one (default: every generation)")
    args = parser.parse_args()

    wanted = {args.generation: WORKBOOKS[args.generation]} if args.generation \
        else WORKBOOKS

    # Walked once and shared: the library holds every generation's folders, and
    # crawling it per workbook would only be slower.
    folders, duplicates = find_image_folders()
    return max(export(generation, path, folders, duplicates)
               for generation, path in wanted.items())


if __name__ == "__main__":
    raise SystemExit(main())
