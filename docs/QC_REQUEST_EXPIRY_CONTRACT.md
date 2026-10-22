# expired: the contract

The answer to `QC_REQUEST_EXPIRY.md`. A sixth state, terminal, set by the
database for requests production can no longer be shown.

Owned by the QC Data Manager project: the DDL is
`sql/qc_requests/011_expire_unanswerable_requests.sql` in that repository,
which also applies it. PDM reads the state and asks for changes rather than
making them.

**Status: written, not yet applied to either database.** This page describes
what it will be.

## The state

```sql
SELECT electrode, kind, state, is_final FROM qc_requests.vw_qc_request;
```

`state` can now be `expired`, and `is_final` is **1** for it, alongside `sent`
and `declined`. Nothing else changes: no new column, no new procedure for you
to call, and no change to the view's shape. If your renderer already treats
`is_final = 1` as "a flat badge and no buttons", it handles this correctly
before you touch anything. The only thing to add is the label and the colour,
so that the word does not fall through as a raw `expired`.

Both applications say **"Request expired"**.

## We adopted both your conditions, with two deviations

Either condition is enough on its own, as you proposed.

**1. The electrode was made more than 30 days ago.** Your window, so it is a
parameter with a default of 30 rather than a literal, and changing it is one
call away when you tell us the number has moved.

> **Deviation: this one does not apply to `manual` requests.** A manual
> request is somebody deciding on purpose that QC wants this specific
> electrode, and the first condition would expire it within the minute, which
> would make the button useless for exactly the electrodes it is most needed
> for. A manual request for an old electrode still expires, through the second
> condition, after a fortnight. You wrote that a fresh request for an old
> electrode is the right way to re-ask for one; this is what makes that
> possible.

**2. Nothing has touched it for 14 days.** The backstop. We picked your
suggested N. It is measured from `updated_at_utc`, so it reads as "nobody has
moved this in a fortnight" whichever state it is in, and it is also the only
condition that catches a request whose electrode is not in `dbo.MetaData` at
all, where the first has no date to judge by and deliberately does nothing.

> **Deviation: both states where production has the move, not only
> `awaiting_production`.** 3 of your 57 are in `to_be_sent`: a technician
> agreed to send one and the electrode then left the list, so it cannot be
> marked sent either, and QC is waiting for something that is not coming.
> That is the thing this is meant to stop. `awaiting_qc` is **not** expired,
> ever: that one is QC's to answer and QC can answer it from QCDM whatever
> your window says.

## What it catches today

Measured against `dataloggingTest` on 22.10.2026. Your figures reproduce
exactly:

```
open requests (is_final = 0):                          78
naming an electrode outside the 30 day window:         57
   awaiting_production, kind interval                  47
   awaiting_production, kind fault                      7
   to_be_sent,          kind interval                   3
   awaiting_qc                                          0
```

So the rule expires **all 57** on its first run, as you asked, and the second
condition expires nothing today: the oldest open request is 10 days old.

## When it runs

Inside `usp_raise_due_requests`, which both applications already call every 60
seconds. **Nothing changes on your side to get it**, and it keeps running
whenever either application is open, which was your reason for wanting the
rule out of PDM in the first place.

To run it once on purpose, which either of us may do:

```sql
DECLARE @n INT;
EXEC qc_requests.usp_expire_unanswerable_requests @expired = @n OUTPUT;
SELECT @n;
```

It returns its count through an `OUTPUT` parameter and no result set, so that
calling it from inside the sweep cannot put an extra result set in front of
the `raised, note` row that both applications read. The parameters are
`@window_days` (30) and `@stale_days` (14).

## QC can no longer ask for one either

The rule only answers half of it. A request that is expired the minute it is
raised is still a request somebody pressed a button for, so QCDM now **hides
the Request for QC button** on any electrode older than the same window. The
column is simply empty there: no button to press, no dialog, and a click does
nothing.

That is a QCDM change and needs nothing from you. It matters to you only
because it means the expiry rule should not see a steady trickle of new
requests from us for old electrodes. If you do see any, they came in by
another route and are worth telling us about.

The number lives in `config/config.json` on our side and as `@window_days` in
the procedure. Both move when you tell us yours has.

## Guarantees

* **`expired` is terminal to every decision.** No procedure accepts it,
  nothing to approve and nothing to undo. **One exception, added by `015`:** if
  the electrode turns up on the QC board after all, the card moves it to
  `sent`, because the card is evidence of where the electrode is and `expired`
  was a claim about that. See `QC_ARRIVAL_MARKS_SENT_CONTRACT.md`. So
  `is_final` means "nobody is waiting on an answer", not "this row is frozen":
  do not cache a terminal row as never changing.
* **No row is deleted.** The request keeps its `kind`, its `vat` and both
  timestamps, so "what happened to B13739" has an answer.
* **`updated_at_utc` is when it expired**, and `created_at_utc` is still when
  QC asked. The gap between them is how long nobody could answer.
* **Nothing is re-raised.** The sampling rule counts an `interval` request as
  the vat's origin once raised, and expiring it does not give that back, so
  the countdown is unaffected and no replacement request appears.
* **`is_final` stays the way to tell live from closed.** We will not add a
  state without setting it.

## What is not promised

* **That a request cannot expire while you are looking at it.** It can: the
  sweep runs every minute from both sides. Your existing guard already covers
  this, because every move passes the state you had on screen and a row that
  has since expired matches nothing, so you get `changed = 0` rather than a
  wrong move. No new handling needed, but the message a technician sees for
  `changed = 0` will occasionally mean "this expired a moment ago".

## Before production

`011` has to run on `dataloggingDev` and then on production, after `001` to
`010`. None of the `qc_requests` migrations are on Dev yet. The first run on a
database expires its whole backlog at once, so run it when somebody is
watching rather than overnight.
