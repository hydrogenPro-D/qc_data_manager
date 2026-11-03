"""The coupon icon says what QC is going to get, so it stops when nothing is.

Declined and expired both mean the electrode is not coming: one because
production said so, one because nobody could be asked in time. Sent is the
opposite case and keeps its icon, since there the coupons describe what QC
actually has.

Tested through process(), not against the helper, because the way this went
wrong before was a derived column computed correctly and then dropped on the
way out.
"""

import unittest

import pandas as pd

from src.data_access.electrodes.electrode_columns import ElectrodeColumns
from src.data_access.electrodes.electrode_processor import ElectrodeProcessor
from src.data_access.qc_requests.request_columns import (AWAITING_PRODUCTION,
                                                         DECLINED, EXPIRED,
                                                         QC_COUPONS_COLUMN,
                                                         QC_STATUS_COLUMN,
                                                         SENT, state_label)

COUPONS = "all_five"


def _frame(*states: str) -> pd.DataFrame:
    markings = [f"B1600{n}" for n in range(len(states))]
    raw = pd.DataFrame([{
        str(ElectrodeColumns.DATE): "2026-09-01T12:00:00",
        str(ElectrodeColumns.MARKING): marking,
        str(ElectrodeColumns.FULL_NAME): "CATHODE",
        str(ElectrodeColumns.RECIPE): "11",
    } for marking in markings])
    requests = {marking: {"state": state, "coupons": COUPONS}
                for marking, state in zip(markings, states)}
    out = ElectrodeProcessor(qc_request_provider=lambda: requests).process(raw)
    return out.set_index(str(ElectrodeColumns.MARKING))


class TestWhichRequestsStillShowTheirCoupons(unittest.TestCase):

    def test_an_open_request_shows_them(self) -> None:
        frame = _frame(AWAITING_PRODUCTION)
        self.assertEqual(frame.loc["B16000", QC_COUPONS_COLUMN], COUPONS)

    def test_a_sent_one_keeps_them(self) -> None:
        """What QC has in its hand is worth saying."""
        frame = _frame(SENT)
        self.assertEqual(frame.loc["B16000", QC_COUPONS_COLUMN], COUPONS)

    def test_a_declined_one_drops_them(self) -> None:
        frame = _frame(DECLINED)
        self.assertEqual(frame.loc["B16000", QC_COUPONS_COLUMN], "")

    def test_an_expired_one_drops_them(self) -> None:
        frame = _frame(EXPIRED)
        self.assertEqual(frame.loc["B16000", QC_COUPONS_COLUMN], "")

    def test_the_states_are_still_spelt_out(self) -> None:
        """Dropping the icon must not take the words with it."""
        frame = _frame(DECLINED, EXPIRED)
        self.assertEqual(frame.loc["B16000", QC_STATUS_COLUMN],
                         state_label(DECLINED))
        self.assertEqual(frame.loc["B16001", QC_STATUS_COLUMN],
                         state_label(EXPIRED))


if __name__ == "__main__":
    unittest.main()
