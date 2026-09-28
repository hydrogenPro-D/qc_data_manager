"""What a doubting electrode is actually waiting for.

``Needs testing`` says an electrode sits inside a fault's doubt, not that
anybody intends to put this one on a rig. The two the propagation flags as
test-first are the ones to test; the rest of the zone is waiting on whatever
those turn up. This works out which of the two waits each one is in.

The zone is shared cause, not distance: two failures close together make ONE
continuous zone whose electrodes record both origins, so "shares any cause with
me" is the right question and it needs no special case for either shape.

Pure functions, no Qt and no DB. The findings come in as a callable so the QC
board stays on the other side of the boundary.
"""

from __future__ import annotations

from typing import Callable

from src.data_access.electrodes.electrode_columns import ElectrodeStatus
from src.data_access.electrodes.status_propagation import electrode_number
from src.data_access.qc_board.qc_board_columns import SEM_ACCEPTED, SEM_RESULT

_NEEDS_TESTING = str(ElectrodeStatus.NEEDS_TESTING)
_WAITING_FOR_SEM = str(ElectrodeStatus.WAITING_FOR_SEM)
_WAITING_FOR_IC = str(ElectrodeStatus.WAITING_FOR_IC)


def _distance(marking: str, other: str) -> int:
    """How far apart two electrodes are, for picking the nearest tester.

    By number, which inside one vat's run is the same order as the run itself:
    a zone only ever spans one lane, because that is the lane its doubt
    travelled along.
    """
    here, there = electrode_number(marking), electrode_number(other)
    return abs(here - there) if here is not None and there is not None else 10 ** 9


def _speaks_for(marking: str, tester: str, faults: set[str]) -> bool:
    """Whether that tester's result will settle this electrode.

    Only on its FAR side, away from the fault. A pass proves the vat had
    recovered by the time the tester was plated, not where it recovered, so
    the stretch between the tester and the fault stays in doubt and the test
    says nothing about it. The tester therefore has to sit on the same side of
    the fault and nearer to it than we are.
    """
    here, there = electrode_number(marking), electrode_number(tester)
    if here is None or there is None:
        return False
    for fault in faults:
        at = electrode_number(fault)
        if at is None:
            continue
        if (there - at) * (here - at) > 0 and abs(there - at) < abs(here - at):
            return True
    return False


def waiting_statuses(effective: dict[str, str], causes: dict[str, set[str]],
                     ongoing: set[str], test_first: set[str],
                     findings: Callable[[str], dict] | None = None
                     ) -> dict[str, str]:
    """``{marking: which wait it is in}`` for the doubting electrodes.

    Args:
        effective: Status per electrode, straight out of the propagation.
        causes: Every decision behind each electrode's status.
        ongoing: On the QC board and not yet decided, so still being tested.
        test_first: The electrodes to pull first. These keep Needs testing:
            it is an instruction about themselves, not a wait on somebody else.
        findings: ``marking -> {sem_result: ..., ic_result: ...}``.

    Only a tester standing BETWEEN this electrode and the fault decides, and
    of those the nearest. One on the far side of the fault, or further out than
    we are, will never settle us however its tests go. The IC result is
    deliberately not read: a card is waited on until it reaches a terminal
    column and is finalised, by which point the propagation has resolved the
    zone anyway.
    """
    doubting = [marking for marking, status in effective.items()
                if status == _NEEDS_TESTING and marking not in test_first]
    if not doubting:
        return {}

    # Only the ones being tested can end a wait, so the rest of the board is
    # of no interest here.
    testers = [marking for marking in ongoing if causes.get(marking)]
    out: dict[str, str] = {}
    for marking in doubting:
        zone = causes.get(marking) or set()
        mine = [t for t in testers
                if causes[t] & zone and _speaks_for(marking, t, zone)]
        out[marking] = _WAITING_FOR_IC if _cleared_sem(mine, marking, findings) \
            else _WAITING_FOR_SEM
    return out


def _cleared_sem(testers: list[str], marking: str,
                 findings: Callable[[str], dict] | None) -> bool:
    """Whether the nearest electrode being tested has passed SEM.

    Ties break towards the one that has passed: equidistant either side, the
    further-along answer is the more informative of the two.
    """
    if not testers or findings is None:
        return False
    nearest = min(testers, key=lambda t: (
        _distance(marking, t),
        0 if str((findings(t) or {}).get(SEM_RESULT, "")) == SEM_ACCEPTED else 1))
    return str((findings(nearest) or {}).get(SEM_RESULT, "")) == SEM_ACCEPTED
