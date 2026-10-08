"""Two electrodes on the board at once: which one is each row waiting on?

The near one, always. With two cards in the same zone the rows between them
were naming whichever came first out of a set, so a row would say it was held
by an electrode far up the table while the test it was actually waiting on sat
next door. Worse, a card that had already passed SEM could not end anybody's
wait, because the card had been taken out of its own zone.
"""

import unittest

from src.data_access.electrodes.electrode_columns import ElectrodeStatus
from src.data_access.electrodes.status_propagation import propagate_detailed
from src.data_access.electrodes.waiting_status import waiting_statuses
from src.data_access.qc_board.qc_board_columns import SEM_ACCEPTED, SEM_RESULT

NUMBERS = [14971, 14974, 14977, 14982, 14985, 14993, 15002, 15006, 15009, 15012]
MARKINGS = [f"B{number}" for number in NUMBERS]
VATS = {marking: "1" for marking in MARKINGS}
FAULT = "B14993"
NEAR, FAR = "B14977", "B15009"
BELOW = ["B14971", "B14974"]

WAITING_FOR_SEM = str(ElectrodeStatus.WAITING_FOR_SEM)
WAITING_FOR_IC = str(ElectrodeStatus.WAITING_FOR_IC)


def _findings(passed) -> callable:
    return lambda marking: ({SEM_RESULT: SEM_ACCEPTED}
                            if marking in passed else {SEM_RESULT: ""})


def _resolve(on_board, sem_passed=()) -> tuple[dict, dict]:
    """Statuses and causes, as the processor builds them."""
    effective, _sources, priorities, causes = propagate_detailed(
        MARKINGS, {}, {FAULT: str(ElectrodeStatus.TESTED_AND_SCRAPPED)},
        vats=VATS, ongoing=set(on_board))
    flagged = {m for m in priorities
               if effective.get(m) == str(ElectrodeStatus.NEEDS_TESTING)}
    effective.update(waiting_statuses(effective, causes, set(on_board),
                                      flagged, _findings(set(sem_passed))))
    return effective, causes


class TestACardStaysInItsOwnZone(unittest.TestCase):
    """Being tested does not take an electrode out of the fault's doubt."""

    def test_the_card_keeps_the_causes_it_was_swept_by(self) -> None:
        _effective, causes = _resolve([NEAR])
        self.assertIn(FAULT, causes[NEAR])

    def test_and_names_itself_as_well(self) -> None:
        """So the lock and the tooltip point at the card, not at the scrap."""
        _effective, causes = _resolve([NEAR])
        self.assertIn(NEAR, causes[NEAR])


class TestWhatTheZoneIsWaitingOn(unittest.TestCase):
    """A SEM pass on the near card moves the rows beyond it to the IC wait."""

    def test_nobody_has_passed_sem_yet(self) -> None:
        effective, _causes = _resolve([NEAR, FAR])
        for marking in BELOW:
            self.assertEqual(effective[marking], WAITING_FOR_SEM, marking)

    def test_the_near_card_passing_sem_ends_the_sem_wait(self) -> None:
        effective, _causes = _resolve([NEAR, FAR], sem_passed=[NEAR])
        for marking in BELOW:
            self.assertEqual(effective[marking], WAITING_FOR_IC, marking)

    def test_the_far_card_passing_does_not(self) -> None:
        """It is on the other side of the fault, so it settles nothing here."""
        effective, _causes = _resolve([NEAR, FAR], sem_passed=[FAR])
        for marking in BELOW:
            self.assertEqual(effective[marking], WAITING_FOR_SEM, marking)


class TestWhichCardHoldsTheRow(unittest.TestCase):
    """Picked by distance, and the same on every render."""

    def test_the_nearer_card_is_named(self) -> None:
        from src.data_access.qc_board.qc_board_db_store import _nearest

        self.assertEqual(_nearest("B14974", [FAR, NEAR]), NEAR)
        self.assertEqual(_nearest("B14974", [NEAR, FAR]), NEAR)

    def test_a_tie_is_settled_the_same_way_every_time(self) -> None:
        from src.data_access.qc_board.qc_board_db_store import _nearest

        self.assertEqual(_nearest("B100", ["B090", "B110"]),
                         _nearest("B100", ["B110", "B090"]))

    def test_no_cards_names_nobody(self) -> None:
        from src.data_access.qc_board.qc_board_db_store import _nearest

        self.assertEqual(_nearest("B14974", []), "")


if __name__ == "__main__":
    unittest.main()
