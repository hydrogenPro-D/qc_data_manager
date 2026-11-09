# QC Data Manager

Operator-facing PyQt6 desktop app for electrode quality control:
**read, validate, write, visualize**. One maximized window, one tab per entity,
tablet-friendly targets.

| Tab | What it shows |
| --- | --- |
| **Electrodes** | Every produced electrode, its QC status, and the status breakdown beside it |
| **QC** | The kanban board where a tested electrode's verdict is decided and finalised |
| **SEM EDX** | SEM measurements by generation and electrode type, with the images behind them |
| **QC Sampling** | How often QC should test, worked out per vat from the runs of good and bad |

## Running from source

```powershell
py -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt
venv\Scripts\python.exe main.py
```

A database connection is required at startup. Copy
`config/db_connection.example.json` to `config/db_connection.json` and fill in
`DB_DRIVER`, `DB_SERVER`, `DB_DATABASE`, `DB_USERNAME` and `DB_PASSWORD`. The
real file is git-ignored, so credentials never reach the repository.

The app fails fast: `build_backend()` runs once at construction, and an
unreachable database stops the launch with a titled error dialog rather than
breaking halfway through a shift.

Tunables (poll interval, SEM image roots, the sampling default range) live in
`config/config.json` and are read through `src/data_access/app_config.py`. The
tests read the same file, so they cannot drift from the app.

## Architecture

Three layers, with dependencies only ever pointing downward. The data layer
knows nothing about Qt.

```
GUI (src/gui)              MainWindow > tab widgets > shared widgets
   |                       MainWindow holds the backend; tabs get callables
Backend (src/data_access)  build_backend() > DbManager
   |                       reads:  dbo.MetaData, qc_data.vw_* views
Database                   writes: qc_data.usp_* stored procedures
```

## Repository layout

```
main.py                        entry point, calls run_app()
config/config.json             tunables
config/db_connection*.json     credentials, git-ignored
config/version.json            which build this is
src/app_version.py             reading and bumping that number
src/data_access/
  backend.py                   build_backend(), the one construction point
  db_manager.py                SQL reader, normalization, watermarks
  electrodes/ qc_board/ sem_edx/ qc_requests/ sampling/
  tests/
src/gui/
  gui.py                       MainWindow (composition root) and run_app()
  electrodes_gui.py qc_board_gui.py sem_edx_gui.py sampling_gui.py
  styles/ widgets/ tests/
sql/migrations/                qc_data schema, this app's own
sql/qc_requests/               the schema shared with Production Data Manager
sql/maintenance/               one-off corrections, not migrations
tools/generate_executable.py   PyInstaller one-dir build
data/ETL/                      the import scripts that seeded the database
docs/                          contracts and design notes
```

`qc_sampling_script/` and `live_dashboard/` are separate small applications
kept in this repository, each with its own README, entry point and tests. The
sampling script is a deliberate standalone copy of the sampling maths that
points at production; the QC Sampling tab reads whatever the app is connected
to.

## Tests

`unittest`, run headless. Processors get pure data-in, data-out tests; widgets
get tests with fake callbacks and synthetic frames. No test touches a live
database.

```powershell
$env:QT_QPA_PLATFORM="offscreen"
venv\Scripts\python.exe -m unittest discover -s src -p "test_*.py" -t .
```

The full run takes about two and a half minutes, so while building a change,
run only the modules it touches:

```powershell
$env:QT_QPA_PLATFORM="offscreen"
venv\Scripts\python.exe -m unittest src.gui.tests.test_electrodes_gui -v
```

Discovery is rooted at `src`, so the two side applications are run separately:

```powershell
venv\Scripts\python.exe -m unittest discover -s qc_sampling_script -p "test_*.py" -t .
```

Offscreen Qt hides real layout bugs, so a change to the look is confirmed by
grabbing the widget to a PNG and looking at it, not by a passing test alone.

## Versioning

One number in `config/version.json`, alongside the moment it was released:

```json
{ "version": "1.3", "released_at": "2026-11-04 13:16:40" }
```

Deliberately not semver. It is a build counter for an internal tool and only
the minor part moves: 1.0, 1.1 ... 1.9, 1.10. That ordering is only correct if
the parts are compared as integers, so anything comparing versions must use
`tuple(int(part) for part in version.split("."))` and never the strings.

The running app reads it through `src/app_version.py` and puts it in the window
title. A build running from source has no release to name and titles itself
`(dev build)`.

Nobody edits the file by hand. The build script writes it, and the repository
copy is the record of what last shipped, so it has to be committed: a fresh
clone that is missing it starts the count again at 1.0 over the top of the
history.

## Building the executable

```powershell
venv\Scripts\python.exe tools\generate_executable.py
```

A PyInstaller one-dir bundle lands in `dist\QC Data Manager\`, with `config\`,
the assets and the ODBC driver installer from `driver\` copied in beside the
executable.

The script asks two questions before anything slow runs:

1. **Which database should this build talk to?** Test is offered first and is
   what plain Enter picks, because a bundle looks identical either way once it
   has been handed over. The bundle gets its own connection file, a copy of the
   repository one with the database field changed, so building for one database
   cannot change what you are testing against. Anything other than the test
   database has to be confirmed by name, and the last line printed says which
   database it was.
2. **Bump the version?** Only for a release. A test build keeps the current
   number, so the twenty test builds between two releases do not burn twenty
   version numbers.

Nothing is written until the build succeeds, so a failed build never records a
release that did not ship.

## Database migrations

Numbered `.sql` files applied by hand in SSMS or Azure Data Studio. No runner,
no framework. The convention is written up in
[data/MIGRATION_PLAYBOOK.md](data/MIGRATION_PLAYBOOK.md).

Two sets, with a ledger each:

* **`sql/migrations/`** is the `qc_data` schema, this application's own
  business, recorded in `qc_data.schema_migrations`.
* **`sql/qc_requests/`** is the `qc_requests` schema, the published interface
  between QC Data Manager and Production Data Manager, recorded in
  `qc_requests.schema_migrations` and numbered independently. A change here is
  a change to a contract. It is owned by `dbo` so the ownership chain to
  `dbo.MetaData` stays unbroken.

Every file:

* guards on `DB_NAME()`, since Azure SQL has no `USE` and that guard is the
  only thing between you and a wrong-database run,
* is idempotent, so a second run changes nothing,
* records itself in its schema's ledger.

Apply them in order, against the test database first. **`dataloggingDev` is the
production database**, despite the name. There are two databases, not three:
`dataloggingTest` and `dataloggingDev`.

`sql/maintenance/` is a different thing, and deliberately outside both ledgers:
one-off scripts that correct data rather than change shape, run once by an
administrator who has read them.
