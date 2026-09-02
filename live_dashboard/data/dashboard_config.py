"""The dashboard's tunables, read from ``live_dashboard/config.json``.

Its own file rather than a section of the app's config: the screen is a
separate program with a separate life cycle, and an operator retuning the
rotation speed on the TV machine should not be editing the tool everybody
else runs.

Every getter falls back to a built-in default, so a missing or malformed file
degrades to a working screen rather than to a crash on a machine nobody is
sitting at.
"""

from __future__ import annotations

import json
import os

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "config.json")

DEFAULTS: dict[str, int] = {
    "rotation_seconds": 4,
    "transition_ms": 620,
    "refresh_interval_ms": 60000,
    "vat_rows": 6,
}

_cache: dict | None = None


def _config() -> dict:
    """The parsed file, read once. An unreadable one reads as empty."""
    global _cache
    if _cache is None:
        try:
            with open(_CONFIG_PATH, encoding="utf-8") as handle:
                _cache = json.load(handle)
        except (OSError, ValueError) as e:
            print(f"Could not read {_CONFIG_PATH}, using defaults: {e}")
            _cache = {}
    return _cache


def _positive_int(key: str) -> int:
    """One tunable, guaranteed to be a number above zero.

    A zero rotation would stop the deck and a zero interval would spin the
    worker thread flat out, so a nonsense value falls back rather than being
    honoured.
    """
    value = _config().get(key, DEFAULTS[key])
    try:
        number = int(value)
    except (TypeError, ValueError):
        return DEFAULTS[key]
    return number if number > 0 else DEFAULTS[key]


def rotation_seconds() -> int:
    """How long one window holds the screen."""
    return _positive_int("rotation_seconds")


def transition_ms() -> int:
    """How long the slide between two windows takes."""
    return _positive_int("transition_ms")


def refresh_interval_ms() -> int:
    """How often the numbers are re-read."""
    return _positive_int("refresh_interval_ms")


def vat_rows() -> int:
    """How many vats the league table lists."""
    return _positive_int("vat_rows")
