"""Where the bundled artwork lives, frozen or from source.

Outside the GUI package on purpose. ``main.py`` sets the window icon before it
imports the GUI, and importing anything under ``src.gui`` to find a file would
pull the whole package in and undo that.
"""

from __future__ import annotations

import sys
from pathlib import Path

ICON_ICO = "app_icon.ico"
ICON_PNG = "app_icon.png"


def _search_roots() -> list[Path]:
    """Every folder an assets directory could sit under, best guess first.

    PyInstaller 6 puts bundled data in _internal beside the exe and points
    sys._MEIPASS at it, earlier versions put it beside the exe itself, and the
    exe folder is also where a hand-dropped replacement would land. All three
    are tried so the lookup survives a PyInstaller upgrade.
    """
    if not getattr(sys, "frozen", False):
        return [Path(__file__).resolve().parents[1]]
    beside = Path(sys.executable).parent
    roots = [Path(getattr(sys, "_MEIPASS", beside)), beside, beside / "_internal"]
    return list(dict.fromkeys(roots))


def asset_path(name: str) -> Path:
    """One asset, wherever this is running from.

    The first existing candidate wins; with none, the first root is returned so
    the caller gets a path to report rather than an exception.
    """
    candidates = [root / "assets" / name for root in _search_roots()]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]
