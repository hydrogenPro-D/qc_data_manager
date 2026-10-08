"""What "mark all as done" writes down.

One rule matters more than the rest: the decided electrodes are always
recorded. The archive is what consumes a card, so a plan that leaves one out
leaves the card on the board and the button looks broken to an operator. It has
now gone wrong twice, each time because a new condition was added to the same
expression without the invariant being stated separately, so it is stated
separately here and tested on its own.
"""

import unittest

from src.data_access.db_manager import DbManager
from src.data_access.electrodes.electrode_columns import ElectrodeStatus

FAILED = str(ElectrodeStatus.TESTED_AND_SCRAPPED)
RELEASED = str(ElectrodeStatus.RELEASED)
NOT_TESTED = str(ElectrodeStatus.NOT_TESTED)

CARD = "B14993"


def plan(effective, base, decisions, held=(), sources=None) -> dict:
    return DbManager._to_freeze(effective, base, decisions, set(held),
                                sources or {})


class TestTheDecidedCardIsAlwaysRecorded(unittest.TestCase):
    """Whatever else is true of it. Nothing else takes the card off the board."""

    def test_even_when_its_status_did_not_move(self) -> None:
        frozen = plan({CARD: FAILED}, {CARD: FAILED}, {CARD: FAILED})
        self.assertIn(CARD, frozen)

    def test_even_when_its_own_frozen_row_is_out_of_date(self) -> None:
        """The regression: it filtered itself out of its own finalise.

        A card swept Released by a verdict that was later re-archived has a
        frozen row older than that verdict, which makes it stale. Stale rows
        are left for whichever card holds them, and this one holds itself.
        """
        frozen = plan({CARD: FAILED}, {CARD: RELEASED}, {CARD: FAILED},
                      held={CARD})
        self.assertIn(CARD, frozen)

    def test_it_is_recorded_as_its_own_cause(self) -> None:
        frozen = plan({CARD: FAILED}, {}, {CARD: FAILED})
        self.assertEqual(frozen[CARD], (FAILED, CARD))


class TestWhatElseIsRecorded(unittest.TestCase):
    """Everything that is not a decided card is recorded only where it moved."""

    SWEPT = "B14990"

    def test_an_electrode_the_sweep_moved(self) -> None:
        frozen = plan({self.SWEPT: RELEASED}, {self.SWEPT: NOT_TESTED},
                      {CARD: FAILED}, sources={self.SWEPT: CARD})
        self.assertEqual(frozen[self.SWEPT], (RELEASED, CARD))

    def test_not_one_that_already_read_that_way(self) -> None:
        frozen = plan({self.SWEPT: RELEASED}, {self.SWEPT: RELEASED},
                      {CARD: FAILED})
        self.assertNotIn(self.SWEPT, frozen)

    def test_not_one_a_card_staying_on_the_board_holds(self) -> None:
        """That card will freeze it, with its own time rather than this one's."""
        frozen = plan({self.SWEPT: RELEASED}, {self.SWEPT: NOT_TESTED},
                      {CARD: FAILED}, held={self.SWEPT},
                      sources={self.SWEPT: "B14900"})
        self.assertNotIn(self.SWEPT, frozen)

    def test_unless_this_very_decision_is_what_caused_it(self) -> None:
        """A card's own sweep is the card's to freeze, held or not.

        The electrodes scrapped beside a failure are the case: they were left
        out, kept an older row, and a card elsewhere then held that row. They
        showed no date and locked to an electrode that cannot move them.
        """
        frozen = plan({self.SWEPT: str(ElectrodeStatus.SCRAPPED)},
                      {self.SWEPT: NOT_TESTED}, {CARD: FAILED},
                      held={self.SWEPT}, sources={self.SWEPT: CARD})
        self.assertEqual(frozen[self.SWEPT],
                         (str(ElectrodeStatus.SCRAPPED), CARD))

    def test_missing_from_base_counts_as_not_tested(self) -> None:
        frozen = plan({self.SWEPT: RELEASED}, {}, {CARD: FAILED})
        self.assertIn(self.SWEPT, frozen)


if __name__ == "__main__":
    unittest.main()
