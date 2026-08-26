"""One place that knows how to talk to SQL Server.

``DbManager`` owns a session and hands it to the stores, so nothing below it
holds a connection string, opens a connection or knows a driver name. That is
what keeps the board store and the SEM source testable with a fake: they only
ever call ``query``, ``rows`` and ``call``.

Connections are opened per call. Reads happen at startup and on refresh, writes
are user-paced, so nothing here is in a hot path. The polling connection is the
one exception and stays in ``DbManager``, where it is reused.
"""

from __future__ import annotations

import datetime
import struct
from decimal import Decimal

import pandas as pd

# pyodbc has no built-in handler for DATETIMEOFFSET, so a raw fetch of any such
# column raises "ODBC SQL type -155 is not yet supported". Every connection
# registers the converter below. dbo.MetaData.[Date] is one of these.
SQL_DATETIMEOFFSET = -155


def handle_datetimeoffset(raw: bytes) -> datetime.datetime:
    """Decode SQL Server's DATETIMEOFFSET wire format into a tz-aware datetime."""
    year, month, day, hour, minute, second, nanos, tz_hour, tz_minute = struct.unpack(
        "<6hI2h", raw)
    return datetime.datetime(
        year, month, day, hour, minute, second, nanos // 1000,
        datetime.timezone(datetime.timedelta(hours=tz_hour, minutes=tz_minute)),
    )


def normalize_cell(value):
    """One DB value made display-friendly.

    Every NULL/NaN/NaT collapses to None, DATE/DATETIME to an ISO string, TIME
    to 'HH:MM:SS' and Decimal to float, so nothing downstream has to ask which
    of those it is holding.
    """
    try:
        if value is None or pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, datetime.datetime):
        return (value.date().isoformat() if value.time() == datetime.time(0, 0)
                else value.isoformat(sep=" "))
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, datetime.time):
        return value.strftime("%H:%M:%S")
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, str):
        # Fixed-width NCHAR columns come back space-padded
        # (e.g. 'CATHODE                       ').
        return value.strip()
    return value


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Every cell of a frame through :func:`normalize_cell`."""
    return df.map(normalize_cell) if not df.empty else df


class SqlSession:
    """Opens connections and runs statements. Holds no state between calls."""

    def __init__(self, connection_string: str) -> None:
        self._connection_string = connection_string

    def connect(self):
        """A connection with the DATETIMEOFFSET converter registered.

        Every path goes through here so no caller can hit the unsupported-type
        error.
        """
        import pyodbc

        connection = pyodbc.connect(self._connection_string)
        connection.add_output_converter(SQL_DATETIMEOFFSET, handle_datetimeoffset)
        return connection

    def query(self, sql: str, params: tuple = ()) -> pd.DataFrame:
        """A SELECT as a normalized frame."""
        columns, rows = self._fetch(sql, params)
        return normalize(pd.DataFrame.from_records(rows, columns=columns))

    def rows(self, sql: str, params: tuple = ()) -> list[dict]:
        """A SELECT as normalized dictionaries, for code that wants plain rows."""
        columns, rows = self._fetch(sql, params)
        return [{column: normalize_cell(value) for column, value in zip(columns, row)}
                for row in rows]

    def call(self, procedure: str, **params) -> list[dict]:
        """Run a stored procedure and return its first result set, if any.

        The parameter names are ours and the values are bound, so nothing the
        operator types reaches the statement text.
        """
        names = list(params)
        assignments = ", ".join(f"@{name} = ?" for name in names)
        statement = f"EXEC {procedure}" + (f" {assignments}" if assignments else "")

        connection = self.connect()
        try:
            cursor = connection.cursor()
            cursor.execute(statement, tuple(params[name] for name in names))
            result: list[dict] = []
            # A procedure that only writes has no description to read.
            if cursor.description:
                columns = [c[0] for c in cursor.description]
                result = [{column: normalize_cell(value)
                           for column, value in zip(columns, row)}
                          for row in cursor.fetchall()]
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _fetch(self, sql: str, params: tuple = ()) -> tuple[list[str], list[tuple]]:
        connection = self.connect()
        try:
            cursor = connection.cursor()
            cursor.execute(sql, params) if params else cursor.execute(sql)
            columns = [c[0] for c in cursor.description]
            return columns, [tuple(row) for row in cursor.fetchall()]
        finally:
            connection.close()
