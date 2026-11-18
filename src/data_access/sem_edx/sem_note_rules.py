"""The five results a SEM sample can carry, and how the old free text maps onto them.

The note used to be typed, which produced 163 spellings of five opinions:
``Microcracked``, ``Mickrocracked``, ``Microcraks``, ``micro cracks`` and
``Microcracking`` are all the same result. From now the result is chosen from
:data:`CATEGORIES` and anything else an operator wants to say goes in the
comment beside it.

:func:`classify` is what reads the old text. It is deliberately conservative:
it never invents a result it cannot see a word for, and it never edits what
somebody wrote. The one exception is :data:`_BY_HAND`, a handful of notes QC
read and decided on themselves. A note that says more than its result keeps its whole text in
the comment, so nothing is lost and nothing is half rewritten.

Pure functions, no Qt and no database, so the mapping can be tested against
every real note before any of it is written down.
"""

from __future__ import annotations

import re

# Worst last. The order is the scale QC reads them on, and it is what the list
# in the sample window offers.
FINE = "Fine"
FEW_MICRO_CRACKS = "Few micro cracks"
MICROCRACKS = "Microcracks"
LESS_CRACKED = "Less cracked"
CRACKED = "Cracked"

CATEGORIES: tuple[str, ...] = (FINE, FEW_MICRO_CRACKS, MICROCRACKS,
                               LESS_CRACKED, CRACKED)

# Spellings of one word that have actually been typed into this column. Applied
# to a lowercased note before anything is matched, so the rules below only have
# to know the correct spelling.
_SPELLINGS = (
    # micro, mikro, mickro, then any of the ways "cracks" has been spelled,
    # including the ones that lost the r: microcacks, mickrocrackes.
    (r"mi[ck]+ro[\s\-]*c[r]?[ao][ck]+(s|ed|ing|es)?", "microcracks"),
    (r"\bc[r]?a[ck]+(s|ed|es)?\b", "cracks"),
)

# In order, first match wins. A result is read off the words that name it, and
# the scale runs from the most specific phrase to the least: "very few small
# cracks" has to be read before "cracks" or it would come out as Cracked.
_RULES = (
    (r"\bno\s+cracks?\b|\bnot\s+cracked\b|\bcrack\s*free\b", FINE),
    # No word boundary before "crack": the word it has to find is usually
    # inside "microcracks", which is one word.
    (r"\b(very\s+few|a\s+few|few)\b[^.;]{0,40}?crack", FEW_MICRO_CRACKS),
    # "Slightly cracked" is a plate that cracked a little, not one with a few
    # microcracks in it, so it reads as the milder half of Cracked.
    (r"\bless\s+crack|\bslight(ly)?\s+crack", LESS_CRACKED),
    (r"\bmicrocracks\b", MICROCRACKS),
    (r"\bsmall\s+cracks?\b|\bsmall/micro|\btiny\s+cracks?\b", MICROCRACKS),
    (r"\bcrack", CRACKED),
    (r"\bfine\b|\bgood\b|\bok\b", FINE),
)

# "Less cracked" is the odd one: it reads as less bad, and it is written by
# people who mean a plate worse than microcracked. So it is tried before the
# microcrack rule but after the "few" one, because "a few less cracked areas"
# has never been written and "few microcracks" has, 63 times.
# Read by hand. These notes say nothing about cracks at all, somebody pasted a
# measured value into the column, and QC looked the three samples up and called
# them Fine. Keyed on the exact text, so no other note is touched.
_BY_HAND = {
    "0.055353410012392204": FINE,
    "0.09385018171583047": FINE,
    "0.5276924615662871": FINE,
}

_COMPILED = tuple((re.compile(pattern), result) for pattern, result in _RULES)
_COMPILED_SPELLINGS = tuple((re.compile(pattern), fixed)
                            for pattern, fixed in _SPELLINGS)

# What is left once the result's own words are taken out. Anything else means
# the note was carrying more than a result, and that text is worth keeping.
_NOISE = re.compile(r"[^a-z0-9]+")
_RESULT_WORDS = re.compile(
    r"\b(very|a|few|less|small|tiny|no|not|slight|slightly|micro|microcracks|"
    r"cracks?|cracked|cracking|fine|good|ok|sample|is|was|the|and|rigid|seems)\b")


def normalize(note) -> str:
    """One note, lowercased and with the known misspellings straightened out."""
    text = str(note or "").strip().lower()
    for pattern, fixed in _COMPILED_SPELLINGS:
        text = pattern.sub(fixed, text)
    return text


def result_of(note) -> str:
    """Which of :data:`CATEGORIES` a note says, or '' where it says none."""
    text = normalize(note)
    if not text:
        return ""
    if text in _BY_HAND:
        return _BY_HAND[text]
    for pattern, result in _COMPILED:
        if pattern.search(text):
            return result
    return ""


def says_only_its_result(note) -> bool:
    """Whether the note is the result and nothing else.

    ``'Few microcracks'`` is, ``'Few microcracks, some salt deposits present'``
    is not, and neither is ``'Cracked Vat 30'``: the vat is a fact about the
    plate and belongs in the comment rather than being thrown away.
    """
    leftover = _NOISE.sub("", _RESULT_WORDS.sub("", normalize(note)))
    return not leftover


def classify(note) -> tuple[str, str]:
    """``(result, comment)`` for one old note.

    THE COMMENT IS THE NOTE, WORD FOR WORD, wherever the note said more than
    its result. Cutting the result's words out of a sentence and keeping the
    rest produces text nobody wrote, and these are somebody's observations
    about a plate they looked at, so they are moved whole or not at all.
    """
    text = str(note or "").strip()
    if not text:
        return "", ""
    result = result_of(text)
    if not result:
        # No word for a result anywhere in it. The text is still somebody's
        # note, so it is kept, and the result is left for a human to set.
        return "", text
    return result, "" if says_only_its_result(text) else text
