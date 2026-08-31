"""04 — Load the prepared files into the qc_data schema.

The other scripts in this folder end at a file. This one carries those files the
last step, into Azure SQL, so the data stops being per machine. It is the second
half of the pipeline, not a replacement: 01 to 03 still build the files, and
this pushes what they built.

    electrode_status.csv      ->  qc_data.electrode_status
    sem_edx_<gen>.csv         ->  qc_data.sem_electrode
                                  qc_data.sem_sample
                                  qc_data.sem_element_reading
                                  qc_data.sem_sample_image

electrode_ranges.csv is NOT loaded. It is the authored source that
01_export_electrode_status.py turns into the per-electrode statuses above, and
nothing reads it back: the range view on the tab is collapsed at runtime from
those statuses.

A STATUS SET BY HAND IS NEVER OVERWRITTEN. electrode_status is rebuilt from the
ranges on every run, so the merge skips rows whose source is 'manual' or each
run would silently undo every correction somebody made.

Safe to re-run. Every table is merged on its natural key, so a second run
updates what changed and leaves the rest alone. Rows in the database that the
files no longer mention are reported and, only with ``--prune``, deleted.

BULK INSERT cannot read a local file on Azure SQL, so the rows go through the
driver instead: staged into a temporary table with fast_executemany, then merged
in one statement.

The login is asked for at the keyboard every run and stored nowhere. It has to
be an account that may write to the tables, which the application's own user
deliberately is not.

Run:
    venv\\Scripts\\python.exe data\\ETL\\04_load_to_database.py --dry-run
    venv\\Scripts\\python.exe data\\ETL\\04_load_to_database.py
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.data_access.electrodes.electrode_columns import (  # noqa: E402
    ElectrodeStatus,
    normalize_status,
)
from src.data_access.electrodes.electrode_columns import (  # noqa: E402
    normalize_marking,
)
from src.data_access.sem_edx.sem_edx_columns import split_images  # noqa: E402
from src.data_access.sem_edx.sem_edx_source import (  # noqa: E402
    GENERATIONS,
    load_sem_edx,
    measured_columns,
)
from src.data_access.sem_edx.sem_edx_columns import element_symbol  # noqa: E402

import pandas as pd  # noqa: E402

CSV_DIR = PROJECT_ROOT / "data" / "csv"
STATUS_CSV = CSV_DIR / "electrode_status.csv"
TESTED_CSV = CSV_DIR / "tested_electrodes.csv"

# Where the server and database are read from. The login is NOT read from
# either of these: this script merges into the tables directly, so it needs
# rights the application deliberately does not have (see 002), and that
# credential is typed at the keyboard and kept nowhere.
FALLBACK_CONNECTION = PROJECT_ROOT / "config" / "db_connection.json"
DEFAULT_CONNECTION = PROJECT_ROOT / "config" / "db_connection_writer.json"

SCHEMA = "qc_data"

# The databases this may load into, the same two the migrations guard on. Test
# first, because that is what plain Enter picks and what a mistake should land
# in.
from etl_databases import (  # noqa: E402
    KNOWN_DATABASES,
    ask_database as _ask_database,
    confirm_database as _confirm_database,
)


def ask_database(suggested: str = "") -> str:
    return _ask_database(suggested, action="loaded")


def confirm_database(database: str) -> None:
    _confirm_database(database, action="loaded")
# Nothing loads until these have been applied, since the loader would otherwise
# fail halfway with an unhelpful "invalid object name".
REQUIRED_MIGRATIONS = (
    "001_create_schema_and_ledger.sql",
    "004_create_electrode_status_tables.sql",
    "005_create_qc_board_tables.sql",
    "006_create_sem_edx_reference_tables.sql",
    "007_create_sem_edx_measurement_tables.sql",
)

# Test ongoing is deliberately not one of them: it is read off the QC board at
# render time and has no row in qc_data.electrode_status_kind, so a range naming
# it would load right up to a foreign key violation.
KNOWN_STATUSES = {str(s) for s in ElectrodeStatus
                  if s is not ElectrodeStatus.TEST_ONGOING}


# ---- reading the files ---------------------------------------------------
def marking_number(marking) -> int | None:
    """'B14174' -> 14174. None when the marking carries no number."""
    match = re.fullmatch(r"\s*[A-Za-z]*\s*(\d+)\s*", str(marking))
    return int(match.group(1)) if match else None


def read_csv_rows(path: Path) -> list[dict]:
    if not path.exists():
        print(f"   {path.name} is not there, skipped")
        return []
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_statuses() -> list[tuple]:
    """One row per electrode, with where its status came from.

    Two sources: a curated range, or the tested list. A status set by hand is
    the third, and it is written by the application rather than loaded here.
    The merge leaves those rows alone, see ``keep_where`` below.
    """
    tested = {(r.get("electrode") or "").strip()
              for r in read_csv_rows(TESTED_CSV) if (r.get("electrode") or "").strip()}

    by_marking: dict[str, tuple] = {}
    for row in read_csv_rows(STATUS_CSV):
        electrode = (row.get("electrode") or "").strip()
        status = normalize_status(row.get("status") or "")
        if not electrode or status not in KNOWN_STATUSES:
            continue
        by_marking[electrode] = (
            electrode, status, "tested" if electrode in tested else "range")

    return list(by_marking.values())


def read_sem_measurements() -> tuple[list, list, list, list]:
    """Every SEM file, flattened into the four tables that hold it.

    One row per element reading rather than a column per element, because
    which elements a sheet records differs by generation and has already
    changed once. Nothing derived is carried: every ratio and average is
    arithmetic over these percentages.

    The file is one flat row per point, so the per electrode and per sample
    fields repeat down it and are collapsed back here.
    """
    electrodes: dict[tuple, dict] = {}
    samples: dict[tuple, tuple] = {}
    readings: list[tuple] = []
    images: dict[tuple, list[str]] = {}

    for generation in GENERATIONS:
        df = load_sem_edx(generation)
        if df.empty:
            continue
        elements = measured_columns(df)

        for row in df.to_dict("records"):
            electrode = normalize_marking(row.get("electrode", ""))
            position = str(row.get("position", "")).strip()
            electrode_type = str(row.get("electrode_type", "")).strip().upper()
            if not electrode or not position or not electrode_type:
                continue

            marking_key = (generation, electrode)
            known = electrodes.setdefault(
                marking_key, {"electrode_type": electrode_type, "folder": ""})
            # One electrode is an anode or a cathode, never both. Said out loud
            # rather than silently kept, since it means two sub tabs disagree.
            if known["electrode_type"] != electrode_type:
                print(f"   {generation} {electrode}: called both "
                      f"{known['electrode_type']} and {electrode_type}, keeping "
                      f"the first")
            # The folder is per electrode, so the first row naming one wins.
            if not known["folder"]:
                known["folder"] = str(row.get("folder", "")).strip()

            key = (*marking_key, position)
            samples.setdefault(key, (
                *key,
                str(row.get("zoom", "")).strip(),
                str(row.get("operator", "")).strip(),
                str(row.get("note", "")).strip(),
                str(row.get("date", "")).strip()))
            if key not in images:
                images[key] = split_images(row.get("images", ""))

            point = int(pd.to_numeric(row.get("measurement"), errors="coerce") or 0)
            if not point:
                continue

            for column in elements:
                value = row.get(column)
                if value is None or pd.isna(value):
                    continue
                readings.append((*key, point, element_symbol(column), f"{value:g}"))

    electrode_rows = [(*key, known["electrode_type"], known["folder"])
                      for key, known in electrodes.items()]
    image_rows = [(*key, ordinal, name)
                  for key, names in images.items()
                  for ordinal, name in enumerate(names, start=1)]
    return electrode_rows, list(samples.values()), readings, image_rows


# ---- talking to the database ---------------------------------------------
def server_settings(path: Path) -> dict:
    """Driver and server, read from a connection file.

    Only the fields that are not secret, and not the database either: which one
    to load is asked, since the app's connection file follows whatever the
    person at the keyboard is currently testing against. Any username and
    password the file happens to carry is ignored: this script merges into the
    tables directly, so it needs rights the application deliberately does not
    have, and that credential is typed rather than stored.
    """
    if not path.exists():
        raise SystemExit(
            f"No connection settings at {path}. Copy "
            f"config/db_connection.example.json to {path.name} and fill in "
            f"DB_DRIVER and DB_SERVER (it is git-ignored).")
    with open(path, "r", encoding="utf-8") as handle:
        settings = json.load(handle)

    missing = [key for key in ("DB_DRIVER", "DB_SERVER")
               if not str(settings.get(key, "")).strip()]
    if missing:
        raise SystemExit(f"{path.name} is missing: {', '.join(missing)}")
    return settings


def ask_credentials(username: str = "") -> tuple[str, str]:
    """Ask for the login to load with, at the keyboard.

    Never written anywhere: not to a file, not to the environment, not to the
    printed summary. It is an account that can write to every table in the
    schema, which is exactly the thing that should not be sitting on disk. The
    password is read without echo, the same way the playbook has you type one
    into 003 and close the file without saving.
    """
    import getpass

    username = username.strip() or input("Username: ").strip()
    if not username:
        raise SystemExit("No username given, nothing was loaded.")

    password = getpass.getpass(f"Password for {username}: ")
    if not password:
        raise SystemExit("No password given, nothing was loaded.")
    return username, password


def connect(settings: dict, database: str, username: str, password: str):
    import pyodbc

    connection = pyodbc.connect(
        f"DRIVER={{{settings['DB_DRIVER']}}};"
        f"SERVER={settings['DB_SERVER']};"
        f"DATABASE={database};"
        f"UID={username};"
        f"PWD={password};"
        f"Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;",
        autocommit=False)
    print(f"Connected to {database} as {username}")
    return connection


def check_migrations(cursor) -> None:
    """Refuse to load until the tables exist, with a message that says which
    file to run rather than an invalid object name."""
    try:
        cursor.execute(f"SELECT filename FROM [{SCHEMA}].[schema_migrations]")
        applied = {row[0] for row in cursor.fetchall()}
    except Exception:
        raise SystemExit(
            f"No [{SCHEMA}].[schema_migrations] table. Run the files in "
            f"sql/migrations in order, starting with 001.")

    missing = [name for name in REQUIRED_MIGRATIONS if name not in applied]
    if missing:
        raise SystemExit("Not applied yet: " + ", ".join(missing))


def merge(cursor, table: str, columns: list[str], keys: list[str],
          rows: list[tuple], prune: bool, types: dict[str, str] | None = None,
          keep_where: str = "") -> Counter:
    """Stage rows in a temporary table, then merge them in one statement.

    Args:
        types: Target types for columns that are not text, so the staging table
            converts once rather than the MERGE relying on an implicit cast. An
            empty cell becomes NULL rather than a conversion error, which is
            what an unmeasured element and a sample with no date both look like.
        keep_where: A condition on the target alone that protects rows this
            script does not own, used by both the update and the delete.
            Target alone because WHEN NOT MATCHED BY SOURCE cannot see the
            staging table. electrode_status passes it so a status set by hand
            survives a reload of the ranges.

    Returns what happened, by action. Rows the files no longer mention are
    counted either way and deleted only when asked.
    """
    counts: Counter = Counter()
    if not rows:
        return counts

    types = types or {}
    staging = f"#stage_{table}"
    # Staged as text and converted on the way out: a driver that guesses the
    # type per batch gets it wrong the moment a column is entirely empty.
    definition = ", ".join(f"[{c}] NVARCHAR(400) NULL" for c in columns)
    cursor.execute(f"IF OBJECT_ID('tempdb..{staging}') IS NOT NULL DROP TABLE {staging}")
    cursor.execute(f"CREATE TABLE {staging} ({definition})")

    placeholders = ", ".join("?" for _ in columns)
    cursor.fast_executemany = True
    cursor.executemany(
        f"INSERT INTO {staging} ({', '.join(f'[{c}]' for c in columns)}) "
        f"VALUES ({placeholders})", rows)

    def source(column: str) -> str:
        """One staged column, converted to what the target column holds."""
        if column not in types:
            return f"s.[{column}]"
        target, value = types[column], f"NULLIF(s.[{column}], N'')"
        # Through FLOAT first, because CONVERT to DECIMAL refuses scientific
        # notation and a small percentage is written as 1e-05.
        if target.startswith("DECIMAL"):
            value = f"TRY_CONVERT(FLOAT, {value})"
        return f"TRY_CONVERT({target}, {value})"

    protected = f" AND NOT ({keep_where})" if keep_where else ""
    updatable = [c for c in columns if c not in keys]
    on_clause = " AND ".join(f"t.[{k}] = {source(k)}" for k in keys)
    # Only touch a row that actually differs, so row_version moves when the data
    # moved and not merely because the loader ran again. A table that is
    # nothing but its key gets no update clause at all: a matched row already
    # says everything the file says.
    changed = " OR ".join(
        f"ISNULL(CAST(t.[{c}] AS NVARCHAR(400)), N'') <> "
        f"ISNULL(CAST({source(c)} AS NVARCHAR(400)), N'')" for c in updatable)
    assignments = ", ".join(f"t.[{c}] = {source(c)}" for c in updatable)
    matched = (f"WHEN MATCHED AND ({changed}){protected} THEN UPDATE SET {assignments}"
               if updatable else "")

    statement = f"""
        MERGE [{SCHEMA}].[{table}] WITH (HOLDLOCK) AS t
        USING {staging} AS s ON {on_clause}
        {matched}
        WHEN NOT MATCHED BY TARGET THEN
            INSERT ({', '.join(f'[{c}]' for c in columns)})
            VALUES ({', '.join(source(c) for c in columns)})
        {f'WHEN NOT MATCHED BY SOURCE{protected} THEN DELETE' if prune else ''}
        OUTPUT $action;
    """
    cursor.execute(statement)
    for row in cursor.fetchall():
        counts[row[0]] += 1

    if not prune:
        cursor.execute(
            f"SELECT COUNT(*) FROM [{SCHEMA}].[{table}] t "
            f"WHERE NOT EXISTS (SELECT 1 FROM {staging} s WHERE {on_clause})")
        orphans = cursor.fetchone()[0]
        if orphans:
            counts["ONLY IN DATABASE"] = orphans

    cursor.execute(f"DROP TABLE {staging}")
    return counts


def report(table: str, counts: Counter) -> None:
    if not counts:
        print(f"   {table:32} nothing to load")
        return
    parts = ", ".join(f"{action.lower()} {count}"
                      for action, count in sorted(counts.items()))
    print(f"   {table:32} {parts}")


# ---- the pipeline ---------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--connection", type=Path, default=None,
                        help="where the driver and server are read from "
                             "(default: config/db_connection.json). Its "
                             "database only suggests the default; the login is "
                             "always asked for, never read from a file")
    parser.add_argument("--database", default="",
                        choices=KNOWN_DATABASES,
                        help="skip the database prompt")
    parser.add_argument("--username", default="",
                        help="skip the username prompt. The password is always "
                             "asked for")
    parser.add_argument("--dry-run", action="store_true",
                        help="do everything, report it, then roll back")
    parser.add_argument("--prune", action="store_true",
                        help="also delete rows the files no longer mention")
    args = parser.parse_args()

    path = args.connection or (FALLBACK_CONNECTION if FALLBACK_CONNECTION.exists()
                               else DEFAULT_CONNECTION)
    settings = server_settings(path)
    database = args.database or ask_database(str(settings.get("DB_DATABASE", "")))
    if not args.dry_run:
        confirm_database(database)

    print(f"\nLoading into {database} on {settings['DB_SERVER']}.\n"
          f"This writes to the tables directly, so it needs an account that "
          f"may write to {SCHEMA}, the one you run the migrations with.")
    connection = connect(settings, database, *ask_credentials(args.username))
    cursor = connection.cursor()
    check_migrations(cursor)

    print("\nReading the files")
    statuses = read_statuses()
    print(f"   {len(statuses)} electrode statuses")

    electrodes, samples, readings, images = read_sem_measurements()
    print(f"   {len(electrodes)} SEM electrodes, {len(samples)} samples, "
          f"{len(readings)} element readings, {len(images)} captures")

    # The keys the SEM tables are merged on, each one its parent's key plus the
    # column that tells its rows apart.
    marking_key = ["generation", "electrode"]
    sample_key = [*marking_key, "position"]
    point_key = [*sample_key, "point"]

    print(f"\nLoading{' (dry run)' if args.dry_run else ''}")
    try:
        # A status somebody set on the Electrodes tab is never touched here,
        # or every load would quietly undo the corrections made since the last
        # one. The by-hand rows this file carries still land the first time,
        # through the insert; after that the database is the one that knows.
        report("electrode_status", merge(
            cursor, "electrode_status", ["electrode", "status", "source"],
            ["electrode"], statuses, args.prune,
            keep_where="t.[source] = N'manual'"))
        # Parents before children, so a foreign key always has something to
        # point at. Pruning runs in the same order, and the cascades on the
        # child tables clear anything a removed sample left behind.
        # The blank cells go in as NULL rather than as an empty string, so
        # "not recorded" reads the same whatever wrote the row.
        report("sem_electrode", merge(
            cursor, "sem_electrode", [*marking_key, "electrode_type", "folder"],
            marking_key, electrodes, args.prune,
            types={"folder": "NVARCHAR(400)"}))
        report("sem_sample", merge(
            cursor, "sem_sample",
            [*sample_key, "zoom", "operator", "note", "measured_on"],
            sample_key, samples, args.prune,
            types={"zoom": "NVARCHAR(16)", "operator": "NVARCHAR(128)",
                   "note": "NVARCHAR(512)", "measured_on": "DATE"}))
        report("sem_element_reading", merge(
            cursor, "sem_element_reading", [*point_key, "symbol", "value"],
            [*point_key, "symbol"], readings, args.prune,
            types={"point": "INT", "value": "DECIMAL(9,4)"}))
        report("sem_sample_image", merge(
            cursor, "sem_sample_image", [*sample_key, "ordinal", "filename"],
            [*sample_key, "ordinal"], images, args.prune,
            types={"ordinal": "INT"}))
    except Exception as e:
        connection.rollback()
        print(f"\nRolled back, nothing was written: {e}")
        return 1

    if args.dry_run:
        connection.rollback()
        print("\nDry run, rolled back. Run again without --dry-run to keep it.")
    else:
        connection.commit()
        print("\nCommitted.")

    if not args.prune:
        print("Rows counted as ONLY IN DATABASE were left alone. Pass --prune "
              "to delete them.")
    connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
