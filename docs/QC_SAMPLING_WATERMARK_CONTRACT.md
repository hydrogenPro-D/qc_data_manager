# vw_vat_sampling_watermark: the contract

The answer to `QC_SAMPLING_WATERMARK.md`. One view, granted to PDM, cheap
enough for the 5 second poll, that moves when QC changes how often a vat is
sampled.

Owned by the QC Data Manager project: the DDL is
`sql/qc_requests/010_sampling_interval_watermark.sql` in that repository, which
also applies it. PDM reads it and asks for changes rather than making them.

**Status: written, not yet applied to either database.** This page describes
what it will be. Nothing here is live until `010` has run.

## The view

```sql
SELECT vat, sampling_interval, updated_at_utc, row_version
  FROM qc_requests.vw_vat_sampling_watermark;
```

| column | type | what it is |
|---|---|---|
| `vat` | `nvarchar(32)` | the bare step number as a string, `'28'`, never `'Vat 28'` |
| `sampling_interval` | `int` | test one electrode in every N, between 1 and 1000 |
| `updated_at_utc` | `datetime2` | when the interval was last changed, UTC |
| `row_version` | `rowversion` (`binary(8)`) | what you compare |

One row per vat **that has an interval set**. A vat with no row is not sampled
and no request is ever raised for it, which is the same rule
`vw_vat_sampling_status` reports as a NULL `sampling_interval`. Today there are
three rows, 28, 29 and 30.

## How to poll it

Exactly as you already poll `vw_qc_request`:

```sql
SELECT MAX(row_version) FROM qc_requests.vw_vat_sampling_watermark;
```

Re-read `vw_vat_sampling_status` when it moves, and not otherwise. Three rows
and no counting, so this belongs on the 5 second timer, not the 60 second one.

You can also read all four columns on that poll if it suits your code better.
It is the same cost at this size, and then a change brings the new figure with
it: `sampling_interval` is there so that noticing a change and learning what it
changed to are one query, not two.

## What it is

**Nothing new is stored.** `row_version` is the `ROWVERSION` that
`qc_requests.vat_sampling_interval` has carried since migration `002`, for
QCDM's own polling. A `ROWVERSION` is maintained by SQL Server and moves on
every insert and update to its row, whichever application makes the write. So
the marker you asked for already existed and has always been correct. What was
missing was a view with its name on it and a grant.

That matters for your risk assessment: there is no new trigger, no new column
to be kept in step by hand, and no code path that could forget to bump it.

## What moves it, and what does not

**It moves when:**

* QC sets an interval to a different figure.
* A vat gets an interval for the first time.

**It does not move when:**

* Electrodes are produced. `remaining` in `vw_vat_sampling_status` counts down
  as the line plates, and this watermark stands still while it does. Keep your
  60 second re-read for that. What this buys you is the other half: a rate QC
  has just typed reaches the line at once rather than within a minute.
* A request is raised, approved, declined or sent. That is
  `vw_qc_request`'s watermark, which you already poll.
* An interval is set to the figure it already had. `usp_vat_interval_set`
  skips the write when there is nothing to change, so nothing changed and
  nothing is announced. Do not treat a no-op save as a change you missed.

## Guarantees

What this view promises, so you can build on it:

* **The four column names and their meanings do not change.** Anything added
  is added on the end.
* **`row_version` only ever increases**, database-wide, as `ROWVERSION` does.
  Comparing for inequality is enough; ordering also works if you want it.
* **No row is ever deleted.** A `ROWVERSION` cannot announce a row that is
  gone, which is the usual trap with this kind of marker. There is no delete
  path: `usp_vat_interval_set` inserts or updates and nothing else, and a vat
  that stops being sampled keeps its row. **This is why you do not need a
  `COUNT(*)` beside the version**, unlike `vw_qc_request`.
* **If a delete path is ever added**, this view gains a row count and you are
  told before it lands.
* **`vat` agrees with `vw_qc_request.vat` and `vw_vat_sampling_status.vat`.**
  Same spelling, same source, joinable.

What it does **not** promise:

* That `sampling_interval` here and in `vw_vat_sampling_status` are read in the
  same instant. They come from the same table, so they cannot disagree about a
  settled value, but two separate queries a second apart can of course straddle
  a change. If that matters, read the status view after the watermark, not
  before.

## The grant

`010` issues it, conditionally on the user existing:

```sql
GRANT SELECT ON OBJECT::qc_requests.vw_vat_sampling_watermark TO db_production_data_manager_user;
```

Check it landed, as PDM's own login:

```sql
SELECT HAS_PERMS_BY_NAME('qc_requests.vw_vat_sampling_watermark', 'OBJECT', 'SELECT') AS can_read;
```

If `010` runs on a database where `db_production_data_manager_user` does not
exist yet, it prints a line saying the grant was skipped and carries on.
Re-running `010` is safe and issues it.

**`vw_vat_sampling_interval` stays ungranted**, deliberately. It is QCDM's own
read of the same table, it names the column `electrodes_between` as the table
does, and granting it would turn one of our working parts into a contract we
then could not change. The watermark view names the column `sampling_interval`
so that everything PDM reads agrees with everything else PDM reads.

No table permissions, as everywhere else in this schema.

## Before production

`010` has to run on `dataloggingDev` and then on the production database, in
order, after `001` to `009`. None of the `qc_requests` migrations are on Dev
yet. Poll the old way until you have checked the grant on the database you are
actually pointed at.
