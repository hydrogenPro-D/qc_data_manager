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
