# qc_requests: the contract between QCDM and PDM

What Production Data Manager needs to know to talk to the shared schema. The
QC Data Manager project owns it: the DDL lives in that repository under
`sql/qc_requests/` and applies the migrations. PDM consumes what is here and
asks for changes rather than making them.

Everything below is live on **dataloggingTest** today, except migration
`015`, which is written and waiting to be applied. `001` to `014` all ran by
22.10.2026. Nothing has been applied to **dataloggingDev** yet, see "Before
production" at the end.

## The shape of it

A request is a conversation about one electrode. Either side can start it, and
whoever did **not** start it is the one who answers.

```
        QC asks                        a technician offers
           │                                   │
           ▼                                   ▼
    awaiting_production                  awaiting_qc
           │                                   │
    PDM approves / declines            QCDM approves / declines
           │                                   │
           └──────────► to_be_sent ◄───────────┘
                             │
                      PDM marks it sent
                             ▼
                           sent
```

`sent` and `declined` are final. A row in either never moves again. So is
`expired`, which is off the diagram because nobody chooses it: the database
sets it on a request production can no longer be shown, see below.

**One row per electrode, ever.** The marking is the primary key. A declined
electrode can never be requested again by any route, including by hand, and a
sent one cannot be asked for twice. Raising a request for an electrode that
already has one is not an error: it changes nothing and says so.

## Reading

```sql
SELECT electrode, kind, state, is_final, vat,
       created_at_utc, updated_at_utc, row_version
  FROM qc_requests.vw_qc_request;
```

| column | |
|---|---|
| `electrode` | the marking, as `dbo.MetaData` spells it. The key. |
| `kind` | `interval`, `fault` or `manual`, see below |
| `state` | one of the five above, or `expired`, see below |
| `is_final` | 1 for `sent`, `declined` and `expired`, so nothing has to name them |
| `vat` | the plating vat as it stood when the request was raised, or NULL |
| `created_at_utc`, `updated_at_utc` | UTC. There is no record of *who*. |
| `row_version` | what a polling watermark compares |
| `coupons` | which coupons the request wants, see below |

`kind` records why the request exists, which matters when one is declined: an
`interval` decline leaves a gap in the sampling, a `fault` one leaves a fault
zone unresolved, and a `manual` one is somebody's own business.

- `interval` — the sampling rule came round to this electrode
- `fault` — a failure nearby, and this is one of the ones to test next
- `manual` — somebody asked for it on purpose

## Writing

Five procedures, and PDM is granted exactly these. QC's half is granted only
to QCDM, so calling it fails on permissions rather than on trust.

```sql
EXEC qc_requests.usp_request_offer              @electrode = N'B16296';
EXEC qc_requests.usp_request_production_approve @electrode = N'B16296',
                                                @expected_state = N'awaiting_production';
EXEC qc_requests.usp_request_production_decline @electrode = N'B16296',
                                                @expected_state = N'awaiting_production';
EXEC qc_requests.usp_request_mark_sent          @electrode = N'B16296',
                                                @expected_state = N'to_be_sent';
EXEC qc_requests.usp_raise_due_requests;
```

`@electrode` is `NVARCHAR(32)` and `@expected_state` is `NVARCHAR(24)`.
**`@expected_state` is required** on every move: it used to default to NULL and
NULL meant "no guard", so a caller that forgot it lost its concurrency
protection with nothing to show for it. Omitting it now raises an error rather
than quietly moving the row.

**Every write returns one row, one column, `changed`.** It is 1 when the row
moved and 0 when it did not. Zero is not an error and is the normal answer in
three cases: somebody else moved the row first, the electrode already has a
request, or the row is not in the state the call starts from.

```
usp_request_offer               → changed: 1 if a request was created
usp_request_production_approve  → awaiting_production → to_be_sent
usp_request_production_decline  → awaiting_production → declined
                                → to_be_sent          → declined
usp_request_mark_sent           → to_be_sent → sent
```

**`@expected_state` is the state you had on screen.** Two applications write
these rows, so a decision can land while a technician is looking at one. Pass
what the screen showed; if the row has moved since, nothing happens and
`changed` comes back 0. Refresh and say so, rather than letting the write
quietly do nothing and look broken.

Note that `usp_request_offer` creates the request in `awaiting_qc` with kind
`manual`, and works out the vat itself. There is nothing to pass but the
marking.

**An electrode that is not in `dbo.MetaData` is refused**, with `changed = 0`
rather than an error, the same answer as asking twice. The marking is the
primary key and there is no way to withdraw a request, so a typo would
otherwise leave a row that could only ever be declined.

### Declining after approval

`QC_DECLINE_AFTER_APPROVAL.md` asked for this and migration `009` adds it.
`usp_request_production_decline` now moves a request from **`to_be_sent`** as
well as from `awaiting_production`, so an electrode that turns out to be
damaged, already shipped or simply not findable after a technician has agreed
to send it can be recorded as such:

```sql
EXEC qc_requests.usp_request_production_decline @electrode = N'B16256',
                                                @expected_state = N'to_be_sent';
```

Pass the state you had on screen, exactly as before. Nothing else moves:
`changed` is still 1 on success and 0 when the row is not in the state you
named, `declined` is still terminal, and the request still cannot be reopened.

We granted the extra state rather than adding
`usp_request_production_unavailable`, which you offered as the alternative.
`declined` already means production is not sending this one, and why it is not
being sent does not change what the row means; a second procedure would be
another grant and another branch on your side to reach the same final state.

**It does not reopen the sampling.** An `interval` request still counts as the
vat's origin once raised, so declining it late leaves the same gap in the
sampling that declining it early does, and the next request falls one interval
after it either way.

### Requests nobody can answer any more

`QC_REQUEST_EXPIRY.md` asked for this and migration `011` adds it.
`QC_REQUEST_EXPIRY_CONTRACT.md` is the same thing on its own page, with the
numbers and the guarantees.

`state` can now be **`expired`**, with `is_final = 1` beside `sent` and
`declined`. It is set by the database, never by either application: a request
is expired when the electrode was made more than 30 days ago, so it can never
appear on your list, or when nothing has moved it for 14 days. Both
applications show it as **"Request expired"**.

The rule runs inside `usp_raise_due_requests`, which you already call every 60
seconds, so nothing changes on your side beyond a label and a colour.

Two things we did not do exactly as asked, both argued on the other page: a
`manual` request is exempt from the 30 day condition, because expiring
somebody's deliberate ask within the minute would make that button useless for
old electrodes; and `to_be_sent` expires as well as `awaiting_production`,
because an electrode a technician agreed to send and then could not find is
equally unanswerable, and that covers 3 of the 57.

### A request can reach `sent` without you

Migrations `012` to `015` add `usp_request_arrived_at_qc`, granted to QCDM
only. When an electrode is put on the QC board, **its request becomes `sent`
from whatever state it was in**, and an electrode with **no request at all gets
one, already `sent`**, kind `manual`.
`QC_ARRIVAL_MARKS_SENT_CONTRACT.md` is the same thing on its own page, with the
full state table and the guarantees.

**Why.** Somebody fetches the electrode and walks it to QC, and the last tick
in PDM gets forgotten. The request then reads as outstanding on both screens,
and `011` expires it a fortnight later as unanswerable, which is the one thing
it is not: it was answered by hand and the hand never wrote it down. A card on
the QC board is the only thing in either application that says a physical
electrode is here, so it is what closes the request.

**What this is not.** It is not a grant of `usp_request_mark_sent`, which is
still yours alone and still means "production handed this over". This is a
different statement, from different evidence, through a different procedure,
so that neither grant implies the other.

Three things that follow for PDM:

* **A row can move to `sent` while a technician is looking at it.** Your
  existing guard already covers this: every move passes the state you had on
  screen, and a row that has since been marked sent matches nothing, so you
  get `changed = 0` rather than a wrong move.
* **An open offer closes too**, as of `013`. A card means QC accepted it and
  the electrode arrived, both at once, so the row goes straight to `sent`
  without passing through `to_be_sent` where you could see it. `012` left
  `awaiting_qc` out and that was wrong: an offer QC had already answered by
  taking the electrode read as "proposal pending" on your screen for nine
  days.
* **A terminal row can move, as of `015`.** A card beats `declined` and
  `expired` too: both are claims about where the electrode is, and the card
  settles it. `is_final` means "nobody is waiting on an answer", not "frozen",
  so do not cache a final row as never changing. Nothing else moves one.

## The sampling rule: PDM must call this

```sql
EXEC qc_requests.usp_raise_due_requests;   -- → raised, note
```

This is the one that needs explaining, because it is a write that PDM makes on
nobody's behalf.

QC samples one electrode in every N per plating vat, N being a number QC sets
and changes. Somebody has to notice when production has made the next one due.
The rule lives in the database rather than in either application, because the
database is the only thing that is always up: QCDM is closed at the weekend,
and a rule written twice in two languages is a rule that will disagree with
itself.

But a database only acts when something calls it. **So both applications call
this, and whoever is open keeps the sampling moving.** It is idempotent: it
raises what is due and nothing else, so two callers at once cost one wasted
query and calling it a hundred times raises nothing the hundred-and-first time.

**PDM calls it once on open and then every 60 seconds.** That is a decision,
not a suggestion: QCDM does exactly the same, and PDM is the one that is open
all working day, so in practice PDM is what keeps the sampling moving. QCDM
alone would leave a weekend's worth of electrodes unrequested until somebody
in QC opens the app on Monday, by which point they are harder to retrieve.

Do not call it on the same five second timer used to notice requests.
Production turns out an electrode every ten minutes or so, so once a minute is
already far more often than the answer can change, and the rule does real work
each time: it reads the electrode list and the process log per vat.

Run it off whatever thread PDM uses for its other background reads. Nothing is
rendered from the answer, the new requests arrive on the next ordinary poll
like any other change, so a failed sweep costs one minute and nothing else.

It returns `raised` (how many requests it created, usually 0) and `note`,
which is always NULL and is kept only so the result shape does not change.

If the electrode list is not in this database the procedure now **fails** on
the missing table rather than returning a note. That is deliberate: it is a
deployment fault, not a state to cope with, and the note it used to return was
worse than useless, see the migration note in `005`.

New requests from it arrive in `awaiting_production`, which means **they are
PDM's to answer**.

## Where requests come from, so nothing surprises you

| | raised by | state it starts in | who answers |
|---|---|---|---|
| sampling came round | the rule, called by either app | `awaiting_production` | PDM |
| an electrode failed | QCDM, when a failure is finalised | `awaiting_production` | PDM |
| QC asked for one | QCDM, by hand | `awaiting_production` | PDM |
| a technician offered one | PDM | `awaiting_qc` | QCDM |

The second one is automatic and worth knowing about: when QC finalises a
failure, the electrodes at the edge of the fault zone, roughly plus and minus
three, get a `fault` request each. Those appear without anybody pressing
anything in PDM.

## Noticing changes

`row_version` is a `ROWVERSION`, so the cheap poll is:

```sql
SELECT MAX(row_version), COUNT(*) FROM qc_requests.vw_qc_request;
```

Reload when either moves. QCDM polls every five seconds. The design note on
PDM's side already specifies a count badge on the tab regardless of which tab
is open, which still seems right: a request waiting is a question nobody has
answered.

## The sampling status view

`QC_SAMPLING_STATUS_SPEC.md` asked for this and migration `006` adds it, in the
shape you asked for:

```sql
SELECT vat, sampling_interval, last_sampled_electrode, last_sampled_at_utc,
       produced_since, remaining
  FROM qc_requests.vw_vat_sampling_status;
```

One row per vat the line actually plates, whether or not QC samples it.
`remaining` is the number to display.

**It cannot drift from the rule, because they are now the same code.**
`usp_raise_due_requests` was rewritten onto the two views the status view is
built from, so the counting exists once. Checked against the live test data
before and after: both pick the same 82 electrodes, with nothing in either that
is not in the other.

### Your three questions

**1. A vat never sampled.** `produced_since` does not count from the beginning.
It is NULL, along with `last_sampled_electrode` and `remaining`. The rule counts
from the last electrode *requested* in a vat, falling back to the last one
*tested*, so with neither there is no origin and **the rule raises nothing for
that vat at all**, however many electrodes it makes. Word it as not being
sampled yet rather than as a countdown, because no request is coming until QC
tests one.

**2. Already overdue.** `remaining` is `0`, never negative. It means the next
sweep will raise it, which is inside a minute since both applications call the
rule every 60 seconds. The view works this out from the procedure's own
condition, so `0` means precisely "the next sweep raises one here".

### Changing an interval no longer reconsiders the past

Migration `007` answers `QC_SAMPLING_INTERVAL_FINDING.md`. The rule used to plan
the whole schedule from a fixed origin on every run, so changing the interval
moved every past boundary onto a different electrode and the sweep raised all of
them at once. It now counts from the **last request** in the vat, falling back
to the last electrode tested when there are none.

Three consequences for PDM:

* **A new interval decides what happens next and nothing already produced.**
  Everything before the last request is behind the origin.
* **At most one request per vat per sweep**, which is the cap you asked for. It
  falls out of the anchor moving to each new request rather than being a
  separate limit. A backlog drains at one a minute instead of arriving at once.
* **`last_sampled_electrode` is the last one requested**, not the last one
  tested, since the specification asked for it to be whatever the countdown
  counts from. It may therefore be a request that was declined. Label it
  "counting from" if that reads better on the panel.

A **manual** request does not move the origin. Somebody asking for one electrode
on purpose is not the sampling coming round. Interval and fault requests both
do move it.

**3. Vats beyond 28 to 30.** No, and not by adding an interval row either: the
rule names 28, 29 and 30 in its SQL, so a fourth vat is a change on our side.
Keep grouping anything else separately. The view lists whichever of the three
the process log actually shows.

### Two things to know before you build the panel

**`last_sampled_at_utc` is usually NULL, and that is not a bug.** There is no
record anywhere of when an electrode was tested. The column carries a real time
only when the evidence is a QC board card or an archived verdict. Where the only
evidence is a status row, the timestamp on it is when the loader last ran, not
when anybody tested anything: 592 such rows on the test database carry 2
distinct times between them. Publishing that as a sampling date would put a
load date on an operator's screen, so the view returns NULL instead. Two of the
three vats are NULL today. **Do not make it a line of the panel.**

**Do not put this on the 5 second poll.** It reads the electrode list and the
process log, and takes **about 1.7 seconds** on the test database with every
index in place. On the 5 second timer, with a few PDM clients open, that is
close to continuous load for a number that changes when an electrode is
produced, roughly every ten minutes. Read it on the same 60 second timer that
calls `usp_raise_due_requests`, or when the panel is opened. `vw_qc_request`
stays on the 5 second poll as before; it is cheap.

### Noticing an interval change without paying for it

`QC_SAMPLING_WATERMARK.md` asked for this and migration `010` adds it.
`QC_SAMPLING_WATERMARK_CONTRACT.md` is the same thing on its own page, with the
guarantees spelled out:

```sql
SELECT vat, sampling_interval, updated_at_utc, row_version
  FROM qc_requests.vw_vat_sampling_watermark;
```

Three rows and no counting, so it is as cheap as the request watermark and
belongs on the same 5 second poll. Compare `MAX(row_version)` exactly as you
already do for `vw_qc_request`, and re-read the expensive status view only when
it moves.

**Nothing new is stored.** That `row_version` is the `ROWVERSION` the interval
table has carried since `002`, for QCDM's own polling, and a `ROWVERSION` moves
on every write to its row whoever makes it. The marker you asked for already
existed and has always been right. What was missing was a view with its name on
it. `sampling_interval` travels with it, so a change you have just noticed does
not need a second query to find out the new figure.

**It moves for the configuration, not for production.** Setting or changing an
interval moves it, and so does a vat getting one for the first time. Electrodes
being produced do not, so `remaining` in `vw_vat_sampling_status` can change
while this stands still. Keep the 60 second re-read for that. What this buys is
the other half: a rate QC has just typed reaches the line at once instead of
within a minute.

Setting an interval to the figure it already had moves nothing, because
`usp_vat_interval_set` skips the write when there is nothing to change. Nothing
changed, so nothing is announced.

**No count is needed beside it**, unlike `vw_qc_request`. A `ROWVERSION` cannot
announce a row that is gone, which is the usual trap with this kind of marker,
but there is no path that deletes an interval: the only write inserts or
updates, and a vat that stops being sampled keeps its row. If a delete is ever
added, this section changes with it.

## Which coupons a request wants

`QC_COUPONS_SPEC.md` asked for this and migration `008` adds it, with the
spellings you chose:

| value | meaning |
|---|---|
| `all_five` | centre and the four corners |
| `top_right` | the top right coupon only |
| NULL | not recorded, which is every request raised before `008` |

**You will not see `none` on a request.** It is a valid setting on our side and
it means QC wants nothing, which is not a request at all: the procedure raises
no row for it. So a request that exists always wants something.

### Your three questions

**1. Who sets it, and when?** QCDM, as the request is raised, and it does not
change afterwards, exactly as you assumed. A request that has gone to
production means what it meant when it was sent; a technician part way through
cutting one does not get the answer changed under them because somebody edited a
setting. The one exception is an electrode **you** offer: it arrives with NULL,
because production proposes the electrode and QC decides what it wants from it,
and QC fills it in when it accepts. It is set before the state leaves
`awaiting_qc`, so a row you see as `to_be_sent` always has its coupons.

**2. Is there a sensible default?** No, and deliberately. NULL means not
recorded and only ever appears on rows that predate this. Every new request
carries a real value, so a blank cell is history rather than a gap.

**3. Are three values enough?** Yes for now, and a fourth is cheap on our side:
the values are rows in `qc_requests.coupon_choice`, not a constraint. Tell us
before you draw it and we will add the row.

### What decides it

QC sets three standing choices, one per reason a request is raised:

| reason | what it covers | today |
|---|---|---|
| automatic sampling | the interval rule | all five |
| SEM failure | the electrodes around a crack | top right |
| IC failure | the electrodes around an IC failure | nothing |

A hand raised request is asked for one at a time rather than taking a setting.
An electrode whose SEM **and** IC both failed takes the **stricter** of the two,
where strictness runs nothing, top right, all five.

**One consequence reaches you.** With IC set to nothing, an IC failure no longer
produces any requests for its neighbours at all. Those requests used to arrive
in a burst of six after a finalise, so expect fewer, not a fault.

## Grants

Migration `004` issues these, conditionally on the user existing:

```sql
GRANT SELECT  ON OBJECT::qc_requests.vw_qc_request                  TO db_production_data_manager_user;
GRANT EXECUTE ON OBJECT::qc_requests.usp_request_offer              TO db_production_data_manager_user;
GRANT EXECUTE ON OBJECT::qc_requests.usp_request_production_approve TO db_production_data_manager_user;
GRANT EXECUTE ON OBJECT::qc_requests.usp_request_production_decline TO db_production_data_manager_user;
GRANT EXECUTE ON OBJECT::qc_requests.usp_request_mark_sent          TO db_production_data_manager_user;
GRANT EXECUTE ON OBJECT::qc_requests.usp_raise_due_requests         TO db_production_data_manager_user;
```

And `006` adds the one for the status view:

```sql
GRANT SELECT ON OBJECT::qc_requests.vw_vat_sampling_status          TO db_production_data_manager_user;
```

And `010` the one for the interval watermark:

```sql
GRANT SELECT ON OBJECT::qc_requests.vw_vat_sampling_watermark       TO db_production_data_manager_user;
```

The interval table's own view, `vw_vat_sampling_interval`, stays ungranted for
the same reason: it is QCDM's read, it names the column as the table does, and
granting it would turn one of our working parts into a contract.

The two views it is built from, `vw_sampling_position` and
`vw_tested_electrode`, are deliberately not granted. Ownership chaining covers
reading them through the status view, and they are our working parts rather
than anything you should read directly or depend on.

Named objects, never the schema. A grant on the schema would hand PDM whatever
is added next without anybody deciding to, and would hand it QC's half of the
workflow as well.

**No table permissions at all.** The state guard lives where it cannot be
bypassed, QCDM can reshape the tables underneath without touching PDM's
grants, and neither application holds rights it could misuse.

Check they landed, as PDM's own login:

```sql
SELECT HAS_PERMS_BY_NAME('qc_requests.vw_qc_request', 'OBJECT', 'SELECT')  AS can_read,
       HAS_PERMS_BY_NAME('qc_requests.usp_request_mark_sent', 'OBJECT', 'EXECUTE') AS can_write;
```

If `004` ran before `db_production_data_manager_user` existed on that database,
it printed a line saying the grants were skipped and carried on. Re-run `004`;
it is safe to run again.

## Things deliberately left out

- **No traceability.** Nothing records who approved, declined or sent
  anything. Every operator of either application connects as the same
  contained user, so the column would be one constant on every row.
- **No reason on a decline.** It is always the same reason in practice: the
  electrode is physically hard to retrieve.
- **No history of the sampling interval.** It is a setting, not a record.
  Changing it updates the row and does not disturb requests already raised.
- **No way to withdraw a request.** There is no "cancel". Declining is the
  answer to a request you cannot fulfil.

## What the rule needs to be in place

`usp_raise_due_requests` reads `dbo.MetaData` and `dbo.ProcessData` to work
out which electrode is next in a vat, so **the electrode list has to be in the
same database as `qc_requests`**. On production it is. If it is ever not, the
procedure fails on the missing table. It used to return a note instead, and
that note was the bug `005` fixes: it was decided by `OBJECT_ID`, which returns
NULL for a table the caller holds no rights on, so PDM got "no electrode list"
for a table that was right there.

It only counts electrodes whose vat is unambiguous: `B` markings on the
production recipes, in vats 28, 29 or 30, and exactly one of them. The process
log records the plan and a handful of electrodes list two vats; those are left
out of the rule entirely, which is the same thing QCDM's screen does with them.

A vat with no interval set is not sampled at all. There is no default hiding in
the SQL, so a vat that appears on the line waits for somebody in QCDM to set
one.

## Before production

Applied on **dataloggingTest** only. For **dataloggingDev**, run in order:

```
sql/qc_requests/001_create_qc_requests_schema.sql
sql/qc_requests/002_create_vat_sampling_interval.sql
sql/qc_requests/003_create_request_tables.sql
sql/qc_requests/004_create_request_procedures.sql
sql/qc_requests/005_fix_caller_visibility.sql
sql/qc_requests/006_vat_sampling_status.sql
sql/qc_requests/007_count_from_the_last_request.sql
sql/qc_requests/008_coupon_settings.sql
sql/qc_requests/009_decline_after_approval.sql
```

**`005` is required before PDM can call the sampling rule at all.** Without it
`usp_raise_due_requests` returns `raised = 0` for any caller that holds no
rights under `dbo`, which is PDM. Run it on Test as well; Test had 001 to 004
applied before `005` existed.

Each one checks its own prerequisites and refuses if run out of order. `002`
seeds vats 28, 29 and 30 at an interval of 25; change them in QCDM's QC
Sampling tab, not in SQL.

One thing to expect on the first run of the rule against real data: it counts
from the last electrode **tested** in each vat, so if a vat has not been
sampled for a while it will raise one request per interval since. On the test
database that came to 82. On production it should be a handful, but look at
`SELECT COUNT(*) FROM qc_requests.vw_qc_request` straight afterwards rather
than assuming.
