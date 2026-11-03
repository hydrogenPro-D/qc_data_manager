"""07 — Download dbo.MetaData from Dev into a CSV.

The electrode list: one row per produced electrode, and the table every tab in
the application starts from. It only exists on the production database, so a
test database works against a snapshot of it, and this is what takes the
snapshot. 08 puts the copy on Test.

    dataloggingDev.dbo.MetaData  ->  data/csv/metadata.csv
                                     data/csv/metadata_columns.json

The same shape as 05 and 06, which do this for dbo.ProcessData, and for the
same reasons: two files because a CSV carries no types, and the column
definitions read from INFORMATION_SCHEMA rather than written down here, so the
copy behaves like the real thing rather than like a guess at it.

THE WHOLE TABLE BY DEFAULT. It is a copy of somebody else's table and not this
project's to trim. Pass --electrodes-only for just the rows the application
itself reads, the B markings on the recipes it shows, which is a fraction of it
and is all the sampling rules need.

The login is asked for at the keyboard and stored nowhere. It needs SELECT on
dbo.MetaData.

Run:
    venv\\Scripts\\python.exe data\\ETL\\07_export_metadata.py
    venv\\Scripts\\python.exe data\\ETL\\07_export_metadata.py --electrodes-only
"""

from __future__ import annotations

import argparse
import csv
import getpass
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

CONNECTION_FILE = PROJECT_ROOT / "config" / "db_connection.json"
CSV_DIR = PROJECT_ROOT / "data" / "csv"
DATA_CSV = CSV_DIR / "metadata.csv"
COLUMNS_JSON = CSV_DIR / "metadata_columns.json"

SCHEMA, TABLE = "dbo", "MetaData"
KNOWN_DATABASES = ("dataloggingDev", "dataloggingTest")

# The rows the application itself reads. Taken from METADATA_FILTER in
# db_manager rather than invented here, so a snapshot holds what the app would
# have shown and not a different set of electrodes.
ELECTRODE_FILTER = ("[Marking] LIKE 'B%' "
                    "AND [Full_name] IN ('CATHODE', 'ANODE', 'MM1', 'CATHODE NEW')")

# Above this, the whole table is worth a second thought before it is written to
# a file somebody has to move around.
LARGE = 500_000
BATCH = 5000


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


def ask_credentials() -> tuple[str, str]:
    """The login to read with, typed rather than stored."""
    username = input("Username: ").strip()
    if not username:
        raise SystemExit("No username given, nothing was exported.")
    password = getpass.getpass(f"Password for {username}: ")
    if not password:
        raise SystemExit("No password given, nothing was exported.")
    return username, password


def connect(settings: dict, database: str, username: str, password: str):
    import pyodbc

    return pyodbc.connect(
        f"DRIVER={{{settings['DB_DRIVER']}}};"
        f"SERVER={settings['DB_SERVER']};"
        f"DATABASE={database};"
        f"UID={username};PWD={password};"
        f"Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;")


def read_columns(cursor) -> list[dict]:
    """The source's own column definitions, in the table's own order."""
    cursor.execute(
        "SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, "
        "       NUMERIC_PRECISION, NUMERIC_SCALE, DATETIME_PRECISION, IS_NULLABLE "
        "  FROM INFORMATION_SCHEMA.COLUMNS "
        " WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? "
        " ORDER BY ORDINAL_POSITION", (SCHEMA, TABLE))
    columns = [
        {"name": row[0], "type": str(row[1]).upper(), "length": row[2],
         "precision": row[3], "scale": row[4], "datetime_precision": row[5],
         "nullable": str(row[6]).upper() == "YES"}
        for row in cursor.fetchall()]
    if not columns:
        raise SystemExit(f"{SCHEMA}.{TABLE} is not there, or the login cannot "
                         f"see it. Nothing was exported.")
    return columns


def select_list(columns: list[dict]) -> str:
    """The column list to SELECT, with the awkward types converted to text.

    The driver can neither read nor write a DATETIMEOFFSET: reading one raises
    "ODBC SQL type -155 is not yet supported". Style 126 keeps the offset, so
    the value that lands in the CSV is the value that left.
    """
    return ", ".join(
        f"CONVERT(NVARCHAR(64), [{c['name']}], 126) AS [{c['name']}]"
        if c["type"] == "DATETIMEOFFSET" else f"[{c['name']}]"
        for c in columns)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database", default=KNOWN_DATABASES[0],
                        choices=KNOWN_DATABASES,
                        help="where to read from (default: dataloggingDev)")
    parser.add_argument("--electrodes-only", action="store_true",
                        help="only the B markings on the recipes the "
                             "application shows")
    parser.add_argument("--yes", action="store_true",
                        help="do not ask before exporting a large table")
    args = parser.parse_args()

    settings = server_settings()
    print(f"Reading {SCHEMA}.{TABLE} from {args.database} "
          f"on {settings['DB_SERVER']}")
    username, password = ask_credentials()

    connection = connect(settings, args.database, username, password)
    try:
        cursor = connection.cursor()
        columns = read_columns(cursor)
        where = f" WHERE {ELECTRODE_FILTER}" if args.electrodes_only else ""
        total = cursor.execute(
            f"SELECT COUNT(*) FROM [{SCHEMA}].[{TABLE}]{where}").fetchval()
        print(f"   {len(columns)} columns: "
              f"{', '.join(c['name'] for c in columns)}")
        print(f"   {total:,} rows to export".replace(",", " "))

        if total > LARGE and not args.yes:
            if input("That is a lot. Export anyway? [y/N] ").strip().lower() != "y":
                print("Nothing was exported.")
                return 1

        CSV_DIR.mkdir(parents=True, exist_ok=True)
        with open(COLUMNS_JSON, "w", encoding="utf-8") as handle:
            json.dump({"schema": SCHEMA, "table": TABLE, "source": args.database,
                       "electrodes_only": args.electrodes_only,
                       "columns": columns}, handle, indent=2)
        print(f"   column definitions -> {COLUMNS_JSON.name}")

        cursor.execute(f"SELECT {select_list(columns)} "
                       f"FROM [{SCHEMA}].[{TABLE}]{where}")
        written = 0
        with open(DATA_CSV, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow([c["name"] for c in columns])
            while True:
                rows = cursor.fetchmany(BATCH)
                if not rows:
                    break
                # NULL and empty string are both written as an empty field. 08
                # puts NULL back for every column that allows one.
                writer.writerows([["" if v is None else v for v in row]
                                  for row in rows])
                written += len(rows)
                print(f"\r   {written:,} rows".replace(",", " "), end="", flush=True)
    finally:
        connection.close()

    size_mb = DATA_CSV.stat().st_size / (1024 * 1024)
    print(f"\n\nWrote {written:,} rows to {DATA_CSV} ({size_mb:.1f} MB)"
          .replace(",", " "))
    print("Next: data/ETL/08_load_metadata_to_test.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
