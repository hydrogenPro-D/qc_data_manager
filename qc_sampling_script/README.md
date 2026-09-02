# QC Sampling Analysis

Measures how long the good and bad runs actually are, so the testing rate can
be argued from this plant's own history instead of an assumed defect rate.

```powershell
venv\Scripts\python.exe qc_sampling_script\main.py
```

## What it does

Type a first and last electrode marking. It walks every vat's electrodes in
production order and measures the unbroken stretches:

* **Left, per vat**, listed 28, 29, 30 every time: the electrode count, then
  for each side how many electrodes, how many separate runs, and the average,
  median, standard deviation and share. **Electrodes = Bad + Good** and the two
  shares add up to 100, so every row can be checked at a glance. Under it, a dot
  at each average with a line reaching one standard deviation either side.
* **Under it, all vats pooled**: the same columns again as a single totals row,
  so every combined figure sits directly beneath the per vat figure it totals.
* **Right**: a histogram of how long a run lasts, and a table saying what
  testing interval each catch rate would need and what it would cost in
  destroyed production.

## It reads the real database, and only reads

The tool opens **`dataloggingDev`**, the production database, named by
`database.production` in `config/config.json`. The electrode list and the vats
already came from there; this points the QC schema at it too, so real
electrodes are never mixed with test verdicts.

The database in use is written in red in the top right corner the whole time.
It names **both** databases when they differ, because then "the database" is
not one thing and naming half of it would be the misleading half.

**Nothing here can write.** The backend is wrapped in
[`ReadOnlyBackend`](data/read_only.py), which exposes the four reads the window
makes and refuses everything else: `set_electrode_status`,
`finalise_qc_decisions` and the rest are not reachable through it and raise if
asked for. That is not a security boundary, Python has none, and a determined
person can still dig the wrapped object out. It is there so an accidental write
is impossible and a deliberate one has to be written on purpose.

If the production database cannot be opened, the tool says so in a dialog and
carries on against the configured one rather than refusing to start. It cannot
mislead you by doing that, because the banner always names what is actually
being read.

## Only the vats in service

`KEPT_VATS` in [data/run_lengths.py](data/run_lengths.py) is `("28", "29",
"30")`. Everything else the process log names is dropped before anything is
measured: retired vats, blank entries, and the rows that give two vats for one
electrode. The count of what was left out is shown beside the range, so a
window where most electrodes fall outside those three is visible rather than
silently smaller. Edit that tuple when a vat comes or goes.

## The two decisions behind the numbers

**Undecided electrodes are stepped over, always.** They are a gap in what has
been looked at, not evidence that the chemistry changed at that point. Ending a
run at one chopped genuine runs into several short pieces, and short runs are
exactly what drives the recommended testing rate down, so that error would have
made the tool ask for far more testing than the process needs.

**Only the four verdicts are counted anywhere.** Released, rejected, tested and
passed, tested and failed. An electrode nobody has ruled on yet says nothing
about the chemistry, so it is not in the total, not in a run and not in a share.
That is what makes every row reconcile with nothing left over.

**A run is one unbroken stretch of the same verdict.** Not a "spell", not a
range: the from/to boxes at the top already claim the word range. A bad run of
9 means nine electrodes in a row from that vat that will not ship.

**Runs are measured inside each vat, never along the plant sequence.** A
chemistry excursion belongs to one vat. Walking the plant-wide sequence would
break every stretch up with the other vats' electrodes running alongside it and
report bad runs far shorter than they really are. The pooled figure on the
right is the same per-vat runs counted once, not a second measurement.

**What counts as good or bad is about the process, not the electrode.** A
tested-and-passed electrode is destroyed, but it is still evidence the chemistry
was right at that point, so it counts as good. Tested-and-failed and rejected
both count as bad. Not tested, needs testing and test ongoing say nothing, so by
default they **end** the run they interrupt. That is the honest reading, and
the `Step over untested electrodes` box joins the stretches either side instead,
so you can see whether untested gaps are cutting your runs short.

## How the testing table is worked out

A bad run of length `L` is caught by a test every `k` electrodes with
probability `min(1, L / k)`: the test either lands inside the run or it does
not. Averaged over the runs actually measured, not over an assumed
distribution, so the answer is about this plant.

The table then reports the **widest** gap that still hits each catch rate, since
every electrode tested is an electrode destroyed.

Watch the shortest runs rather than the average. They are what a wide gap
steps over, and they set the rate. If the table asks for a gap tighter than you
can afford to destroy, the honest conclusion is that sampling cannot cover this
on its own and you need a measurement that does not destroy the electrode.

## Layout

```
qc_sampling_script/
  main.py                    entry point
  data/run_lengths.py        DataFrame -> run lengths and stats. Pure, no Qt
  gui/sampling_window.py     the window
  gui/charts.py              the two matplotlib plots
  tests/
```

`run_lengths.py` has no Qt and no database, so every figure the window prints
can be checked directly.

## Tests

```powershell
$env:QT_QPA_PLATFORM="offscreen"; venv\Scripts\python.exe -m unittest discover -s qc_sampling_script -p "test_*.py" -t .
```
