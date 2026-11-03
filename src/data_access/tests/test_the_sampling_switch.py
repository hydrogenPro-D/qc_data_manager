"""Reading and throwing the switch that stops automatic sampling.

A fake session, so this is the reader's own rules and nothing else: what it
makes of a row, of an empty answer, and of a database that has never had
migration 016 and so has neither the view nor the procedure.
"""

import unittest

from src.data_access.sampling import switch_db_source as switch


class _Session:
    """Answers with whatever it was handed, and records what was called."""

    def __init__(self, answer=None, fails: bool = False) -> None:
        self.answer = answer if answer is not None else []
        self.fails = fails
        self.calls: list[tuple] = []

    def rows(self, sql, params=()):
        if self.fails:
            raise RuntimeError("Invalid object name 'vw_sampling_switch'.")
        return self.answer

    def call(self, procedure, **params):
        if self.fails:
            raise RuntimeError("could not find stored procedure")
        self.calls.append((procedure, params))
        return [{"changed": 1}]


class TestReadingIt(unittest.TestCase):

    def test_on_comes_back_as_true(self) -> None:
        self.assertIs(switch.load_switch(_Session([{"is_on": True}])), True)

    def test_off_comes_back_as_false(self) -> None:
        self.assertIs(switch.load_switch(_Session([{"is_on": False}])), False)

    def test_a_bit_arriving_as_a_number_is_read_the_same(self) -> None:
        self.assertIs(switch.load_switch(_Session([{"is_on": 1}])), True)
        self.assertIs(switch.load_switch(_Session([{"is_on": 0}])), False)

    def test_a_database_without_the_view_answers_none(self) -> None:
        """Not off. The tab hides the control rather than offering a dead one."""
        self.assertIsNone(switch.load_switch(_Session(fails=True)))

    def test_so_does_no_row_at_all(self) -> None:
        self.assertIsNone(switch.load_switch(_Session([])))

    def test_and_so_does_a_null(self) -> None:
        self.assertIsNone(switch.load_switch(_Session([{"is_on": None}])))


class TestThrowingIt(unittest.TestCase):

    def test_on_passes_the_starting_point(self) -> None:
        session = _Session()
        self.assertTrue(switch.set_switch(session, True, start_now=True))
        procedure, params = session.calls[0]
        self.assertIn("usp_sampling_switch_set", procedure)
        self.assertEqual(params, {"on": 1, "start_now": 1})

    def test_waiting_a_full_interval_is_the_other_value(self) -> None:
        session = _Session()
        switch.set_switch(session, True, start_now=False)
        self.assertEqual(session.calls[0][1], {"on": 1, "start_now": 0})

    def test_off_says_so_and_asks_for_nothing(self) -> None:
        session = _Session()
        switch.set_switch(session, False)
        self.assertEqual(session.calls[0][1], {"on": 0, "start_now": 0})

    def test_a_write_that_could_not_land_says_false(self) -> None:
        self.assertFalse(switch.set_switch(_Session(fails=True), True))
