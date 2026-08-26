"""Build the QC Data Manager executable with PyInstaller (one-dir bundle).

Everything (exe name, spec path, dist folder) derives from EXECUTABLE_NAME.
Runtime config is copied into the bundle afterwards; the DB connection file is
git-ignored and injected here at build time so credentials stay out of git.

WHICH DATABASE IS ASKED, NOT INHERITED. The bundle gets its own connection
file: a copy of the repo one with a single field changed, the database. The
driver, the server and the login are the same wherever it points, so they are
carried over untouched. The repo copy is never modified, so building for one
database cannot change what you are testing against.

A build for anything but the test database has to be confirmed by name, and the
last thing printed is which database it was, because a bundle looks identical
either way once it is handed over.
"""

from pathlib import Path
import json
import shutil
import subprocess
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = Path(__file__).resolve().parent
ENTRY_SCRIPT = PROJECT_ROOT / "main.py"
DIST_PATH = PROJECT_ROOT / "dist"
WORK_PATH = PROJECT_ROOT / "build"
EXECUTABLE_NAME = "QC Data Manager"
HIDDEN_IMPORTS = [
    "PyQt6.QtCore",
    "PyQt6.QtGui",
    "PyQt6.QtWidgets",
]
SPEC_FILE = TOOLS_DIR / f"{EXECUTABLE_NAME}.spec"
CONFIG_FOLDER = PROJECT_ROOT / "config"
DRIVER_FOLDER = PROJECT_ROOT / "driver"
EXECUTABLE_FOLDER = DIST_PATH / EXECUTABLE_NAME
CONNECTION_FILE = CONFIG_FOLDER / "db_connection.json"

# The databases a bundle may target, the same pair the migrations guard on.
# Test first, because that is what plain Enter picks.
KNOWN_DATABASES = ("dataloggingTest", "dataloggingDev")


def ask_database(suggested: str = "") -> str:
    """Which database the bundle will talk to, asked rather than inherited.

    Test is offered first and is what plain Enter picks: a bundle looks the
    same either way once it is handed over, so the harmless one should be the
    easy one to get.
    """
    default = suggested if suggested in KNOWN_DATABASES else KNOWN_DATABASES[0]

    print("\nWhich database should this build talk to?")
    for number, name in enumerate(KNOWN_DATABASES, start=1):
        marker = "  (default)" if name == default else ""
        print(f"   {number}) {name}{marker}")

    answer = input(f"Database [{default}]: ").strip()
    if not answer:
        return default
    if answer.isdigit() and 1 <= int(answer) <= len(KNOWN_DATABASES):
        return KNOWN_DATABASES[int(answer) - 1]
    for name in KNOWN_DATABASES:
        if answer.lower() == name.lower():
            return name
    raise SystemExit(f"{answer!r} is not one of {', '.join(KNOWN_DATABASES)}. "
                     f"Nothing was built.")


def confirm_database(database: str) -> None:
    """Ask again before building anything but a test bundle."""
    if database == KNOWN_DATABASES[0]:
        return
    typed = input(f"\n{database} is not the test database, and whoever runs "
                  f"this bundle will be writing to it.\nType the database name "
                  f"to continue: ").strip()
    if typed != database:
        raise SystemExit("Not confirmed, nothing was built.")


def _read_connection_file() -> dict:
    """The repo connection file, checked for everything a bundle needs."""
    if not CONNECTION_FILE.exists():
        raise SystemExit(
            f"No connection settings at {CONNECTION_FILE}. The bundle would "
            f"have nothing to connect to.")
    with open(CONNECTION_FILE, "r", encoding="utf-8") as handle:
        settings = json.load(handle)

    missing = [key for key in ("DB_DRIVER", "DB_SERVER", "DB_USERNAME",
                               "DB_PASSWORD")
               if not str(settings.get(key, "")).strip()]
    if missing:
        raise SystemExit(f"{CONNECTION_FILE.name} is missing: "
                         f"{', '.join(missing)}. Nothing was built.")
    return settings


def _write_bundle_connection(destination: Path, database: str) -> str:
    """The connection file the bundle ships with. Returns the username in it.

    A copy of the repo file with ONE field changed, the database. The driver,
    the server and the login are the same wherever the bundle points, so they
    are carried over untouched rather than asked for again.
    """
    settings = _read_connection_file()
    settings["DB_DATABASE"] = database

    destination.parent.mkdir(parents=True, exist_ok=True)
    with open(destination, "w", encoding="utf-8") as handle:
        json.dump(settings, handle, indent=2)
        handle.write("\n")
    print(f"Wrote: {destination} ({database} as {settings['DB_USERNAME']})")
    return str(settings["DB_USERNAME"])


def _run_pyinstaller(command: list[str]) -> None:
    """Run PyInstaller, surfacing its output and failing loudly."""
    print("Running:", " ".join(command))
    result = subprocess.run(command, cwd=PROJECT_ROOT, capture_output=True, text=True)
    print(result.stdout)
    if result.stderr:
        print("STDERR:", result.stderr)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, command, output=result.stdout, stderr=result.stderr)


def _copy_file(src: Path, dst: Path) -> None:
    """Copy one file into the bundle, creating parents. Missing = skip."""
    if not src.exists():
        print(f"Source file does not exist (skipping): {src}")
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    print(f"Copied: {src} -> {dst}")


def _build_from_existing_spec() -> None:
    print(f"Using existing spec (no regeneration): {SPEC_FILE}")
    _run_pyinstaller([sys.executable, "-m", "PyInstaller", "--noconfirm", str(SPEC_FILE)])


def _bootstrap_spec() -> None:
    print(f"Spec file not found. Bootstrapping a new one at: {SPEC_FILE}")
    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--onedir", "--windowed",
        "--name", EXECUTABLE_NAME,
        "--specpath", str(TOOLS_DIR),
        "--distpath", str(DIST_PATH),
        "--workpath", str(WORK_PATH),
    ]
    for hidden_import in HIDDEN_IMPORTS:
        command.extend(["--hidden-import", hidden_import])
    command.append(str(ENTRY_SCRIPT))
    _run_pyinstaller(command)


def build_executable() -> None:
    """Build the bundle, then copy config (and the ODBC installer, if present).

    Everything that needs asking is asked first, before PyInstaller runs, so a
    wrong answer costs nothing.

    Raises:
        FileNotFoundError: If main.py or config/config.json is missing.
    """
    if not ENTRY_SCRIPT.exists():
        raise FileNotFoundError(f"Could not find entry script: {ENTRY_SCRIPT}")
    if not (CONFIG_FOLDER / "config.json").exists():
        raise FileNotFoundError(
            f"config/config.json is missing — the app cannot run without it: "
            f"{CONFIG_FOLDER / 'config.json'}"
        )

    suggested = str(_read_connection_file().get("DB_DATABASE", ""))

    database = ask_database(suggested)
    confirm_database(database)

    if SPEC_FILE.exists():
        _build_from_existing_spec()
    else:
        _bootstrap_spec()

    config_dest = EXECUTABLE_FOLDER / "config"
    _copy_file(CONFIG_FOLDER / "config.json", config_dest / "config.json")
    # Written rather than copied: the repo copy follows whatever is being
    # tested, and a bundle must say which database it was built for.
    username = _write_bundle_connection(config_dest / "db_connection.json",
                                        database)
    # The app runs this at startup if the ODBC driver isn't installed.
    _copy_file(DRIVER_FOLDER / "msodbcsql.msi", EXECUTABLE_FOLDER / "driver" / "msodbcsql.msi")

    print(f"\nBuild finished. Executable output is under: {DIST_PATH}")
    print(f"Spec file is at: {SPEC_FILE}")
    _announce(database, username)


def _announce(database: str, username: str) -> None:
    """Say which database this bundle talks to, last and unmissably.

    Two builds look identical in the folder, so the one thing that tells them
    apart has to be the last thing on screen.
    """
    line = "=" * 62
    print(f"\n{line}")
    print(f"  THIS BUILD TALKS TO: {database}")
    print(f"  signing in as:       {username}")
    if database != KNOWN_DATABASES[0]:
        print(f"  This is not the test database. Do not hand it out as one.")
    print(f"  The password is inside {EXECUTABLE_FOLDER.name}\\config, in clear")
    print(f"  text. Whoever gets the folder gets the login.")
    print(line)


if __name__ == "__main__":
    build_executable()
