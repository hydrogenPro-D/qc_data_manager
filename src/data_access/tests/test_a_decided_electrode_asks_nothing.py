"""A finished electrode has nothing to ask production, so its column is empty.

Released, scrapped, or on a rig this minute: in all of those the question has
no answer anybody is waiting for, and a request standing against the row only
reads as work outstanding. Sent survives, because that one records where the
electrode went rather than asking anything.

Through process(), since the rule reads the status the processor itself works
out, and that status is not what the raw row carries.
"""

import unittest

import pandas as pd

from src.data_access.electrodes.electrode_columns import (ElectrodeColumns,
                                                          ElectrodeStatus,
                                                          NOTHING_LEFT_TO_ASK)
from src.data_access.electrodes.electrode_processor import ElectrodeProcessor
from src.data_access.qc_requests.request_columns import (AWAITING_PRODUCTION,
                                                         AWAITING_QC,
                                                         QC_COUPONS_COLUMN,
                                                         QC_STATUS_COLUMN,
                                                         SENT, TO_BE_SENT,
                                                         state_label)

MARKING = "B16000"


def _shown(status: ElectrodeStatus, state: str) -> tuple[str, str]:
    """``(what the QC request column says, what the coupon icon says)``."""
    raw = pd.DataFrame([{
        str(ElectrodeColumns.DATE): "2026-10-20T12:00:00",
        str(ElectrodeColumns.MARKING): MARKING,
        str(ElectrodeColumns.FULL_NAME): "CATHODE",
        str(ElectrodeColumns.RECIPE): "11",
    }])
    requests = {MARKING: {"state": state, "coupons": "all_five"}} if state else {}
    out = ElectrodeProcessor(
        status_by_marking={MARKING: str(status)},
        qc_request_provider=lambda: requests).process(raw)
    return (str(out.iloc[0][QC_STATUS_COLUMN]),
            str(out.iloc[0][QC_COUPONS_COLUMN]))


class TestWhatADecidedElectrodeShows(unittest.TestCase):

    def test_every_decided_status_drops_an_open_request(self) -> None:
        for status in NOTHING_LEFT_TO_ASK:
            for state in (AWAITING_PRODUCTION, AWAITING_QC, TO_BE_SENT):
                with self.subTest(status=str(status), state=state):
                    self.assertEqual(_shown(status, state), ("", ""))

    def test_sent_survives_with_its_coupons(self) -> None:
        """Where the electrode went is worth keeping, and so is what came off
        it."""
        for status in NOTHING_LEFT_TO_ASK:
            with self.subTest(status=str(status)):
                self.assertEqual(_shown(status, SENT),
                                 (state_label(SENT), "all_five"))


class TestWhatAnUnfinishedElectrodeShows(unittest.TestCase):
    """The rule must not reach the rows the column exists for."""

    def test_not_tested_keeps_its_request(self) -> None:
        self.assertEqual(_shown(ElectrodeStatus.NOT_TESTED, AWAITING_PRODUCTION),
                         (state_label(AWAITING_PRODUCTION), "all_five"))

    def test_needs_testing_keeps_its_request(self) -> None:
        self.assertEqual(
            _shown(ElectrodeStatus.NEEDS_TESTING, AWAITING_PRODUCTION)[0],
            state_label(AWAITING_PRODUCTION))

    def test_a_decided_electrode_nobody_asked_for_is_still_blank(self) -> None:
        self.assertEqual(_shown(ElectrodeStatus.RELEASED, ""), ("", ""))


if __name__ == "__main__":
    unittest.main()
