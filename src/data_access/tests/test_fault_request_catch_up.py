"""The sweep that asks for test-first electrodes nobody asked production for.

The ±3 around a failure are requested at the instant that failure is finalised
and at no other time, so a flag that outlived its finalise is never acted on.
Five electrodes sat like that: shown in the pull list as to be tested first, and
never asked for, because they were frozen before the requests existed.

So the same set the pull list shows is swept on a timer. What matters is that it
asks for exactly what is outstanding: not what has been asked for already, not
what is on a rig right now, and not a flag that no longer stands.
"""

import unittest

from src.data_access.db_manager import DbManager


class _Backend(DbManager):
    """The three answers the sweep reads, and a note of what it asked for.

    Built without __init__ on purpose: the sweep touches none of the connection
    machinery, and giving it a database to stand up would test the database.
    """

    def __init__(self, settled, asked=(), on_board=(), fails=False) -> None:
        self._settled = set(settled)
        self._asked = {marking: "awaiting_production" for marking in asked}
        self._on_board = set(on_board)
        self._fails = fails
        self.requested: list[str] = []

    # -- what the real one reads -----------------------------------------
    def _settled_priorities(self) -> set:
        return set(self._settled)

    def get_qc_requests(self) -> dict:
        if self._fails:
            raise RuntimeError("the request schema is not there")
        return dict(self._asked)

    def _request_fault_tests(self, priorities: set) -> int:
        # The real one skips what is on the board and calls one procedure per
        # marking. Only the skipping is this sweep's business.
        wanted = sorted(set(priorities) - self._on_board)
        self.requested.extend(wanted)
        return len(wanted)


class TestWhatTheSweepAsksFor(unittest.TestCase):

    def test_it_asks_for_a_flag_nobody_asked_for(self) -> None:
        """The five that were stuck."""
        backend = _Backend(settled=["B15032", "B15016"])
        self.assertEqual(backend.raise_missing_fault_requests(), 2)
        self.assertEqual(backend.requested, ["B15016", "B15032"])

    def test_it_leaves_alone_one_that_already_has_a_request(self) -> None:
        """Including a declined or sent one: there is one row per electrode,
        ever, so asking again could only ever do nothing."""
        backend = _Backend(settled=["B15032", "B15016"], asked=["B15032"])
        self.assertEqual(backend.raise_missing_fault_requests(), 1)
        self.assertEqual(backend.requested, ["B15016"])

    def test_it_leaves_alone_one_that_is_on_a_rig(self) -> None:
        backend = _Backend(settled=["B15032", "B15016"], on_board=["B15016"])
        self.assertEqual(backend.raise_missing_fault_requests(), 1)
        self.assertEqual(backend.requested, ["B15032"])

    def test_nothing_outstanding_costs_no_query(self) -> None:
        """It runs every minute for the life of the session, so the quiet case
        has to be free. A database that would fail the read proves it."""
        backend = _Backend(settled=[], fails=True)
        self.assertEqual(backend.raise_missing_fault_requests(), 0)
        self.assertEqual(backend.requested, [])

    def test_it_asks_again_for_nothing_once_they_are_all_requested(self) -> None:
        """The steady state. Every sweep after the first sends no procedure."""
        backend = _Backend(settled=["B15032"], asked=["B15032"])
        self.assertEqual(backend.raise_missing_fault_requests(), 0)
        self.assertEqual(backend.requested, [])


class TestWhenTheRequestSchemaIsNotThere(unittest.TestCase):
    """A database without qc_requests, which is every one before the migrations."""

    def test_it_gives_up_rather_than_asking_blind(self) -> None:
        """Asking without knowing what has been asked would send a procedure
        per flag per minute at a schema that cannot answer."""
        backend = _Backend(settled=["B15032"], fails=True)
        self.assertEqual(backend.raise_missing_fault_requests(), 0)
        self.assertEqual(backend.requested, [])


if __name__ == "__main__":
    unittest.main()
