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
