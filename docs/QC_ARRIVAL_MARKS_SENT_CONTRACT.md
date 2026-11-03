# A card on the QC board marks the request sent: the contract

A request can now reach `sent` without PDM doing anything. This page says when,
why, and what it means for you.

Owned by the QC Data Manager project: the DDL is
`sql/qc_requests/012_arrival_marks_it_sent.sql`, widened by `013`, `014` and `015`,
in that repository, which also applies it. PDM reads the state and asks for changes
rather than making them.

**Status: `012` to `014` are live on dataloggingTest. `015` is written and
waiting.** Nothing is on dataloggingDev yet.

## The problem it closes

QC asks for an electrode. Somebody fetches it and walks it over. Nobody marks
it sent in PDM.

The request then sits in `awaiting_production` or `to_be_sent` for ever. It
reads as work outstanding on both screens, QC is chased for an electrode they
already have, and a fortnight later `011` expires it as unanswerable, which is
the one thing it is not. It *was* answered. The answer just never got written
down.

Nothing in either application says "this physical electrode is here" except a
card on the QC board. So the card is what closes the request.

## The rule

```sql
EXEC qc_requests.usp_request_arrived_at_qc @electrode = N'B16190';  -- -> changed
```

When an electrode is put on the QC board, any request for it in **one of the
three live states becomes `sent`**. QCDM calls this after every card, and
nothing else calls it.

| state before | after a card | why |
|---|---|---|
| `awaiting_production` | **`sent`** | production never answered, but it is here |
| `to_be_sent` | **`sent`** | they agreed and forgot the last tick |
| `awaiting_qc` | **`sent`** | QC accepted the offer by taking the electrode |
| `declined` | **`sent`** | production did not send it; it got here anyway |
| `expired` | **`sent`** | nobody could be asked; it got here anyway |
| `sent` | unchanged | already right |
| *(no request at all)* | a new row, **`sent`** | see below |

It is safe to call on anything. An electrode whose request is already
finished with changes nothing and reports `changed = 0`.

## An electrode nobody asked for gets a request anyway

`014` answers `QC_BOARD_WITHOUT_REQUEST.md`. Where a card appears and **no
request exists at all**, one is created, already `sent`, with kind `manual`:

```
no request for this electrode  +  a card appears  ->  a request, state 'sent'
```

Which is your first suggestion, adopted as written. The card is evidence the
electrode reached QC, `sent` is what that means, and `manual` reads correctly:
QC asked for it informally, by walking over. B15719 happened because the
arrival was never recorded anywhere you could see, so your screen was right to
offer Propose for QC and the hole was ours.

Two details:

* **No coupons on it.** Nobody chose any, because nobody raised the request.
  Empty says "not recorded", which is true; filling in the default would invent
  an instruction nobody gave. Expect no coupon icon on these rows.
* **No window check.** This records a fact rather than asking for anything, so
  the 30 days do not apply. A card on a six month old electrode still writes
  its `sent` row.

## This is not a grant of `usp_request_mark_sent`

That procedure is still yours alone, and it still means what it meant:
production handed the electrode over. This is a different statement, made by a
different application, from different evidence, and it is deliberately a
separate procedure so that neither grant implies the other. QCDM is granted
this one and not yours; you are granted yours and not this one.

## What it means for PDM

**A row can move to `sent` while a technician is looking at it.** Nothing new
is needed to handle that. Every move you make passes the state you had on
screen, and a row that has since been marked sent matches nothing, so you get
`changed = 0` rather than a wrong move. The only change is that `changed = 0`
will occasionally mean "QC already has it" rather than "somebody else answered
first". If your message for that case names production, it may be worth
widening the wording.

**You will see fewer expiries.** The 14 day backstop in `011` was catching
these, and catching them wrongly. Requests that are genuinely answered by hand
now close as `sent` on the day the electrode arrives.

**Your `sent` count becomes more accurate, not less.** Every row this moves is
an electrode that really did reach QC. Before, the same electrode was at QC
with a request that said otherwise.

## Your second point was already answered

You asked us to decide what a card does to an open proposal, either way. We had
already chosen the first of your two options the day before, in `013`, for the
same reason you give: an offer QC has answered by taking the electrode is
answered, whatever the row says. The section below was written before your
report and stands unchanged.

**It does not reach B15719**, though, and that is worth being clear about: that
card had already been finalised and taken off the board, so there was no card
left for `013` to work from. `014`'s catch up closes it instead, by a different
piece of evidence, below.

## An open offer closes too, and why that changed

`012` left `awaiting_qc` alone, on the argument that an offer is QC's question
to answer. That was wrong, and B15779 showed it: you offered it on 13.10, QC
took the electrode and put it on the board, and the row sat at `awaiting_qc`.
QCDM stopped showing it, because an electrode on a rig has nothing to ask
about, while your screen read **"proposal pending"** for an electrode QC had
been holding for nine days. The offer had been accepted in the only way that
matters, and nobody was ever going to press anything.

So `013` includes it. A card means both things at once, QC accepted and the
electrode arrived, both evidenced by the same card, so the row goes straight to
`sent` rather than through `to_be_sent` and out again in the same instant.
Nothing records the steps a request took, only where it is, so a state it would
have held for no time is not something anybody loses.

**What this means for your screen:** an offer can be accepted without you
seeing `to_be_sent` in between. If you count offers accepted, count arrivals at
`sent` rather than transitions through `to_be_sent`.

## A terminal row can now move, and this is a change

**Please read this one.** `012` to `014` left `declined` and `expired` alone,
and both this page and `QC_REQUEST_EXPIRY_CONTRACT.md` promised you that a
final row never moves again. `015` withdraws that promise for one case: a card.

B15749 is why. It was requested, you declined it, and QC got hold of the
electrode anyway and put it on a rig. The request stayed `declined`, so both
screens said "Request declined" about an electrode sitting in QC. The row was
stating something false.

The principle we had was right about **decisions** and wrong about **facts**.
`declined` and `expired` are both claims about where the electrode is: nobody
is sending it, nobody could be asked for it. A card is the electrode being
here, which settles the question those states were guessing at. So a card now
means `sent` from wherever the row was, and the only state left alone is `sent`
itself.

**What this costs you.** Nothing can land wrongly: your state guard still
passes the state you had on screen, so a row that has moved matches nothing and
you get `changed = 0`. The one thing that would break is **caching a terminal
row as settled for ever and never re-reading it**. If you do that anywhere,
stop: `is_final` now means "nobody is waiting on an answer", not "this row is
frozen".

**What it costs us.** The row stops recording that it was ever declined.
Nothing here keeps a history, only a current state, so there is no column that
could hold both; `updated_at_utc` shows when it changed. If knowing that
production refused an electrode QC then tested anyway turns out to matter, say
so and we will talk about a history table.

## Guarantees

* **The card is written first.** The request is only closed after the card has
  landed, so an electrode whose card failed to save has not arrived anywhere
  and its request does not move.
* **A failure to close the request never costs the card.** If the request
  schema is out of reach, the card is still on the board and the request keeps
  its state, which is exactly how it behaved before this existed. Somebody
  closes it by hand as they do today.
* **No other state is touched**, ever, and the two that are move only to
  `sent`.
* **It is idempotent.** Re-adding a card changes nothing the second time:
  the row is already `sent`.
* **One row per electrode, still.** The create path only fires where there is
  no request at all; everything else is a move of the row already there.
* **`sent` is the only state a card cannot change**, because it is already
  what a card would set.

## The ones already there

`013` closes the requests of every electrode **standing on the board** when it
runs, because the procedure fires when a card is added and those cards were
added before it existed.

`014` then closes any open request whose electrode has **already been tested**,
read from its own archived verdict. That is what catches B15719, whose card is
long gone. Only an electrode's own verdict counts: one swept by somebody else's
decision carries an archive row and never went anywhere near a rig.

On `dataloggingTest` that is **B15719 and nothing else**. B16194 is a genuine
open offer and is left alone, as you asked.

**Nothing is backfilled.** Ten tested electrodes there have no request at all,
from before any of this existed. Creating `sent` requests for them would invent
a record of arrivals nobody logged, and would move your "sent in the last 7
days" counts for days that are already over. The new rule applies to cards from
here on.

## Before production

`012`, `013` and `014` have to run on `dataloggingDev`, which despite its name
is the production database,
after `001` to `011`. None of the `qc_requests` migrations are on Dev yet.
