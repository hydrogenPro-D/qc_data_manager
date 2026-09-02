# The pulsing loading placeholder

How the "breathing" placeholder works, why it is built the way it is, and how
to put it on something new.

The window opens before the reads finish, so for a second or two the tables
have nothing in them. A blank table reads as *there is nothing here*, which is
a lie. A pulsing skeleton reads as *this is coming*, which is the truth.

There are three variants, and picking the right one is most of the work.

| Variant | Used when | Where |
|---|---|---|
| **Whole table skeleton** | the entire table is still empty | [skeleton_overlay.py](../src/gui/widgets/skeleton_overlay.py) |
| **Single column bars** | the table has rows, one column is still filling | `LoadingCellDelegate` in [table_delegates.py](../src/gui/widgets/table_delegates.py) |
| **A still grey ring** | the donut chart has no numbers yet | `set_loading` in [status_donut_widget.py](../src/gui/widgets/status_donut_widget.py) |

## The pulse itself

One function drives all of it, so everything on screen breathes at the same
rate. It lives in `skeleton_overlay.py` and is imported by anything that
pulses.

```python
SHIMMER_TICK_MS = 90
SHIMMER_PERIOD = 12          # ticks in one full breath

def pulse_strength(step: int) -> float:
    half = SHIMMER_PERIOD / 2
    position = step % SHIMMER_PERIOD
    return position / half if position < half else 2 - position / half
```

A `QTimer` on 90ms increments an integer `step` and asks for a repaint. The
function turns that counter into a number from 0 to 1 and back down again.

Three deliberate choices:

- **A triangle wave, not a sine.** It brightens and dims at one steady rate. A
  sine eases at the top and bottom of the curve, which makes it look like a
  heartbeat rather than breathing.
- **12 ticks at 90ms is a 1.08 second breath.** The reads take about a second,
  so a faster pulse would suggest the window is busy rather than waiting.
- **The strength drives alpha, not colour.** `bar_color()` maps 0 to 1 onto
  alpha 0.16 to 0.40 on a single grey (`#94A3B8`). Fading opacity keeps the
  bars sitting on the surface colour instead of cycling through hues.

The counter is a plain integer that only ever increases. There is no animation
framework and no state to reset, so `step % SHIMMER_PERIOD` is the whole loop.

## Variant 1: the whole table skeleton

`SkeletonOverlay` is a `QWidget` **child of the table**, painted over it. It is
not rows in the table.

**Why an overlay rather than placeholder rows.** Fake rows would reach the
filter bars, the row styling, the status pill delegate and the image buttons,
none of which should ever see a row that does not exist. Every one of those
would need a "but not if it is fake" branch. An overlay needs none, because as
far as the table is concerned it is simply empty.

Three things make it behave:

```python
# Clicks belong to whatever is underneath: this is a picture of a table
# loading, not something to interact with.
self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
parent.installEventFilter(self)     # so it can follow the table's size
self.setGeometry(parent.rect())
```

The event filter matters. A child widget does not resize with its parent, so
without watching for the parent's `Resize` event the overlay stops covering the
table the moment the window changes size.

`paintEvent` fills the surface, paints a header row a shade darker
(`strength + 0.35`), then repeats a row of rounded bars down the height of the
widget. Column widths come from a fixed list of fractions, indexed with
`(row + column * 3) % len(WIDTHS)`, so no two rows line up and it reads as a
table rather than as a block.

Lifecycle is two calls:

```python
self._skeleton = SkeletonOverlay(self.table, columns=6)
...
def show_loading(self):  self._skeleton.start()   # window opens
def load_data(self, df): self._skeleton.stop()    # first read lands
```

`load_data` stops it unconditionally, so a read that returns nothing still
clears the placeholder. There is also `hide_loading()` for a read that never
answers at all, which stops the pulse and lets the tab show its honest empty
state.

## Variant 2: one column still filling

The Electrodes tab shows its rows as soon as the electrodes arrive, but the Vat
column comes from a separate read of the process log that lands later. Covering
the whole table again would hide rows that are ready.

`LoadingCellDelegate` extends `RowFillDelegate` rather than replacing it, so
the column keeps its hover outline and poll flash like every other column. It
paints the normal cell first, then a pulsing bar **only in cells that are
empty**:

```python
def paint(self, painter, option, index):
    super().paint(painter, option, index)
    strength = self._pulse()
    if strength < 0 or str(index.data() or "").strip():
        return          # done pulsing, or this cell already has a value
    ...draw the bar...
```

The delegate is given a callable, not a value, so it asks how bright to be at
paint time:

```python
def _shimmer_strength(self) -> float:
    if not self._shimmer_timer.isActive():
        return -1.0                       # nothing pending, paint normally
    return pulse_strength(self._shimmer_step)
```

**A negative number means "stop", which is the only signal the delegate needs.**
That keeps all the decision making in the tab and none of it in the delegate.

`_place_vat_delegate()` decides when to run: if the Vat column exists but no
row has a value in it yet, start the timer; as soon as any value shows, stop it.

### The trap this variant walks into

A column that does not exist cannot shimmer. The Vat column was originally
added to the frame only once the vat data arrived, so during the wait there was
nothing on screen to pulse, and the column appeared from nowhere a second later
looking like a glitch.

The fix was on the data side, not the GUI side: the backend exposes
`vats_pending()`, and the processor adds the column while the read is still in
flight:

```python
if vats or self._vats_pending():
    ...include the Vat column...
```

If you add a shimmer for a late arriving column, check the column is present
and empty during the wait, not absent.

## Variant 3: the donut, deliberately still

The chart shows a plain grey ring while it waits, and it does **not** pulse.
From `set_loading`:

> Deliberately still, where the tables shimmer: redrawing a matplotlib canvas
> eleven times a second for the sake of a two second wait is a lot of work to
> say something this ring can say by just sitting there.

The point of the loading state here is to stop the chart showing its "No data"
state, which would claim the filters matched nothing rather than that the
numbers have not arrived. A static ring says that just as well.

Since matplotlib is now loaded lazily, both the loading ring and the "No data"
text are drawn by hand with `QPainter` in `_RingPlaceholder`, so nothing on the
startup path touches the plotting library at all. `_RING_WIDTH` is shared
between the placeholder and the real wedges so the two are the same shape and
the swap is invisible.

## Adding one to a new tab

For a whole table:

1. `self._skeleton = SkeletonOverlay(self.table, columns=<roughly your column count>)`
   in `init_ui`.
2. `show_loading()` calls `self._skeleton.start()`.
3. Your `load_data` calls `self._skeleton.stop()` at the top, before anything
   else, so every path clears it.
4. `MainWindow` calls `show_loading()` when it builds the tab, and the deferred
   load calls `load_data` when the read lands.

For one late column, copy the Vat wiring in
[electrodes_gui.py](../src/gui/electrodes_gui.py): a `QTimer` on
`SHIMMER_TICK_MS`, an integer step, a `_shimmer_strength` returning `-1.0` when
idle, and a `LoadingCellDelegate` set on that column only. Then make sure the
column exists while empty.

## Things worth remembering

- **Share the pulse.** Two timers drift apart within seconds and two rhythms on
  one screen look broken. Everything imports `pulse_strength` and
  `SHIMMER_TICK_MS` from the one module.
- **The overlay must be mouse transparent**, or it silently eats clicks meant
  for the table underneath.
- **Watch the parent's resize**, or the overlay stops covering the table.
- **Stop the placeholder on every exit path**, including the empty result and
  the failed read, not only the happy one.
- **Do not animate an expensive surface.** A `QPainter` widget repainting
  eleven times a second is nothing. A matplotlib canvas doing the same is not.
- **Slower than you think.** The first attempts were faster and read as the
  window struggling. Waiting should look calm.
