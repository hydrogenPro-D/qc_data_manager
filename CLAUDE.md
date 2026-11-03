# Project guidance for Claude

QC Data Manager — a single-window, tabbed PyQt6 desktop app for operators:
**read → validate → write → visualize**. Same architecture as the sibling app
`QC_multifactor`. This file is the condensed version of that project's
`ARCHITECTURE_GUIDE.md`; follow it when adding to this codebase.

## Code style

- Keep comments short: concise and compact, **1–2 lines max**. No long
  explanatory blocks — say what's needed and move on.
- Comment the *why*, not the *what*. Docstrings on classes and non-obvious
  methods; type hints on signatures.
- **Never use a dash as punctuation** (`-`, `–`, `—`) in anything written: UI
  strings, tooltips, dialogs, reports, comments, docstrings, commit messages or
  chat replies. Use a comma, a full stop, a colon or brackets instead.
  Hyphenated compound words (`read-only`, `full-screen`) are fine.
- Don't repeat the same literal twice in one expression. Build the string once
  in a variable and append to it.

## Tech stack

PyQt6 · pandas (every record set is a `DataFrame`) · pyodbc (SQL Server / Azure
SQL) · matplotlib in `FigureCanvasQTAgg` · pyqtgraph (the sampling curve) ·
qtawesome · PyInstaller (one-dir).

## The three layers

Dependencies only ever point downward — the data layer knows nothing about Qt.

```
GUI (src/gui)          MainWindow → tab widgets → shared widgets
   │  MainWindow holds the backend; tabs get injected callables only
Backend (src/data_access)   build_backend() → DbManager
                            reads  → dbo.MetaData, qc_data.vw_* views
                            writes → stored procedures (qc_data.usp_*)
```

Four tabs: **Electrodes**, **QC** (the board), **SEM EDX** and **QC Sampling**.
Everything they show lives in `qc_data`, except the electrodes themselves, which
are read from `dbo.MetaData` and never written.

**QC Sampling** is the odd one: it reads nothing of its own. It is handed the
same processed electrode frame the Electrodes tab gets, measures the runs of
good and bad in it, and says how often QC should test. So it has no columns
enum, no view and no watermark, and it polls under the `electrodes` key. There
is a standalone copy of the same maths in `qc_sampling_script/`, which is
deliberately separate and points at production; the tab reads whatever the app
is connected to.

### Data access

- **Reads through views, writes through procedures.** No ad-hoc SQL on base
  tables. View column names and proc parameter names are the contract.
- **Columns enums are that contract** (`electrode_columns.py`,
  `sem_edx_columns.py`, `qc_board_columns.py`). Never rename a value casually —
  it's a live DB contract. Display text is separate (see "Display vs contract").
- **Processors** (`electrode_processor.py`, `sem_edx_processor.py`) turn raw
  rows into GUI-ready frames: select/order columns, compute derived values,
  normalize dates. Pure `DataFrame → DataFrame`, no Qt, no DB — so they
  unit-test trivially.
- **Normalization** happens once, on the way out of the reader: `Decimal →
  float`, dates → ISO, every NULL/NaN/NaT → a single "empty".
- **Natural keys, not row indexes.** A record is identified by what it is, an
  electrode marking or a (generation, electrode, position), so the GUI never
  maps a clicked row back to a surrogate id and a re-sorted table cannot make an
  edit hit the wrong record.
- **Watermark polling.** Each entity exposes `(max_row_version, row_count)`;
  the GUI polls it on a timer and reloads that tab when it moves. See
  `watermark.py`. After our own write, the baseline is reseeded so we don't
  reload on our own change.
- **Config-driven tunables.** Limits, units and intervals live in
  `config/config.json` (`app_config.py`). Tests read the same file, so they
  never drift from the app. `app_config` is read-only with one exception,
  `set_sampling_default_range`, which rewrites the file from what was loaded
  so nothing else in it moves.
- **One construction point.** MainWindow calls `build_backend()`
  ([backend.py](src/data_access/backend.py)) **once, at construction** —
  blocking and fail-fast, so an unavailable database fails at launch, not
  mid-use. There is deliberately no facade class over it: a pass-through
  wrapper only duplicated every signature.

### GUI

- **MainWindow is the composition root** (`gui.py`): builds the backend,
  constructs each tab injecting its callables, hosts the `QTabWidget`, owns the
  auto-refresh timer (polls only the visible tab), and handles startup failure
  with a titled error dialog before exit. It is the *only* GUI module that
  imports from `src.data_access` for data.
- **Tabs are thin and never touch the backend.** They receive
  `on_data_saved(row, row_index=None, delete=False)`,
  `on_check_for_updates()`, and any `get_*` config providers as plain callables.
  That's what lets tests feed fakes and hand-built DataFrames.
- **Shared widgets carry the behavior** (`src/gui/widgets/`): `DataTableWidget`,
  `FilterBarWidget`, `StatusDonutWidget`, `NoWheelTabWidget` (which crossfades),
  the table delegates and the dialogs. New shared behavior goes into the widget
  — never copy-pasted between tabs.
- **Tablet-first.** Big targets, one thing on screen, double-tap a row to open
  it, deliberately thick scrollbars.
- **Write flow**: the tab calls an injected callable, the backend calls one
  procedure, then the tab re-reads. Local echoes are avoided where the database
  normalises what it stores: the SEM tab drops its cache and re-reads so the
  screen shows what actually landed, not what was sent.

### Two rules that keep coming up

- **Display vs contract.** Table headers, form labels and plot titles come from
  display maps / `field_label_overrides`. Relabelling the UI is a GUI change;
  renaming a stored identifier is a coordinated DB + app migration.
- **Feature-by-presence.** Optional columns (e.g. `Row added`) are shown only
  if the source actually returns them, and dependent logic falls back cleanly.
  The app works before and after a DB migration lands.

### Visual style

One maximized window, top tab bar, teal accent (`#14B8A6` / `#55BDBD`), white
rounded cards with soft teal shadows, deliberately thick (35px) scrollbars with
zeroed arrows, no wheel-switching of tabs, short fades between pages. All of it
is centralized in `src/gui/styles/` — change the look in one place, never
inline CSS in a widget.

**Never scope a card's stylesheet to a bare `QWidget`.** It matches every plain
child too, so a border meant for the card gets drawn around each legend row and
around the empty page of a stacked widget. Give it an object name.

## Layout

```
main.py                       entry point → run_app()
config/config.json            tunables (poll interval, SEM image roots +
                              layout, the QC Sampling default range)
config/db_connection*.json    credentials — git-ignored
src/data_access/
  backend.py                  build_backend() — the one construction point
  app_config.py               config.json loader
  db_manager.py               SQL reader (views) + normalization + watermarks
  watermark.py                shared polling contract
  electrodes/                 columns enum, processor, status propagation
  qc_board/                   columns enum + the DB-backed board store
  sem_edx/                    columns enum, processor, DB source, result parser
  sampling/                   run lengths and the testing rate they imply. Pure
  tests/
src/gui/
  gui.py                      MainWindow (composition root) + run_app()
  electrodes_gui.py           the electrode table
  qc_board_gui.py             the kanban board
  sem_edx_gui.py              the SEM tab, generation then electrode type
  sampling_gui.py             the QC Sampling tab, a tab per vat
  sampling_charts.py          its gain curve, drawn with pyqtgraph
  date_display.py             timestamp formatting
  styles/                     button/table/input/tab styles
  widgets/                    reusable widgets and dialogs
  tests/
tools/generate_executable.py  PyInstaller one-dir build
```

## Adding a new tab

1. Add a columns enum + processor under `src/data_access/<entity>/`.
2. Add the tables, a `vw_*` view and the `usp_*` procedures as a numbered
   migration under `sql/migrations/`, then a reader beside the other `*_db_source`
   modules.
3. Add `get_* / save_* / get_*_update_callback` to `DbManager` — that trio is
   the contract MainWindow wires a tab with.
4. Copy the closest existing tab, `sem_edx_gui.py` for anything with sub tabs
   and detail windows, `electrodes_gui.py` for a plain filtered table.
5. Wire it in `MainWindow` with the update callback, and add its entity to
   `qc_data.vw_change_watermark` so other machines' edits show up.
6. Add a processor test and a widget test with injected fakes.

## Commands

```powershell
venv\Scripts\python.exe main.py                                     # run
$env:QT_QPA_PLATFORM="offscreen"; venv\Scripts\python.exe -m unittest discover -s src -p "test_*.py" -t .
venv\Scripts\python.exe tools\generate_executable.py                # build
```

Tests are `unittest`, run headless with `QT_QPA_PLATFORM=offscreen`.
Processors get pure data-in/data-out tests; widgets get tests with fake
callbacks and synthetic DataFrames — no live database.

**Run the whole suite only when asked.** It takes two and a half minutes, which
is most of the wait on an ordinary change. Save it for the moment the branch is
being committed, released or called finished, and say so when you want it run.
While building, run the one or two modules the change touches and nothing else.
