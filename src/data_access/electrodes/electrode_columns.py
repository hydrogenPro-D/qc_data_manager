"""Enum of electrode column names — the contract between the app and the DB.

These string values ARE the dbo.MetaData column names. Never rename a value
casually: a rename is a coordinated DB + app change. What the operator sees is
separate (see COLUMN_DISPLAY_NAMES in the GUI tab).
"""

import re
from enum import Enum


class ElectrodeColumns(Enum):
    """Enumeration of the electrode columns read from dbo.MetaData."""

    # Declaration order is the table's column order.
    DATE = "Date"
    MARKING = "Marking"
    FULL_NAME = "Full_name"
    RECIPE = "Rcp_nr"

    def __str__(self) -> str:
        """Return the string value of the column name."""
        return self.value


# A recipe variant is still the electrode it is a variant of: the process log
# names it separately, but the QC board and the SEM tab only have anodes and
# cathodes. Keyed by the stored Full_name, trimmed and upper-cased. The
# Electrodes tab is unaffected and still shows the name as recorded.
ELECTRODE_TYPE_ALIASES: dict[str, str] = {
    "CATHODE NEW": "CATHODE",
}


def canonical_electrode_type(name) -> str:
    """'CATHODE NEW' to 'CATHODE'. The type everything downstream works in."""
    text = str(name or "").strip().upper()
    return ELECTRODE_TYPE_ALIASES.get(text, text)


# Derived columns added by the processor — not stored in dbo.MetaData.
STATUS_COLUMN = "Status"
PRIORITY_COLUMN = "Test priority"

# Which plating vat the electrode went through, joined in from dbo.ProcessData
# at display time. Shown only when that table is readable, see
# electrode_vat_db.py.
VAT_COLUMN = "Vat"

# Which live QC-board decision currently drives this electrode's status (empty
# when none does). A decision still on the board can be dragged elsewhere, and
# every electrode it reached would move with it — so those aren't safe to edit
# by hand until the decision is finalised.
LOCKED_BY_COLUMN = "Locked by"

# Marks the electrodes to pull for testing first — those sitting just outside a
# scrap's safety margin, where the fault most likely started or stopped.
PRIORITY_FLAG = "Test first"

# When this electrode's status stopped moving: marked as done on the QC board,
# or set by hand here. Empty while it is still on the board, since a card that
# can be dragged has not settled anything yet.
FINALISED_COLUMN = "Status finalised"


def status_family(status: str) -> str:
    """Collapse a status to its outcome, for the range list in the report.

    RELEASED MEANS SELLABLE, so a tested electrode is NOT one of them. Testing
    destroys it: it passed, its verdict releases the neighbours it vouches for,
    and the electrode itself is gone. Counting it as released overstated what
    could be shipped by exactly the number of electrodes that had been tested.
    It gets a family of its own instead.

    The scrapped side stays merged. A tested and failed electrode is destroyed
    AND rejected, so calling it scrapped is true either way.

    Needs testing is its own outcome, it is an instruction rather than a
    verdict.
    """
    text = str(status).strip()
    if text == ElectrodeStatus.TESTED_AND_RELEASED.value:
        return ElectrodeStatus.TESTED_AND_RELEASED.value
    if text == ElectrodeStatus.RELEASED.value:
        return ElectrodeStatus.RELEASED.value
    if text in (ElectrodeStatus.TESTED_AND_SCRAPPED.value, ElectrodeStatus.SCRAPPED.value):
        return ElectrodeStatus.SCRAPPED.value
    if text == ElectrodeStatus.NEEDS_TESTING.value:
        return ElectrodeStatus.NEEDS_TESTING.value
    return ElectrodeStatus.NOT_TESTED.value


class ElectrodeStatus(Enum):
    """The QC outcome of an electrode.

    Two axes: the outcome (released / scrapped), and whether it was *tested* —
    decided directly on the QC board — or merely inferred from a neighbour's
    verdict by the propagation rules. ``NOT_TESTED`` is neither.
    """

    TESTED_AND_RELEASED = "Tested and Released"
    RELEASED = "Released"
    TESTED_AND_SCRAPPED = "Tested and Scrapped"
    SCRAPPED = "Scrapped"
    NEEDS_TESTING = "Needs testing"
    NOT_TESTED = "Not Tested"
    # On the QC board and not yet decided. Never stored: it is read off the
    # board on every render, so it appears when a card is added and goes when
    # the card is decided or removed. That is also why it is not offered as a
    # status somebody can set by hand.
    TEST_ONGOING = "Test ongoing"
    # What a doubting electrode is really waiting on. Stored as Needs testing;
    # which of the two it reads depends on how far the electrode being tested
    # in its zone has got, so both are worked out on every render like
    # TEST_ONGOING and neither is in qc_data.electrode_status_kind.
    WAITING_FOR_SEM = "Waiting for additional SEM approval"
    WAITING_FOR_IC = "Waiting for IC release / not QC sample"

    def __str__(self) -> str:
        """Return the string value of the status."""
        return self.value


# Worked out on every render and never written: the database's
# electrode_status_kind does not hold them, and none may be set by hand.
NEVER_STORED = frozenset({
    ElectrodeStatus.TEST_ONGOING,
    ElectrodeStatus.WAITING_FOR_SEM,
    ElectrodeStatus.WAITING_FOR_IC,
})


# Values written by an older build, mapped to the spelling used now. Anything
# reading a stored status passes it through normalize_status first, so a file
# saved before the fix still loads instead of reading as Not Tested.
RENAMED_STATUSES: dict[str, str] = {
    "Scraped": ElectrodeStatus.SCRAPPED.value,
    "Tested and Scraped": ElectrodeStatus.TESTED_AND_SCRAPPED.value,
}


def normalize_status(status: str) -> str:
    """Bring a stored status up to the current spelling."""
    text = str(status).strip()
    return RENAMED_STATUSES.get(text, text)


def normalize_marking(marking: str) -> str:
    """One spelling of an electrode's marking. ``'B000897'`` -> ``'B897'``.

    The Gen 3.1 workbook pads the number to no fixed width: the same electrode
    appears as B897 in one column, B0897 in another and B000897 in a sample
    name. Left alone, those are three electrodes, none of which joins to
    dbo.MetaData. Anything that is not a letter prefix and a number is returned
    trimmed but otherwise untouched.
    """
    text = str(marking).strip()
    match = re.fullmatch(r"([A-Za-z]*)0*(\d+)", text)
    return f"{match.group(1)}{match.group(2)}" if match else text
