# How an electrode's status is decided

Testing one electrode says something about its neighbours. This document is the
complete set of rules for what, written as scenarios: you do this, and that
happens. Every table below is real output from `status_propagation.py`, not a
sketch.

Two ideas carry the whole thing. Read these first or nothing else will make
sense.

**1. Everything travels along the vat, not along the line.** Electrodes are
plated in vats (28, 29, 30) and the line alternates between them, so B14466 and
B14464 are neighbours *in vat 28* while B14465 sits between them on the line in
a different vat. A fault is the vat's doing, so it spreads to the electrodes
that vat made, and skips over everything else.

**2. A measurement always beats a guess.** Only two statuses are measurements:
`Tested, passed` and `Tested, failed`. Somebody put those electrodes on a rig.
Everything else is inferred from them, and an inference can always be replaced
by a better one.

---

## The six statuses

| Status | Meaning | Measured? |
|---|---|---|
| `Tested, passed` | put on a rig, it was good | **yes** |
| `Tested, failed` | put on a rig, it was bad | **yes** |
| `Released` | presumed good, because of a pass nearby | no, a guess |
| `Scrapped` | presumed bad, because of a fault nearby | no, a guess |
| `Needs testing` | in doubt, somebody should test it | no, a request |
| `Not Tested` | nothing is known about it | no |

Plus one that is never stored: **`Test ongoing`**, which means the electrode is
on the QC board right now. It is worked out fresh every time the screen is
drawn.

---

## Scenario 1: you set an electrode to Tested, passed

A pass does two different things in the two directions, and they are not
symmetric. **Behind it, it releases. Ahead of it, it only clears doubt.**

The reason: the vat was evidently plating properly up to the moment this
electrode came out, so what it made before is presumed good. But a test done at
09:00 says nothing about what the vat did at 11:00.

### 1a. Behind it, nothing in the way

Everything untested behind it in the same vat becomes `Released`, back until it
meets an electrode that already has a status.

```
B14426   not tested
B14425   TESTED PASSED   <-- you set this
B14424   released
B14423   released
B14422   released
   ...and so on backwards
```

### 1b. Behind it, with a fault in the way

**Nothing is released.** The stretch between the fault and the test stays in
doubt and is flagged for testing instead.

```
B14425   TESTED PASSED   <-- you set this
B14424   needs testing
B14423   needs testing
B14422   needs testing  [test first]
B14421   scrapped
B14420   scrapped
B14419   TESTED FAILED
```

We now know production had recovered **by** B14425, but not **where** it
recovered. Every electrode in between could be on either side of that line, so
each one is a real question.

### 1c. Ahead of it, no fault ahead

Electrodes ahead that were flagged `Needs testing` go back to `Not Tested`.
They were doubted because of a fault the pass now stands between, so that doubt
can no longer reach them.

They do **not** become `Released`. They were plated after the test was done, so
nothing here vouches for them.

```
B14428   not tested     (was needs testing)
B14427   not tested     (was needs testing)
B14426   not tested     (was needs testing)
B14425   TESTED PASSED  <-- you set this
B14424   released
```

### 1d. Ahead of it, with a fault ahead

Nothing ahead is cleared. Those electrodes are in doubt because of the *later*
fault, and that doubt is real.

```
B14436   needs testing  [test first]
B14435   scrapped
B14434   scrapped
B14433   TESTED FAILED   <-- a fault ahead
B14432   scrapped
B14431   scrapped
B14430   needs testing  [test first]
B14429   needs testing
B14428   needs testing
B14427   needs testing
B14426   needs testing
B14425   TESTED PASSED   <-- you set this
B14424   released
```

---

## Scenario 2: you set an electrode to Tested, failed

A fault spreads **both ways equally**, along its own vat.

1. The **two either side** are scrapped with it.
2. The **third each way** is flagged `Needs testing` and marked **test first**.
   Those are where the fault most likely started and stopped, so they are the
   ones worth testing next.
3. **Beyond that**, it keeps marking `Needs testing` outward until it reaches an
   electrode that was tested and passed.

```
B14430   needs testing
B14429   needs testing
B14428   needs testing  [test first]   <-- third above
B14427   scrapped
B14426   scrapped
B14425   TESTED FAILED   <-- you set this
B14424   scrapped
B14423   scrapped
B14422   needs testing  [test first]   <-- third below
B14421   needs testing
B14420   needs testing
```

### 2a. A pass stops it

The doubt cannot travel through an electrode somebody tested and passed.

```
B14429   not tested      <-- the doubt never gets here
B14428   TESTED PASSED   <-- a wall
B14427   scrapped
B14426   scrapped
B14425   TESTED FAILED
```

### 2b. Other vats are untouched

The line alternates between vats. Only vat 28 is affected here.

```
B14470  vat 28   scrapped
B14469  vat 30   not tested
B14468  vat 28   scrapped
B14467  vat 30   not tested
B14466  vat 28   TESTED FAILED
B14465  vat 30   not tested
B14464  vat 28   scrapped
B14463  vat 30   not tested
B14462  vat 28   scrapped
```

The same is true of a pass: it releases its own vat and leaves the others alone.

---

## Scenario 3: you put an electrode on the QC board

It reads **`Test ongoing`**, whatever it read before. A recorded `Scrapped`, a
`Released`, a `Needs testing`, all of them give way: putting it on the board is
the statement that its status is being revisited.

It also loses any **test first** flag, because somebody is already testing it.

```
B14422   needs testing  [test first]
B14421   test ongoing   <-- was released
B14420   test ongoing   <-- was scrapped
B14419   needs testing
```

### 3a. Taking it back off the board

`Test ongoing` is never saved anywhere. Remove the card and the electrode reads
whatever it **would** have read if it had never been on the board.

```
B14421   needs testing   <-- reopened by the fault above
B14420   scrapped        <-- back to its own recorded verdict
```

Note this is not always the status it showed before. B14420 had a verdict of its
own, so it comes back. B14421 only had a guess, and by the time the card came
off, a nearby fault had a better claim on it.

---

## Scenario 4: you re-test something

Setting a new verdict on an electrode that already had one throws away every
guess the old verdict produced, and re-derives them from the new one.

So if B14466 failed, condemning its neighbours, and you later re-test it and it
passes, those neighbours are not left scrapped. They are worked out again.

This is why the app records, for every guessed status, **which** verdict it came
from and **when**. A guess older than the verdict it names was drawn from a
version of that verdict that no longer exists, so it is discarded.

---

## The rules underneath

Everything above follows from four rules.

### What a sweep may overwrite

| Status | Can a sweep change it? |
|---|---|
| `Not Tested` | yes |
| `Needs testing` | yes, it is a request, not an answer |
| `Released` | yes, nobody measured it |
| `Scrapped` | no, it keeps it, but the sweep carries on past |
| `Tested, failed` | no, and the sweep carries on past |
| `Tested, passed` | no, and it **stops the sweep dead** |

A `Released` electrode being overwritten is the surprising one. It matters
because an electrode reading `Released` never appears in anybody's work queue.
If a fault turned up beside it and it kept that status, it would ship without
ever being looked at.

### Only a real pass is a wall

A guess does not bound another guess. `Released` does not stop doubt, only
`Tested, passed` does.

### A scrapped electrode does not stop a sweep

Two nearby faults produce one continuous zone rather than two separate ones.

### Distances

| | |
|---|---|
| scrapped either side of a fault | **2** |
| test-first flag | the **3rd** each way |
| how far `Needs testing` runs | until a `Tested, passed` |

If the process log does not say which vat an electrode came from, the fault
falls back to counting along the production line instead, and stops after the
third. A guess about the line is worth two electrodes, not a whole stretch.

---

## Why a row is locked

A row is locked when a card **currently on the QC board** is what decides its
status. Dragging that card somewhere else would change the row, so it cannot be
pinned by hand at the same time.

Two things that look similar and are not:

* **Because of** names **one** decision, the one recorded against the status.
* **Locked by** covers **every** decision that contributed.

A status can genuinely have more than one cause. A stretch cleared between two
passes needs both of them to stay cleared, so both lock it.

A finalised verdict never locks anything. It cannot be dragged anywhere.
