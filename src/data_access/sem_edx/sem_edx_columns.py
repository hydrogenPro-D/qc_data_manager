"""The SEM EDX column contract, and the layout that varies between sheets.

Two kinds of column, and they behave differently:

* **Fixed columns** (:class:`SemEdxColumns`) are the same for every sample, so
  they are an enum. These string values ARE the header of
  ``data/csv/sem_edx_gen4_1.csv``; renaming one means regenerating the file.
* **Quantities** are not fixed. Anodes carry Ni, S and Co; cathodes gained C and
  N in August 2026, along with the Ni:C, Ni:N and S:N ratios. So which columns
  exist is discovered from the workbook rather than written down here, and
  recorded in a layout file the app reads back (see ``sem_edx_source``).

A quantity is named the way the workbook names it: ``"Ni"`` for an element,
``"Ni:S"`` for a ratio. That single string is enough to know both what it is and
how to compute it, which keeps the layout file readable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SemEdxColumns(Enum):
    """The columns every measurement has, whatever was measured."""

    # Declaration order is the CSV's column order; the element columns follow.
    ELECTRODE = "electrode"
    ELECTRODE_TYPE = "electrode_type"
    POSITION = "position"
    ZOOM = "zoom"
    MEASUREMENT = "measurement"
    OPERATOR = "operator"
    NOTE = "note"
    # When the sample was measured. The workbook records no date at all, so
    # this is empty for everything imported from it and filled for everything
    # read out of a result folder.
    DATE = "date"
    # The result folder the sample was imported from, kept so the image viewer
    # can find its captures again. Empty for the workbook rows.
    FOLDER = "folder"
    # Which captures in that folder belong to this sample, by filename,
    # separated by IMAGE_SEPARATOR. Chosen during an import; filled with
    # everything in the folder for a sample matched up by the ETL. Empty means
    # "work it out from the position", which is what older rows carry.
    IMAGES = "images"

    def __str__(self) -> str:
        """Return the string value of the column name."""
        return self.value


FIXED_COLUMNS: tuple[str, ...] = tuple(str(c) for c in SemEdxColumns)

# An element's stored column: the symbol lowercased, then this. Ratios are
# never stored, they are computed from the elements on the way to the screen.
ELEMENT_SUFFIX = "_pct"
RATIO_SEPARATOR = ":"

# Between the filenames in the images column. A pipe cannot occur in a Windows
# filename, so it can never be mistaken for part of one.
IMAGE_SEPARATOR = "|"


def join_images(names) -> str:
    """Filenames as they are stored in one cell."""
    return IMAGE_SEPARATOR.join(str(n).strip() for n in (names or []) if str(n).strip())


def split_images(stored) -> list[str]:
    """The filenames back out of a stored cell."""
    return [part.strip() for part in str(stored or "").split(IMAGE_SEPARATOR)
            if part.strip()]

# How many points the workbook measures per sample. Only a floor: a sample with
# more gets more columns rather than losing the extras.
POINT_COUNT = 3

# What identifies one sample: a position on one electrode. Most electrodes have
# a single sample, but a full plate map has five.
SAMPLE_KEY = (str(SemEdxColumns.ELECTRODE), str(SemEdxColumns.POSITION))

# Used when no layout file has been written yet, so the app still starts and
# shows something recognisable. The real one comes from the workbook.
DEFAULT_LAYOUT: dict[str, list[str]] = {
    "ANODE": ["Ni", "S", "Ni:S", "Co", "Ni:Co"],
    "CATHODE": ["Ni", "S", "Ni:S", "Co", "Ni:Co"],
}


@dataclass(frozen=True)
class Quantity:
    """One measured column of a sheet: an element, or a ratio of two.

    Constructed from the workbook's own name for it, so ``Quantity("Ni")`` is
    the nickel percentage and ``Quantity("Ni:S")`` is the ratio between two.
    """

    name: str

    @property
    def is_ratio(self) -> bool:
        return RATIO_SEPARATOR in self.name

    @property
    def parts(self) -> tuple[str, str]:
        """``('Ni', 'S')`` for a ratio. Meaningless for an element."""
        numerator, _, denominator = self.name.partition(RATIO_SEPARATOR)
        return numerator.strip(), denominator.strip()

    @property
    def column(self) -> str:
        """Its base column name: ``ni_pct`` for an element, ``ni_s`` for a ratio."""
        if not self.is_ratio:
            return element_column(self.name)
        numerator, denominator = self.parts
        return f"{numerator.lower()}_{denominator.lower()}"

    @property
    def elements(self) -> tuple[str, ...]:
        """Which elements it needs. One for an element, two for a ratio."""
        return self.parts if self.is_ratio else (self.name,)


def element_column(symbol: str) -> str:
    """``'Ni'`` -> ``'ni_pct'``, the column an element is stored in."""
    return f"{str(symbol).strip().lower()}{ELEMENT_SUFFIX}"


def element_symbol(column: str) -> str | None:
    """``'ni_pct'`` -> ``'Ni'``. None when the column holds something else.

    ``capitalize`` is the right rule for chemical symbols: one or two letters,
    the first upper. ``cl`` becomes ``Cl`` and ``c`` becomes ``C``.
    """
    text = str(column)
    if not text.endswith(ELEMENT_SUFFIX):
        return None
    return text[: -len(ELEMENT_SUFFIX)].capitalize() or None


def point_column(quantity: str, point: int) -> str:
    """The column holding one measurement of a quantity, e.g. ``ni_pct_2``."""
    return f"{quantity}_{point}"


def average_column(quantity: str) -> str:
    """The column holding a quantity's average over the sample's points."""
    return f"{quantity}_avg"


def electrode_average_column(quantity: str) -> str:
    """The column holding a quantity averaged over the whole electrode.

    The workbook calls this "Ni:S average per electrodes": one figure covering
    every sample of an electrode, not just the spot on the row. Gen 3.1 fills
    it in; Gen 4.1 leaves it blank, so the app computes it either way.
    """
    return f"{quantity}_electrode_avg"


def first_ratio(layout: list[str] | None) -> Quantity | None:
    """The ratio an electrode-wide average is taken of.

    The sheets only ever average one, and it is always the first they list,
    Ni:S on both generations.
    """
    return next((q for q in quantities(layout) if q.is_ratio), None)


def quantities(layout: list[str] | None) -> list[Quantity]:
    """A layout entry as quantities, in the sheet's own order."""
    return [Quantity(name) for name in (layout or [])]


def element_columns(layout: list[str] | None) -> list[str]:
    """Every element column a layout needs, deduplicated, in order of use.

    A ratio contributes both of its elements, so a sheet that ratios S against
    N still stores both even if it never lists them on their own.
    """
    seen: list[str] = []
    for quantity in quantities(layout):
        for symbol in quantity.elements:
            column = element_column(symbol)
            if column not in seen:
                seen.append(column)
    return seen
