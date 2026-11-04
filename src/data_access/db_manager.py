"""DbManager — the backend: views in, GUI-ready frames out.

The one place that knows where data lives. Everything above it is handed
callables and DataFrames, everything below it is either a view, a procedure or
a pure processor.

    dbo.MetaData        the produced electrodes, read only
    qc_data.vw_*        statuses, the board, the SEM results
    qc_data.usp_*       every write this application makes

Reads come back through the denormalizing views, get normalized (Decimal →
float, dates → ISO, every NULL/NaN/NaT → None) and then run through the
processors, which are pure DataFrame in, DataFrame out.

There is no file based mode. The local CSV and JSON this app started on now
exist only as the input to data/ETL, which loads them into the schema above.

Connections are opened per query/proc call (reads are infrequent: startup +
refresh; writes are user-paced) except the reused watermark-poll connection,
one per thread.

NOTHING RESEEDS A WATERMARK AFTER OUR OWN WRITE. It used to, so the next poll
would not see our own change and reload for nothing. Each reseed re-read the
watermarks, so a card drop cost four round trips and about half a second of
frozen window, to save a reload that now costs 19ms on a background thread. The
redundant reload is by far the cheaper of the two.
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import threading
from pathlib import Path, PureWindowsPath

import pandas as pd

from src.data_access import app_config
from src.data_access.electrodes.electrode_columns import (
    ElectrodeColumns,
    ElectrodeStatus,
    canonical_electrode_type,
    normalize_marking,
)
from src.data_access.electrodes.electrode_processor import ElectrodeProcessor
from src.data_access.electrodes.status_propagation import electrode_number
from src.data_access.electrodes.electrode_status_db import (
    load_manual_edit_times,
    load_status_by_marking as load_status_from_db,
)
from src.data_access.electrodes.electrode_vat_db import load_vat_by_marking
from src.data_access.electrodes.failure_reason_db import load_failure_reasons
from src.data_access.electrodes.status_propagation import propagate_detailed
from src.data_access.qc_board.qc_board_columns import (IC_FAILED, IC_RESULT,
                                                       SEM_CRACKED, SEM_RESULT)
from src.data_access.qc_board.qc_board_db_store import QcBoardDbStore
from src.data_access.qc_requests import coupon_db_source, request_db_source
from src.data_access.qc_requests import coupon_columns
from src.data_access.qc_requests.request_columns import KIND_FAULT
from src.data_access.sampling.interval_db_source import (load_intervals,
                                                         set_interval)
from src.data_access.sampling.switch_db_source import load_switch, set_switch
from src.data_access.sem_edx import sem_edx_db_source as sem_db
from src.data_access.sem_edx import sem_edx_library
from src.data_access.sem_edx.sem_edx_columns import (
    DEFAULT_LAYOUT,
    RATIO_SEPARATOR,
    SemEdxColumns,
    SAMPLE_KEY,
    element_symbol,
    quantities,
    split_images,
)
from src.data_access.sem_edx.sem_edx_processor import SemEdxProcessor
from src.data_access.sem_edx.sem_edx_result_parser import (
    ParsedFolder,
    ParsedMeasurement,
    base_position,
    candidate_images,
    is_known_position,
    parse_result_folder,
    result_images,
    sample_locations,
)
from src.data_access.sem_edx.sem_edx_source import (
    DEFAULT_GENERATION,
    portable_folder,
    resolve_folder,
)
from src.data_access.sql_session import (
    SQL_DATETIMEOFFSET,
    SqlSession,
    handle_datetimeoffset as _handle_datetimeoffset,
    normalize as _normalize_frame,
)
from src.data_access.watermark import WatermarkPoller

# The schema this application owns.
QC_SCHEMA = "qc_data"

# What the qc_data watermark view reports on, and what each one reloads.
_ELECTRODE_STATUS_ENTITY = "electrode_status"
_QC_BOARD_ENTITY = "qc_board"
_SEM_EDX_ENTITY = "sem_edx"
# The shared schema's requests. Watched by the Electrodes tab, because that is
# where they are shown and the other application is what writes them.
_QC_REQUEST_ENTITY = "qc_requests"

# The two verdicts that condemn their neighbours. Named here because the one
# thing asked of them is whether a decision is a failure.
_SCRAPPED_VERDICTS = {str(ElectrodeStatus.SCRAPPED),
                      str(ElectrodeStatus.TESTED_AND_SCRAPPED)}

# Production metadata: one row per produced electrode. Read straight from the
# base table (no view exists for it yet); [Marking] carries the B-number.
METADATA_SCHEMA = "dbo"
METADATA_TABLE = "MetaData"
METADATA_KEY = "Id"
_METADATA_ENTITY = "metadata"

# The columns the Electrodes tab needs, in display order. These are the stored
# identifiers (the contract); what the operator sees is mapped in the GUI.
METADATA_COLUMNS = [c.value for c in ElectrodeColumns]

# Only electrode markings are of interest; the table also holds A-numbered and
# unmarked rows (~2k of them) from other runs. Recipes other than the two
# production ones (test/dryer/buffer runs) are excluded the same way.
# No TRIM on [Full_name], although it is fixed-width and comes back padded:
# SQL Server ignores trailing spaces when comparing, so = and IN match anyway,
# and wrapping the column in a function only stops an index from being used.
# What reaches the screen is trimmed by normalize_cell, at the reader.
METADATA_FILTER = (
    "[Marking] LIKE 'B%' "
    "AND [Full_name] IN ('CATHODE', 'ANODE', 'MM1', 'CATHODE NEW')"
)

# The numeric part of a marking ('B14174' → 14174), for sorting in production
# order. TRY_CONVERT rather than CONVERT so an odd marking sorts last instead
# of failing the whole query.
MARKING_NUMBER_SQL = "TRY_CONVERT(BIGINT, SUBSTRING(TRIM([Marking]), 2, 20))"

def _tail_key(value) -> str:
    """A stored folder, comparable: forward slashes, no case, no stray edges.

    Through PureWindowsPath so a row that used the other separator, or a
    trailing one, still compares equal to the same folder.
    """
    text = str(value or "").strip()
    return PureWindowsPath(text).as_posix().strip("/").lower() if text else ""


def _marking_key(value) -> str:
    """An electrode marking, comparable: no case, no spaces."""
    return "".join(str(value or "").split()).upper()


class DriverNotInstalledError(RuntimeError):
    """Raised when the required ODBC driver is absent even after its installer ran."""


class DriverInstallDeclinedError(DriverNotInstalledError):
    """Raised when the user declines to install the required ODBC driver."""


class DbManager:
    """SQL backend. Reads through views, writes through stored procedures."""

    def __init__(self, config_file_name: str = "db_connection.json",
                 install_confirm=None, ask_image_root=None,
                 database: str = "") -> None:
        # ``install_confirm`` is an optional ``(driver_name, installer_path) -> bool``
        # callback asked before the ODBC-driver installer runs. It lets the GUI pop a
        # Yes/No dialog while this data-layer class stays Qt-free. None (headless /
        # tests) installs without asking.
        self._install_confirm = install_confirm
        # ``() -> str``, a folder picker for the SEM images library, asked only
        # when the search cannot find it. Same reason it is a callable.
        self._ask_image_root = ask_image_root

        self._base_directory = app_config.base_directory()
        self._config_path = self._base_directory / "config" / config_file_name

        cfg = self._load_config(self._config_path)
        # An ETL script can point this at the other database without editing
        # the connection file. The login is whatever the file says: the same
        # contained user exists on both.
        if database:
            cfg = {**cfg, "DB_DATABASE": database}
        self._driver_name = cfg["DB_DRIVER"]
        self._conn_str = self._build_connection_string(cfg)
        # dbo.MetaData and dbo.ProcessData belong to the production system: this
        # application only ever reads them, so they can come from a different
        # database than the one it writes its own schema to. Setting
        # DB_PLANT_DATABASE lets a test build work against the real electrode
        # list without a copy of it going stale. Absent, it is the same database
        # and the same connection.
        plant = str(cfg.get("DB_PLANT_DATABASE", "") or "").strip()
        self._database = str(cfg["DB_DATABASE"])
        self._plant_database = plant or str(cfg["DB_DATABASE"])
        self._plant_conn_str = (
            self._conn_str if not plant or plant == cfg["DB_DATABASE"]
            else self._build_connection_string({**cfg, "DB_DATABASE": plant}))

        # No fallback backend — if the driver is missing, install it and wait.
        self._ensure_driver_installed()

        self._session = SqlSession(self._conn_str)
        self._plant_session = (
            self._session if self._plant_conn_str == self._conn_str
            else SqlSession(self._plant_conn_str))
        if self._plant_conn_str != self._conn_str:
            print(f"Reading the plant tables from {self._plant_database}, "
                  f"writing to {cfg['DB_DATABASE']}")
        # Which vat each electrode was plated in, joined in from the process
        # log. Deliberately NOT read here: the window has nothing to do with
        # this column while it opens, and the process log is a far bigger table
        # than the electrode list. The GUI asks for it on a worker thread once
        # it is up, see load_vats, and the column appears when it arrives.
        self._vats: dict[str, str] = {}
        # Reasons for failures the board never saw, read on first use. None
        # means not read yet, which is not the same as none recorded.
        self._historic_reasons: dict[str, dict] | None = None
        # True until the process log has been read once, so the tab can show the
        # column as loading rather than as absent. A database with no process
        # log answers instantly and the column is dropped, as before.
        self._vats_pending = True
        # The QC board lives here too, so its decisions can override the base
        # status on every read. The GUI shares this one instance.
        # It takes the vats because a frozen sweep is only unfrozen by a card
        # from the same run, and the store is where that is decided.
        self._qc_board = QcBoardDbStore(self._session,
                                        vat_provider=lambda: self._vats,
                                        on_arrival=self.qc_request_arrived)
        # When each by-hand correction was made, so a frozen board verdict on
        # the same electrode can be ordered against it. Kept in step on write:
        # nothing re-reads the statuses after startup.
        self._manual_edits = load_manual_edit_times(self._session)
        # What QC wants cut from a sampled electrode, per reason. Read here
        # because the statuses depend on it: a failure QC wants nothing from
        # queues no neighbours, so this has to be known before the first render.
        self._coupons = coupon_db_source.load_settings(self._session)
        self._electrodes = ElectrodeProcessor(
            status_by_marking=load_status_from_db(self._session),
            overrides_provider=self._sweeping_decisions,
            # Every card, not only the decided ones: an electrode being
            # tested drives its own row and has to lock it.
            board_provider=self._qc_board.on_board,
            base_overrides_provider=self._settled_statuses,
            settled_priorities_provider=self._settled_priorities,
            vat_provider=lambda: self._vats,
            vat_pending_provider=self.vats_pending,
            ongoing_provider=self._qc_board.ongoing_electrodes,
            unqueued_provider=self._unqueued_scraps,
            pinned_provider=lambda: set(self._manual_edits),
            finalised_provider=self._finalised_times,
            findings_provider=self._findings_with_history,
            revisited_provider=self._qc_board.revisited_by,
            qc_request_provider=self.get_qc_request_details)
        # Which quantities each sheet shows, from config.json. Read per
        # generation on first use, and one processor per generation and
        # electrode type, since none of the four measures quite the same thing.
        self._sem_edx_layouts: dict[str, dict[str, list[str]]] = {}
        self._sem_edx_processors: dict[tuple[str, str], SemEdxProcessor] = {}
        self._sem_edx_caches: dict[str, pd.DataFrame] = {}
        self._metadata_cache = None  # last raw electrode read, for reprocessing
        # (MAX(Id), COUNT(*)) as of the last poll, and as of the read that
        # filled the cache. Different means dbo.MetaData itself moved and the
        # rows have to be fetched again, rather than only reprocessed.
        self._produced_seen = None
        self._produced_applied = None
        # One reused connection PER THREAD: the poll runs on a worker thread
        # and a pyodbc connection cannot be shared across threads.
        self._poll_local = threading.local()
        # dbo.MetaData has no watermark view of its own, so it gets a
        # (MAX(Id), COUNT(*)) query — same contract, different shape.
        self._metadata_poller = WatermarkPoller(self._metadata_watermarks)
        # The tables this app owns report through their own view, in the schema
        # this app owns, so the board and the SEM tab notice each other's work.
        self._qc_poller = WatermarkPoller(self._qc_watermarks)

    # ---- connection ---------------------------------------------------
    @staticmethod
    def _load_config(config_path: Path) -> dict:
        if not config_path.exists():
            raise FileNotFoundError(f"DB connection config not found: {config_path}")
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        required = ["DB_DRIVER", "DB_SERVER", "DB_DATABASE", "DB_USERNAME", "DB_PASSWORD"]
        missing = [k for k in required if not str(cfg.get(k, "")).strip()]
        if missing:
            raise ValueError(
                f"{config_path.name} is missing/empty fields: {', '.join(missing)} "
                f"({config_path})"
            )
        return cfg

    @staticmethod
    def _build_connection_string(cfg: dict) -> str:
        return (
            f"DRIVER={{{cfg['DB_DRIVER']}}};"
            f"SERVER={cfg['DB_SERVER']};"
            f"DATABASE={cfg['DB_DATABASE']};"
            f"UID={cfg['DB_USERNAME']};"
            f"PWD={cfg['DB_PASSWORD']};"
            f"Encrypt=yes;TrustServerCertificate=no;"
        )

    def _ensure_driver_installed(self) -> None:
        """Install the configured ODBC driver if absent, blocking until done."""
        import pyodbc

        if self._driver_name in pyodbc.drivers():
            return

        installer = self._base_directory / "driver" / "msodbcsql.msi"
        if not installer.exists():
            raise DriverNotInstalledError(
                f"ODBC driver '{self._driver_name}' is not installed and its "
                f"installer was not found at {installer}."
            )

        # Ask first (the GUI shows a Yes/No dialog). Declining aborts startup.
        if self._install_confirm is not None and not self._install_confirm(self._driver_name, installer):
            raise DriverInstallDeclinedError(
                f"Installation of the required ODBC driver '{self._driver_name}' "
                f"was declined. The application cannot start without it."
            )

        subprocess.run(["msiexec", "/i", str(installer)])
        if self._driver_name not in pyodbc.drivers():
            raise DriverNotInstalledError(
                f"ODBC driver '{self._driver_name}' is still not installed after "
                f"running the installer. Install it and restart the app."
            )

    # ---- reads --------------------------------------------------------
    def _connect(self, plant: bool = False):
        """Open a connection with the DATETIMEOFFSET converter registered.

        Every read/poll path goes through here so no caller can hit the
        unsupported-type error. ``plant`` opens the database the production
        tables are read from, which is the same one unless it was configured
        otherwise.
        """
        import pyodbc

        conn = pyodbc.connect(self._plant_conn_str if plant else self._conn_str)
        conn.add_output_converter(SQL_DATETIMEOFFSET, _handle_datetimeoffset)
        return conn

    def _query(self, sql: str) -> pd.DataFrame:
        """Run a SELECT and return the normalized result."""
        return self._session.query(sql)

    def _query_plant(self, sql: str) -> pd.DataFrame:
        """The same, against the database the production tables are read from."""
        return self._plant_session.query(sql)

    @staticmethod
    def _normalize(df: pd.DataFrame) -> pd.DataFrame:
        """Kept as the name the rest of this class and its tests use; the rule
        itself lives in ``sql_session`` so the stores normalize identically."""
        return _normalize_frame(df)

    # ---- QC requests (qc_requests) ------------------------------------
    def get_qc_requests(self) -> dict[str, str]:
        """``{electrode: request state}``. Read on every render.

        The other application answers these, so a decision made there has to
        reach the screen on the next poll rather than at the next restart.
        """
        return request_db_source.load_requests(self._session)

    def get_qc_request_details(self) -> dict[str, dict]:
        """``{electrode: {state, requested, coupons}}``. Read on every render.

        One read for all three, so a screen that grows another thing to show
        about a request does not grow another round trip with it.
        """
        return request_db_source.load_request_details(self._session)

    def raise_qc_request(self, electrode: str, kind: str,
                         coupons: str = "") -> bool:
        """Ask production for one electrode, saying what to cut from it."""
        return request_db_source.raise_request(
            self._session, electrode, kind,
            coupons or coupon_columns.ALL_FIVE)

    # ---- coupon settings (qc_requests) --------------------------------
    def get_coupon_settings(self) -> dict[str, str]:
        """``{reason: coupons}`` for all three. The copy read at startup."""
        return dict(self._coupons)

    def set_coupon_setting(self, reason: str, coupons: str) -> bool:
        """Record one setting and adopt it, so the next render follows it.

        Kept in step here rather than re-read: the statuses are computed from
        these, and a screen that still showed the old answer until a restart
        would make the dialog look as if it had not worked.
        """
        written = coupon_db_source.set_setting(self._session, reason, coupons)
        self._coupons = coupon_db_source.load_settings(self._session)
        return written

    def set_request_coupons(self, electrode: str, coupons: str) -> bool:
        """Say what QC wants from an electrode a technician offered."""
        return coupon_db_source.set_request_coupons(
            self._session, electrode, coupons)

    def _failure_reasons(self) -> dict[str, dict]:
        """Historic reasons, read once. ``{}`` where migration 018 is absent."""
        if self._historic_reasons is None:
            self._historic_reasons = load_failure_reasons(self._session)
        return self._historic_reasons

    def _findings_with_history(self, marking: str) -> dict:
        """The board's findings, falling back to the historic table.

        ONLY the processor is given this, and only for the failure badge. It is
        deliberately not what ``_unqueued_scraps`` reads: that decides which
        failures queue their neighbours for testing, and a reason recorded for
        an electrode judged a year ago is not a reason to queue anything now.
        """
        found = self._qc_board.findings(marking)
        if any(found.values()):
            return found
        return self._failure_reasons().get(str(marking).strip(), found)

    def _unqueued_scraps(self) -> set[str]:
        """Scraps whose neighbours QC does not want tested.

        A failure queues the electrodes around it for testing, and that queue
        is only worth making if QC wants coupons from them. An IC failure asks
        for none by default, so the run of Needs testing past its margin is not
        created at all: nothing is queued, nothing is requested, and the pull
        list does not carry an instruction nobody intends to act on.

        The margin itself is untouched. Those electrodes are condemned by the
        fault, which is a statement about what happened rather than a request
        for work.
        """
        unqueued = set()
        for marking, status in self._sweeping_decisions().items():
            if str(status) not in _SCRAPPED_VERDICTS:
                continue
            found = self._qc_board.findings(marking)
            if not coupon_columns.wanted(self._coupons_for_failure(found)):
                unqueued.add(marking)
        return unqueued

    def _coupons_for_failure(self, found: dict) -> str:
        """What the neighbours of a failure are worth, from what failed."""
        return coupon_columns.for_failure(
            str(found.get(SEM_RESULT, "")) == SEM_CRACKED,
            str(found.get(IC_RESULT, "")) == IC_FAILED,
            self._coupons)

    def approve_qc_request(self, electrode: str, expected_state: str) -> bool:
        """Accept one a technician offered."""
        return request_db_source.approve(self._session, electrode, expected_state)

    def decline_qc_request(self, electrode: str, expected_state: str) -> bool:
        """Turn down one a technician offered."""
        return request_db_source.decline(self._session, electrode, expected_state)

    def qc_request_arrived(self, electrode: str) -> bool:
        """Close an open request because the electrode is on the QC board.

        Called by the board store after every card. Production marks a request
        sent when they hand the electrode over, and that step gets forgotten:
        the request then reads as outstanding on both screens and 011 expires
        it a fortnight later as unanswerable, which is the one thing it is
        not. The card is the evidence that it was answered.
        """
        return request_db_source.arrived_at_qc(self._session, electrode)

    def raise_due_qc_requests(self) -> int:
        """Run the sampling rule. Safe to call as often as anybody likes."""
        return request_db_source.raise_due_requests(self._session)

    def raise_outstanding_requests(self) -> int:
        """Both automatic rules, for the sweep that runs them on a timer."""
        return self.raise_due_qc_requests() + self.raise_missing_fault_requests()

    def raise_missing_fault_requests(self) -> int:
        """Ask for any test-first electrode production has not been asked for.

        The ±3 around a failure are requested when that failure is finalised,
        which is the right moment because the flags are frozen by that call. It
        is also the ONLY moment, and a flag outlives it: anything finalised
        before this feature existed, or while the request schema was out of
        reach, keeps its flag and is never asked for. Five electrodes sat like
        that, visible in the pull list and invisible to production.

        IN PYTHON, NOT IN SQL, unlike the sampling rule. The set is what the
        pull list shows, and that is frozen flags minus the ones a hand edit
        superseded minus the ones whose inference went stale, and staleness is
        decided against the vat runs. Writing that a second time in SQL is how
        the two halves of the unfreeze came to disagree; one of them is enough.
        The cost is that it only runs while QC has the app open, which is also
        true of the finalise that should have raised them.
        """
        outstanding = self._settled_priorities()
        if not outstanding:
            return 0
        # One read rather than a procedure call per flag every minute: in the
        # steady state everything here already has a request and nothing is sent.
        try:
            asked = set(self.get_qc_requests())
        except Exception as e:
            print(f"Could not check which electrodes already have a request: {e}")
            return 0
        return self._request_fault_tests(outstanding - asked)

    # ---- sampling intervals (qc_requests) -----------------------------
    def get_sampling_intervals(self) -> dict[str, int]:
        """``{vat: electrodes between samples}``.

        Read on every render rather than cached: the other application reads
        the same table to decide what is due, so a figure changed on another
        machine has to show up here without a restart.
        """
        return load_intervals(self._session)

    def set_sampling_interval(self, vat: str, electrodes_between: int) -> bool:
        """Record one vat's interval. False when it could not be written."""
        return set_interval(self._session, vat, electrodes_between)

    def get_sampling_switch(self) -> bool | None:
        """Is automatic sampling on? None where there is no switch to read.

        None is not off. It means this database has not had migration 016, so
        the tab hides the control rather than offering one that writes nowhere.
        """
        return load_switch(self._session)

    def set_sampling_switch(self, on: bool, start_now: bool = False) -> bool:
        """Throw the switch for the whole line. False if it did not take.

        ``start_now`` is read only on the way on: True asks for the next
        electrode each vat makes, False counts a full interval from where the
        vat stands now. See 016.
        """
        return set_switch(self._session, on, start_now)

    # ---- production metadata (dbo.MetaData) ---------------------------
    def get_metadata(self) -> pd.DataFrame:
        """Every B-marked electrode, highest marking first.

        Sorted by the marking's number rather than by [Id]: rows don't always
        reach the database in marking order (B13904 lands before B13903), and
        an out-of-sequence row makes the ±2 margin and the sweeps look wrong
        even when they're right — they work on the marking sequence, not on
        insertion order. [Id] only breaks ties.

        The ordering contract: if writes are ever added here, the row-index → id
        lookup must use this same ORDER BY or an edit would hit the wrong row.
        """
        columns = ", ".join(f"[{c}]" for c in METADATA_COLUMNS)
        self._metadata_cache = self._query_plant(
            f"SELECT {columns} FROM [{METADATA_SCHEMA}].[{METADATA_TABLE}] "
            f"WHERE {METADATA_FILTER} "
            f"ORDER BY {MARKING_NUMBER_SQL} DESC, [{METADATA_KEY}] DESC")
        return self._electrodes.process(self._metadata_cache)

    def load_vats(self) -> dict[str, str]:
        """Read the vat of every electrode, and adopt it. Safe off the UI thread.

        Empty when the process log is out of reach, which is what a database
        holding only a MetaData snapshot looks like. The Vat column shows as
        loading until this answers, then is dropped if nothing came back.
        """
        try:
            self._vats = load_vat_by_marking(self._plant_session)
        finally:
            self._vats_pending = False
        return self._vats

    def database_name(self) -> str:
        """The database the app writes to. The plant tables may be elsewhere."""
        return self._database

    def plant_database_name(self) -> str:
        """Where dbo.MetaData and dbo.ProcessData are read from.

        Not always the same as ``database_name``: the app writes its own schema
        to one database and reads the electrode list from the production one.
        """
        return self._plant_database

    def is_test_database(self) -> bool:
        """Whether the writes are going somewhere other than production.

        False when no production database is configured: a warning that cannot
        tell the two apart would cry wolf on the real one.
        """
        production = app_config.production_database()
        return bool(production) and self._database.lower() != production.lower()

    def vats_pending(self) -> bool:
        """Whether the vat join is still on its way."""
        return self._vats_pending

    def reprocess_metadata(self) -> pd.DataFrame:
        """Re-run the processor over the last read — no database round trip.

        A QC-board move only changes the status override, so re-querying ~12k
        rows to see it would be a second of latency for nothing.
        """
        if self._metadata_cache is None:
            return self.get_metadata()
        return self._electrodes.process(self._metadata_cache)

    def get_qc_board_store(self) -> QcBoardDbStore:
        """The QC board the GUI mutates; shared with the status override."""
        return self._qc_board

    # ---- SEM EDX ------------------------------------------------------
    def get_sem_edx_generations(self) -> list[str]:
        """The generations there is data for, oldest first, one tab each."""
        return sem_db.load_generations(self._session) or [DEFAULT_GENERATION]

    def get_sem_edx_types(self, generation: str = DEFAULT_GENERATION) -> list[str]:
        """The electrode types one generation's layout describes."""
        return list(self._layout_of(generation))

    def get_sem_edx_layout(self, generation: str = DEFAULT_GENERATION
                           ) -> dict[str, list[str]]:
        """Which quantities each electrode type holds, in the sheet's order.

        Gen 3.1 measures Ni and S; Gen 4.1 adds Co, and C and N on its cathodes.
        Two generations, two sets of columns, from one piece of code.
        """
        return {key: list(value) for key, value in self._layout_of(generation).items()}

    def get_sem_edx(self, generation: str, electrode_type: str) -> pd.DataFrame:
        """One row per SEM sample of one electrode type of one generation.

        Read from the CSV rather than the database, and cached in its raw form
        so a re-process after an import needs no file read.
        """
        return self._sem_edx_processor(generation, electrode_type).process(
            self._sem_edx_rows(generation, electrode_type))

    def get_all_sem_edx(self) -> dict[str, dict[str, pd.DataFrame]]:
        """Every sheet, as ``{generation: {electrode type: frame}}``.

        One call so the whole tab can be filled from a single deferred job, and
        the same shape the SEM poll already answers in.
        """
        return {generation: {electrode_type: self.get_sem_edx(generation, electrode_type)
                             for electrode_type in self.get_sem_edx_types(generation)}
                for generation in self.get_sem_edx_generations()}

    def get_sem_edx_measurements(self, generation: str, electrode_type: str,
                                 electrode: str, position: str) -> pd.DataFrame:
        """The individual measurements behind one sample, for the detail view."""
        return self._sem_edx_processor(generation, electrode_type).measurements(
            self._sem_edx_rows(generation, electrode_type), electrode, position)

    def _layout_of(self, generation: str) -> dict[str, list[str]]:
        """One generation's layout, read once from config.json.

        Which quantities a sheet shows is presentation, not measurement, so it
        is configuration rather than a table: no query can derive it, since
        five elements give ten possible ratios and a sheet shows five of them.
        """
        key = str(generation).strip()
        if key not in self._sem_edx_layouts:
            self._sem_edx_layouts[key] = app_config.sem_edx_layout(key)
        return self._sem_edx_layouts[key]

    def _sem_edx_processor(self, generation: str,
                           electrode_type: str) -> SemEdxProcessor:
        """One processor per generation and type, built from that sheet's layout."""
        key = (str(generation).strip(), str(electrode_type).strip().upper())
        if key not in self._sem_edx_processors:
            self._sem_edx_processors[key] = SemEdxProcessor(
                self._layout_of(key[0]).get(key[1]))
        return self._sem_edx_processors[key]

    def _sem_edx_rows(self, generation: str, electrode_type: str) -> pd.DataFrame:
        """The measurements of one electrode type, in stored order."""
        df = self._sem_edx_measurements(generation)
        if df.empty:
            return df
        column = str(SemEdxColumns.ELECTRODE_TYPE)
        match = df[column].astype(str).str.strip().str.upper() == str(
            electrode_type).strip().upper()
        return df[match]

    def import_sem_edx_folder(self, generation: str, folder, ask_type=None,
                              pick_images=None, confirm=None, positions=None
                              ) -> tuple[ParsedFolder, list[str]]:
        """Read a result folder into the database.

        Which tab a sample lands on is decided by its electrode type, looked up
        in dbo.MetaData from the marking, so a cathode folder appears under
        Cathodes and an anode under Anodes with nobody choosing.

        Args:
            generation: Which generation the samples belong to.
            folder: The result folder, named for the electrode.
            ask_type: ``(electrode, choices) -> str | None``, called only when
                the database has never heard of the marking. Kept as a callable
                so this layer stays Qt-free, the same way the driver install
                prompt works. Returning None leaves the sample untyped, and it
                is reported rather than filed somewhere arbitrary.
            confirm: ``(result, already_present) -> dict | None``, asked once
                per folder immediately before anything is written. The dict is
                the by-hand fields to stamp on every sample it adds; None
                refuses the folder, and then nothing is written at all.
            pick_images: ``(electrode, position, candidates, suggested) -> list``,
                which captures in the folder belong to each sample. Also a
                callable for the same reason. Returning None keeps the
                suggestion.
            positions: Only these spots, when a folder holds several and one is
                wanted on its own. None takes whatever the folder holds.

        Returns:
            The parse result and the samples skipped as already present. A
            sample is identified by its electrode *and* position: one electrode
            can be mapped at up to five positions, so the marking alone would
            refuse legitimate imports.
        """
        expected = {symbol for layout in self._layout_of(generation).values()
                    for quantity in quantities(layout)
                    for symbol in quantity.elements}
        result = parse_result_folder(folder, sorted(expected))
        # One folder can hold five spots off one electrode, and the picker
        # offers them separately, so an import can be asked for just one.
        if positions:
            wanted = {base_position(p) for p in positions}
            result.measurements = [m for m in result.measurements
                                   if base_position(m.position) in wanted]
        if not result.measurements:
            return result, []

        existing = self._sem_edx_measurements(generation)
        electrode_column, position_column = SAMPLE_KEY
        known = set(zip(existing[electrode_column].astype(str).str.strip(),
                        existing[position_column].astype(str).str.strip()))

        # Resolved once per electrode, so the operator is asked at most once for
        # a folder rather than once per measurement.
        types = self._resolve_types(
            {m.electrode for m in result.measurements}, result, ask_type,
            generation)
        result.electrode_types = dict(types)

        # Asked once per sample, not once per measurement: the three points of
        # a sample share their captures.
        images = self._resolve_images(result, known, pick_images)

        fresh, skipped = [], []
        for measurement in result.measurements:
            key = (measurement.electrode, measurement.position)
            if key in known:
                if key not in skipped:
                    skipped.append(key)
                continue
            fresh.append(measurement)

        # Nothing is written until the folder can be stored in a form everybody
        # can open. A personal path would look like a successful import and then
        # show captures to one machine.
        folder = portable_folder(result.folder, self._ask_image_root) if fresh else ""
        if fresh and not folder:
            result.problems.append(
                f"{result.folder.name} is not inside the shared SEM images "
                f"library, or the library could not be found on this machine, "
                f"so nothing was imported. Import it from the synced library, "
                f"or point the app at the library when it asks.")
            return result, [f"{e} {p}" for e, p in skipped]

        if fresh:
            # NOTHING IS WRITTEN UNTIL THIS COMES BACK. The by-hand fields are
            # asked for before the write rather than patched on after it, so a
            # sample is never on screen missing them, and a refusal leaves the
            # folder unimported instead of filed without an operator.
            values = ({} if not callable(confirm)
                      else confirm(result, [f"{e} {p}" for e, p in skipped]))
            if values is None:
                return result, [f"{e} {p}" for e, p in skipped]
            self._store_sem_edx(generation, folder, result, fresh, types, images)
            if values:
                for electrode, position in dict.fromkeys(
                        (m.electrode, m.position) for m in fresh):
                    self.update_sem_edx_sample(
                        generation, electrode, position, values)
        return result, [f"{e} {p}" for e, p in skipped]

    def _store_sem_edx(self, generation: str, folder: str, result: ParsedFolder,
                       fresh: list, types: dict[str, str],
                       images: dict[tuple[str, str], list[str]]) -> None:
        """Write the newly parsed samples.

        One call per electrode rather than per measurement: a folder can name
        more than one, and each is its own row in sem_electrode.
        """
        for electrode in dict.fromkeys(m.electrode for m in fresh):
            electrode_type = types.get(electrode, "")
            if not electrode_type:
                continue        # already reported by _resolve_types
            sem_db.import_measurements(
                self._session, generation, electrode, electrode_type, folder,
                [m for m in fresh if m.electrode == electrode], images)
        # Re-read rather than echo: the database fills in what it normalises.
        self._sem_edx_caches.pop(generation, None)

    def sem_edx_elements(self, generation: str, electrode_type: str) -> list[str]:
        """Which elements this generation measures on this electrode type.

        The layout's quantities minus the ratios: a ratio is arithmetic over two
        elements, never typed in, which is why the sample window will not let
        one be edited either.
        """
        quantities = self._layout_of(generation).get(
            str(electrode_type).strip().upper(), [])
        return [q for q in quantities if RATIO_SEPARATOR not in str(q)]

    def add_sem_edx_sample(self, generation: str, electrode: str, position: str,
                           readings: dict, electrode_type: str = "",
                           zoom: str = "", date: str = "", folder: str = "",
                           images=None) -> str:
        """Record one sample that was typed in rather than parsed.

        For a result folder the parser could make nothing of: the numbers are on
        screen in front of the operator either way, and refusing them because a
        Word report is malformed loses the measurement, not just the file.

        Args:
            readings: ``{point: {'Ni': 24.5, ...}}``, the points as typed. A
                blank stays out of the dict and is stored as missing rather
                than as a zero.
            electrode_type: Looked up from the marking when not given, the same
                way an import decides which tab a sample lands on.

        Returns the electrode type it was filed under. Raises if that cannot be
        settled, since a sample with no type shows on no tab at all.
        """
        electrode = normalize_marking(electrode)
        position = str(position).strip()
        if not electrode or not position:
            raise ValueError("An electrode and a position are both needed.")

        electrode_type = (str(electrode_type).strip().upper()
                          or self.get_electrode_type(electrode).upper())
        if electrode_type not in self._layout_of(generation):
            raise ValueError(
                f"{electrode} is not a type {generation} has a tab for "
                f"({electrode_type or 'unknown'}), so the sample would show "
                f"nowhere.")

        measurements = [
            ParsedMeasurement(
                electrode=electrode, position=position, point=int(point),
                elements={str(symbol): float(value)
                          for symbol, value in (values or {}).items()
                          if value is not None},
                date=str(date or ""), zoom=str(zoom or ""),
                source="typed in by hand")
            for point, values in sorted((readings or {}).items())]
        if not measurements:
            raise ValueError("No measurement points were given.")

        # Shortened against the synced library root, exactly as an import
        # does it: an absolute path starts with one operator's user folder and
        # would open for nobody else.
        stored = portable_folder(folder, self._ask_image_root) if folder else ""
        sem_db.import_measurements(
            self._session, generation, electrode, electrode_type,
            stored, measurements,
            {(electrode, position): list(images or [])})
        # Re-read rather than echo: the database fills in what it normalises.
        self._sem_edx_caches.pop(generation, None)
        return electrode_type

    def update_sem_edx_sample(self, generation: str, electrode: str,
                              position: str, values: dict[str, str]) -> bool:
        """Set the by-hand fields on every measurement of one sample.

        The zoom, operator and note belong to the sample rather than to any one
        point, so all of its rows carry the same value.
        """
        df = self._sem_edx_measurements(generation)
        electrode_column, position_column = SAMPLE_KEY
        match = ((df[electrode_column].astype(str).str.strip() == str(electrode).strip())
                 & (df[position_column].astype(str).str.strip() == str(position).strip()))
        if not match.any():
            return False

        for column, value in values.items():
            if column in df.columns:
                df.loc[match, column] = value

        # The procedure writes all three fields, so a caller setting one has to
        # send the other two back as they are. Passing only the operator used to
        # blank the zoom the import had just read off the image sidecars.
        current = df.loc[match].iloc[0]

        def field(column: str) -> str:
            column = str(column)
            if column in values:
                return str(values.get(column) or "")
            return "" if column not in df.columns else str(current.get(column) or "")

        sem_db.update_sample(
            self._session, generation, electrode, position,
            zoom=field(SemEdxColumns.ZOOM),
            operator=field(SemEdxColumns.OPERATOR),
            note=field(SemEdxColumns.NOTE))
        self._sem_edx_caches[generation] = df
        return True

    def delete_sem_edx_sample(self, generation: str, electrode: str,
                              position: str) -> bool:
        """Remove one sample and every measurement taken at it.

        False when there was no such sample. Only what was read off the
        captures goes: the captures themselves live in the shared library and
        are left alone.
        """
        df = self._sem_edx_measurements(generation)
        electrode_column, position_column = SAMPLE_KEY
        match = ((df[electrode_column].astype(str).str.strip() == str(electrode).strip())
                 & (df[position_column].astype(str).str.strip() == str(position).strip()))
        if not match.any():
            return False

        if not sem_db.delete_sample(self._session, generation, electrode,
                                    position):
            return False
        self._sem_edx_caches[generation] = df[~match].reset_index(drop=True)
        return True

    def update_sem_edx_measurements(self, generation: str, electrode: str,
                                    position: str,
                                    values: dict[int, dict[str, float | None]]) -> bool:
        """Correct the measured percentages of individual points of one sample.

        Args:
            values: ``{point: {element column: value or None}}``. None means a
                reading that was never taken, which is stored as missing rather
                than as a zero.

        Only the percentages are written. Every ratio and average is arithmetic
        over them, so correcting one figure corrects everything derived from
        it, which is why the ratios are not editable in the first place.
        """
        df = self._sem_edx_measurements(generation)
        electrode_column, position_column = SAMPLE_KEY
        sample = ((df[electrode_column].astype(str).str.strip() == str(electrode).strip())
                  & (df[position_column].astype(str).str.strip() == str(position).strip()))
        if not sample.any():
            return False

        point_column = str(SemEdxColumns.MEASUREMENT)
        changed = False
        for point, readings in (values or {}).items():
            match = sample & (pd.to_numeric(df[point_column], errors="coerce") == point)
            if not match.any():
                continue
            for column, value in readings.items():
                if column not in df.columns:
                    continue
                df.loc[match, column] = value
                changed = True
                sem_db.set_reading(self._session, generation, electrode,
                                   position, point, element_symbol(column),
                                   value)

        if changed:
            self._sem_edx_caches[generation] = df
        return changed

    def sem_edx_import_candidates(self, generation: str) -> list[dict]:
        """Result folders for one generation the database has nothing from.

        GENEROUS ON PURPOSE. A folder it cannot match is listed rather than
        hidden: importing one twice writes nothing, because every measurement
        already on record is skipped, while hiding one loses the capture.
        """
        folders = sem_edx_library.generation_folders(generation, self._ask_image_root)
        if not folders:
            return []

        recorded = self._recorded_samples(generation)
        markings = {_marking_key(e) for e, _ in recorded}
        # Anode or cathode, from dbo.MetaData. Built once rather than asked per
        # folder: get_electrode_type rescans the whole cache each call, which
        # over six hundred folders is a scan too many.
        types = self._types_by_marking()

        candidates = []
        for path in folders:
            # Only the spots the plate is really mapped at, plus any odd
            # spelling this generation already uses. Everything else is a
            # filename quirk, and splitting a folder on those would offer
            # "Site" and "EDX" as if they were places on an electrode.
            spots = [(electrode, position) for electrode, position in sample_locations(path)
                     if is_known_position(position)
                     or (normalize_marking(electrode), base_position(position)) in recorded]
            if not spots:
                # No readable report, so there is no spot to compare. Offered
                # once, unless this electrode already has something on record.
                if _marking_key(path.name) in markings:
                    continue
                candidates.append({
                    "name": path.name, "path": str(path), "position": "",
                    "type": types.get(normalize_marking(path.name), "")})
                continue
            for electrode, position in spots:
                if (normalize_marking(electrode), base_position(position)) in recorded:
                    continue
                candidates.append({
                    "name": path.name, "path": str(path), "position": position,
                    # Blank when the electrode list has never heard of it,
                    # which is normal for the older Gen 3.1 names.
                    "type": types.get(normalize_marking(electrode or path.name), "")})

        # Newest first: the folder somebody just captured is the one they are
        # here for, and it would otherwise be at the bottom of six hundred.
        # Anything with no number in its name sorts last rather than first.
        candidates.sort(key=lambda c: (electrode_number(c["name"]) is None,
                                       -(electrode_number(c["name"]) or 0),
                                       c["name"].lower()))
        return candidates

    def sem_images_available(self, folder) -> bool:
        """Whether a stored folder can be found on this machine.

        Asked once per row while a table is drawn, so it must stay cheap: it is
        a path join and one directory check, about 0.016ms. Deliberately does
        NOT offer the folder picker, since a table being painted is no place to
        interrupt somebody with a dialog.
        """
        return bool(folder) and resolve_folder(folder) is not None

    def list_sem_edx_images(self, folder, position: str = "", images: str = "") -> list:
        """The captures of one sample, for the image viewer.

        The stored folder is a tail under the shared library, so it is resolved
        against this machine's root before anything is read. The recorded
        filenames decide what belongs to the sample; the position is the
        fallback for rows stored before that column existed.
        """
        resolved = resolve_folder(folder, self._ask_image_root)
        if resolved is None:
            return []
        return result_images(resolved, position=position,
                             names=split_images(images))

    def get_electrode_type(self, marking: str) -> str:
        """CATHODE / ANODE for one marking, from dbo.MetaData. '' if unknown.

        Lets an imported sample carry its type without anyone typing it: the
        result folder names the electrode, and the database already knows what
        that electrode is. Recipe variants are folded onto the type they are a
        variant of, since the SEM tab has a tab per type and not per recipe.
        """
        if self._metadata_cache is None or self._metadata_cache.empty:
            return ""
        # Compared through the normaliser, since Gen 3.1 pads its markings and
        # dbo.MetaData does not: B000897 has to find B897.
        markings = self._metadata_cache[str(ElectrodeColumns.MARKING)].astype(
            str).map(normalize_marking)
        match = self._metadata_cache[markings == normalize_marking(marking)]
        if match.empty:
            return ""
        return canonical_electrode_type(
            match.iloc[0][str(ElectrodeColumns.FULL_NAME)])

    def _recorded_samples(self, generation: str) -> set[tuple[str, str]]:
        """Every ``(electrode, spot)`` this generation already has readings for.

        Per spot rather than per electrode, because five spots off one
        electrode are five separate imports.
        """
        df = self._sem_edx_measurements(generation)
        electrode_column, position_column = SAMPLE_KEY
        if df.empty or electrode_column not in df or position_column not in df:
            return set()
        return {(normalize_marking(str(electrode)), base_position(str(position)))
                for electrode, position
                in zip(df[electrode_column], df[position_column])}

    def _types_by_marking(self) -> dict[str, str]:
        """``{normalised marking: 'ANODE' | 'CATHODE' | ...}`` from dbo.MetaData.

        Empty before the electrode list has been read, which leaves the type
        blank rather than wrong.
        """
        cache = self._metadata_cache
        if cache is None or cache.empty:
            return {}
        marking_column = str(ElectrodeColumns.MARKING)
        name_column = str(ElectrodeColumns.FULL_NAME)
        if marking_column not in cache or name_column not in cache:
            return {}
        markings = cache[marking_column].astype(str).map(normalize_marking)
        names = cache[name_column].map(canonical_electrode_type)
        return {marking: name for marking, name in zip(markings, names) if marking}

    def _resolve_types(self, electrodes: set[str], result: ParsedFolder,
                       ask_type, generation: str = DEFAULT_GENERATION
                       ) -> dict[str, str]:
        """The electrode type of each marking, asking only when the DB is silent.

        A sample whose type ends up empty would sit in the file showing on no
        tab at all, so every case that leads there is reported.
        """
        choices = list(self._layout_of(generation))
        resolved: dict[str, str] = {}

        for electrode in sorted(electrodes):
            electrode_type = self.get_electrode_type(electrode).upper()
            if electrode_type and electrode_type not in choices:
                result.problems.append(
                    f"{electrode} is recorded as a {electrode_type}, and there "
                    f"is no tab for that, so it will not appear anywhere.")
            elif not electrode_type:
                electrode_type = str(
                    ask_type(electrode, choices) if callable(ask_type) else "").upper()
                if not electrode_type:
                    result.problems.append(
                        f"{electrode} is not in the database and no type was "
                        f"chosen, so it will not appear on either tab.")
            resolved[electrode] = electrode_type
        return resolved

    def _resolve_images(self, result: ParsedFolder, known: set,
                        pick_images) -> dict[tuple[str, str], list[str]]:
        """Which captures belong to each sample being imported.

        The folder holds every position mapped on the electrode. What the
        filenames say about position is the suggestion; the operator has the
        last word, since a filename is not always right about which spot a
        capture came from.

        WHAT IS OFFERED IS SCOPED BY FOLDER, though. Where a plate's spots are
        filed in a subfolder each, only that spot's folder is shown: the
        filenames may be wrong about which spot, but the folder they were put
        in rarely is, and offering all fifteen made the choice harder rather
        than freer. Where the captures all share one folder, that is still
        everything, because then the filename is the only thing separating
        them and second-guessing it is the operator's job.
        """
        candidates = candidate_images(result.folder)
        if not candidates:
            return {}

        chosen: dict[tuple[str, str], list[str]] = {}
        samples = [(m.electrode, m.position) for m in result.measurements]
        for key in dict.fromkeys(s for s in samples if s not in known):
            electrode, position = key
            matched = result_images(result.folder, electrode, position)
            suggested = [p.name for p in matched]
            homes = {p.parent for p in matched}
            offered = ([p for p in candidates if p.parent in homes] if homes
                       else list(candidates))
            fallback = [p.name for p in offered]
            picked = None
            if callable(pick_images):
                picked = pick_images(electrode, position, offered,
                                     suggested or fallback)
            if picked is None:
                picked = suggested or fallback
            chosen[key] = list(picked)
        return chosen

    def _sem_edx_measurements(self, generation: str) -> pd.DataFrame:
        """One generation's rows, read once and kept until something writes."""
        key = str(generation).strip()
        if key not in self._sem_edx_caches:
            self._sem_edx_caches[key] = sem_db.load_sem_edx(self._session, key)
        return self._sem_edx_caches[key]

    def _superseded_by_hand(self) -> set[str]:
        """Electrodes whose frozen verdict a later hand correction replaced.

        Both cover the same electrode and both are settled, so the later
        statement is the one that stands: a frozen verdict beats an older
        correction, a correction beats the verdict it was written to fix.
        Without this the archive silently won every time and a by-hand edit on
        a finalised electrode looked like it did nothing.

        Both timestamps come back through the same normalizer, in UTC, so they
        compare as text.
        """
        if not self._manual_edits:
            return set()
        return {marking for marking, frozen_at in self._qc_board.archived_times().items()
                if self._manual_edits.get(marking, "") > frozen_at}

    def _settled_statuses(self) -> dict[str, str]:
        """Statuses that no longer depend on the propagation rules.

        The finalised sweeps, except where the operator corrected that
        electrode by hand afterwards, or where the verdict they were drawn from
        has since been replaced by a re-test.
        """
        superseded = self._superseded_by_hand() | self._qc_board.stale_inferences()
        return {marking: status
                for marking, status in self._qc_board.archived_statuses().items()
                if marking not in superseded}

    def _settled_verdicts(self) -> dict[str, str]:
        """Finalised verdicts, offered as decisions so their sweeps re-run.

        Freezing the sweep's RESULT was not enough. Re-testing an electrode
        writes a new verdict, and the neighbours the old one condemned are
        dropped as stale, but nothing then reclaims them: only a decision
        sweeps, and a finalised verdict was not one. So the electrodes around a
        re-tested one fell back to Not Tested instead of following the new
        result. A verdict is a verdict whether it was frozen or not.
        """
        superseded = self._superseded_by_hand()
        return {marking: status
                for marking, status in self._qc_board.archived_verdicts().items()
                if marking not in superseded}

    def _sweeping_decisions(self) -> dict[str, str]:
        """Everything that sweeps: the finalised verdicts and the live board.

        The board wins where both name an electrode, since it is the newer of
        the two by definition. NOT the same as what locks a row: see
        ``board_provider``, which is the live half of this on its own.
        """
        return {**self._settled_verdicts(), **self._qc_board.status_overrides()}

    def _finalised_times(self) -> dict[str, str]:
        """``{marking: when its status settled}``, for the Status finalised column.

        Two clocks, the later one wins: a verdict frozen by "mark all as done"
        and a correction made by hand here. An electrode back on the board is
        left out, since its status is being revisited and no longer settled.
        """
        stale = self._qc_board.stale_inferences()
        live = self._qc_board.on_board()
        # settled_times, not archived_times: a stretch that was unfrozen by a
        # test and released again settled when it came back, not when the sweep
        # that test interrupted was written down.
        times = {marking: when
                 for marking, when in self._qc_board.settled_times().items()
                 if marking not in stale}
        for marking, when in self._manual_edits.items():
            if when > times.get(marking, ""):
                times[marking] = when
        return {marking: when for marking, when in times.items()
                if when and marking not in live}

    def _settled_priorities(self) -> set[str]:
        """Frozen test-first flags, minus the superseded ones.

        The flag travels with the verdict that produced it. Replacing that
        verdict by hand and leaving "Test first" stuck beside the new status
        tells an operator to go and test an electrode on the strength of a
        decision that no longer applies to it.
        """
        return (self._qc_board.archived_priorities()
                - self._superseded_by_hand()
                - self._qc_board.stale_inferences())

    def _known_markings(self) -> list[str]:
        """Every electrode the app knows about, from the last read."""
        if self._metadata_cache is None:
            self.get_metadata()
        if self._metadata_cache is None or self._metadata_cache.empty:
            return []
        column = str(ElectrodeColumns.MARKING)
        return list(self._metadata_cache[column].astype(str).str.strip())

    def preview_finalise(self, marking: str | None = None) -> tuple[int, int]:
        """``(decided electrodes, electrodes that would be frozen)``.

        Pass a marking to preview finalising just that one.
        """
        frozen, _priorities = self._finalise_plan(marking)
        decided = self._decisions_for(marking)
        return len(decided), len(frozen)

    def finalise_qc_decisions(self, marking: str | None = None) -> int:
        """Freeze decided electrodes and everything their sweeps reached.

        Without a marking this takes every decided electrode. With one it takes
        that electrode alone, leaving the rest of the board live. Afterwards
        those statuses no longer move when other decisions change around them.
        """
        frozen, priorities = self._finalise_plan(marking)
        if not frozen:
            return 0
        consumed = {marking} if marking else None
        count = self._qc_board.archive(frozen, priorities, consumed)
        # The plan's own record of what caused each flag, rather than the board
        # read back afterwards: the archive has just moved those rows about, and
        # a lookup that missed fell back to the stricter of the two settings and
        # asked for an electrode the rule says not to ask for.
        self._request_fault_tests(priorities, frozen)
        return count

    def _request_fault_tests(self, priorities: set[str],
                             frozen: dict | None = None) -> int:
        """Ask production for the electrodes a failure says to test next.

        The test-first set IS the answer: the propagation already works out
        which electrodes sit at the edge of a fault zone and would settle it,
        which is the plus and minus three around a failure. Raising a request
        for each of them is what turns that instruction into something
        production can act on, rather than a flag somebody has to notice.

        AFTER THE ARCHIVE, not before. The flags are frozen by that call, so a
        request raised first could name an electrode the finalise then decides
        differently about.

        Three things are skipped. One already on the board is being tested, so
        asking for it again is noise. One that already has a request keeps it,
        which the procedure enforces rather than this: one row per electrode,
        ever. And one with a verdict of its own was never in the test-first
        set to begin with.

        Failures here are printed and swallowed. The finalise has already
        happened and is what the operator was waiting for; a request that
        could not be raised is worth saying out loud but not worth undoing a
        board full of decisions over.

        WHAT TO CUT COMES FROM THE FAILURE THAT CAUSED THE FLAG, not from a
        setting read here: a SEM failure and an IC failure are answered
        differently, and these electrodes are around one or the other.
        """
        if not priorities:
            return 0
        on_board = self._qc_board.on_board()
        raised = 0
        for marking in sorted(priorities):
            if marking in on_board:
                continue
            coupons = self._coupons_for_priority(marking, frozen)
            try:
                if request_db_source.raise_request(self._session, marking,
                                                   KIND_FAULT, coupons):
                    raised += 1
            except Exception as e:
                print(f"Could not request {marking} after the failure: {e}")
        return raised

    def _coupons_for_priority(self, marking: str,
                              frozen: dict | None = None) -> str:
        """What to cut from one test-first electrode, from the failure behind it.

        The verdict that flagged it is what says which rule applies. ``frozen``
        is the finalise's own ``{marking: (status, caused_by)}``, consulted
        first because it is the plan that produced this flag; the board is only
        asked for a flag that did not come from one.

        Where the cause cannot be worked out at all, the strictest of the two
        failure settings is used rather than none: a request that asks for too
        much wastes a coupon, one that asks for too little wastes the trip.
        """
        source = ""
        if frozen and marking in frozen:
            source = str(frozen[marking][1] or "")
        source = source or self._qc_board.caused_by(marking)
        if not source:
            return coupon_columns.strictest(
                self._coupons.get(coupon_columns.REASON_SEM),
                self._coupons.get(coupon_columns.REASON_IC))
        return self._coupons_for_failure(self._qc_board.findings(source))

    def _decisions_for(self, marking: str | None) -> dict[str, str]:
        """Every board decision, or just the one named."""
        decisions = self._qc_board.status_overrides()
        if marking is None:
            return decisions
        return {marking: decisions[marking]} if marking in decisions else {}

    def _finalise_plan(self, marking: str | None = None
                       ) -> tuple[dict[str, tuple[str, str]], set[str]]:
        """What a finalise would freeze: ``{marking: (status, caused_by)}``.

        The electrodes a decision swept onto are only recorded when the status
        actually differs from the settled base: one already reading Released for
        another reason is not re-recorded.

        THE DECIDED ELECTRODES ARE ALWAYS RECORDED, even when their status does
        not move. Finalising is what takes a card off the board, and the archive
        is what consumes it. Skipping an electrode that already read Released
        left "mark all as done" doing nothing at all, with the card stuck in
        Release for good, which is what an operator sees as the button being
        broken.
        """
        decisions = self._decisions_for(marking)
        if not decisions:
            return {}, set()
        markings = self._known_markings()
        if not markings:
            return {}, set()

        base = self._electrodes.base_status()
        # The same inputs the tab renders with. A finalise that swept differently
        # would freeze statuses nobody was shown.
        # THE REOPENED VERDICTS SWEEP TOO, not only the cards being finalised.
        # A card on the board holds the whole stretch around it, and the far
        # half of that stretch is held by an older verdict rather than by this
        # card. Without that verdict sweeping here, half the stretch was given
        # the new date and half kept the date of the sweep the card
        # interrupted. Only the verdicts whose stretches are actually reopened:
        # every verdict ever recorded would re-sweep the whole table and
        # rewrite rows that have nothing to do with the card in hand.
        reopened = self._qc_board.reopened_sources()
        settled = self._settled_verdicts()
        sweeping = {**{m: s for m, s in settled.items() if m in reopened},
                    **decisions}
        # THE SAME ARGUMENTS THE SCREEN IS COMPUTED FROM, unqueued included.
        # This is a second call to the propagation, and what it works out is
        # what gets frozen and requested. Left without the unqueued set it
        # disagreed with the Electrodes tab: a failure QC wants no coupons from
        # showed no zone on screen and still froze one here, then asked
        # production for the electrodes in it.
        effective, sources, priorities, _causes = propagate_detailed(
            markings, base, sweeping, vats=self._vats,
            pinned=set(self._manual_edits),
            unqueued=self._unqueued_scraps())
        # What another card still holds is not this finalise's to freeze. Those
        # stretches are unfrozen because of a card that is staying on the board,
        # and freezing them here would stamp them with somebody else's time.
        held = self._qc_board.stale_inferences(
            board=self._qc_board.on_board() - set(decisions))
        frozen = self._to_freeze(effective, base, decisions, held, sources)
        return frozen, {m for m in priorities if m in frozen}

    @staticmethod
    def _to_freeze(effective: dict[str, str], base: dict[str, str],
                   decisions: dict[str, str], held: set[str],
                   sources: dict[str, str]) -> dict[str, tuple[str, str]]:
        """Which electrodes a finalise records, and what each was drawn from.

        THE DECIDED ELECTRODES ARE ALWAYS RECORDED, before any other test and
        whatever else is true of them. Finalising is what takes a card off the
        board and the archive is what consumes it, so an empty plan leaves the
        card where it is and the button looks broken. ``held`` cost us this
        once: a card whose own frozen row happened to be out of date filtered
        itself out of its own finalise.

        Everything else is recorded only where it moved, and one already
        reading Released for another reason is not re-recorded.

        ``held`` leaves alone what another card on the board is holding, so one
        operator's finalise does not stamp somebody else's open work. It never
        applies to what THESE decisions caused: a card's own sweep is the card's
        to freeze. Leaving those out cost us the scrapped electrodes beside a
        failure, which kept an older row that a card elsewhere then held, so
        they showed no date and locked to an electrode that cannot move them.
        """
        not_tested = str(ElectrodeStatus.NOT_TESTED)
        return {
            marking: (status, sources.get(marking, marking))
            for marking, status in effective.items()
            if marking in decisions
            or (status != base.get(marking, not_tested)
                and (sources.get(marking) in decisions or marking not in held))
        }

    def undo_last_action(self) -> bool:
        """Reverse the most recent board change, whatever kind it was."""
        return self._qc_board.undo()

    def set_electrode_status(self, marking: str, status: str) -> None:
        """Set one electrode's status by hand. Never propagates.

        There is no way to clear one: the value underneath was overwritten, and
        a status that came from the tested list cannot be worked out again from
        the database. To change a hand set status you set another one.

        The in-memory base is patched too, so the tab shows it immediately
        rather than at the next restart.
        """
        self._qc_board.set_electrode_status(marking, status)
        self._electrodes.set_base_status(marking, status)
        # Stamped the way the database would: this correction is newer than any
        # verdict already frozen for it, so it is the one the tab shows.
        self._manual_edits[str(marking).strip()] = dt.datetime.now(
            dt.timezone.utc).replace(tzinfo=None).isoformat(sep=" ")

    def get_metadata_update_callback(self):
        return self._metadata_poller.make_update_callback(
            _METADATA_ENTITY, self._reload_electrodes, "data")

    def _metadata_watermarks(self) -> dict:
        """Everything the Electrodes tab's Status column depends on.

        Three sources, because a status is not just a stored value:

            dbo.MetaData        which electrodes exist at all
            electrode_status    what the ranges and the by-hand edits say
            qc_board            live decisions, which SWEEP onto neighbours
            qc_requests         what the other application has asked for

        The third one is easy to forget and was: move a card to Scrapped on one
        machine and up to five electrodes change status on every other machine,
        without a row of dbo.MetaData or electrode_status moving. Watching only
        the first two left the tab stale until somebody visited the QC tab.

        The fourth is the other application's half of the QC request workflow:
        a technician offering an electrode, or answering something QC asked
        for, changes a column on this tab without touching anything else here.

        The poller only compares watermarks for equality, so a tuple of the
        four is as good as one number. A database that has not had the
        qc_requests migrations run returns no such entity and the tuple simply
        carries None there, which compares fine.
        """
        sql = (f"SELECT MAX([{METADATA_KEY}]), COUNT(*) "
               f"FROM [{METADATA_SCHEMA}].[{METADATA_TABLE}] "
               f"WHERE {METADATA_FILTER}")
        # Against the plant database, which may not be the one we write to.
        row = self._poll(sql, plant=True)
        produced = (int(row[0] or 0), int(row[1] or 0))
        # Kept for the reload that follows, which uses it to decide whether the
        # 11k rows need reading again or only reprocessing. Set here rather than
        # queried again, since the poller calls the two back to back.
        self._produced_seen = produced

        qc = self._qc_watermarks()
        return {_METADATA_ENTITY: (produced,
                                   qc.get(_ELECTRODE_STATUS_ENTITY),
                                   qc.get(_QC_BOARD_ENTITY),
                                   qc.get(_QC_REQUEST_ENTITY))}

    def _reload_electrodes(self) -> pd.DataFrame:
        """The Electrodes tab's frame, after whatever moved.

        Runs on the polling thread. The board is re-read first, because a live
        decision on another machine changes what this frame says: the statuses
        are computed from it, not stored.

        The 11k rows are only fetched again when dbo.MetaData itself moved,
        which is rare. A board move costs a reprocess of the cached rows, about
        20ms, instead of a second of query.
        """
        # Ignored when it predates our own write, which would otherwise show
        # the statuses of a board the operator has already changed.
        self._qc_board.apply(self._qc_board.fetch())
        if self._metadata_cache is None or self._produced_seen != self._produced_applied:
            self._produced_applied = self._produced_seen
            # New electrodes have new vats. Only on this path, which is the
            # rare one: a board move does not change what was plated where.
            self.load_vats()
            return self.get_metadata()
        return self.reprocess_metadata()

    def _qc_watermarks(self) -> dict:
        """Poll qc_data.vw_change_watermark → {entity: (row_version, count)}.

        An entity with no rows at all is simply absent from the view, which
        reads as None here and still compares unequal the moment its first row
        arrives.
        """
        rows = self._poll_all(
            f"SELECT entity, max_row_version, row_count "
            f"FROM [{QC_SCHEMA}].[vw_change_watermark]")
        return {r[0]: (int(r[1]), int(r[2])) for r in rows}

    def get_qc_board_update_callback(self):
        """Reload the board when another machine moves a card."""
        return self._qc_poller.make_update_callback(
            _QC_BOARD_ENTITY, self._reload_qc_board, "data")

    def _reload_qc_board(self) -> dict:
        """Read the whole board and hand it back.

        Read here, adopted by the GUI: this runs on the polling thread, and
        filling the shared store from it could let a render catch it half
        populated.
        """
        return self._qc_board.fetch()

    def get_sem_edx_update_callback(self):
        """Reload every SEM sub tab when another machine imports a folder."""
        return self._qc_poller.make_update_callback(
            _SEM_EDX_ENTITY, self._reload_sem_edx, "data")

    def _reload_sem_edx(self) -> dict[str, dict[str, pd.DataFrame]]:
        """``{generation: {electrode type: frame}}`` after dropping the cache."""
        self._sem_edx_caches.clear()
        return {generation: {electrode_type: self.get_sem_edx(generation, electrode_type)
                             for electrode_type in self.get_sem_edx_types(generation)}
                for generation in self.get_sem_edx_generations()}

    # ---- polling ------------------------------------------------------
    def _poll_connection(self, plant: bool = False):
        """This thread's polling connection, opened once (lazily).

        Opening a fresh connection every few seconds per tab was the original
        UI-lag source, and a connection cannot be shared between the worker
        thread that polls and the main thread that writes, so there is one
        each.
        """
        attribute = "plant_conn" if plant else "conn"
        conn = getattr(self._poll_local, attribute, None)
        if conn is None:
            conn = self._connect(plant=plant)
            setattr(self._poll_local, attribute, conn)
        return conn

    def _poll(self, sql: str, plant: bool = False):
        """Run one watermark query on the reused connection, returning its first
        row. If the connection dropped (Azure closes idle ones), reconnect and
        retry once."""
        import pyodbc

        try:
            cursor = self._poll_connection(plant).cursor()
            cursor.execute(sql)
            return cursor.fetchone()
        except pyodbc.Error:
            # stale connection — reconnect and retry once
            self._close_poll_conn(plant)
            cursor = self._poll_connection(plant).cursor()
            cursor.execute(sql)
            return cursor.fetchone()

    def _poll_all(self, sql: str) -> list:
        """Every row of one watermark query, on the reused connection."""
        import pyodbc

        try:
            cursor = self._poll_connection().cursor()
            cursor.execute(sql)
            return cursor.fetchall()
        except pyodbc.Error:
            self._close_poll_conn()  # stale connection — reconnect and retry once
            cursor = self._poll_connection().cursor()
            cursor.execute(sql)
            return cursor.fetchall()

    def _close_poll_conn(self, plant: bool | None = None) -> None:
        """Drop this thread's polling connections, or just one of them."""
        wanted = ("conn", "plant_conn") if plant is None else (
            ("plant_conn",) if plant else ("conn",))
        for attribute in wanted:
            conn = getattr(self._poll_local, attribute, None)
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
                setattr(self._poll_local, attribute, None)

    def close(self) -> None:
        """Release the polling connection (call on app shutdown)."""
        self._close_poll_conn()
