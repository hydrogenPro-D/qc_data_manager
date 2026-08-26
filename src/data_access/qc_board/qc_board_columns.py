"""The QC board's stages and electrode types.

Mirrors the columns of the "QC progress overview" workbook. The enum *values*
are what gets written to the board file, so renaming one is a data migration,
not a relabel — the display text lives in the GUI.
"""

from enum import Enum

from src.data_access.electrodes.electrode_columns import ElectrodeStatus


class QcStage(Enum):
    """A column of the board. An electrode sits in exactly one at a time."""

    WAITING = "waiting_for_qc"
    IC_DONE = "ic_done"
    SEM_DONE = "sem_done"
    WAITING_APPROVAL = "waiting_for_approval"
    WAITING_FOR_RETEST = "waiting_for_retest"
    RETEST_ONGOING = "retest_ongoing"
    RELEASE = "release"
    SCRAPPED = "scrapped"

    def __str__(self) -> str:
        """Return the stored value of the stage."""
        return self.value


class ElectrodeType(Enum):
    """Which half of a stage an electrode belongs in."""

    ANODE = "ANODE"
    CATHODE = "CATHODE"

    def __str__(self) -> str:
        """Return the stored value of the type."""
        return self.value


# Board order, left to right.
STAGE_ORDER: list[QcStage] = [
    QcStage.WAITING,
    QcStage.SEM_DONE,
    QcStage.IC_DONE,
    QcStage.WAITING_APPROVAL,
    QcStage.WAITING_FOR_RETEST,
    QcStage.RETEST_ONGOING,
    QcStage.RELEASE,
    QcStage.SCRAPPED,
]

# Stages that existed in an earlier version of the board, mapped to where their
# electrodes should land now. Without this a saved board would silently lose
# every electrode still sitting in a retired column.
RETIRED_STAGES: dict[str, QcStage] = {
    "approved_by_ic": QcStage.WAITING_APPROVAL,
    "scraped": QcStage.SCRAPPED,   # spelling fix, boards saved before it
}

# The two terminal stages decide an electrode's QC status; everything earlier
# leaves it Not Tested. These are the *tested* variants — the electrode was
# actually put through QC, as opposed to inheriting a neighbour's verdict.
# Findings recorded against an electrode while it moves through QC. They are
# observations, not verdicts: none of them changes its status on its own.
IC_RESULT = "ic_result"
SEM_RESULT = "sem_result"
FINDING_FIELDS = (IC_RESULT, SEM_RESULT)

# Every finding has three states, and the third one matters: "the test has not
# been run" is a different fact from "it ran and found nothing". The SEM result
# was a cracked tick box and could not tell them apart, see migration 013.
PENDING = ""

# IC outcome. One value rather than two booleans, since an electrode cannot
# have both passed and failed.
IC_PENDING = PENDING
IC_PASSED = "passed"
IC_FAILED = "failed"
IC_RESULTS = (IC_PENDING, IC_PASSED, IC_FAILED)

# SEM outcome. Looking for cracks is what SEM does here, so its two outcomes
# are named after what it finds rather than pass and fail. CRACKED is the one
# that colours the card.
SEM_PENDING = PENDING
SEM_CRACKED = "cracked"
SEM_ACCEPTED = "accepted"
SEM_RESULTS = (SEM_PENDING, SEM_CRACKED, SEM_ACCEPTED)

# Which values each finding may hold, so a reader can validate them in a loop
# instead of naming all three.
FINDING_VALUES = {IC_RESULT: IC_RESULTS, SEM_RESULT: SEM_RESULTS}


STAGE_STATUS: dict[QcStage, str] = {
    QcStage.RELEASE: str(ElectrodeStatus.TESTED_AND_RELEASED),
    QcStage.SCRAPPED: str(ElectrodeStatus.TESTED_AND_SCRAPPED),
}
