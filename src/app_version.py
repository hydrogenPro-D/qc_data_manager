"""Which build this is.

One number, bumped by hand at release time, written to ``config/version.json``
beside the exe. The running app reads it for the window title, and anything that
needs to know which build is installed can read the same file.

Deliberately not semver: it is a build counter for an internal tool. The bump is
minor only, 1.0, 1.1 ... 1.9, 1.10, which only orders correctly if the parts are
compared as integers. Anything comparing versions must do
``tuple(int(part) for part in version.split("."))``, never the strings.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from src.data_access.app_config import base_directory

VERSION_FILENAME = "version.json"
# What a build running from source calls itself. Never written to a file.
UNKNOWN = "dev"


def version_file_path() -> Path:
    """config/version.json, beside the exe when frozen, else in the repo.

    Through the same resolver config.json uses, so a bundle cannot end up
    reading its settings from one folder and its version from another.
    """
    return base_directory() / "config" / VERSION_FILENAME


def read_version_info() -> dict:
    """``{"version": str, "released_at": str}``.

    A missing or unreadable file is not an error. Running from source there is
    no release to name, and a half written file must not stop the app starting.
    Every path returns the same shape, so no caller has to branch on it.
    """
    missing = {"version": UNKNOWN, "released_at": ""}
    path = version_file_path()
    if not path.exists():
        return missing
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return missing
    return {"version": str(data.get("version", UNKNOWN)),
            "released_at": str(data.get("released_at", ""))}


def current_version() -> str:
    """Just the number, or "dev"."""
    return read_version_info()["version"]


def next_version(previous: str | None) -> str:
    """Bump the minor part: 1.0 to 1.1, 1.9 to 1.10. Nothing yet starts at 1.0."""
    if not previous:
        return "1.0"
    try:
        major, minor = str(previous).strip().split(".", 1)
        return f"{int(major)}.{int(minor) + 1}"
    except (ValueError, AttributeError):
        return "1.0"


def write_version_file(path: Path, version: str) -> dict:
    """Write the version and the moment of release. Returns what was written."""
    info = {"version": version,
            "released_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    return info
