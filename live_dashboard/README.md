# Live Dashboard

The office wall screen. Three windows onto the same QC data, rotating on their
own, meant to be left running on a TV and never touched.

```powershell
venv\Scripts\python.exe live_dashboard\main.py
```

Escape or Ctrl+Q closes it, Right arrow advances by hand for a demonstration.

## What it shows

One screen per time window, `Last 24 hours` then `Last 7 days` then
`Last 30 days`, sliding leftward from one to the next. Each carries:

* four headline figures: produced, QC pass rate, fault rate, and how many
  electrodes are on the QC board right now
* the **status ring**, the same chart the printed report uses
* the **production** and **QC results** blocks, the same two the report prints,
  with a share bar behind each line and a passed-against-failed bar underneath
* **production per hour** on the 24 hour screen and **per day** on the others
* the **vat league table**, worst fault rate first

The fault rate tile changes colour on its own: teal under 5%, amber to 10%,
coral above. It is the only tile that does, so it is the only one that draws
the eye when something is wrong.

## Configuration

`config.json`, beside this file:

| key | what it does |
| --- | --- |
| `rotation_seconds` | how long one screen holds. **Currently 4, for testing.** 20 to 30 is right for a wall |
| `transition_ms` | how long the slide takes |
| `refresh_interval_ms` | how often the database is re-read |
| `vat_rows` | how many vats the league table lists |

A missing or malformed file falls back to the defaults rather than failing to
start, since nobody is sitting at the machine to read an error.

## How it is built

Nothing about QC is re-implemented here, only re-presented. The dashboard calls
the app's `build_backend()`, gets the app's `ElectrodeProcessor` output, and
draws the app's `StatusDonutWidget`, so a change to how a status is worked out
reaches the wall as well as the desk.

The same three layers as the app, and the same rule that dependencies only
point downward:

```
live_dashboard/
  main.py                     entry point, puts the repo root on sys.path
  config.json                 rotation, transition, refresh, vat rows
  data/
    dashboard_config.py       config loader
    dashboard_stats.py        DataFrame -> WindowStats. Pure, no Qt, no DB
  gui/
    dashboard_window.py       composition root: backend, rotation, reread
    dashboard_page.py         one window's cards
    slide_deck.py             the sliding pages
    chrome.py                 brand bar and rotation strip
    palette.py                every colour and size decision
    widgets/                  card, kpi tile, summary table, vat table,
                              throughput chart, pass rate bar, donut card
  tests/
```

`dashboard_stats.py` is deliberately free of Qt and of the database: every
figure the screen shows can be checked in a unit test, which matters more here
than anywhere else in the codebase, because nobody is in front of this program
to notice a number going wrong.

## Two decisions worth knowing about

**The reread waits for a still screen.** Reading twelve thousand rows blocks
the UI thread for about a second, and a second of frozen animation is exactly
what makes a wall screen look broken. The refresh timer only marks a read as
due; the read itself happens when the deck finishes a slide.

**A failed read keeps the last good figures.** The board does not clear and does
not raise, it writes how old the numbers are into the header and tries again at
the next interval. A blank screen at 06:00 tells nobody anything; an hour-old
number that admits it is an hour old still does.

## Tests

```powershell
$env:QT_QPA_PLATFORM="offscreen"; venv\Scripts\python.exe -m unittest discover -s live_dashboard -p "test_*.py" -t .
```

The stats tests inject `now`, so they do not depend on when they are run. The
window tests build a real window around a fake backend, so they never touch a
database.
