# Automatic sampling can be switched off: the contract

The interval rule now has an on/off switch, held in the database and read by
the procedure you already call. **Nothing on your side has to change for it to
work**, but three things you read change shape, and one of them changes
meaning, so this page is worth ten minutes.

Owned by the QC Data Manager project: the DDL is
`sql/qc_requests/016_the_sampling_switch.sql` in that repository, which also
applies it.

**Status: live on `dataloggingTest`.** Not yet on `dataloggingDev`, which is
the production database.

## Why

Both applications are being released before the line is running again. A
couple of electrodes will be made to try them out, and the rule cannot tell
those from production: it would ask QC to test them and ask a technician to
fetch them, and there is nobody to do either. QC asked for a way to stop it
without stopping the applications.

## The same procedure, still called by both of us

```sql
EXEC qc_requests.usp_raise_due_requests;   -- → raised, note
```

Unchanged, called the same way, on the same 60 second timer. While the switch
is off it raises nothing and comes back with:

```
raised  note
------  ----------------------------------
     0  Automatic sampling is switched off.
```

**`note` has been NULL on every row this ever returned, and now sometimes is
not.** It is the only case that puts a sentence there. If you show it, whoever
is watching finds out why the number is zero; if you ignore the column, nothing
breaks. Nothing else about the result set changes.

**The expiry rule in `011` still runs while the switch is off**, before the
switch is even read. Expiring a request nobody can answer is housekeeping on
what is already there, and it should not stop because sampling has.

## What it does not touch

* **A request somebody raises by hand**, from either application.
* **The requests raised around a SEM or IC failure.**
* **Requests already raised.** Switching off stops new ones; it withdraws
  nothing. Everything outstanding is still outstanding and still yours to
  answer.

Those are answers to something that has happened rather than a sampling rate,
and they are still wanted on a line that is only being tried out.

## Reading it

```sql
SELECT is_on, updated_at_utc, row_version FROM qc_requests.vw_sampling_switch;
```

Granted to you. One row, always: `is_on` is a `BIT` and never NULL.

**QC throws it, you read it.** `usp_sampling_switch_set` is granted to QCDM
only, the same way `usp_request_mark_sent` is granted to you only. If
production ever needs to throw it, say so and we will talk about it rather than
widening the grant quietly.

## Two views you read gain a column

Added at the end of each, so a caller reading by name is unaffected and one
reading by position still finds what it had.

```sql
SELECT vat, sampling_interval, last_sampled_electrode, last_sampled_at_utc,
       produced_since, remaining, sampling_on      -- new
  FROM qc_requests.vw_vat_sampling_status;

SELECT vat, sampling_interval, sampling_on,        -- new
       updated_at_utc, row_version
  FROM qc_requests.vw_vat_sampling_watermark;
```

**`remaining` is NULL for every vat while the switch is off.** This is the one
change of meaning. NULL already meant "the rule raises nothing here", which is
exactly the case, and a countdown to a request that will not be raised is worse
than no countdown. `sampling_on` is what tells the two apart on screen: NULL
with `sampling_on = 0` is "switched off", NULL with `sampling_on = 1` is the
old "nothing tested yet, or no interval set".

**The watermark moves when the switch moves.** `MAX(row_version)` over
`vw_vat_sampling_watermark` answers both questions, as it did before, so a
switch thrown in the QC lab reaches your screen within one poll with no second
query. That works because a `ROWVERSION` comes from one counter for the whole
database, so the value from the switch row and the value from an interval row
are comparable; the view hands you the later of the two.

## Switching on asks a question, and this is the part worth knowing

The count runs from the last request in a vat, or the last electrode tested in
it. Production carries on while the switch is off, so by the time somebody
switches it back on that anchor is far behind, and the rule would start
marching through everything made since, one electrode a minute, every one of
them too old to fetch. That is the `007` flood again by another route.

So **switching on moves each vat's anchor to the newest electrode it has**, and
the person switching it on answers one question:

| answer | what happens |
|---|---|
| the very next electrode | each vat asks for the first electrode it makes from now |
| a full interval | each vat counts its interval from where it stands now |

The second is stored as `due_next` on `vat_sampling_interval` and retires
itself: it applies only while the stored anchor is still what the count runs
from, so the first request past it supersedes the anchor and the ordinary
interval resumes. Nothing has to remember to clear it.

**What this means for your panel.** `remaining` counts to whichever of the two
it is, because the rule and the view share one definition, as they have since
`006`. Expect to see `remaining = 1` on a vat that has just been switched on
with the first answer. `last_sampled_electrode` is, as `007` said, whatever the
countdown counts from, and after a switch-on that is the newest electrode in
the vat rather than one that was sampled. `last_sampled_at_utc` is then NULL,
which is honest: it was not sampled.

## Guarantees

* **Off means off everywhere.** The rule is in the database, so this is the
  line's switch and not one application's. There is no second switch in either
  application that could disagree with it.
* **Throwing it twice does nothing the second time.** `usp_sampling_switch_set`
  returns `changed = 0` and re-anchors nothing, so switching on twice cannot
  push the next sample a further interval away.
* **Switching off writes nothing to any request.** It stops the `INSERT` and
  that is all.
* **One switch for the line, not one per vat**, like the coupon settings in
  `008`. Whether QC is sampling at all is a question about QC. If per vat ever
  becomes the right answer, the table it would live in is already per vat.
* **Seeded off.** This is the one migration in the schema that changes
  behaviour by running, deliberately: it is the safe end on a database that has
  never raised a request, and switching on is one button and one question.

## Before production

`016` has to run on `dataloggingDev`, after `001` to `015`. **That database is
production**, whatever its name says; there are two, not three, and
`dataloggingTest` is the other one. None of the `qc_requests` migrations are on
it yet.

Because it seeds off, **the first thing somebody does on a database after
running it is throw the switch**, or nothing will be sampled there. On
production that is the point: it stays off until the line is running.
