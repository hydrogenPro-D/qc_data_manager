"""A finalised electrode's test-first flag is the one its finalise recorded.

The flag is worked out by the sweeps and frozen when a failure is marked as
done. The sweeps go on running afterwards, over every verdict including the
frozen ones, so they keep producing an answer for electrodes that already have
one. Where the two disagreed the live answer was being added on top.

B13831 is what that looked like: frozen as Needs testing from B14762 with the
flag NOT set, and shown in the pull list as one to test first, with no card open
to explain it. It could not be requested either, because the request sweep asks
for frozen flags, so it sat there as an instruction nobody could act on.
"""

import unittest

import pandas as pd

from src.data_access.electrodes.electrode_columns import (
    ElectrodeColumns,
    ElectrodeStatus,
    PRIORITY_COLUMN,
    PRIORITY_FLAG,
    STATUS_COLUMN,
)
from src.data_access.electrodes.electrode_processor import ElectrodeProcessor

SCRAPPED = str(ElectrodeStatus.TESTED_AND_SCRAPPED)
NEEDS_TESTING = str(ElectrodeStatus.NEEDS_TESTING)

# A scrap, and the electrodes its doubt reaches either side.
MARKINGS = [f"B{13820 + n}" for n in range(24)]
SCRAP = "B13830"
# Just outside the scrap's margin, where the fault most likely started or
# stopped. That is where a sweep puts a test-first flag: plus and minus three.
EDGE = "B13833"
OTHER_EDGE = "B13827"


def _processor(settled_statuses, settled_flags) -> ElectrodeProcessor:
    return ElectrodeProcessor(
        status_by_marking={},
        overrides_provider=lambda: {SCRAP: SCRAPPED},
        base_overrides_provider=lambda: dict(settled_statuses),
        settled_priorities_provider=lambda: set(settled_flags))


def _flags(settled_statuses, settled_flags) -> set:
    frame = pd.DataFrame([
        {str(ElectrodeColumns.DATE): "2026-08-27 08:00:00",
         str(ElectrodeColumns.MARKING): marking,
         str(ElectrodeColumns.FULL_NAME): "CATHODE",
         str(ElectrodeColumns.RECIPE): 11} for marking in MARKINGS])
    out = _processor(settled_statuses, settled_flags).process(frame)
    return {row[str(ElectrodeColumns.MARKING)] for _, row in out.iterrows()
            if str(row[PRIORITY_COLUMN]).strip() == PRIORITY_FLAG}


class TestTheSweepStillFlagsWhatIsNotFinalised(unittest.TestCase):
    """The behaviour this is a condition on, so it cannot quietly go."""

    def test_nothing_finalised_means_the_sweep_decides(self) -> None:
        self.assertIn(EDGE, _flags({}, set()))

    def test_a_frozen_row_that_agrees_keeps_the_flag(self) -> None:
        """Through the settled set, which is where a frozen flag comes from."""
        self.assertIn(EDGE, _flags({EDGE: NEEDS_TESTING}, {EDGE}))


class TestAFrozenRowWithoutTheFlag(unittest.TestCase):
    """The bug. The finalise said no, and the live sweep said yes anyway."""

    def test_the_sweep_does_not_add_a_flag_the_finalise_left_off(self) -> None:
        self.assertNotIn(EDGE, _flags({EDGE: NEEDS_TESTING}, set()))

    def test_the_rest_of_the_zone_is_unaffected(self) -> None:
        """Only the electrode whose row is settled stops being flagged.

        The other edge of the same scrap keeps its flag, so this takes the one
        row's word for that one row rather than standing the zone down.
        """
        loose = _flags({}, set())
        frozen = _flags({EDGE: NEEDS_TESTING}, set())
        self.assertEqual(loose - frozen, {EDGE})
        self.assertIn(OTHER_EDGE, frozen)

    def test_a_row_that_no_longer_stands_falls_back_to_the_sweep(self) -> None:
        """A stale frozen row is left out of the settled statuses, so the
        electrode takes the sweep's answer again rather than being stuck
        without a flag for ever."""
        self.assertIn(EDGE, _flags({}, set()))


if __name__ == "__main__":
    unittest.main()
