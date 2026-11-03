# QC Sampling Analysis

Measures how long the good and bad runs actually are, so the testing rate can
be argued from this plant's own history instead of an assumed defect rate.

```powershell
venv\Scripts\python.exe qc_sampling_script\main.py
```

## The explainer

[`docs/how_sampling_is_decided.pdf`](docs/how_sampling_is_decided.pdf) is the
three page version for people who will never open this tool: streaks, why they
make sampling work, the two numbers, and the gain curve. Rebuild it with

```powershell
venv\Scripts\python.exe qc_sampling_script\tools\make_explainer.py
```

Every figure in it is computed by the functions in `run_lengths.py`, so the
document cannot drift from the app. The run lengths it is written around are
frozen in the script rather than read live: a document that quietly changes
every time it is rebuilt is not a document.

## What it does

Type a first and last electrode marking. It walks every vat's electrodes in
production order and measures the unbroken stretches:

* **Across the top, per vat**, listed 28, 29, 30 every time: the electrode
  count, then for each side how many electrodes, how many separate runs, and
  the average, median, standard deviation and share. **Electrodes = Bad +
  Good** and the two shares add up to 100, so every row can be checked at a
  glance. A totals row underneath carries the same columns for all vats
  pooled, so every combined figure sits directly beneath the figure it totals.
* **Below that, a tab per vat** and one for all of them together. Each tab
  holds the same three things side by side: the table of testing intervals,
  the gain curve drawn from it, and what that vat is sampled at today.

The vats are not the same process. One with long excursions can be sampled far
more loosely than one that fails in short bursts, and a single plant-wide rate
over-tests the good vat to protect against the bad one.

## Why no catch rate is shown

Every interval has one, it is what sizes the whole table, and it is deliberately
not on screen. `detection` counts a run as caught the moment any electrode in it
is tested, so "catches 90%" is really "90% of bad output is in runs that get
sampled". It is not a promise to recover those electrodes: the test lands
somewhere inside the run, so a good part of it is already made, and the verdict
takes about three days to come back on top of that.

The overstatement is worst exactly where the figure looks most impressive.
Sampling vat 28 one in 300 puts the catch rate near 13%, of which roughly half
is still stoppable in practice, while at one in 12 the run is caught early and
little is lost. Read as a promise it would flatter loose sampling, so the
columns and the plot label are hidden (`SHOW_CATCH_RATE`, and the readout in
`charts.py`) and the decision is left to **Gain**, which is measured in
electrodes and not in probabilities.

## The per vat table is only as wide as its columns

Each column is sized to what is actually in it, measured across both the per vat
table and the totals row underneath so the bold figures in the totals cannot be
clipped by a column sized for the thinner ones above.

Spare card width used to be shared out among the columns so the table filled it.
That made a column holding "28" as wide as one holding a date, and it also
overflowed: the leftover was measured against a viewport width that had not
settled yet, so the last column ended up clipped behind a horizontal scrollbar
that should never have appeared. The table stops at its last column now.

`_align` sets a **maximum** width rather than a fixed one, so the table still
squeezes and scrolls sideways on a window too narrow for its content rather than
eliding figures. And the two tables go into the card with **no alignment flag**:
an aligned item is given its size hint, which for a QTableWidget is a default
256px with nothing to do with its columns.

Three things had to line up before the horizontal scrollbar finally stayed away,
and all three are the same mistake in different clothes, sizing the widget when
the columns live in the **viewport**:

* The maximum has to include the frame, or the viewport ends up a few pixels
  short of holding the columns it was sized for.
* These tables never scroll vertically. One row per vat, and `_fit` heights them
  to all of them, so a bar that appeared for a pixel of rounding only cost
  viewport width and brought the horizontal one with it.
* The layout has to be told. The first pass measures text before the stylesheet
  font is applied, so the columns come out narrower than they end up needing and
  the widget is left holding a maximum worked out from the old figures, a pass
  behind its own columns. `_align` returns the total and `_layout_tables` runs
  once more whenever it moves. It converges because `_wanted` measures the items
  rather than reading the current widths back.

## The control bar fits on a laptop

The bar wraps. It is a [`FlowLayout`](gui/flow_layout.py), not a
`QHBoxLayout`: one row on a wide screen, and on a narrow one the groups drop
onto a second and a third row instead of the right hand end being cut off with
no scrollbar to reach it.

Three details that took a second pass:

* **Groups are boxed before they go in**, so a wrap can only fall between
  groups. Adding the widgets one at a time let a row break between a slider and
  its own label.
* **The hairline separators squeeze to nothing** when they land at the edge of a
  wrapped row, where a hairline separates nothing and reads as a stray tick.
  Squeezed rather than hidden: toggling visibility from inside a layout pass
  invites a loop.
* **The database banner sits outside the wrapping part**, pinned right. It is
  the one thing that must never be the piece that scrolls away.

The fields are the width of a marking (`FIELD_WIDTH`) rather than the width of
the sentence that used to be their placeholder, which is in the tooltip now.
Buttons are padded to their own labels instead of being given a fixed width.

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

The one exception writes nowhere near the database:
[`current_intervals.py`](data/current_intervals.py) keeps the interval each vat
is sampled at today in `current_intervals.json`, a local file beside the script.
That figure will come from the app once the app can set it.

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

## How far the plot goes

The x axis runs from 1 out to **four times the best interval**
(`WINDOW_MULTIPLE`). Scaled to the peak rather than fixed, because the peak is
what the plot is for: that puts it a quarter of the way across whatever the
vat's numbers turn out to be, with the rise to its left and a real fall to its
right.

Nothing fixed can do both. A span of 60 was a plateau on one vat and cut the
hill off on the next, and a window of "the optimum plus or minus ten" is worse
again: at a best of 9 it stops while the gain is still near its peak, and at a
best of 30 it hides the entire rise.

Only a vat where no interval ever pays has no peak to scale to, and that falls
back to drawing until the gain has dropped under half its best.

## The shaded band

Around the peak, the stretch of intervals scoring within 5% of it (`FLAT_BAND`).
That band is the honest answer to "how precise do we have to be": the top of the
curve is flat, often across twenty or thirty intervals, and a single marked
point implies a precision the data does not support. Five percent rather than
ten, which shaded so much of the window that it stopped reading as a band.

## The y axis is zoomed in

Fitted to the stretch from **half the best interval to double it**
(`ZOOM_RATIO`), not to the whole curve. Testing every electrode scores about
minus fifty percent by construction, and an axis stretched to reach that leaves
the part actually being decided a few pixels tall, reading as dead flat. The
curve leaving the frame further out says "far worse" perfectly well, and the
hover readout is clamped back into view so it does not leave with it.

A fixed number of intervals either side is the same idea and was tried first. It
fails where it is needed most: on a vat whose best is 9, ten either side reaches
interval 1, the axis stretches to the crash, and nothing is zoomed at all. A
ratio holds on every vat.

## The tradeoff curves

Drawn with **pyqtgraph**. Catch rate along the bottom, and any of **Net
saved**, **Production destroyed**, **Payback** and **Test every** switched on
with the checkboxes.

Each metric gets **its own stacked plot**, not its own y axis on a shared one.
Three lines over one frame with three differently scaled axes meant tracing
each line back to the axis painted in its colour, and an interval reaching 200
electrodes squashed everything else into the floor. Stacked and x-linked, each
line is read against a single axis, and a point on one plot is the same catch
rate as the point directly above it.

**Net saved is the one to read first.** It is `(bad stopped x worth) - (good
electrodes destroyed)`, per 1000 produced, and unlike payback it has a **peak**,
which is marked. Payback says whether a rate pays for itself, and that always
needs a threshold from somebody; net saved says by how much, so the peak is a
recommendation the tool can make on its own.

`Test every` is off by default, since it is the reciprocal of the cost and says
the same thing twice.

The payback line is flat at the left and then falls away, and both halves are
real. While the interval is wide, each test lands in about one run, so the
return per test is constant at `bad_share x E[L2]/E[L] / (1 - bad_share)`,
the length-weighted mean run. Past that you are tightening beyond what the long
runs need, extra tests land in runs already caught or in short ones, and the
return drops. **The elbow is the answer to "how often should we test".**

The dotted line at 1.0 is where you stop as many as you destroy. It is a
landmark, not a recommendation: what an escape really costs you beyond its lost
efficiency is a business judgement and is deliberately not in the tool.

## What a rate is worth

The fourth column, **Payback**. A catch rate on its own
cannot say whether testing is worth running: catching 90% of a 2% problem and
90% of a 44% problem are the same row of the table and completely different
trades. This is the term that tells them apart.

```
saved_per_good_lost = (bad_share x sampling_interval x catch_rate) / (1 - bad_share)
```

Per 1000 electrodes, for a vat that is 25.8% bad, tested 1 in 11, catching 90%:

```
bad electrodes that exist   1000 x 0.258            = 258
of those, stopped           258  x 0.90             = 232
electrodes destroyed        1000 / 11               =  91
of those, bad anyway        91   x 0.258            =  24
so GOOD electrodes lost     91   -  24              =  67

                            232 stopped / 67 lost   = 3.44
```

The batch size cancels, which is why the formula has no 1000 in it.

**Only the good electrodes destroyed count as a loss.** A test lands on a
random electrode, so `bad_share` of the ones it destroys were bad anyway and
cost nothing. Dividing by every test understated the value of testing, badly on
a vat where much of the output is bad to begin with.

**A bad electrode is worth** is the second slider. It is how much of a good
electrode a bad one still delivers if it ships. At 0%, the default, a bad
electrode is worthless, so stopping one is worth a whole electrode. At 50% it
still does half the job, so stopping it only saves the half it was missing and
the payback halves. At 100% a bad electrode is as good as a good one and
stopping it is worth nothing. It changes only the payback: how often to test
comes from the run lengths, which know nothing about efficiency.

**Net saved per 1000** is the last column, and it is the one to pick a row by.
Payback is a RATIO, gain divided by cost, so it climbs for ever as the interval
widens and on its own always points at the loosest row, which catches almost
nothing. Net saved is the same two quantities SUBTRACTED rather than divided:

```
net saved = (bad stopped)  -  (good electrodes destroyed)
          = 1000 x bad_share x catch_rate x (1 - worth)
            -  (1000 / interval) x (1 - bad_share)
```

A difference has a peak where a ratio does not, because testing rarely means
the gain is tiny and testing often means the cost explodes. **The peak row is
tinted**, and it is the recommendation. Where the line really sits is a business call rather than a measurement:
if a bad electrode reaching a customer costs five times a scrapped one, then
anything above 0.2 pays. Nothing in the data can answer that.

Read the column downward and it always falls. The first tests land in the long
runs and are hugely profitable; every extra one is chasing shorter runs for
less. Where it crosses 1.0 is where sampling stops paying for itself.

## Which row is marked best

The **strict peak** of the gain curve, and on an exact tie the **shortest
interval**, which errs towards catching more.

A tolerance band was tried here first, treating anything within a couple of
percent of the peak as tied and recommending the shortest of them. It was
dropped: the recommendation should be the best interval, not a cautious
neighbour of it. What the band was really compensating for is now shown instead
of hidden, see below.

The table and the plot use the same function, so the tinted row and the marked
peak can never name different intervals.

## The acceptable band

The top of the gain curve is often level over a wide stretch of intervals, so
the strict peak is decided by hundredths of a percentage point and one step of
the worth slider can slide it from one end of the ridge to the other. On vat 28
it moves from 1 in 14 to 1 in 78 between 37% and 38%, for four electrodes in ten
thousand.

So every interval scoring within **`ACCEPTABLE_GAIN`, 0.2 percentage points of
production**, of the best is marked as a choice somebody could defend: **bold**
in the table, shaded on the plot. Only the peak is tinted and only the peak is
marked with a dot, so the recommendation is still one interval.

That band is stable where the peak is not. Over the same slider steps that move
vat 28's peak from 14 to 78, its acceptable band stays at 13 to 79. And it is
not a blanket licence: vat 30 over the same range has a band of 10 to 11,
because that curve genuinely peaks.

The figure is absolute rather than a share of the peak, because the Gain column
is where it is read off. A row saying +8.56% beside a best of +8.60% is the same
decision whatever the peak happens to be.

## The panel beside each plot

Every tab ends with the same panel: a row per vat carrying the interval that vat
is **currently** sampled at, and under it a line saying how far that is from the
best interval on that vat's own curve. Sample every 12th where the best is 15 and
it reads "Optimal interval is 3 electrodes less frequent".

Said in frequency rather than as a signed number, because a longer interval
means **less** testing and a plus sign in front of it reads as more. A vat
already on its best interval gets no line at all.

The pooled tab has no row of its own, because an interval is set on a vat and
not on the plant.

Intervals are typed in and saved as they are typed, and each vat holds its own.
They start at 12 until somebody says otherwise. Because every tab shows every
vat, a figure typed on one appears on all of them.

A number typed into one of these boxes is committed when you press Enter or
click away, not as each digit lands, so typing 15 over 12 never saves a 1 on the
way. Clicking anywhere outside the box ends the edit and clears the caret and
its selection: neither the plot nor the cards take focus when clicked, so
without that the caret sat there long after the pointer had moved on.

## Merge gaps

A slider in the control bar. Two bad runs are treated as one if at most that
many good electrodes sit between them, and those are absorbed into the merged
run, so it spans the whole zone. It cascades, so three short stretches with two
small gaps become one run.

**It defaults to 1**, so a single good electrode between two bad runs is
bridged: one passing measurement inside a bad stretch is more often a marginal
electrode than a genuine recovery. Drag it to 0 for no merging at all. Merging
makes bad runs longer, longer runs are easier to catch, and the tool then
recommends testing LESS often. Every other choice in this tool errs toward more
testing; this is the only one that errs the other way, so it is opt in and you
move it yourself.

Before raising it, look at what the good electrode in the gap actually is. A
`Tested, passed` is a real measurement, the highest quality data in the window,
and merging over it discards it. A plain `Released` was never tested and is only
an inference, so bridging that is far easier to defend.

## About the fitted curves

The curve is a normal distribution with the vat's own mean and standard
deviation, scaled so every peak is the same height: they are compared with each
other, and an unscaled density would make the tightest vat the tallest for
reasons nobody is trying to read. Height carries no information, position and
width do.

Be aware of what the fit assumes. Run lengths are counts: positive, whole
numbers, usually piled up near the short end with a long tail to the right. A
normal curve is symmetric and unbounded, so it is a rough description rather
than the right family, and it puts a little probability below zero where none
exists. A vat with fewer than three runs gets its runs plotted as dots instead,
since a curve through two points says more than the data does.

**The recommendation table does not use the curves.** It works off the measured
run lengths directly, so a poor fit cannot move the number that matters.

## How the testing table is worked out

A bad run of length `L` is caught by a test every `k` electrodes with
probability `min(1, L / k)`: the test either lands inside the run or it does
not. Measured over the runs this plant actually had, not an assumed
distribution.

**The target is a share of bad ELECTRODES, not of runs.** Counting runs treats
a stub of 3 and an excursion of 25 as the same event, and they are not: missing
the stub costs three electrodes, missing the excursion costs twenty-five. Since
a short run is also the hard one to catch, counting runs equally made the tool
demand a punishing rate to chase the cases that matter least.

The table reports the **widest** gap that still hits each target, since every
electrode tested is an electrode destroyed. It is worked out separately for
each vat and for all of them pooled.

Short runs are genuinely uncatchable by destructive sampling at any affordable
rate, and that is fine: they are usually a small share of the bad volume. If
they turn out to be most of it, sampling cannot cover this on its own and you
need a measurement that does not destroy the electrode.

## Layout

```
qc_sampling_script/
  main.py                    entry point
  data/run_lengths.py        DataFrame -> run lengths and stats. Pure, no Qt
  gui/sampling_window.py     the window
  gui/charts.py              the gain curve, one per vat
  gui/flow_layout.py         the wrapping layout the control bar is built on
  tools/make_explainer.py    builds the explainer PDF from the same functions
  docs/                      the explainer PDF itself
  data/current_intervals.py  what each vat is sampled at today, a local file
  tests/
```

`run_lengths.py` has no Qt and no database, so every figure the window prints
can be checked directly.

## Tests

```powershell
$env:QT_QPA_PLATFORM="offscreen"; venv\Scripts\python.exe -m unittest discover -s qc_sampling_script -p "test_*.py" -t .
```
