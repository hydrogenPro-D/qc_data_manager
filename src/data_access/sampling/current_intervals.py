"""What each vat is actually sampled at today, so it can be compared with the
best rate the run lengths suggest.

It used to be a JSON file on one machine, which meant the figure one person
typed was invisible to everybody else. It now lives in
``qc_requests.vat_sampling_interval``, because it stopped being a note to self
the moment the request rules started reading it: the interval is what decides
which electrode is due next, and that question is asked by both applications.

Pure here, no Qt and no connection of its own. The numbers arrive as a dict and
the backend is what fetched them, which is what keeps the rule testable with
plain data in and data out.
"""

from __future__ import annotations

# What a vat with no row reads at. The migration seeds every vat on the line at
# this, so it is normally what is stored rather than a fallback; it stands in
# for a vat that appeared after the migration ran, and it is display only. The
# rule that raises requests reads the table and has no default of its own, so a
# vat nobody has set is not sampled rather than sampled at a guess.
DEFAULT_INTERVAL = 25
# The widest and tightest a hand-typed interval may be. One means testing every
# electrode, which is not a sampling rate at all but is worth allowing so the
# figure can be typed while thinking.
MIN_INTERVAL = 1
MAX_INTERVAL = 1000


def sane(value) -> bool:
    """Whether this is an interval somebody could have meant."""
    try:
        return MIN_INTERVAL <= int(value) <= MAX_INTERVAL
    except (TypeError, ValueError):
        return False


def current_for(intervals: dict, vat: str) -> int:
    """What this vat is sampled at now, or the default if nobody has said.

    A vat with no row reads as the default, which is also what an unreachable
    table gives: the tab then shows the figure it always showed rather than a
    blank, and says nothing it cannot stand behind.
    """
    value = (intervals or {}).get(str(vat))
    return int(value) if sane(value) else DEFAULT_INTERVAL


def deviation(current: int, best: int | None) -> int | None:
    """How many electrodes the current interval is from the best one.

    Unsigned, because the panel says which side it is on in words. None when
    there is no best to compare against, which is not the same as being on it.
    """
    if not best or not current:
        return None
    return abs(int(best) - int(current))
