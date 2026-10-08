"""A test under way inside a stretch stops that stretch being released.

Two passes with nothing but untested electrodes between them release everything
in between, which is right until somebody puts one of those electrodes on a rig.
Whatever that test says governs the stretch: if it comes back scrapped the fault
condemns the electrodes either side of it, which is the opposite of releasing
them. So the sweep waits, and the stretch reads Not Tested until there is an
answer.

The same applies to a stretch that was released and finalised earlier: putting
one of its electrodes back on the board unfreezes the whole stretch.
"""

import unittest

from src.data_access.electrodes.electrode_columns import ElectrodeStatus
from src.data_access.electrodes.status_propagation import propagate_detailed
from src.data_access.qc_board.qc_board_db_store import QcBoardDbStore

MARKINGS = [f"B{number}" for number in range(100, 111)]
VATS = {marking: "1" for marking in MARKINGS}
PASSED = str(ElectrodeStatus.TESTED_AND_RELEASED)
SCRAPPED = str(ElectrodeStatus.TESTED_AND_SCRAPPED)
RELEASED = str(ElectrodeStatus.RELEASED)
NOT_TESTED = str(ElectrodeStatus.NOT_TESTED)
ONGOING = str(ElectrodeStatus.TEST_ONGOING)

# The two passes, and the electrode in the middle somebody decides to test.
FIRST, LAST, MIDDLE = "B100", "B110", "B105"
BETWEEN = [f"B{number}" for number in range(101, 110)]


def swept(decisions: dict, ongoing=()) -> dict:
    return propagate_detailed(MARKINGS, {}, decisions, vats=VATS,
                              ongoing=set(ongoing))[0]


class TestTheLiveSweep(unittest.TestCase):
    """What the Electrodes tab shows while the card is on the board."""

    def setUp(self) -> None:
        self.passes = {FIRST: PASSED, LAST: PASSED}

    def test_two_passes_release_everything_between_them(self) -> None:
        """The behaviour this is a condition on, so it cannot quietly go."""
        effective = swept(self.passes)
        self.assertEqual([effective[m] for m in BETWEEN],
                         [RELEASED] * len(BETWEEN))

    def test_a_test_under_way_holds_the_whole_stretch(self) -> None:
        effective = swept(self.passes, ongoing=[MIDDLE])
        self.assertEqual(effective[MIDDLE], ONGOING)
        for marking in BETWEEN:
            if marking == MIDDLE:
                continue
            self.assertEqual(effective[marking], NOT_TESTED, marking)

    def test_both_sides_are_held_not_just_the_far_one(self) -> None:
        """A scrap at the electrode under test would reach both ways."""
        effective = swept(self.passes, ongoing=[MIDDLE])
        self.assertEqual(effective["B109"], NOT_TESTED)   # nearest the pass
        self.assertEqual(effective["B101"], NOT_TESTED)   # far side

    def test_releasing_it_sweeps_the_stretch_after_all(self) -> None:
        effective = swept({**self.passes, MIDDLE: PASSED})
        for marking in BETWEEN:
            if marking == MIDDLE:
                continue
            self.assertEqual(effective[marking], RELEASED, marking)

    def test_scrapping_it_does_the_usual_thing(self) -> None:
        effective = swept({**self.passes, MIDDLE: SCRAPPED})
        self.assertEqual(effective["B104"], str(ElectrodeStatus.SCRAPPED))
        self.assertEqual(effective["B106"], str(ElectrodeStatus.SCRAPPED))

    def test_a_test_outside_the_stretch_changes_nothing(self) -> None:
        effective = swept(self.passes, ongoing=["B99"])
        self.assertEqual([effective[m] for m in BETWEEN],
                         [RELEASED] * len(BETWEEN))


class _Store(QcBoardDbStore):
    """The archive and the board only, with no database behind them.

    ``board`` is every card on it, decided or not: both hold a stretch, because
    neither has been marked as done.
    """

    def __init__(self, archived: dict, board) -> None:
        self._archived = archived
        self._board = set(board)

    def on_board(self) -> set:
        return self._board

    def ongoing_electrodes(self) -> set:
        return self._board

    def status_overrides(self) -> dict:
        return {}


def _finalised_stretch() -> dict:
    """B110 passed, and its sweep back over the stretch was marked as done."""
    archived = {LAST: {"status": PASSED, "from": LAST, "at": "T1"}}
    for marking in BETWEEN:
        archived[marking] = {"status": RELEASED, "from": LAST, "at": "T1"}
    return archived


class TestTheDateTheStretchCarries(unittest.TestCase):
    """B14932 and B14892 passed, their sweep was frozen, then B14917 was tested.

    The rows between them were frozen on the 6th, went Not Tested while B14917
    sat on the board, and were released again when B14917 passed on the 7th.
    They read the 6th, which is a date on which they were something else.
    """

    SWEPT_AT = "2026-10-06 15:37:00"
    TESTED_AT = "2026-10-07 11:11:00"

    def _archive(self, middle_at: str = "") -> dict:
        archived = {LAST: {"status": PASSED, "from": LAST, "at": self.SWEPT_AT}}
        for marking in BETWEEN:
            archived[marking] = {"status": RELEASED, "from": LAST,
                                 "at": self.SWEPT_AT}
        if middle_at:
            archived[MIDDLE] = {"status": PASSED, "from": MIDDLE,
                                "at": middle_at}
        return archived

    def test_the_original_sweep_keeps_its_own_date(self) -> None:
        times = _Store(self._archive(), []).settled_times()
        self.assertEqual({times[m] for m in BETWEEN}, {self.SWEPT_AT})

    def test_a_later_test_inside_it_restamps_the_whole_stretch(self) -> None:
        times = _Store(self._archive(self.TESTED_AT), []).settled_times()
        for marking in BETWEEN:
            if marking == MIDDLE:
                continue
            self.assertEqual(times[marking], self.TESTED_AT, marking)

    def test_both_halves_of_the_stretch_not_just_one(self) -> None:
        """Each row alone only reaches its own verdict, which split the date."""
        times = _Store(self._archive(self.TESTED_AT), []).settled_times()
        self.assertEqual(times["B101"], self.TESTED_AT)   # below the test
        self.assertEqual(times["B109"], self.TESTED_AT)   # above it

    def test_the_bounding_verdict_is_not_restamped(self) -> None:
        """It was passed throughout. Nothing about it moved."""
        times = _Store(self._archive(self.TESTED_AT), []).settled_times()
        self.assertEqual(times[LAST], self.SWEPT_AT)

    def test_an_older_verdict_inside_it_changes_nothing(self) -> None:
        times = _Store(self._archive("2026-09-28 13:41:00"), []).settled_times()
        self.assertEqual(times["B101"], self.SWEPT_AT)


class TestTheFinalisedStretch(unittest.TestCase):
    """A stretch released and frozen earlier, reopened by a later test."""

    def test_nothing_is_stale_while_the_board_is_empty(self) -> None:
        self.assertEqual(_Store(_finalised_stretch(), []).stale_inferences(),
                         set())

    def test_a_test_in_the_middle_unfreezes_the_whole_stretch(self) -> None:
        stale = _Store(_finalised_stretch(), [MIDDLE]).stale_inferences()
        self.assertEqual(stale, set(BETWEEN))

    def test_the_verdict_itself_is_left_alone(self) -> None:
        """It was measured. Only what was inferred from it gives way."""
        self.assertNotIn(LAST, _Store(_finalised_stretch(),
                                      [MIDDLE]).stale_inferences())

    def test_a_test_outside_the_stretch_unfreezes_nothing(self) -> None:
        self.assertEqual(_Store(_finalised_stretch(), ["B99"]).stale_inferences(),
                         set())

    def test_a_decided_card_holds_the_stretch_just_the_same(self) -> None:
        """Dragged to Release but not finalised, so nothing has settled yet.

        The stretch reads Released again, from this card rather than from the
        sweep it interrupted, and must not go back to showing that sweep's date.
        """
        stale = _Store(_finalised_stretch(), [MIDDLE]).stale_inferences()
        self.assertEqual(stale, set(BETWEEN))

    def test_the_whole_stretch_locks_to_that_card(self) -> None:
        """Moving it would change every one of them, so none can be hand set."""
        locks = _Store(_finalised_stretch(), [MIDDLE]).revisited_by()
        self.assertEqual(set(locks), set(BETWEEN))
        self.assertEqual(set(locks.values()), {MIDDLE})

    def test_an_electrode_back_on_the_board_still_drives_its_neighbours(self) -> None:
        """Its own verdict stands until a new result replaces it.

        Only a swept electrode shadows the rest of its stretch; one carrying a
        measurement of its own is settled behaviour from the re-test work.
        """
        archived = _finalised_stretch()
        stale = _Store(archived, [LAST]).stale_inferences()
        self.assertEqual(stale, set())


if __name__ == "__main__":
    unittest.main()


class TestAScrapStaysSettled(unittest.TestCase):
    """B14993 failed and was finalised; B14980 is on the board beyond its margin.

    The electrodes B14993 condemned were locking to B14980 and showing no date,
    on the grounds that B14980 was swept by the same verdict. It was, but that
    says nothing about them: nothing a card does can move a scrapped electrode,
    so B14993's verdict settles them and keeps them settled.
    """

    FAILED_AT, CARD = "B14993", "B14980"
    WHEN = "2026-10-08 10:44:00"
    CONDEMNED = ["B14988", "B14991", "B14995", "B14996"]
    IN_DOUBT = ["B14985", "B14999"]

    def _store(self) -> "_Store":
        archived = {self.FAILED_AT: {"status": SCRAPPED, "from": self.FAILED_AT,
                                     "at": self.WHEN}}
        for marking in self.CONDEMNED:
            archived[marking] = {"status": str(ElectrodeStatus.SCRAPPED),
                                 "from": self.FAILED_AT, "at": self.WHEN}
        for marking in [*self.IN_DOUBT, self.CARD]:
            archived[marking] = {"status": str(ElectrodeStatus.NEEDS_TESTING),
                                 "from": self.FAILED_AT, "at": self.WHEN}
        return _Store(archived, [self.CARD])

    def test_the_rejected_ones_keep_their_verdict(self) -> None:
        stale = self._store().stale_inferences()
        for marking in self.CONDEMNED:
            self.assertNotIn(marking, stale, marking)

    def test_so_nothing_locks_them(self) -> None:
        locks = self._store().revisited_by()
        for marking in self.CONDEMNED:
            self.assertNotIn(marking, locks, marking)

    def test_and_they_keep_the_date_the_verdict_was_finalised_on(self) -> None:
        times = self._store().settled_times()
        for marking in self.CONDEMNED:
            self.assertEqual(times[marking], self.WHEN, marking)

    def test_the_ones_still_in_doubt_do_move_with_the_card(self) -> None:
        """Needs testing is exactly what that card's result decides."""
        locks = self._store().revisited_by()
        for marking in self.IN_DOUBT:
            self.assertEqual(locks.get(marking), self.CARD, marking)
