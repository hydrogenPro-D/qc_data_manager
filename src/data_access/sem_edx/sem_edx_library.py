"""Finding the shared SEM images library on this machine.

The captures live in a SharePoint library that everybody syncs with OneDrive,
and OneDrive names the local folder after WHAT WAS SYNCED, not after the site.
Sync the whole document library and you get

    ...\\HydrogenPro ASA\\Advanced Surface Plating ApS - Documents\\General\\SEM images\\Production SEM images

Sync the General folder itself and you get, with no General level at all,

    ...\\HydrogenPro ASA\\Advanced Surface Plating ApS - General\\SEM images\\Production SEM images

Everything below ``Production SEM images`` is identical for everyone, which is
why only that tail is stored: ``B productionline\\Gen4.1\\B14293``. So the whole
problem is finding where that folder sits on THIS machine.

It used to be a list of guessed spellings in config.json, which is how a
coworker ended up with none of their images opening and one absolute path in
the database. Guessing is now a search, and the search is anchored so it stays
cheap: find the library folder by its prefix, three levels under the user
profile, then find the marker inside it. Breadth first, stopping at the first
match, about 4ms.

The result is remembered for the session. It cannot be written to config.json,
which the build copies into the bundle: a path found on one machine would then
be shipped to everyone.
"""

from __future__ import annotations

import os
from collections import deque
from pathlib import Path

from src.data_access import app_config

# The synced library, whatever suffix OneDrive gave it.
LIBRARY_PREFIX = "advanced surface plating aps"
# The folder every stored tail hangs off.
MARKER = "production sem images"
# What tells the real one from a copy somebody left in a Downloads folder.
PROOF = "B productionline"

# How deep to look for each. The library is two levels under the profile on both
# known machines, and the marker three levels inside it; the allowances are one
# more than that, not open ended.
LIBRARY_DEPTH = 3
MARKER_DEPTH = 4

# Found once per session. None means "not looked yet", so a failed search is
# not repeated on every capture.
_root: Path | None = None
_searched = False


def _subdirectories(folder: Path) -> list[Path]:
    """Directories inside one folder, ignoring the ones we may not read."""
    try:
        with os.scandir(folder) as entries:
            return [Path(e.path) for e in entries if e.is_dir(follow_symlinks=False)]
    except (PermissionError, OSError):
        return []


def _breadth_first(base: Path, matches, max_depth: int) -> Path | None:
    """The shallowest directory under ``base`` that matches, or None.

    Breadth first and returning immediately, which is the whole performance
    story: depth first kept walking the five hundred electrode folders after it
    already had the answer, and took eighty times longer.
    """
    queue = deque([(base, 0)])
    while queue:
        folder, depth = queue.popleft()
        for child in _subdirectories(folder):
            if matches(child):
                return child
            if depth < max_depth:
                queue.append((child, depth + 1))
    return None


def discover_root(home: Path | None = None) -> Path | None:
    """Search this machine for the SEM images library. None if it is not here."""
    home = home or Path(os.path.expandvars("%USERPROFILE%"))
    if not home.is_dir():
        return None

    library = _breadth_first(
        home, lambda p: p.name.lower().startswith(LIBRARY_PREFIX), LIBRARY_DEPTH)
    if library is None:
        return None

    return _breadth_first(
        library,
        lambda p: p.name.lower() == MARKER and (p / PROOF).is_dir(),
        MARKER_DEPTH)


def image_root(ask=None) -> Path | None:
    """Where the SEM images library is, or None if it cannot be found.

    Looked for in three places, in order:

    1. ``sem_edx.image_roots`` in config.json, for a machine that needs telling.
    2. The search above.
    3. ``ask``, an optional ``() -> str`` the GUI passes to open a folder
       picker. Kept as a callable so this layer stays Qt-free, the same way the
       driver install prompt does.

    The answer is kept for the session. Asking happens at most once, since an
    operator who cancels should not be pestered on every capture.
    """
    global _root, _searched

    if _root is not None:
        return _root

    if not _searched:
        _searched = True
        for configured in app_config.sem_edx_image_roots():
            if configured.is_dir():
                _root = configured
                return _root
        _root = discover_root()
        if _root is not None:
            return _root

    if _root is None and callable(ask):
        chosen = str(ask() or "").strip()
        if chosen:
            path = Path(chosen)
            # The picker may land on the library, or on any folder above the
            # marker, so look inside it too rather than refusing a near miss.
            if path.name.lower() != MARKER:
                path = _breadth_first(
                    path, lambda p: p.name.lower() == MARKER, MARKER_DEPTH) or path
            if path.is_dir():
                _root = path
    return _root


def set_root(folder) -> None:
    """Use this folder for the rest of the session. For tests, and the picker."""
    global _root, _searched
    _root = Path(folder) if folder else None
    _searched = True


def forget_root() -> None:
    """Search again next time. For tests."""
    global _root, _searched
    _root, _searched = None, False
