"""06 — Load the exported ProcessData CSV into the Test database.

The other half of 05. It creates dbo.ProcessData on Test from the column
definitions 05 wrote down, then fills it, so the Vat column has something to
read on a database that only ever received a MetaData snapshot.

    data/csv/process_data.csv  ->  dataloggingTest.dbo.ProcessData

WRITES ONLY TO dataloggingTest. The target is checked against an allow list
before anything is created, because the obvious way to get this wrong is to run
it in the direction it came from and overwrite the production log.

NOT A MIGRATION. It fills a test database with a copy of somebody else's table,
which is tooling, not schema this project owns. Migration 014 grants SELECT on
dbo.ProcessData by presence, so the grant starts working on Test the moment
this has run.

REPLACES WHAT IS THERE. A half loaded copy is worse than none, so an existing
table is emptied first, in the same transaction as the load: either the whole
copy lands or nothing changes.

INDEXES THE COPY to match dataloggingDev, since --recreate drops the table and
takes them with it. Without them every join on Marking scans 361k rows and the
QC sampling rule is slower on Test than it will ever be in production.

The login is asked for at the keyboard and stored nowhere. It needs CREATE
TABLE and INSERT on the target.

Run:
    venv\\Scripts\\python.exe data\\ETL\\06_load_process_data_to_test.py --dry-run
    venv\\Scripts\\python.exe data\\ETL\\06_load_process_data_to_test.py
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
DATA_CSV = CSV_DIR / "process_data.csv"
COLUMNS_JSON = CSV_DIR / "process_data_columns.json"

# The one database this may write to. Everything else is refused, whatever is
# passed on the command line.
ALLOWED_TARGETS = {"dataloggingTest"}

# Types the driver cannot bind in bulk. fast_executemany binds by the target
# column's type, and for these it raises "Invalid character value for cast
# specification" on text it would otherwise accept row by row.
AWKWARD = {"DATETIMEOFFSET", "DATETIME2", "DATETIME", "SMALLDATETIME"}

BATCH = 1000

# What dataloggingDev and production have on this table, in the order they must
# be created: the clustered key first, or the other two are rebuilt when it
# arrives. The same three as sql/maintenance/match_test_process_data_indexes.sql,
# which is the by-hand version for a Test database loaded before this existed.
INDEXES = (
    ("PK_ProcessData",
     "ALTER TABLE [{schema}].[{table}] "
     "ADD CONSTRAINT [PK_ProcessData] PRIMARY KEY CLUSTERED ([Id]);"),
    ("IX_ProcessData_Marking",
     "CREATE NONCLUSTERED INDEX [IX_ProcessData_Marking] "
     "ON [{schema}].[{table}] ([Marking]);"),
    ("IX_ProcessData_Vat",
     "CREATE NONCLUSTERED INDEX [IX_ProcessData_Vat] "
     "ON [{schema}].[{table}] ([Proces], [Marking]);"),
)


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
    """The login to write with, typed rather than stored."""
    username = input("Username: ").strip()
    if not username:
        raise SystemExit("No username given, nothing was loaded.")
    password = getpass.getpass(f"Password for {username}: ")
    if not password:
        raise SystemExit("No password given, nothing was loaded.")
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


def read_definition() -> dict:
    """What 05 wrote down about the source table."""
    if not COLUMNS_JSON.exists() or not DATA_CSV.exists():
        raise SystemExit(
            f"Run data/ETL/05_export_process_data.py first, it writes "
            f"{DATA_CSV.name} and {COLUMNS_JSON.name}.")
    with open(COLUMNS_JSON, "r", encoding="utf-8") as handle:
        definition = json.load(handle)
    if not definition.get("columns"):
        raise SystemExit(f"{COLUMNS_JSON.name} names no columns.")
    return definition


def sql_type(column: dict) -> str:
    """One column's type, spelled the way CREATE TABLE wants it."""
    data_type = str(column["type"]).upper()
    if data_type in ("NVARCHAR", "VARCHAR", "NCHAR", "CHAR", "BINARY", "VARBINARY"):
        length = column.get("length")
        return f"{data_type}({'MAX' if length in (-1, None) else int(length)})"
    if data_type in ("DECIMAL", "NUMERIC"):
        return f"{data_type}({int(column['precision'])},{int(column['scale'])})"
    if (data_type in ("DATETIME2", "DATETIMEOFFSET", "TIME")
            and column.get("datetime_precision") is not None):
        return f"{data_type}({int(column['datetime_precision'])})"
    return data_type


def create_statement(definition: dict) -> str:
    """The stand-in table, from the source's own types.

    No identity and no key: this is a copy carrying the original values, and an
    identity would only need turning off to insert them.
    """
    lines = [f"    [{c['name']}] {sql_type(c)}"
             f"{' NULL' if c.get('nullable', True) else ' NOT NULL'}"
             for c in definition["columns"]]
    body = ",\n".join(lines)
    return (f"CREATE TABLE [{definition['schema']}].[{definition['table']}] "
            f"(\n{body}\n);")


def ensure_indexes(cursor, schema: str, table: str) -> list:
    """Create whichever of Dev's indexes this copy is missing, and name them.

    After the rows, not before: inserting 361k rows through three indexes is
    slower than building them over a filled table.
    """
    made = []
    for name, statement in INDEXES:
        if cursor.execute("SELECT 1 FROM sys.indexes "
                          "WHERE object_id = OBJECT_ID(?) AND name = ?",
                          f"[{schema}].[{table}]", name).fetchval():
            continue
        cursor.execute(statement.format(schema=schema, table=table))
        made.append(name)
    return made


def placeholders(columns: list) -> str:
    """One placeholder per column, converting the text back where needed."""
    return ", ".join(
        "CONVERT(DATETIMEOFFSET, ?, 126)"
        if str(c["type"]).upper() == "DATETIMEOFFSET" else "?"
        for c in columns)


def target_columns(cursor, schema: str, table: str) -> dict:
    """``{column: whether it allows NULL}`` on the target, empty if no table."""
    cursor.execute(
        "SELECT COLUMN_NAME, IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS "
        " WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?", (schema, table))
    return {row[0]: str(row[1]).upper() == "YES" for row in cursor.fetchall()}


def loadable(definition: dict, target: dict) -> list:
    """The columns to load, which is what both sides have.

    THE TABLE ON TEST IS NOT ALWAYS WHAT THIS EXPORT HOLDS. A table left behind
    by an earlier full export has every column Dev has, several of them NOT
    NULL, and a --vat-only export has two. Loading it verbatim fails on the
    first row with a NULL in a column that does not allow one.

    Refused when the target needs something this export has not got, with the
    fix named, rather than failing halfway through.
    """
    shared = [c for c in definition["columns"] if c["name"] in target]
    if not shared:
        raise SystemExit("The table on the target has none of the exported "
                         "columns. It is not the same table. Nothing was loaded.")

    have = {c["name"] for c in shared}
    required = [name for name, nullable in target.items()
                if not nullable and name not in have]
    if required:
        raise SystemExit(
            f"The table on the target declares {', '.join(required)} NOT NULL, "
            f"and this export has no such column. Either re-export the whole "
            f"table with 05 (no --vat-only), or pass --recreate to rebuild the "
            f"table from what was exported. Nothing was loaded.")
    return shared


def read_rows(columns: list) -> list:
    """The CSV as rows, with empty fields back to NULL where one is allowed."""
    names = [c["name"] for c in columns]
    nullable = [c.get("nullable", True) for c in columns]
    rows: list[tuple] = []
    with open(DATA_CSV, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [n for n in names if n not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"{DATA_CSV.name} has no {', '.join(missing)}. "
                             f"It does not match {COLUMNS_JSON.name}.")
        for row in reader:
            rows.append(tuple(
                None if (row.get(name) or "") == "" and allows_null else row.get(name)
                for name, allows_null in zip(names, nullable)))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database", default=sorted(ALLOWED_TARGETS)[0],
                        help="the target database (only dataloggingTest is allowed)")
    parser.add_argument("--dry-run", action="store_true",
                        help="say what would happen and write nothing")
    parser.add_argument("--recreate", action="store_true",
                        help="drop the target table and rebuild it from what "
                             "was exported. Takes its grants with it, see 014")
    args = parser.parse_args()

    if args.database not in ALLOWED_TARGETS:
        raise SystemExit(
            f"Refusing to write to {args.database}. This only loads into "
            f"{', '.join(sorted(ALLOWED_TARGETS))}.")

    definition = read_definition()
    schema, table = definition["schema"], definition["table"]
    available = len(read_rows(definition["columns"]))
    print(f"{DATA_CSV.name}: {available:,} rows, "
          f"{len(definition['columns'])} columns".replace(",", " "))
    print(f"   exported from {definition.get('source', 'unknown')}"
          f"{', vat columns only' if definition.get('vat_only') else ''}")
    print(f"   target {args.database}.{schema}.{table}")
    if not available:
        print("Nothing to load.")
        return 1
    if args.dry_run:
        print("\nDry run, nothing was written. A table created from scratch "
              "would be:\n")
        print(create_statement(definition))
        print("\nWhat goes into a table that already exists depends on the "
              "columns it has, which needs a connection to see.")
        return 0

    settings = server_settings()
    username, password = ask_credentials()
    connection = connect(settings, args.database, username, password)
    try:
        cursor = connection.cursor()
        # Belt and braces: the connection string named the database, this
        # checks what the server actually gave us.
        actual = cursor.execute("SELECT DB_NAME()").fetchval()
        if actual not in ALLOWED_TARGETS:
            raise SystemExit(f"Connected to {actual}, which is not a target "
                             f"this may write to. Nothing was loaded.")

        # WORKED OUT BEFORE ANYTHING IS DELETED. Emptying the table and then
        # finding the columns do not line up leaves the target empty and the
        # operator reaching for a backup.
        target = target_columns(cursor, schema, table)
        if target and args.recreate:
            print("   dropping and rebuilding the table from what was exported")
            cursor.execute(f"DROP TABLE [{schema}].[{table}];")
            target = {}

        if target:
            loading = loadable(definition, target)
            skipped = [c["name"] for c in definition["columns"]
                       if c["name"] not in target]
            if skipped:
                print(f"   the table here has no {', '.join(skipped)}, "
                      f"so those are not loaded")
            before = cursor.execute(
                f"SELECT COUNT(*) FROM [{schema}].[{table}]").fetchval()
            print(f"   loading {len(loading)} of {len(definition['columns'])} "
                  f"columns over {before:,} rows already there"
                  .replace(",", " "))
            cursor.execute(f"DELETE FROM [{schema}].[{table}];")
        else:
            loading = definition["columns"]
            print("   creating the table")
            cursor.execute(create_statement(definition))

        rows = read_rows(loading)

        # Bound one row at a time when a date type is involved, see AWKWARD.
        types = {str(c["type"]).upper() for c in loading}
        cursor.fast_executemany = not (types & AWKWARD)
        columns = ", ".join(f"[{c['name']}]" for c in loading)
        insert = (f"INSERT INTO [{schema}].[{table}] ({columns}) "
                  f"VALUES ({placeholders(loading)})")

        for start in range(0, len(rows), BATCH):
            cursor.executemany(insert, rows[start:start + BATCH])
            done = min(start + BATCH, len(rows))
            print(f"\r   {done:,} rows".replace(",", " "), end="", flush=True)

        after = cursor.execute(f"SELECT COUNT(*) FROM [{schema}].[{table}]").fetchval()
        connection.commit()

        # Indexed after the commit, so a failure here costs the indexes and not
        # the rows that just landed.
        try:
            made = ensure_indexes(cursor, schema, table)
            connection.commit()
            if made:
                print(f"\n   indexed: {', '.join(made)}")
        except Exception as e:
            connection.rollback()
            print(f"\n   the rows are in, but indexing failed: {e}")
            print("   run sql/maintenance/match_test_process_data_indexes.sql "
                  "to put that right")
    except Exception as e:
        connection.rollback()
        print(f"\nRolled back, nothing was written: {e}")
        return 1
    finally:
        connection.close()

    print(f"\n\n{schema}.{table} on {args.database} now holds {after:,} rows"
          .replace(",", " "))
    print("Run sql/migrations/014_grant_read_on_process_data.sql if you have "
          "not yet, then the Vat column appears on the next launch.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
