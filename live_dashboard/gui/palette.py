"""The one place the wall screen's look is decided.

Same house style as QC Data Manager, teal on white cards, scaled up for a room
rather than a desk. Nothing here is inlined into a widget, so the screen can be
retuned for a brighter office by editing this file alone.

Everything is sized for distance. The research on wall boards is consistent on
one point: a screen read from four or five metres needs type several times the
size a desk needs, and it needs fewer things on it. So the numbers are large,
the cards are few, and colour carries meaning that the labels also spell out.
"""

from __future__ import annotations

from src.data_access.electrodes.electrode_columns import ElectrodeStatus

# ---------------------------------------------------------------------------
# Ink and surfaces
# ---------------------------------------------------------------------------
# Not pure white. A wall screen at full white is a lamp, and every card floating
# on it loses its edge; a hint of the teal in the backdrop gives the cards
# something to sit on and ties the screen to the brand at the same time.
BACKDROP_TOP = "#EDF5F5"
BACKDROP_BOTTOM = "#DCEBEC"
CARD = "#FFFFFF"
CARD_EDGE = "#DCE8EA"

INK = "#12232B"
INK_MUTED = "#66787F"
INK_FAINT = "#9AAAB0"

# ---------------------------------------------------------------------------
# Brand
# ---------------------------------------------------------------------------
# The app's accent, and the two neighbours it needs to make a header gradient
# read as one colour moving rather than as two colours meeting.
TEAL = "#14B8A6"
TEAL_DEEP = "#0B7268"
TEAL_MID = "#0D9488"
TEAL_SOFT = "#55BDBD"
TEAL_WASH = "#E6F5F2"

# The warm counterweight. Teal alone across a whole screen reads cold and, worse,
# flat: with every card the same hue nothing stands out, which is the failure
# mode of a dashboard that is trying to be tasteful. One warm accent fixes both.
AMBER = "#E09A26"
AMBER_WASH = "#FDF1DC"
CORAL = "#E45A52"
CORAL_WASH = "#FBE4E2"
INDIGO = "#3566C7"
INDIGO_WASH = "#E4ECFB"

# ---------------------------------------------------------------------------
# Status colours, taken from the app rather than reinvented
# ---------------------------------------------------------------------------
# Imported at the point of use so this module stays importable without Qt: the
# app's copy lives beside a pile of widgets. Kept as one dict so the donut, the
# summary rows and the vat bars cannot disagree about what "Rejected" looks like.
STATUS_ACCENTS: dict[str, str] = {
    str(ElectrodeStatus.TESTED_AND_RELEASED): "#0D9488",
    str(ElectrodeStatus.RELEASED): "#66C4B0",
    str(ElectrodeStatus.TESTED_AND_SCRAPPED): "#7A0F2E",
    str(ElectrodeStatus.SCRAPPED): "#E45A52",
    str(ElectrodeStatus.NEEDS_TESTING): "#E09A26",
    str(ElectrodeStatus.NOT_TESTED): "#8A959B",
    str(ElectrodeStatus.TEST_ONGOING): "#3566C7",
}
STATUS_TINTS: dict[str, str] = {
    str(ElectrodeStatus.TESTED_AND_RELEASED): "#D6EFE9",
    str(ElectrodeStatus.RELEASED): "#E8F6F2",
    str(ElectrodeStatus.TESTED_AND_SCRAPPED): "#F6DCE3",
    str(ElectrodeStatus.SCRAPPED): "#FBE4E2",
    str(ElectrodeStatus.NEEDS_TESTING): "#FDF1DC",
    str(ElectrodeStatus.NOT_TESTED): "#F1F3F4",
    str(ElectrodeStatus.TEST_ONGOING): "#E4ECFB",
}

# The two things the line makes, for the throughput bars. Not a status, so it
# does not belong in the maps above.
CATHODE = TEAL_MID
ANODE = INDIGO

# A rate at or above this is bad enough to say so in colour. Below it the bar
# stays teal, so the screen is not permanently shouting.
FAULT_WARN_PCT = 5.0
FAULT_BAD_PCT = 10.0

# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
CARD_RADIUS = 20
CARD_PADDING = 22
GRID_GAP = 18
# Big enough to read across a room, small enough that six of them fit.
KPI_NUMBER_PT = 54
KPI_CAPTION_PT = 13
CARD_TITLE_PT = 15
BODY_PT = 15
ROW_PT = 16

FONT = "'Segoe UI', 'Inter', Arial, sans-serif"
# Named for QFont, which takes one family rather than a CSS stack. The big
# numerals are the one place a substituted font shows, since a fallback
# draws the decimal point at a different height and "63.3" reads as "63·3".
NUMERAL_FAMILY = "Segoe UI"


def fault_colour(rate: float) -> str:
    """Teal, amber or coral, by how bad a fault rate is.

    Three steps rather than a gradient. A wall screen is glanced at, and a
    continuous colour ramp cannot be read at a glance: what a viewer can do is
    tell three colours apart and remember which one is bad.
    """
    if rate >= FAULT_BAD_PCT:
        return CORAL
    if rate >= FAULT_WARN_PCT:
        return AMBER
    return TEAL_MID


def card_style(accent: str = "") -> str:
    """A white card, optionally topped by a coloured rule.

    The rule is what stops six white rectangles reading as one grey mass, and
    it is also the only place a card states its own category, so it is never
    the only signal: every card has a title as well.
    """
    top = (f"border-top: 4px solid {accent};"
           f"border-top-left-radius: {CARD_RADIUS}px;"
           f"border-top-right-radius: {CARD_RADIUS}px;") if accent else ""
    return (f"QFrame#card {{ background: {CARD};"
            f"border: 1px solid {CARD_EDGE};"
            f"border-radius: {CARD_RADIUS}px; {top} }}")


def backdrop_style() -> str:
    """The wash the cards sit on."""
    return (f"QWidget#backdrop {{ background: qlineargradient("
            f"x1:0, y1:0, x2:0.35, y2:1,"
            f"stop:0 {BACKDROP_TOP}, stop:1 {BACKDROP_BOTTOM}); }}")


def header_style() -> str:
    """The brand bar across the top, teal moving to its deep shade."""
    return (f"QFrame#header {{ background: qlineargradient("
            f"x1:0, y1:0, x2:1, y2:0,"
            f"stop:0 {TEAL_DEEP}, stop:0.55 {TEAL_MID}, stop:1 {TEAL});"
            f"border-radius: {CARD_RADIUS}px; }}")


def title_style(size: int = CARD_TITLE_PT, colour: str = INK,
                weight: int = 700, spacing: str = "0.4px") -> str:
    """A card heading, or anything else that has to read as one."""
    return (f"color: {colour}; font-family: {FONT}; font-size: {size}pt;"
            f"font-weight: {weight}; letter-spacing: {spacing};"
            f"background: transparent;")
