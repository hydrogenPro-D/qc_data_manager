"""What each vat is actually sampled at today, so it can be compared with the
best rate the run lengths suggest.

A placeholder for something the app will own. The plan is for the interval to
be set in QC Data Manager and stored in the database; until then it is typed in
here and kept in a small JSON file in ``config/``.

**This is the one thing in this tool that writes anything**, and it writes to a
local file, never to the database. The backend is still wrapped read only, see
``read_only.py``.
"""

from __future__ import annotations

import json
import os

# What a vat is assumed to be sampled at until somebody says otherwise.
DEFAULT_INTERVAL = 12
# The widest and tightest a hand-typed interval may be. One means testing every
# electrode, which is not a sampling rate at all but is worth allowing so the
# figure can be typed while thinking.
MIN_INTERVAL = 1
MAX_INTERVAL = 1000

# Beside the app's other settings rather than in the package, so it survives
# a reinstall and is git-ignored with the rest of config/.
_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "config", "sampling_intervals.json")

_cache: dict[str, int] | None = None


def _load() -> dict[str, int]:
    """The stored intervals, read once. An unreadable file reads as empty.

    Deliberately forgiving: this is a convenience note to self, not data, and a
    corrupted file should cost the defaults rather than the whole window.
    """
    global _cache
    if _cache is None:
        try:
            with open(_PATH, encoding="utf-8") as handle:
                stored = json.load(handle)
            _cache = {str(vat): int(value) for vat, value in stored.items()
                      if _sane(value)}
        except (OSError, ValueError, AttributeError) as e:
            if os.path.exists(_PATH):
                print(f"Could not read {_PATH}, starting from defaults: {e}")
            _cache = {}
    return _cache


def _sane(value) -> bool:
    try:
        return MIN_INTERVAL <= int(value) <= MAX_INTERVAL
    except (TypeError, ValueError):
        return False


def current_for(vat: str) -> int:
    """What this vat is sampled at now, or the default if nobody has said."""
    return _load().get(str(vat), DEFAULT_INTERVAL)


def set_current(vat: str, interval: int) -> None:
    """Record one vat's interval and write the file.

    Written on every change rather than on close. The file is three lines long
    and a tool nobody has closed properly should not lose what was typed in it.
    """
    if not _sane(interval):
        return
    _load()[str(vat)] = int(interval)
    try:
        with open(_PATH, "w", encoding="utf-8") as handle:
            json.dump(_cache, handle, indent=2, sort_keys=True)
    except OSError as e:
        print(f"Could not write {_PATH}: {e}")


def deviation(current: int, best: int | None) -> int | None:
    """How many electrodes the current interval is from the best one.

    Unsigned, because the panel says which side it is on in words. None when
    there is no best to compare against, which is not the same as being on it.
    """
    if not best or not current:
        return None
    return abs(int(best) - int(current))
