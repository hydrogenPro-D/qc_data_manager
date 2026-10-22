"""Which coupons a request asks for, and what decides it.

The stored values are the contract with the other application and with
``qc_requests.coupon_choice``. The display text is separate, as everywhere else.

A coupon is a piece cut out of the electrode and sent to QC. Three answers:
all five of them, the top right one alone, or none, and the third is not a
small version of the others. **Nothing means no request at all**: an electrode
production is asked for but cuts nothing from is a trip for nobody, and the
neighbours a failure would have queued are not queued either.
"""

from __future__ import annotations

# The stored values. PDM reads these spellings straight off the view.
NONE = "none"
TOP_RIGHT = "top_right"
ALL_FIVE = "all_five"

# Why a request was raised. Not the request KINDS: a fault splits into the two
# tests that can find one, because QC answers them differently and that is the
# whole point of the setting.
REASON_INTERVAL = "interval"
REASON_SEM = "sem"
REASON_IC = "ic"
REASONS = (REASON_INTERVAL, REASON_SEM, REASON_IC)

# Least to most. "Both tests failed, so take the stricter" is then a max, and
# "is anything wanted" is a comparison against the first.
ORDER = (NONE, TOP_RIGHT, ALL_FIVE)

COUPON_LABELS: dict[str, str] = {
    NONE: "Nothing",
    TOP_RIGHT: "Top right coupon",
    ALL_FIVE: "All five coupons",
}

REASON_LABELS: dict[str, str] = {
    REASON_INTERVAL: "Automatic sampling",
    REASON_SEM: "Sampling due to SEM failure",
    REASON_IC: "Sampling due to IC failure",
}

# What the three mean with nothing stored, and what a database without the
# migration behaves as. The same values 008 seeds, so the two cannot disagree
# about what QC does today.
DEFAULTS: dict[str, str] = {
    REASON_INTERVAL: ALL_FIVE,
    REASON_SEM: TOP_RIGHT,
    REASON_IC: NONE,
}


def normalize(value) -> str:
    """One of the three, from whatever was stored or typed.

    Tolerant on the way in for the same reason PDM is: ``top-right``,
    ``Top Right`` and ``TOPRIGHT`` all mean the one thing, and anything
    unrecognised reads as nothing rather than raising. A value nobody can make
    sense of is not a reason to refuse to draw the screen.
    """
    text = "".join(str(value or "").split()).replace("-", "_").lower()
    if text in (TOP_RIGHT, "topright"):
        return TOP_RIGHT
    if text in (ALL_FIVE, "allfive", "all5"):
        return ALL_FIVE
    return NONE


def label(value) -> str:
    """The coupons as an operator reads them."""
    return COUPON_LABELS[normalize(value)]


def wanted(value) -> bool:
    """Whether anything at all is being asked for."""
    return normalize(value) != NONE


def strictest(*values) -> str:
    """The strictest of these, which is the answer when more than one applies.

    An electrode whose SEM and IC both failed is covered by two settings, and
    the one that asks for more is the one to obey: the coupons QC wants are
    whatever answers every question being asked of that electrode.
    """
    return max((normalize(value) for value in values),
               key=ORDER.index, default=NONE)


def for_failure(sem_failed: bool, ic_failed: bool, settings: dict) -> str:
    """What a failure's neighbours are worth, from what the failure was.

    Neither test having failed is not an absence of information: the electrode
    was scrapped for some other reason and nothing says which rule applies, so
    it takes the stricter of the two rather than silently none.
    """
    if not sem_failed and not ic_failed:
        return strictest(settings.get(REASON_SEM), settings.get(REASON_IC))
    reasons = ([REASON_SEM] if sem_failed else []) + \
              ([REASON_IC] if ic_failed else [])
    return strictest(*(settings.get(reason) for reason in reasons))


def with_defaults(stored: dict | None) -> dict[str, str]:
    """The three settings, with anything missing filled in from DEFAULTS."""
    stored = stored or {}
    return {reason: normalize(stored.get(reason, DEFAULTS[reason]))
            for reason in REASONS}
