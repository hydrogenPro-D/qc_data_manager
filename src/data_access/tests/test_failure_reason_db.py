"""The historic failure reasons, and the precedence they sit at.

The point of this table is that it records a reason without acting as a
decision, so the tests that matter are the ones about what it must NOT reach.
"""

import unittest

from src.data_access.electrodes.failure_reason_db import load_failure_reasons
from src.data_access.qc_board.qc_board_columns import (IC_FAILED,
                                                       IC_RESULT,
                                                       SEM_CRACKED,
                                                       SEM_RESULT)


class _Session:
    """A session that answers one query, or refuses to."""

    def __init__(self, rows=None, error=None):
        self._rows = rows or []
        self._error = error
        self.asked = []

    def rows(self, sql, *args, **kwargs):
        self.asked.append(sql)
        if self._error:
            raise self._error
        return list(self._rows)


class TestReadingThem(unittest.TestCase):

    def test_it_reads_a_reason_per_electrode(self):
        session = _Session([
            {"electrode": "B4156", IC_RESULT: None, SEM_RESULT: SEM_CRACKED},
            {"electrode": "B3771", IC_RESULT: IC_FAILED, SEM_RESULT: None},
        ])
        reasons = load_failure_reasons(session)
        self.assertEqual(reasons["B4156"][SEM_RESULT], SEM_CRACKED)
        self.assertEqual(reasons["B4156"][IC_RESULT], "")
        self.assertEqual(reasons["B3771"][IC_RESULT], IC_FAILED)

    def test_it_reads_the_view_not_the_table(self):
        session = _Session([])
        load_failure_reasons(session)
        self.assertIn("vw_electrode_failure_reason", session.asked[0])

    def test_a_row_with_nothing_recorded_is_left_out(self):
        session = _Session([{"electrode": "B1", IC_RESULT: "", SEM_RESULT: None}])
        self.assertEqual(load_failure_reasons(session), {})

    def test_markings_are_trimmed_and_blank_ones_dropped(self):
        session = _Session([
            {"electrode": "  B4156 ", IC_RESULT: None, SEM_RESULT: SEM_CRACKED},
            {"electrode": "   ", IC_RESULT: None, SEM_RESULT: SEM_CRACKED},
        ])
        self.assertEqual(list(load_failure_reasons(session)), ["B4156"])

    def test_a_database_without_the_view_reads_as_nothing_recorded(self):
        """Feature by presence: the app works before 018 lands and after."""
        session = _Session(error=RuntimeError("Invalid object name"))
        self.assertEqual(load_failure_reasons(session), {})


class TestPrecedence(unittest.TestCase):
    """``_findings_with_history`` without standing a DbManager up."""

    @staticmethod
    def _resolve(board_answer, historic):
        from src.data_access.db_manager import DbManager

        class _Board:
            @staticmethod
            def findings(_marking):
                return dict(board_answer)

        fake = object.__new__(DbManager)
        fake._qc_board = _Board()
        fake._historic_reasons = historic
        return DbManager._findings_with_history(fake, "B4156")

    def test_the_board_wins_when_it_has_an_answer(self):
        found = self._resolve(
            {IC_RESULT: "", SEM_RESULT: "accepted"},
            {"B4156": {IC_RESULT: IC_FAILED, SEM_RESULT: SEM_CRACKED}})
        self.assertEqual(found[SEM_RESULT], "accepted")

    def test_history_answers_only_where_the_board_is_silent(self):
        found = self._resolve(
            {IC_RESULT: "", SEM_RESULT: ""},
            {"B4156": {IC_RESULT: "", SEM_RESULT: SEM_CRACKED}})
        self.assertEqual(found[SEM_RESULT], SEM_CRACKED)

    def test_neither_knows_and_nothing_is_invented(self):
        found = self._resolve({IC_RESULT: "", SEM_RESULT: ""}, {})
        self.assertEqual(found, {IC_RESULT: "", SEM_RESULT: ""})


class TestItCannotSweep(unittest.TestCase):
    """The reason this table exists at all.

    A sweep starts from a decision, and the decisions are assembled from the
    archive and the live board. If the historic reasons ever reached either,
    121 year-old failures would condemn their neighbours again.
    """

    def test_the_propagation_never_reads_this_table(self):
        import inspect

        from src.data_access.db_manager import DbManager

        for name in ("_settled_verdicts", "_sweeping_decisions",
                     "_unqueued_scraps"):
            source = inspect.getsource(getattr(DbManager, name))
            self.assertNotIn("_findings_with_history", source, name)
            self.assertNotIn("_failure_reasons", source, name)
            self.assertNotIn("_historic_reasons", source, name)


if __name__ == "__main__":
    unittest.main()
