"""A hand set status must stick, whatever the sweeps would rather say.

The rule the operator sees is simple: if a row is not locked, the status you
pick is the status you get. It was not true. With the board empty, so nothing
locked, a finalised scrap still swept and stamped Needs testing straight back
over an electrode somebody had just corrected to Released, because the
propagation could not tell a hand set status from one it had inferred itself.
"""

import unittest

import pandas as pd

from src.data_access.electrodes.electrode_columns import (
    ElectrodeColumns,
    ElectrodeStatus,
    STATUS_COLUMN,
)
from src.data_access.electrodes.electrode_processor import ElectrodeProcessor
from src.data_access.electrodes.status_propagation import propagate_detailed

MARKINGS = [f"B{14460 + i}" for i in range(12)]
SCRAP = {"B14465": str(ElectrodeStatus.TESTED_AND_SCRAPPED)}
VATS = {marking: "1" for marking in MARKINGS}

# Outside the scrap margin, so it reads Needs testing. Inside it, so it reads
# Scrapped. Both are swept, and neither is locked once the card is finalised.
FLAGGED = "B14468"
CONDEMNED = "B14466"


def swept(base, pinned=None) -> dict:
    """Effective statuses for one finalised scrap, sweeping over ``base``."""
    return propagate_detailed(MARKINGS, base, SCRAP, vats=VATS, pinned=pinned)[0]


class TestASweepLeavesAHandSetStatusAlone(unittest.TestCase):
    """No inference may claim an electrode an operator spoke about."""

    def test_every_status_the_dialog_offers_survives(self) -> None:
        for status in ElectrodeStatus:
            if status is ElectrodeStatus.TEST_ONGOING:
                continue  # read off the board, never set by hand
            with self.subTest(status=str(status)):
                shown = swept({FLAGGED: str(status)}, pinned={FLAGGED})
                self.assertEqual(shown[FLAGGED], str(status))

    def test_inside_the_scrap_margin_too(self) -> None:
        """The margin is the harsher sweep: it claims, it does not just flag."""
        shown = swept({CONDEMNED: str(ElectrodeStatus.RELEASED)},
                      pinned={CONDEMNED})
        self.assertEqual(shown[CONDEMNED], str(ElectrodeStatus.RELEASED))

    def test_a_release_sweep_does_not_claim_one_either(self) -> None:
        """Both directions of the release, not only the scrap's doubt."""
        released = {"B14470": str(ElectrodeStatus.TESTED_AND_RELEASED)}
        shown = propagate_detailed(
            MARKINGS, {FLAGGED: str(ElectrodeStatus.NOT_TESTED)}, released,
            vats=VATS, pinned={FLAGGED})[0]
        self.assertEqual(shown[FLAGGED], str(ElectrodeStatus.NOT_TESTED))

    def test_it_does_not_shield_the_electrodes_beyond_it(self) -> None:
        """The operator spoke about one electrode, not about the fault's reach.

        A correction is not a wall: the doubt carries on past it, so the
        neighbours are flagged exactly as they were before.
        """
        shown = swept({"B14467": str(ElectrodeStatus.RELEASED)},
                      pinned={"B14467"})
        self.assertEqual(shown["B14467"], str(ElectrodeStatus.RELEASED))
        self.assertEqual(shown[FLAGGED], str(ElectrodeStatus.NEEDS_TESTING))

    def test_nothing_pinned_sweeps_exactly_as_before(self) -> None:
        self.assertEqual(swept({}), swept({}, pinned=set()))


class TestAHandSetStatusOutranksAFrozenOne(unittest.TestCase):
    """Two settled statements about one electrode: the operator's is the later.

    They used to be ordered by comparing this machine's clock against the
    server's, so a workstation running a little behind quietly lost its edits.
    """

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame([
            {str(ElectrodeColumns.DATE): "2026-08-27 08:00:00",
             str(ElectrodeColumns.MARKING): marking,
             str(ElectrodeColumns.FULL_NAME): "CATHODE",
             str(ElectrodeColumns.RECIPE): 11} for marking in MARKINGS])

    def test_the_edit_shows_with_the_board_empty(self) -> None:
        archived = {"B14465": str(ElectrodeStatus.TESTED_AND_SCRAPPED),
                    FLAGGED: str(ElectrodeStatus.NEEDS_TESTING)}
        by_hand: set = set()
        processor = ElectrodeProcessor(
            status_by_marking={},
            overrides_provider=lambda: dict(SCRAP),   # finalised, still sweeps
            board_provider=lambda: {},                # nothing on the board
            base_overrides_provider=lambda: dict(archived),
            # Frozen WITH its flag, which is what the finalise recorded: this
            # electrode sits three past the scrap, which is the test-first
            # position. Left out, the row said nothing about its own flag and
            # the status underneath it came out as a wait on the zone instead.
            settled_priorities_provider=lambda: {FLAGGED} - set(by_hand),
            vat_provider=lambda: VATS,
            vat_pending_provider=lambda: False,
            pinned_provider=lambda: set(by_hand))

        def shown() -> str:
            out = processor.process(self.frame())
            row = out[out[str(ElectrodeColumns.MARKING)] == FLAGGED]
            return str(row.iloc[0][STATUS_COLUMN])

        self.assertEqual(shown(), str(ElectrodeStatus.NEEDS_TESTING))
        # What set_electrode_status does: write it, patch the base, pin it.
        processor.set_base_status(FLAGGED, str(ElectrodeStatus.RELEASED))
        by_hand.add(FLAGGED)
        self.assertEqual(shown(), str(ElectrodeStatus.RELEASED))


if __name__ == "__main__":
    unittest.main()
