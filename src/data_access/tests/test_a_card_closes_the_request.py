"""Putting an electrode on the QC board says it arrived, so the request closes.

Production marks a request sent when they hand the electrode over, and that
last tick gets forgotten. The request then reads as outstanding on both
screens and is expired a fortnight later as unanswerable, which is the one
thing it is not: somebody answered it by walking the electrode over. The card
is the evidence, so the card is what closes it.

The board store knows nothing about requests. It is handed a callable and runs
it after the card is written, which is what these check: that it runs, that it
runs after, and that a database without the request schema is no worse off
than before.
"""

import unittest

from src.data_access.qc_board.qc_board_columns import ElectrodeType, QcStage
from src.data_access.qc_board.qc_board_db_store import QcBoardDbStore

MARKING = "B16190"


class _Store(QcBoardDbStore):
    """The board with no database behind it, recording what it would write."""

    def __init__(self, on_arrival=None, fails: bool = False) -> None:
        self._entries = {}
        self._archived = {}
        self._last_action = {}
        self._written: list[str] = []
        self._fails = fails
        self._on_arrival = on_arrival

    def _record_entry(self, marking: str, label: str) -> None:
        pass

    def _write(self, procedure: str, **params) -> None:
        if self._fails:
            raise RuntimeError("no connection")
        self._written.append(procedure)

    def _now(self) -> str:
        return "T1"


class TestWhatACardDoes(unittest.TestCase):

    def setUp(self) -> None:
        self.closed: list[str] = []

    def _add(self, **kwargs) -> _Store:
        store = _Store(on_arrival=self.closed.append, **kwargs)
        store.add(MARKING, ElectrodeType.CATHODE, QcStage.WAITING)
        return store

    def test_the_card_closes_the_request(self) -> None:
        self._add()
        self.assertEqual(self.closed, [MARKING])

    def test_the_card_is_written_first(self) -> None:
        """An electrode whose card did not land has not arrived anywhere."""
        order: list[str] = []
        store = _Store(on_arrival=lambda marking: order.append("closed"))
        store._write = lambda procedure, **params: order.append("card")
        store.add(MARKING, ElectrodeType.CATHODE, QcStage.WAITING)
        self.assertEqual(order, ["card", "closed"])

    def test_a_failed_card_closes_nothing(self) -> None:
        with self.assertRaises(RuntimeError):
            self._add(fails=True)
        self.assertEqual(self.closed, [])

    def test_a_board_with_nothing_injected_still_works(self) -> None:
        """Feature by presence: no request schema, no callable, same board."""
        store = _Store()
        store.add(MARKING, ElectrodeType.CATHODE, QcStage.WAITING)
        self.assertIn(MARKING, store._entries)

    def test_a_request_that_cannot_be_closed_leaves_the_card(self) -> None:
        """The card is the part that matters. Somebody closes the request by
        hand, exactly as they did before this existed."""
        def explode(_marking):
            raise RuntimeError("the request schema is not there")

        store = _Store(on_arrival=explode)
        store.add(MARKING, ElectrodeType.CATHODE, QcStage.WAITING)
        self.assertIn(MARKING, store._entries)


if __name__ == "__main__":
    unittest.main()
