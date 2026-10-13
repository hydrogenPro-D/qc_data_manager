"""A frozen sweep is only unfrozen by a card from the same vat.

The line plates 28, 29, 30, 28, 29, 30, so an electrode's neighbours by number
are in other vats and only every third one shares its run. A release sweeps one
vat's run, so the stretch it freezes is every third electrode, spanning three
times as many numbers as it contains.

The unfreezing used to ask only whether a card's number fell between a frozen
row and the verdict it was drawn from. Two thirds of that span belongs to other
vats, so a card that could never have been part of the stretch unfroze it: the
rows lost their Released status, read Not Tested, and named that card as what
was holding them.
"""

import unittest

from src.data_access.electrodes.electrode_columns import ElectrodeStatus
from src.data_access.qc_board.qc_board_db_store import QcBoardDbStore

PASSED = str(ElectrodeStatus.TESTED_AND_RELEASED)
RELEASED = str(ElectrodeStatus.RELEASED)

# B100 to B112, plated 29, 30, 28, 29, ... exactly as the line runs them.
VATS = {f"B{number}": {1: "29", 2: "30", 0: "28"}[number % 3]
        for number in range(100, 120)}
VERDICT = "B112"                       # vat 29, the pass the sweep came from
FROZEN = ["B100", "B103", "B109"]      # vat 29, released by it and finalised
SAME_VAT_CARD = "B106"                 # vat 29, inside the stretch
OTHER_VAT_CARDS = ["B101", "B102", "B104", "B105", "B107", "B108",
                   "B110", "B111"]     # vats 30 and 28, inside the span


class _Store(QcBoardDbStore):
    """The archive, the board and the vats, with no database behind them."""

    def __init__(self, board, vats=VATS) -> None:
        self._archived = {VERDICT: {"status": PASSED, "from": VERDICT,
                                    "at": "T1"}}
        for marking in FROZEN:
            self._archived[marking] = {"status": RELEASED, "from": VERDICT,
                                       "at": "T1"}
        self._board = set(board)
        self._vat_provider = lambda: vats

    def on_board(self) -> set:
        return self._board

    def ongoing_electrodes(self) -> set:
        return self._board

    def status_overrides(self) -> dict:
        return {}


class TestOnlyTheVatsOwnCardsReachIt(unittest.TestCase):

    def test_a_card_from_another_vat_unfreezes_nothing(self) -> None:
        """The bug. Every one of these sits inside the span B100 to B112."""
        for card in OTHER_VAT_CARDS:
            with self.subTest(card=card, vat=VATS[card]):
                self.assertEqual(_Store([card]).stale_inferences(), set())

    def test_a_card_from_another_vat_locks_nothing(self) -> None:
        """What the operator saw: the tooltip named a card two vats away."""
        for card in OTHER_VAT_CARDS:
            with self.subTest(card=card, vat=VATS[card]):
                self.assertEqual(_Store([card]).revisited_by(), {})

    def test_a_card_from_the_same_vat_still_unfreezes_its_stretch(self) -> None:
        """The behaviour this exists for, so the fix cannot have removed it.

        B100 and B103 are behind the card, B109 is past it, and only what the
        card stands between gives way.
        """
        self.assertEqual(_Store([SAME_VAT_CARD]).stale_inferences(),
                         {"B100", "B103"})

    def test_a_card_from_the_same_vat_is_named_as_the_holder(self) -> None:
        held = _Store([SAME_VAT_CARD]).revisited_by()
        self.assertEqual(held, {"B100": SAME_VAT_CARD, "B103": SAME_VAT_CARD})

    def test_with_no_vats_known_every_card_still_counts(self) -> None:
        """A database with no process log reads as before rather than as empty.

        The vat is joined in on a worker thread after the window opens, so this
        is also what the first render sees.
        """
        self.assertEqual(_Store(["B105"], vats={}).stale_inferences(),
                         {"B100", "B103"})


class TestAnUnsettledVat(unittest.TestCase):
    """The process log records the plan, and a few electrodes list two vats."""

    def test_a_card_with_two_vats_is_kept_in_every_run(self) -> None:
        """It may genuinely be standing in this stretch, and releasing a
        stretch that a test is sitting in is the error worth avoiding."""
        vats = {**VATS, "B105": "28, 30"}
        self.assertEqual(_Store(["B105"], vats=vats).stale_inferences(),
                         {"B100", "B103"})

    def test_a_verdict_with_two_vats_swept_the_whole_sequence(self) -> None:
        """So any card could have been in its reach, which is the fallback
        _lanes makes when it froze the stretch in the first place."""
        vats = {**VATS, VERDICT: "28, 29"}
        self.assertEqual(_Store(["B105"], vats=vats).stale_inferences(),
                         {"B100", "B103"})


if __name__ == "__main__":
    unittest.main()
