"""Electrode processor — pure data in, data out, no database and no Qt.

Covers the part that is easy to get wrong twice: a status is not a stored value
but the answer to several sources at once, and the order they are applied in
decides what an operator sees.
"""

import unittest

import pandas as pd

from src.data_access.electrodes.electrode_columns import (
    ElectrodeColumns,
    ElectrodeStatus,
    STATUS_COLUMN,
)
from src.data_access.electrodes.electrode_processor import ElectrodeProcessor


def frame(markings) -> pd.DataFrame:
    """The raw shape a read of dbo.MetaData produces."""
    return pd.DataFrame([
        {str(ElectrodeColumns.DATE): "2026-08-27 08:00:00",
         str(ElectrodeColumns.MARKING): marking,
         str(ElectrodeColumns.FULL_NAME): "CATHODE",
         str(ElectrodeColumns.RECIPE): 11} for marking in markings])


def statuses(decided, ongoing, markings=None) -> dict:
    """``{marking: status}`` as the Electrodes tab would show it."""
    markings = markings or [f"B{14460 + i}" for i in range(8)]
    processor = ElectrodeProcessor(
        status_by_marking={},
        overrides_provider=lambda: decided,
        ongoing_provider=lambda: ongoing)
    out = processor.process(frame(markings))
    return dict(zip(out[str(ElectrodeColumns.MARKING)], out[STATUS_COLUMN]))


class TestOngoingSurvivesAnEmptyBoard(unittest.TestCase):
    """Test ongoing must not depend on something else being decided.

    Dragging the last card out of Release or Scrapped correctly drops the
    sweeps, and used to drop every Test ongoing with them: with no decision
    left the processor took a shortcut and never ran the propagation that
    applies them, so every electrode being tested went back to Not Tested.
    """

    def test_with_a_scrap_on_the_board(self) -> None:
        shown = statuses({"B14464": str(ElectrodeStatus.TESTED_AND_SCRAPPED)},
                         {"B14461"})
        self.assertEqual(shown["B14464"], str(ElectrodeStatus.TESTED_AND_SCRAPPED))
        self.assertEqual(shown["B14461"], str(ElectrodeStatus.TEST_ONGOING))
        # The scrap still reaches the electrodes nobody is testing.
        self.assertEqual(shown["B14463"], str(ElectrodeStatus.SCRAPPED))

    def test_with_nothing_decided_at_all(self) -> None:
        shown = statuses({}, {"B14461", "B14464"})
        self.assertEqual(shown["B14461"], str(ElectrodeStatus.TEST_ONGOING))
        self.assertEqual(shown["B14464"], str(ElectrodeStatus.TEST_ONGOING))
        self.assertEqual(shown["B14462"], str(ElectrodeStatus.NOT_TESTED))

    def test_a_sweep_does_not_overwrite_an_electrode_being_tested(self) -> None:
        """A test under way beats a guess drawn from a neighbour.

        B14463 is inside B14464's scrap margin, and would read Scrapped if
        nobody were testing it. Somebody is, and that test is about to say
        whether the coating is bad rather than assume it.
        """
        shown = statuses({"B14464": str(ElectrodeStatus.TESTED_AND_SCRAPPED)},
                         {"B14463"})
        self.assertEqual(shown["B14463"], str(ElectrodeStatus.TEST_ONGOING))
        # The neighbour nobody is testing still takes the sweep.
        self.assertEqual(shown["B14462"], str(ElectrodeStatus.SCRAPPED))


if __name__ == "__main__":
    unittest.main()


class TestNotTestedNeverShowsAFinalisedDate(unittest.TestCase):
    """Nothing about a Not Tested electrode has settled, so no date stands.

    The database can hold one: a stretch released and marked as done keeps its
    archived timestamp even once the status itself has given way, and a sweep
    cancelled by a test under way leaves exactly that. A date beside Not Tested
    reads as a decision nobody made, so the column is blanked here, in the one
    place the table, the filter and the report all read from.
    """

    MARKINGS = ["B14460", "B14461"]

    def _finalised(self, decided, ongoing=None) -> dict:
        from src.data_access.electrodes.electrode_columns import FINALISED_COLUMN

        processor = ElectrodeProcessor(
            status_by_marking={},
            overrides_provider=lambda: decided,
            ongoing_provider=lambda: ongoing or set(),
            finalised_provider=lambda: {m: "2026-09-30 07:00:00"
                                        for m in self.MARKINGS})
        out = processor.process(frame(self.MARKINGS))
        return dict(zip(out[str(ElectrodeColumns.MARKING)],
                        out[FINALISED_COLUMN]))

    def test_blank_where_the_status_is_not_tested(self) -> None:
        self.assertEqual(self._finalised({}),
                         {marking: "" for marking in self.MARKINGS})

    def test_kept_where_the_electrode_actually_has_a_verdict(self) -> None:
        decided = {"B14460": str(ElectrodeStatus.TESTED_AND_RELEASED)}
        self.assertTrue(self._finalised(decided)["B14460"])

    def test_blank_again_once_that_verdict_is_taken_away(self) -> None:
        self.assertEqual(self._finalised({})["B14460"], "")


class TestALiveCardLeavesNoDate(unittest.TestCase):
    """A card still on the board holds its neighbours, so nothing has settled.

    B14837 dropped into Failed rejects the electrodes around it at once, which
    is the point of the live re-test. Those rows had been released and
    finalised days earlier, and kept that date while reading Rejected: a status
    dated to a day on which it was something else.
    """

    MARKINGS = [f"B{14834 + i}" for i in range(6)]
    FAILED_AT = "B14837"
    WHEN = "2026-09-28 13:54:00"

    def _finalised(self, on_board, pinned=None, by_hand=None) -> dict:
        from src.data_access.electrodes.electrode_columns import FINALISED_COLUMN

        processor = ElectrodeProcessor(
            status_by_marking=by_hand or {},
            overrides_provider=lambda: {
                self.FAILED_AT: str(ElectrodeStatus.TESTED_AND_SCRAPPED)},
            board_provider=lambda: set(on_board),
            pinned_provider=lambda: set(pinned or ()),
            finalised_provider=lambda: {m: self.WHEN for m in self.MARKINGS})
        out = processor.process(frame(self.MARKINGS))
        return dict(zip(out[str(ElectrodeColumns.MARKING)],
                        out[FINALISED_COLUMN]))

    def test_the_swept_neighbours_show_no_date(self) -> None:
        dates = self._finalised(on_board=[self.FAILED_AT])
        self.assertEqual(dates["B14836"], "")
        self.assertEqual(dates["B14838"], "")

    def test_the_date_comes_back_once_the_card_is_finalised(self) -> None:
        """Off the board is what marking it as done does."""
        dates = self._finalised(on_board=[])
        self.assertEqual(dates["B14836"], self.WHEN)

    def test_a_hand_set_status_keeps_its_own_date(self) -> None:
        """Nothing holds it, so no card can take its date away."""
        dates = self._finalised(
            on_board=[self.FAILED_AT], pinned=["B14836"],
            by_hand={"B14836": str(ElectrodeStatus.RELEASED)})
        self.assertEqual(dates["B14836"], self.WHEN)


class TestOnlyAnOutcomeCarriesADate(unittest.TestCase):
    """The four an electrode can finish on, and nothing else.

    Needs testing and the two waits are steps on the way to an outcome, so the
    electrode has not finished and there is nothing to date. The filter reads
    the same column, so they drop out of a Status finalised range too.
    """

    WHEN = "2026-10-08 11:01:00"

    def _finalised(self, by_hand) -> dict:
        from src.data_access.electrodes.electrode_columns import FINALISED_COLUMN

        markings = list(by_hand)
        processor = ElectrodeProcessor(
            status_by_marking=by_hand,
            pinned_provider=lambda: set(markings),
            finalised_provider=lambda: {m: self.WHEN for m in markings})
        out = processor.process(frame(markings))
        return dict(zip(out[str(ElectrodeColumns.MARKING)],
                        out[FINALISED_COLUMN]))

    def test_the_four_outcomes_carry_one(self) -> None:
        from src.data_access.electrodes.electrode_columns import DECIDED

        by_hand = {f"B{14900 + i}": str(status)
                   for i, status in enumerate(sorted(DECIDED, key=str))}
        dates = self._finalised(by_hand)
        for marking, status in by_hand.items():
            self.assertEqual(dates[marking], self.WHEN, status)

    def test_nothing_on_the_way_to_one_does(self) -> None:
        on_the_way = (ElectrodeStatus.NOT_TESTED, ElectrodeStatus.NEEDS_TESTING,
                      ElectrodeStatus.WAITING_FOR_SEM,
                      ElectrodeStatus.WAITING_FOR_IC,
                      ElectrodeStatus.TEST_ONGOING)
        by_hand = {f"B{14900 + i}": str(status)
                   for i, status in enumerate(on_the_way)}
        dates = self._finalised(by_hand)
        for marking, status in by_hand.items():
            self.assertEqual(dates[marking], "", status)

    def test_every_status_is_on_one_side_or_the_other(self) -> None:
        """So a status added later cannot quietly fall through the gap."""
        from src.data_access.electrodes.electrode_columns import DECIDED

        by_hand = {f"B{14900 + i}": str(status)
                   for i, status in enumerate(ElectrodeStatus)}
        dates = self._finalised(by_hand)
        dated = {status for marking, status in by_hand.items() if dates[marking]}
        self.assertEqual(dated, {str(status) for status in DECIDED})


class TestWhyAnElectrodeFailed(unittest.TestCase):
    """The codes behind the badges beside a Tested, failed verdict.

    Both can be true at once and neither has to be: an operator who left the
    findings blank leaves a failure with no reason, which has to read as no
    badge rather than as a guess.
    """

    MARKING = "B14993"

    def _codes(self, status, findings) -> str:
        from src.data_access.electrodes.electrode_columns import FAILURE_COLUMN

        processor = ElectrodeProcessor(
            status_by_marking={self.MARKING: str(status)},
            pinned_provider=lambda: {self.MARKING},
            findings_provider=lambda marking: findings)
        out = processor.process(frame([self.MARKING]))
        return str(out[FAILURE_COLUMN].iloc[0])

    def _failed(self, findings) -> str:
        return self._codes(ElectrodeStatus.TESTED_AND_SCRAPPED, findings)

    def test_the_ic_test_failed(self) -> None:
        self.assertEqual(self._failed({"ic_result": "failed"}), "ic")

    def test_the_sem_found_a_crack(self) -> None:
        self.assertEqual(self._failed({"sem_result": "cracked"}), "crack")

    def test_both_of_them(self) -> None:
        self.assertEqual(
            self._failed({"ic_result": "failed", "sem_result": "cracked"}),
            "ic,crack")

    def test_nothing_written_down_says_nothing(self) -> None:
        self.assertEqual(self._failed({}), "")
        self.assertEqual(self._failed({"ic_result": "", "sem_result": ""}), "")

    def test_a_test_that_passed_is_not_a_reason(self) -> None:
        self.assertEqual(
            self._failed({"ic_result": "passed", "sem_result": "accepted"}), "")

    def test_an_electrode_swept_into_rejected_has_no_reason_of_its_own(self) -> None:
        """It was never tested, so it must not borrow the tested one's."""
        self.assertEqual(
            self._codes(ElectrodeStatus.SCRAPPED, {"ic_result": "failed"}), "")

    def test_nor_has_one_that_passed(self) -> None:
        self.assertEqual(
            self._codes(ElectrodeStatus.TESTED_AND_RELEASED,
                        {"ic_result": "failed"}), "")
