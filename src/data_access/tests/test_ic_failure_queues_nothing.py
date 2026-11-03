"""An IC failure QC wants no coupons from queues nobody and asks for nobody.

The setting is read in two places and they have to agree. The Electrodes tab
computes what is on screen; the finalise computes what gets FROZEN and what
production is then asked for. The second one is the one that writes, and it was
left without the rule: a failure that showed no zone on screen still froze one,
fifty two rows of it, and raised a request for an electrode nobody was ever
going to cut a coupon from.

So the rule is checked where it is written, not only where it is drawn.
"""

import unittest

from src.data_access.db_manager import DbManager
from src.data_access.electrodes.electrode_columns import ElectrodeStatus
from src.data_access.qc_board.qc_board_columns import (IC_FAILED, IC_RESULT,
                                                       SEM_CRACKED, SEM_RESULT)
from src.data_access.qc_requests.coupon_columns import (ALL_FIVE, NONE,
                                                        REASON_IC, REASON_SEM,
                                                        TOP_RIGHT)

SCRAPPED = str(ElectrodeStatus.TESTED_AND_SCRAPPED)
FAILURE = "B16300"
NEIGHBOUR = "B16290"


class _Board:
    """Only what the coupon rules ask of the board."""

    def __init__(self, findings) -> None:
        self._findings = findings

    def findings(self, marking: str) -> dict:
        return dict(self._findings.get(marking, {IC_RESULT: "",
                                                 SEM_RESULT: ""}))

    def caused_by(self, marking: str) -> str:
        return ""


class _Backend(DbManager):
    """The coupon decisions alone, with no connection behind them."""

    def __init__(self, findings, settings) -> None:
        self._coupons = dict(settings)
        self._qc_board = _Board(findings)
        self._decisions = {FAILURE: SCRAPPED}

    def _sweeping_decisions(self) -> dict:
        return dict(self._decisions)


def _backend(ic_failed=True, sem_failed=False, **settings):
    found = {FAILURE: {IC_RESULT: IC_FAILED if ic_failed else "",
                       SEM_RESULT: SEM_CRACKED if sem_failed else ""}}
    return _Backend(found, {REASON_SEM: TOP_RIGHT, REASON_IC: NONE,
                            **settings})


class TestWhichFailuresQueueTheirNeighbours(unittest.TestCase):

    def test_an_ic_failure_queues_nobody(self) -> None:
        """The bug: IC asks for no coupons, so there is nothing to queue."""
        self.assertEqual(_backend()._unqueued_scraps(), {FAILURE})

    def test_a_sem_failure_queues_as_before(self) -> None:
        self.assertEqual(
            _backend(ic_failed=False, sem_failed=True)._unqueued_scraps(),
            set())

    def test_both_failed_takes_the_stricter_and_queues(self) -> None:
        """Top right beats nothing, so the zone stands."""
        self.assertEqual(
            _backend(ic_failed=True, sem_failed=True)._unqueued_scraps(),
            set())

    def test_turning_ic_back_on_queues_again(self) -> None:
        """The setting is what decides it, not the kind of failure."""
        backend = _backend(**{REASON_IC: ALL_FIVE})
        self.assertEqual(backend._unqueued_scraps(), set())


class TestWhatTheRequestAsksFor(unittest.TestCase):
    """Read from the finalise's own plan, not from the board after the fact."""

    def test_the_plan_says_which_failure_flagged_it(self) -> None:
        backend = _backend(ic_failed=False, sem_failed=True)
        frozen = {NEIGHBOUR: ("Needs testing", FAILURE)}
        self.assertEqual(backend._coupons_for_priority(NEIGHBOUR, frozen),
                         TOP_RIGHT)

    def test_an_ic_failure_asks_for_nothing(self) -> None:
        """Which the procedure then refuses, so no request is created."""
        backend = _backend()
        frozen = {NEIGHBOUR: ("Needs testing", FAILURE)}
        self.assertEqual(backend._coupons_for_priority(NEIGHBOUR, frozen),
                         NONE)

    def test_without_the_plan_it_falls_back_to_the_stricter(self) -> None:
        """What went wrong once: the board had moved on and the lookup missed,
        so it asked for the top right coupon of an electrode the rule says not
        to ask for at all. Still the safe direction, but the plan is better."""
        backend = _backend()
        self.assertEqual(backend._coupons_for_priority(NEIGHBOUR, None),
                         TOP_RIGHT)


if __name__ == "__main__":
    unittest.main()
