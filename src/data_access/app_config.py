"""config.json loading — the one place tunables come from.

Validation limits, unit labels and other domain constants live in
``config/config.json`` so a limit change is a config edit, not a code change.
Tests read the same file, so they never drift from the app.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def base_directory() -> Path:
    """Project root at dev time, the exe folder in a PyInstaller bundle."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[2]


_cache: dict | None = None


def load_config(reload: bool = False) -> dict:
    """Return config.json, read once per process."""
    global _cache
    if _cache is not None and not reload:
        return _cache
    path = base_directory() / "config" / "config.json"
    if not path.exists():
        raise FileNotFoundError(f"Application config not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        _cache = json.load(f)
    return _cache


def auto_refresh_interval_ms(default: int = 5000) -> int:
    """How often the GUI polls the visible tab for external changes."""
    return int(load_config().get("ui", {}).get("auto_refresh_interval_ms", default))


def production_database(default: str = "") -> str:
    """Which database name is the real one, so the app can say when it is not.

    Named here rather than worked out from the name itself: the production
    database is called dataloggingDev, so any guess based on the word "dev" or
    "test" would get it exactly backwards.
    """
    return str(load_config().get("database", {}).get("production", default)).strip()


def qc_request_window_days(default: int = 30) -> int:
    """How far back QC may ask production for an electrode.

    Production Data Manager lists the last 30 days of finished electrodes, so
    an older one has no row there for anybody to answer on, and the request
    would be expired by the database within the minute. 0 or less turns the
    limit off, which is how a database with no expiry rule behaves.
    """
    value = load_config().get("qc_requests", {}).get("request_window_days",
                                                     default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def sem_edx_layout(generation: str = "") -> dict:
    """Which quantities each SEM sheet shows, and in what order.

    ``{'Gen4.1': {'ANODE': ['Ni', 'S', 'Ni:S', ...], ...}}``, or one
    generation's entry when named. Configuration rather than data: it says what
    to draw, not what was measured, and no query can derive it, since five
    elements give ten possible ratios and a sheet shows five of them.

    A generation the config says nothing about falls back to a minimal layout,
    so an unknown one costs you the newer columns, not the tab.
    """
    from src.data_access.sem_edx.sem_edx_columns import DEFAULT_LAYOUT

    configured = load_config().get("sem_edx", {}).get("layout", {}) or {}
    layouts = {str(name): {str(k).strip().upper(): [str(q) for q in (v or [])]
                           for k, v in (entry or {}).items()}
               for name, entry in configured.items()}
    if not generation:
        return layouts
    return layouts.get(str(generation).strip()) or {
        key: list(value) for key, value in DEFAULT_LAYOUT.items()}


def sem_edx_image_roots() -> list[Path]:
    """Where the SEM result folders are, on this machine.

    Every entry names the *same* folder in the shared library; they differ only
    in how deep each person syncs it, and in the user profile, which is why
    they carry ``%USERPROFILE%``. Sorted deepest first, so the most specific
    match wins over a shallower one that happens to also contain the folder.
    """
    roots = load_config().get("sem_edx", {}).get("image_roots", []) or []
    expanded = [Path(os.path.expandvars(os.path.expanduser(str(raw))))
                for raw in roots if str(raw).strip()]
    return sorted(expanded, key=lambda p: len(str(p)), reverse=True)


# The two ends of the sampling range that are not a fixed number: whatever the
# first and last electrode on record happen to be on the day.
OLDEST = "oldest"
NEWEST = "newest"
_EDGES = (OLDEST, NEWEST)
_DEFAULT_RANGE = (13900, NEWEST)


def _edge(value, fallback):
    """One end of the range: a marking number, or one of the two edge words."""
    if isinstance(value, str) and value.strip().lower() in _EDGES:
        return value.strip().lower()
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def sampling_default_range() -> tuple:
    """Where the QC Sampling tab's range boxes start.

    ``(from, to)``, each a marking number or ``OLDEST``/``NEWEST``. The words
    are stored rather than resolved to numbers on purpose: "up to the newest
    electrode" has to stay true tomorrow, and a number written down today
    would not.
    """
    stored = load_config().get("sampling", {}).get("default_range", {})
    return (_edge(stored.get("from"), _DEFAULT_RANGE[0]),
            _edge(stored.get("to"), _DEFAULT_RANGE[1]))


def set_sampling_default_range(start, end) -> None:
    """Write the tab's default range back to config.json.

    The whole file is rewritten from what was loaded, so nothing else in it
    moves. Raises if the file cannot be written: this is somebody pressing Save,
    and a setting that silently did not save is worse than an error.
    """
    config = load_config()
    section = config.setdefault("sampling", {})
    section["default_range"] = {"from": _edge(start, _DEFAULT_RANGE[0]),
                                "to": _edge(end, _DEFAULT_RANGE[1])}
    path = base_directory() / "config" / "config.json"
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2)
        handle.write("\n")
