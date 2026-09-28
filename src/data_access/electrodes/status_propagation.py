"""Spreading a QC decision to the untested electrodes around it.

A verdict on one electrode says something about its neighbours, because they
came off the same line minutes apart:

* **Released** — everything untested *before* it is released too, back as far as
  the previous electrode of that same vat that already has a verdict. The vat
  was evidently plating properly over that stretch; the other vats running
  alongside it are a separate question and are left alone.
* **Scrapped** — the two electrodes either side of it are scrapped too, whatever
  they read before unless somebody actually tested them, and the doubt carries
  on outward from there. A coating fault is not assumed to start
  and stop at exactly one electrode. Counted along the electrodes plated in the
  *same vat*: a fault is the vat's doing, so the electrodes it casts doubt on
  are the ones that went through that vat, not the ones that happened to follow
  on the line.
* **Scrapped with no vat on record** — nothing says which run it belonged to, so
  it falls back to the production sequence and is kept short: the two either
  side scrapped, the third each way flagged for testing, and no further. A
  guess about the line is worth two electrodes, not a whole stretch of it.

Both mirror the workbook's ``AnalyzeQCRanges`` macro. Neither ever overwrites an
electrode that already has a verdict: a real measurement always beats an
inference drawn from a neighbour. For the same reason a RELEASED electrode is a
wall: doubt spreading from a scrap stops when it reaches one, rather than
stepping over it to condemn whatever is on the far side. Testing one electrode
therefore settles everything the fault could no longer have reached.

Pure functions — no Qt, no DB, no I/O — so the rules can be tested directly.
"""

from __future__ import annotations

import re

from src.data_access.electrodes.electrode_columns import ElectrodeStatus

# How far a scrap's doubt reaches, counted in electrodes each way.
SCRAP_MARGIN = 2

# Between the vats an electrode was plated in, as the process log spells it.
VAT_SEPARATOR = ","

_RELEASED = str(ElectrodeStatus.RELEASED)
_SCRAPPED = str(ElectrodeStatus.SCRAPPED)
_NOT_TESTED = str(ElectrodeStatus.NOT_TESTED)

# A decision arrives as the *tested* variant; what it spreads to neighbours is
# the plain one, because those electrodes were never actually tested.
_RELEASED_FAMILY = {_RELEASED, str(ElectrodeStatus.TESTED_AND_RELEASED)}
_SCRAPPED_FAMILY = {_SCRAPPED, str(ElectrodeStatus.TESTED_AND_SCRAPPED)}
_NEEDS_TESTING = str(ElectrodeStatus.NEEDS_TESTING)

# What stops a scrap's doubt travelling: an electrode somebody actually put on
# a rig and passed. A plain Released is not that. It is either a range record
# or itself an inference drawn from a pass further along, and a guess has no
# business bounding another guess. Kept separate from _RELEASED_FAMILY, which
# still answers the different question of which verdicts spread a release.
_PROVEN_GOOD = {str(ElectrodeStatus.TESTED_AND_RELEASED)}

# What a sweep may claim: an electrode nobody has actually measured. Not
# Tested is the obvious one; Needs testing is a request for a measurement, not
# a measurement; and a plain Released is a guess drawn from a pass somewhere
# else. A real verdict is never claimed: a scrapped one keeps it and the walk
# carries on past, a tested-and-passed one is the wall and stops it.
_UNMEASURED = {_NOT_TESTED, _NEEDS_TESTING, _RELEASED}
_TEST_ONGOING = str(ElectrodeStatus.TEST_ONGOING)

# The first electrode beyond the scrap margin, each way. These are where the
# fault most likely started or stopped, so they're the ones to test first.
PRIORITY_DISTANCE = SCRAP_MARGIN + 1


def electrode_number(marking: str) -> int | None:
    """'B14174' → 14174. None when the marking carries no number."""
    match = re.search(r"(\d+)", str(marking))
    return int(match.group(1)) if match else None


def propagate(markings: list[str], base_status: dict[str, str],
              decisions: dict[str, str]) -> dict[str, str]:
    """Effective status per electrode, after spreading ``decisions``."""
    return propagate_detailed(markings, base_status, decisions)[0]


def propagate_detailed(
    markings: list[str], base_status: dict[str, str], decisions: dict[str, str],
    vats: dict[str, str] | None = None, ongoing: set[str] | None = None,
    pinned: set[str] | None = None,
) -> tuple[dict[str, str], dict[str, str], set[str]]:
    """Effective statuses, which decision produced each, and the test-first set.

    Args:
        markings: Every electrode in play; order is irrelevant, they get sorted
            by number here.
        base_status: What each electrode reads without any board decision
            (the ranges file, plus anything already finalised). Missing =
            Not Tested.
        decisions: The explicit board verdicts, ``{marking: status}``.
        vats: ``{marking: vat}`` from the process log. An electrode the log
            gives two vats for has no single vat and takes no part in one.
        pinned: Electrodes whose status an operator set by hand. No sweep may
            claim one: a correction is a statement about that electrode, and
            an inference drawn from a neighbour has no business overruling it.
            Told apart by being pinned rather than by what they read, since a
            hand set Released is indistinguishable from a swept one.
        ongoing: Electrodes on the QC board that nobody has decided yet. They
            read Test ongoing whatever they read otherwise, and lose their
            test-first flag: somebody is already testing them. Nothing is
            stored, so taking one off the board restores what it said before.

    Returns:
        ``({marking: status}, {marking: caused_by}, {test first}, {marking:
        every decision that contributed})``. The second map names ONE cause,
        the one to record against a finalised status. The fourth names them
        all, because a status can genuinely have more than one: a stretch
        cleared between two passes needs both of them to stay cleared, and
        anything asking "would this change if that card moved" has to know
        about each.
    """
    numbered = sorted(
        ((electrode_number(m), m) for m in markings if electrode_number(m) is not None),
        key=lambda pair: pair[0],
    )
    if not numbered:
        return {m: base_status.get(m, _NOT_TESTED) for m in markings}, {}, set()

    order = [marking for _, marking in numbered]
    position = {number: index for index, (number, _) in enumerate(numbered)}
    status = [decisions.get(m) or base_status.get(m, _NOT_TESTED) for m in order]
    # An explicit verdict is its own cause.
    sources: dict[str, str] = {m: m for m in order if m in decisions}
    causes: dict[str, set[str]] = {m: {m} for m in order if m in decisions}

    # Which run each scrap's doubt travels along: the whole of production, or
    # one vat's electrodes.
    lanes = _lanes(order, vats or {}, decisions)
    held = set(pinned or ())

    # Order matters. Margins first, since they only claim untested electrodes.
    # Then the doubt around each scrap. Releases run last so they can clear the
    # doubt they actually resolve — and leave the doubt they don't.
    priorities: set[str] = set()
    for marking, verdict in decisions.items():
        if verdict in _SCRAPPED_FAMILY and marking in lanes:
            run, at, _bounded = lanes[marking]
            _scrap_margin(status, order, sources, causes, marking, run, at, held)
    for marking, verdict in decisions.items():
        if verdict in _SCRAPPED_FAMILY and marking in lanes:
            priorities |= _needs_testing(status, order, sources, causes,
                                         marking, *lanes[marking], pinned=held)
    _apply_release_sweeps(status, position, decisions, order, sources,
                          causes, priorities, lanes, held)

    effective = dict(zip(order, status))
    for marking in markings:
        effective.setdefault(marking, base_status.get(marking, _NOT_TESTED))

    # Last, over the top of everything else. Being on the board beats every
    # other reading, including a recorded one: whatever this electrode used to
    # say, somebody has it on a rig now and the answer is about to be replaced.
    # A scrap two along only ever said the coating MIGHT be bad here, and an
    # older verdict is what the test under way exists to revisit.
    #
    # Nothing is written for this. The status is computed on every render, so
    # taking the card off the board simply stops this overlay applying and the
    # electrode reads whatever it read before, verdict and all.
    for marking in (ongoing or ()):
        # No exception for an electrode that already has a verdict. It may well
        # have one, a finalised result from an earlier round, and being back on
        # the board is exactly the statement that the result is being revisited.
        # Its old verdict still drives its neighbours until a new one replaces
        # it; only the electrode itself reads as under test.
        if marking not in effective:
            continue
        effective[marking] = _TEST_ONGOING
        priorities.discard(marking)
        # Credited to ITSELF, replacing whatever swept it. What this electrode
        # now reads comes from its own card and nothing else, so naming the
        # scrap that happened to reach it sent the operator off to finalise a
        # decision that is not holding this row at all. A pinned one is
        # credited here too: every sweep skips it, so it had no cause and no
        # lock despite sitting on the board.
        sources[marking] = marking
        causes[marking] = {marking}
    return effective, sources, priorities, causes


def _single_vat(marking: str, vats: dict[str, str]) -> str:
    """The one vat an electrode was plated in, or "" when that is not settled.

    The process log records the plan, and a handful of electrodes list two
    vats. Those belong to no vat's run: which one actually did the plating is
    exactly what is not known, so a crack in either cannot be pinned on them.
    """
    vat = str(vats.get(marking, "") or "").strip()
    return "" if not vat or VAT_SEPARATOR in vat else vat


def _lanes(order: list[str], vats: dict[str, str],
           decisions: dict[str, str]) -> dict[str, tuple[list[int], int, bool]]:
    """``{decided marking: (the run, where it sits in it, keep it short)}``.

    Every decision gets its vat's run: a scrap to spread its doubt along, a
    release to clear along. One with no settled vat gets the production
    sequence instead, and the third element says to keep that walk short: a
    fallback is a guess about the line, and a guess should not condemn a long
    stretch of it.
    """
    whole = list(range(len(order)))
    index_of = {marking: index for index, marking in enumerate(order)}

    by_vat: dict[str, list[int]] = {}
    for index, marking in enumerate(order):
        vat = _single_vat(marking, vats)
        if vat:
            by_vat.setdefault(vat, []).append(index)

    lanes: dict[str, tuple[list[int], int]] = {}
    for marking, verdict in decisions.items():
        if verdict not in _SCRAPPED_FAMILY and verdict not in _RELEASED_FAMILY:
            continue
        index = index_of.get(marking)
        if index is None:
            continue
        vat = _single_vat(marking, vats)
        run = by_vat.get(vat) if vat else None
        if run is None:
            lanes[marking] = (whole, index, True)
        else:
            lanes[marking] = (run, run.index(index), False)
    return lanes


def _credit(sources: dict[str, str], causes: dict[str, set[str]],
            marking: str, source: str, primary: bool = True) -> None:
    """Record one decision behind one electrode's status.

    ``primary`` is the cause worth naming when only one can be; every call adds
    to the full set either way.
    """
    if primary:
        sources[marking] = source
    else:
        sources.setdefault(marking, source)
    causes.setdefault(marking, set()).add(source)


def _scrap_margin(status: list[str], order: list[str], sources: dict[str, str],
                  causes: dict[str, set[str]],
                  marking: str, run: list[int], at: int,
                  pinned: set[str] | None = None) -> None:
    """Scrap the SCRAP_MARGIN electrodes either side, along ``run``.

    Counted in electrodes of that run rather than by marking, since the
    numbering has gaps and B13877 not existing shouldn't cost B13881 its
    margin. Only untested electrodes are claimed.

    Walked outward one at a time rather than taken as a flat span, because a
    A hand set electrode is skipped but does NOT stop the walk: the operator
    spoke about that one electrode, not about whether a fault reached past it.

    released electrode in between STOPS IT. Somebody put that one on a rig and
    it came back good, so the fault did not reach that far, and the doubt has
    no business stepping over it to condemn the electrode beyond. A scrap in
    the way is different: that one is already condemned and the walk carries on
    past it, so two nearby faults read as one zone.
    """
    for step in (-1, 1):
        for distance in range(1, SCRAP_MARGIN + 1):
            cursor = at + step * distance
            if not 0 <= cursor < len(run):
                break
            index = run[cursor]
            if status[index] in _PROVEN_GOOD:
                break
            if order[index] in (pinned or ()):
                continue
            if status[index] in _UNMEASURED:
                status[index] = _SCRAPPED
                _credit(sources, causes, order[index], marking)


def _needs_testing(status: list[str], order: list[str], sources: dict[str, str],
                   causes: dict[str, set[str]],
                   marking: str, run: list[int], at: int,
                   short: bool = False, pinned: set[str] | None = None) -> set[str]:
    """Flag the doubt either side of one scrap's margin, along ``run``.

    From the first electrode beyond the margin, walk outward marking untested
    ones Needs testing. Only an electrode tested and passed stops the walk — a
    scrapped one keeps its verdict and the walk carries on past it, so two
    nearby scraps produce one continuous zone rather than two stubs, and a
    plain Released is stepped over too: nobody tested it, it reads that way
    because of a pass somewhere further along.

    ``short`` stops after that first electrode. It is set for a scrap with no
    vat on record, where the run is the production sequence and the whole thing
    is an inference from which electrode happened to follow which.

    Returns the markings to test first: the electrode at exactly
    ``PRIORITY_DISTANCE`` each way, whenever that one is in doubt.

    IN DOUBT, NOT NEWLY CLAIMED. A second scrap nearby will already have walked
    over that electrode and left it Needs testing, and the flag used to go only
    to an electrode this walk claimed itself. Two scraps three apart therefore
    stole each other's flags, and which one won came down to the order the
    decisions happened to be read in. An electrode carrying a verdict of its
    own is still passed over: there is nothing left to settle on it.
    """
    priorities: set[str] = set()
    for step in (-1, 1):
        # From the scrap itself, not from the edge of the margin. The margin
        # cells are not marked here, they are only looked at: a released one
        # among them stops this side of the walk before it starts, the same way
        # it stops the margin. Jumping straight to the edge used to hop over a
        # tested-good electrode and flag the stretch beyond it.
        for distance in range(1, len(run)):
            cursor = at + step * distance
            if not 0 <= cursor < len(run):
                break
            index = run[cursor]
            if status[index] in _PROVEN_GOOD:
                break
            if distance < PRIORITY_DISTANCE:
                continue
            edge = at + step * PRIORITY_DISTANCE
            # A plain Released is reopened, not just an untested one. Nobody
            # put it on a rig; it reads that way because of a pass further
            # along. Left alone it would never appear in anybody's work queue
            # again, so an electrode inside a fault's doubt would ship without
            # ever being looked at. Only a real pass is left standing.
            if status[index] in _UNMEASURED and order[index] not in (pinned or ()):
                status[index] = _NEEDS_TESTING
                _credit(sources, causes, order[index], marking, primary=False)
            if cursor == edge and status[index] == _NEEDS_TESTING:
                priorities.add(order[index])
            if short:
                break
    return priorities


def _apply_release_sweeps(status: list[str], position: dict[int, int],
                          decisions: dict[str, str], order: list[str],
                          sources: dict[str, str], causes: dict[str, set[str]],
                          priorities: set[str],
                          lanes: dict[str, tuple[list[int], int, bool]],
                          pinned: set[str] | None = None) -> None:
    """Spread each release as far as the evidence actually reaches.

    Two separate things happen:

    * **Back along its own vat** — the vat was plating properly, so the
      untested electrodes it turned out before this one are presumed fine, back
      to the previous verdict in that vat. Its run, the same one a scrap's
      doubt travels along, because that is what the evidence is about.
    * **Either side** — a run of Needs testing is settled *only* if it doesn't
      butt up against a scrap. A run that ends at a scrap is the stretch
      between that fault and this test: we now know production had recovered by
      here, but not where, so the doubt stands. Behind, the run is released.
      Ahead, it only goes back to Not Tested: the doubt it carried came from a
      fault this test now stands between, but nothing here vouches for an
      electrode plated after the test was done.

    STRICTLY WITHIN THE VAT. A pass says the vat was plating properly; it says
    nothing about what the other vats were doing at the same moment, so the
    electrodes of those vats sitting in between are left alone.
    """
    for marking, verdict in decisions.items():
        if verdict not in _RELEASED_FAMILY:
            continue
        number = electrode_number(marking)
        if number is None or number not in position:
            continue
        index = position[number]

        if marking in lanes:
            run, at, _bounded = lanes[marking]
            step = at - 1
            # A hand set electrode ends the walk the way a verdict does. It is
            # one, just written by an operator rather than read off a rig.
            while (step >= 0 and status[run[step]] == _NOT_TESTED
                   and order[run[step]] not in (pinned or ())):
                status[run[step]] = _RELEASED
                _credit(sources, causes, order[run[step]], marking)
                step -= 1

        # Both ways, but not to the same end. Behind it the electrodes are
        # RELEASED: the vat was plating properly over that stretch. Ahead of it
        # they are only cleared back to Not Tested: the doubt they carried came
        # from a fault this test now stands between, so it no longer reaches
        # them, but a test cannot vouch for electrodes plated after it.
        run, at = (lanes[marking][0], lanes[marking][1]) if marking in lanes else (None, 0)
        for step, becomes in ((-1, _RELEASED), (1, _NOT_TESTED)):
            _clear_resolved_doubt(status, order, sources, causes, priorities,
                                  index, step, marking, run, at, becomes, pinned)


def _clear_resolved_doubt(status: list[str], order: list[str], sources: dict[str, str],
                          causes: dict[str, set[str]],
                          priorities: set[str], index: int, step: int,
                          marking: str, lane: list[int] | None = None,
                          at: int = 0, becomes: str = _RELEASED,
                          pinned: set[str] | None = None) -> None:
    """Settle one contiguous Needs-testing run beside a released electrode,
    unless the run runs into a scrap — that side's doubt is unresolved.

    ``becomes`` is what the run turns into, and it differs by direction: see
    the caller. Either way the run has to be clear of scraps, because a run
    that ends at one is the stretch between that fault and this test.

    ALONG THE VAT'S RUN, the same one the doubt travelled. Walked in production
    order it was broken by the other vats' electrodes sitting in between, so a
    run that really did end at a scrap looked like it ended at a Not Tested
    one, and this cleared doubt it had no business clearing.
    """
    if lane is not None:
        positions = [lane[i] for i in range(at + step, len(lane) if step > 0 else -1, step)]
    else:
        positions = list(range(index + step, len(status) if step > 0 else -1, step))

    found: list[int] = []
    beyond = None
    for position in positions:
        # A hand set electrode is not doubt this release gets to settle, so it
        # is stepped over rather than claimed. It does NOT end the run: ending
        # it there hid whatever lay past it, and a stretch that really ran into
        # a scrap looked clear, so the release settled doubt belonging to a
        # fault it does not stand between.
        if order[position] in (pinned or ()):
            continue
        if status[position] != _NEEDS_TESTING:
            beyond = position
            break
        found.append(position)
    if not found:
        return
    if beyond is not None and status[beyond] in _SCRAPPED_FAMILY:
        return

    # BOTH ENDS are credited. The run is clear because this release is on one
    # side of it AND something that is not a scrap is on the other; take either
    # away and the doubt comes back. Naming only the near one left the far one
    # looking innocent, and a card holding a whole stretch released read as
    # safe to drag away.
    for position_index in found:
        status[position_index] = becomes
        _credit(sources, causes, order[position_index], marking)
        if beyond is not None:
            causes[order[position_index]].add(order[beyond])
        priorities.discard(order[position_index])
