"""Copy dbo.MetaData from dataloggingDev into dataloggingTest.

The production metadata only exists on Dev, so on Test the Electrodes tab has
nothing to read and neither the ranges nor the plus or minus two sweeps can be
exercised. This makes a stand-in: the same table, the same column types, the
rows the application actually shows.

    dataloggingDev.dbo.MetaData  ->  dataloggingTest.dbo.MetaData

NOT A MIGRATION, on purpose. It fills a test database with a copy of somebody
else's table, which is tooling, not schema this project owns. Migration 002
already grants SELECT on dbo.MetaData by presence, so the grant starts working
on Test the moment this has run, with no permission change.

THE COLUMN TYPES ARE READ FROM THE SOURCE, not written down here. A stand-in
with a guessed type is worse than none: it would behave subtly differently from
the real thing, which is the one thing a test database must not do.

Only the rows the app shows are copied, filtered exactly as the app filters
them, so the row count the watermark polls matches too.

WRITES ONLY TO dataloggingTest. The target is checked against an allow list
before anything is created, because the obvious way to get this wrong is to
copy in the other direction.

The login is asked for at the keyboard and stored nowhere. It needs to read on
the source and create and write on the target.

Run:
    venv\\Scripts\\python.exe tools\\copy_metadata_to_test.py --dry-run
    venv\\Scripts\\python.exe tools\\copy_metadata_to_test.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.data_access.db_manager import (  # noqa: E402
    METADATA_COLUMNS,
    METADATA_FILTER,
    METADATA_KEY,
    METADATA_SCHEMA,
    METADATA_TABLE,
)

CONNECTION_FILE = PROJECT_ROOT / "config" / "db_connection.json"

SOURCE_DATABASE = "dataloggingDev"
# The one database this may write to. Everything else is refused, whatever is
# passed on the command line.
ALLOWED_TARGETS = {"dataloggingTest"}

# The key comes along with the columns the app reads: it is what the metadata
# watermark polls and what breaks ties in the display order.
COPIED_COLUMNS = [METADATA_KEY, *METADATA_COLUMNS]

BATCH = 1000


def server_settings() -> dict:
    """Driver and server, from the app's connection file. Nothing secret."""
    if not CONNECTION_FILE.exists():
        raise SystemExit(f"No connection settings at {CONNECTION_FILE}.")
    with open(CONNECTION_FILE, "r", encoding="utf-8") as handle:
        settings = json.load(handle)
    missing = [key for key in ("DB_DRIVER", "DB_SERVER")
               if not str(settings.get(key, "")).strip()]
    if missing:
        raise SystemExit(f"{CONNECTION_FILE.name} is missing: {', '.join(missing)}")
    return settings


def ask_credentials(username: str = "") -> tuple[str, str]:
    """The login to copy with, typed rather than stored."""
    import getpass

    username = username.strip() or input("Username: ").strip()
    if not username:
        raise SystemExit("No username given, nothing was copied.")
    password = getpass.getpass(f"Password for {username}: ")
    if not password:
        raise SystemExit("No password given, nothing was copied.")
    return username, password


def connect(settings: dict, database: str, username: str, password: str):
    import pyodbc

    return pyodbc.connect(
        f"DRIVER={{{settings['DB_DRIVER']}}};"
        f"SERVER={settings['DB_SERVER']};"
        f"DATABASE={database};"
        f"UID={username};PWD={password};"
        f"Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;",
        autocommit=False)


def source_definition(cursor) -> tuple[str, dict[str, str]]:
    """``(CREATE TABLE, {column: type})``, built from the source's own types.

    [Id] becomes a plain key rather than an identity: the copy carries the
    original numbers, and an identity would only need turning off to insert
    them.
    """
    placeholders = ", ".join("?" for _ in COPIED_COLUMNS)
    cursor.execute(
        f"SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, "
        f"       NUMERIC_PRECISION, NUMERIC_SCALE, DATETIME_PRECISION, IS_NULLABLE "
        f"  FROM INFORMATION_SCHEMA.COLUMNS "
        f" WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? "
        f"   AND COLUMN_NAME IN ({placeholders})",
        (METADATA_SCHEMA, METADATA_TABLE, *COPIED_COLUMNS))
    found = {row[0]: row for row in cursor.fetchall()}

    missing = [c for c in COPIED_COLUMNS if c not in found]
    if missing:
        raise SystemExit(
            f"{METADATA_SCHEMA}.{METADATA_TABLE} on the source has no "
            f"{', '.join(missing)}. Nothing was copied.")

    lines = []
    for column in COPIED_COLUMNS:
        _name, data_type, length, precision, scale, dt_precision, nullable = found[column]
        # The key is NOT NULL whatever the source says, since it is the primary
        # key here. Everything else keeps the source's nullability.
        required = column == METADATA_KEY or str(nullable).upper() == "NO"
        lines.append(
            f"    [{column}] "
            f"{_sql_type(data_type, length, precision, scale, dt_precision)}"
            f"{' NOT NULL' if required else ' NULL'}")

    body = ",\n".join(lines)
    definition = (
        f"CREATE TABLE [{METADATA_SCHEMA}].[{METADATA_TABLE}] (\n{body},\n"
        f"    CONSTRAINT [PK_MetaData_standin] PRIMARY KEY ([{METADATA_KEY}])\n);")
    return definition, {column: str(found[column][1]).upper()
                        for column in COPIED_COLUMNS}


def _sql_type(data_type: str, length, precision, scale, dt_precision) -> str:
    """One column's type, spelled the way CREATE TABLE wants it."""
    data_type = str(data_type).upper()
    if data_type in ("NVARCHAR", "VARCHAR", "NCHAR", "CHAR", "BINARY", "VARBINARY"):
        size = "MAX" if length in (-1, None) else int(length)
        return f"{data_type}({size})"
    if data_type in ("DECIMAL", "NUMERIC"):
        return f"{data_type}({int(precision)},{int(scale)})"
    if data_type in ("DATETIME2", "DATETIMEOFFSET", "TIME") and dt_precision is not None:
        return f"{data_type}({int(dt_precision)})"
    return data_type


def read_source(cursor, column_types: dict[str, str]) -> list[tuple]:
    """The rows the app shows, filtered exactly as the app filters them.

    A DATETIMEOFFSET is fetched as ISO 8601 text and inserted as text, because
    the driver can neither read nor write that type: reading one raises "ODBC
    SQL type -155 is not yet supported", and decoding it in Python would only
    move the same problem to the insert. Style 126 keeps the offset, so the
    value that lands is the value that left, and the target column converts it
    back on the way in.
    """
    columns = ", ".join(
        f"CONVERT(NVARCHAR(64), [{c}], 126) AS [{c}]"
        if column_types.get(c) == "DATETIMEOFFSET" else f"[{c}]"
        for c in COPIED_COLUMNS)
    cursor.execute(
        f"SELECT {columns} FROM [{METADATA_SCHEMA}].[{METADATA_TABLE}] "
        f"WHERE {METADATA_FILTER}")
    return [tuple(row) for row in cursor.fetchall()]


def write_target(cursor, rows: list[tuple],
                 column_types: dict[str, str]) -> tuple[int, int]:
    """Replace the stand-in's contents. Returns (deleted, inserted).

    Emptied and refilled rather than merged: this is a snapshot of a table
    nobody edits on Test, and a straight replace cannot leave a row behind that
    the source has since dropped.

    NO fast_executemany HERE. It binds each parameter as the target column's
    own type, so the ISO text standing in for a DATETIMEOFFSET is handed to the
    driver as a timestamp offset and fails with "invalid character value for
    cast specification". NVARCHAR(MAX), which Full_name is, is the other thing
    it does not take. Eleven thousand rows once is not worth the trouble.
    """
    cursor.execute(f"SELECT COUNT(*) FROM [{METADATA_SCHEMA}].[{METADATA_TABLE}]")
    before = int(cursor.fetchone()[0])
    cursor.execute(f"DELETE FROM [{METADATA_SCHEMA}].[{METADATA_TABLE}]")

    columns = ", ".join(f"[{c}]" for c in COPIED_COLUMNS)
    # The offset survives because both sides use style 126.
    placeholders = ", ".join(
        "CONVERT(DATETIMEOFFSET, ?, 126)"
        if column_types.get(c) == "DATETIMEOFFSET" else "?"
        for c in COPIED_COLUMNS)
    cursor.fast_executemany = False
    for start in range(0, len(rows), BATCH):
        cursor.executemany(
            f"INSERT INTO [{METADATA_SCHEMA}].[{METADATA_TABLE}] ({columns}) "
            f"VALUES ({placeholders})", rows[start:start + BATCH])
        print(f"      {min(start + BATCH, len(rows))} of {len(rows)}")
    return before, len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", default=SOURCE_DATABASE,
                        help=f"database to copy from (default: {SOURCE_DATABASE})")
    parser.add_argument("--target", default=sorted(ALLOWED_TARGETS)[0],
                        help="database to copy into. Only a test database is "
                             "accepted, whatever is passed here")
    parser.add_argument("--username", default="",
                        help="skip the username prompt. The password is always "
                             "asked for")
    parser.add_argument("--dry-run", action="store_true",
                        help="do everything, report it, then roll back")
    args = parser.parse_args()

    if args.target not in ALLOWED_TARGETS:
        raise SystemExit(
            f"Refusing to write to {args.target}. This only ever copies into "
            f"{', '.join(sorted(ALLOWED_TARGETS))}.")
    if args.target == args.source:
        raise SystemExit("The source and the target are the same database.")

    settings = server_settings()
    print(f"Copying {METADATA_SCHEMA}.{METADATA_TABLE} from {args.source} into "
          f"{args.target} on {settings['DB_SERVER']}.\n"
          f"Needs an account that may read on the source and create and write "
          f"on the target.")
    username, password = ask_credentials(args.username)

    source = connect(settings, args.source, username, password)
    target = connect(settings, args.target, username, password)
    try:
        source_cursor, target_cursor = source.cursor(), target.cursor()

        definition, column_types = source_definition(source_cursor)
        rows = read_source(source_cursor, column_types)
        print(f"\n{len(rows)} rows qualify on {args.source}")

        target_cursor.execute(
            f"SELECT OBJECT_ID(N'[{METADATA_SCHEMA}].[{METADATA_TABLE}]')")
        exists = target_cursor.fetchone()[0] is not None
        if not exists:
            print(f"\nCreating the stand-in on {args.target}:\n{definition}")
            target_cursor.execute(definition)
        else:
            print(f"\n{METADATA_SCHEMA}.{METADATA_TABLE} already exists on "
                  f"{args.target}, refilling it")

        before, inserted = write_target(target_cursor, rows, column_types)
        print(f"   removed {before}, inserted {inserted}")

        if args.dry_run:
            target.rollback()
            print("\nDry run, rolled back. Run again without --dry-run to keep it.")
        else:
            target.commit()
            print("\nCommitted.")
    except Exception as e:
        target.rollback()
        print(f"\nRolled back, nothing was written: {e}")
        return 1
    finally:
        source.close()
        target.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
