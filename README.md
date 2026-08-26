# qc_data_manager

Operator-facing PyQt6 desktop app for QC data: enter readings, validate them,
store them, trend them. Single maximized window, one tab per entity,
tablet-friendly.

## Running

```powershell
py -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt
venv\Scripts\python.exe main.py
```

A database connection is required at startup. Copy
`config/db_connection.example.json` to `config/db_connection.json` and fill in
the credentials — the real file is git-ignored.

The Electrodes tab reads `dbo.MetaData` (one row per produced electrode,
filtered to B-markings on the CATHODE/ANODE recipes). Writes will go through
`qc.usp_*` stored procedures — see `src/data_access/db_writer.py`.

## Tests

```powershell
$env:QT_QPA_PLATFORM="offscreen"
venv\Scripts\python.exe -m unittest discover -s src -p "test_*.py" -t .
```

## Build

```powershell
venv\Scripts\python.exe tools\generate_executable.py
```

Produces a one-dir PyInstaller bundle under `dist/`, with `config/` copied in
alongside the executable.

## Architecture

See [CLAUDE.md](CLAUDE.md). Short version: MainWindow builds one data backend
and hands tabs plain callables; reads come from SQL views through pure
processors, writes go through stored procedures; tabs are thin and wired with
injected callbacks; shared widgets and styles live in `src/gui/widgets/` and
`src/gui/styles/`.
