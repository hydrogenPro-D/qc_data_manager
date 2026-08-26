"""Spreading a QC decision to the untested electrodes around it.

A verdict on one electrode says something about its neighbours, because they
came off the same line minutes apart:

* **Released** — everything untested *before* it is released too, back as far as
  the previous electrode that already has a verdict. Production was evidently
  fine over that stretch.
* **Scrapped** — the two electrodes produced either side of it are scrapped too.
  A coating fault is not assumed to start and stop at exactly one electrode.
  Counted along the production sequence rather than by electrode number, so
  gaps in the numbering don't swallow the margin.
* **Scrapped with a cracked SEM result** — the same rule, counted along the
  electrodes plated in the *same vat* rather than along production. A crack is
  the vat's doing, so the electrodes it casts doubt on are the ones that went
  through that vat, not the ones that happened to follow on the line.

Both mirror the workbook's ``AnalyzeQCRanges`` macro. Neither ever overwrites an
electrode that already has a verdict: a real measurement always beats an
inference drawn from a neighbour.

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
    vats: dict[str, str] | None = None, cracked: set[str] | None = None,
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
        cracked: The scraps whose SEM result was a crack. Those sweep along
            their vat; every other scrap sweeps along production.

    Returns:
        ``({marking: status}, {marking: caused_by}, {markings to test first})``.
        The second map only covers electrodes a decision actually touched —
        it's what lets a finalised status record *why* it says what it says.
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

    # Which run each scrap's doubt travels along: the whole of production, or
    # one vat's electrodes.
    lanes = _lanes(order, vats or {}, decisions, cracked or set())

    # Order matters. Margins first, since they only claim untested electrodes.
    # Then the doubt around each scrap. Releases run last so they can clear the
    # doubt they actually resolve — and leave the doubt they don't.
    priorities: set[str] = set()
    for marking, verdict in decisions.items():
        if verdict in _SCRAPPED_FAMILY and marking in lanes:
            _scrap_margin(status, order, sources, marking, *lanes[marking])
    for marking, verdict in decisions.items():
        if verdict in _SCRAPPED_FAMILY and marking in lanes:
            priorities |= _needs_testing(status, order, sources, marking,
                                         *lanes[marking])
    _apply_release_sweeps(status, position, decisions, order, sources, priorities)

    effective = dict(zip(order, status))
    for marking in markings:
        effective.setdefault(marking, base_status.get(marking, _NOT_TESTED))
    return effective, sources, priorities


def _single_vat(marking: str, vats: dict[str, str]) -> str:
    """The one vat an electrode was plated in, or "" when that is not settled.

    The process log records the plan, and a handful of electrodes list two
    vats. Those belong to no vat's run: which one actually did the plating is
    exactly what is not known, so a crack in either cannot be pinned on them.
    """
    vat = str(vats.get(marking, "") or "").strip()
    return "" if not vat or VAT_SEPARATOR in vat else vat


def _lanes(order: list[str], vats: dict[str, str], decisions: dict[str, str],
           cracked: set[str]) -> dict[str, tuple[list[int], int]]:
    """``{scrapped marking: (the run to sweep, where it sits in that run)}``.

    A cracked scrap gets its vat's run. Everything else, including a scrap
    whose vat is not settled, gets the whole of production, which is what this
    always did.
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
        if verdict not in _SCRAPPED_FAMILY:
            continue
        index = index_of.get(marking)
        if index is None:
            continue
        vat = _single_vat(marking, vats) if marking in cracked else ""
        run = by_vat.get(vat) if vat else None
        if run is None:
            lanes[marking] = (whole, index)
        else:
            lanes[marking] = (run, run.index(index))
    return lanes


def _scrap_margin(status: list[str], order: list[str], sources: dict[str, str],
                  marking: str, run: list[int], at: int) -> None:
    """Scrap the SCRAP_MARGIN electrodes either side, along ``run``.

    Counted in electrodes of that run rather than by marking, since the
    numbering has gaps and B13877 not existing shouldn't cost B13881 its
    margin. Only untested electrodes are claimed.
    """
    for step in range(max(0, at - SCRAP_MARGIN), min(len(run), at + SCRAP_MARGIN + 1)):
        index = run[step]
        if status[index] == _NOT_TESTED:
            status[index] = _SCRAPPED
            sources[order[index]] = marking


def _needs_testing(status: list[str], order: list[str], sources: dict[str, str],
                   marking: str, run: list[int], at: int) -> set[str]:
    """Flag the doubt either side of one scrap's margin, along ``run``.

    From the first electrode beyond the margin, walk outward marking untested
    ones Needs testing. Only a released electrode stops the walk — an already
    scrapped one keeps its verdict and the walk carries on past it, so two
    nearby scraps produce one continuous zone rather than two stubs.

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
        edge = at + step * PRIORITY_DISTANCE
        cursor = edge
        while 0 <= cursor < len(run):
            index = run[cursor]
            if status[index] in _RELEASED_FAMILY:
                break
            if status[index] == _NOT_TESTED:
                status[index] = _NEEDS_TESTING
                sources.setdefault(order[index], marking)
            if cursor == edge and status[index] == _NEEDS_TESTING:
                priorities.add(order[index])
            cursor += step
    return priorities


def _apply_release_sweeps(status: list[str], position: dict[int, int],
                          decisions: dict[str, str], order: list[str],
                          sources: dict[str, str], priorities: set[str]) -> None:
    """Spread each release as far as the evidence actually reaches.

    Two separate things happen:

    * **Behind it** — untested production is presumed fine back to the previous
      verdict, as before.
    * **Either side** — a run of Needs testing is cleared *only* if it doesn't
      butt up against a scrap. A run that ends at a scrap is the stretch
      between the fault and this test: we now know production had recovered by
      here, but not where, so the doubt stands.
    """
    for marking, verdict in decisions.items():
        if verdict not in _RELEASED_FAMILY:
            continue
        number = electrode_number(marking)
        if number is None or number not in position:
            continue
        index = position[number]

        cursor = index - 1
        while cursor >= 0 and status[cursor] == _NOT_TESTED:
            status[cursor] = _RELEASED
            sources[order[cursor]] = marking
            cursor -= 1

        for step in (-1, 1):
            _clear_resolved_doubt(status, order, sources, priorities, index, step, marking)


def _clear_resolved_doubt(status: list[str], order: list[str], sources: dict[str, str],
                          priorities: set[str], index: int, step: int,
                          marking: str) -> None:
    """Release one contiguous Needs-testing run beside a released electrode,
    unless the run runs into a scrap — that side's doubt is unresolved."""
    run: list[int] = []
    cursor = index + step
    while 0 <= cursor < len(status) and status[cursor] == _NEEDS_TESTING:
        run.append(cursor)
        cursor += step
    if not run:
        return
    if 0 <= cursor < len(status) and status[cursor] in _SCRAPPED_FAMILY:
        return

    for position_index in run:
        status[position_index] = _RELEASED
        sources[order[position_index]] = marking
        priorities.discard(order[position_index])
