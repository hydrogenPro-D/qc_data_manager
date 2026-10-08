"""Entry point.

    venv\\Scripts\\python.exe main.py

The order here is the whole point. The icon is claimed and set before the GUI is
imported, because Windows binds the taskbar button to whatever identity the
process holds when its first window appears. The splash goes up before that
import too, since the import alone is about 1.4 seconds of blank screen. And the
import runs on a worker thread, because a QTimer cannot fire while the main
thread is inside an import, so on the main thread the splash would sit frozen
instead of pulsing.
"""

import sys
import threading


def claim_taskbar_identity() -> None:
    """Take the taskbar button for this app rather than for python.exe.

    Running from source the button otherwise belongs to the interpreter and
    shows Python's icon whatever setWindowIcon says. Must run before the
    QApplication, since Windows binds it when the first window appears.
    Cosmetic, so a failure is swallowed.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "HydrogenPro.QCDataManager")
    except Exception:
        pass


def force_light_theme() -> None:
    """Render the same on every machine, whatever Windows is set to.

    Qt follows the system colour scheme, so a machine with dark mode on hands
    every widget a dark palette. The app's own stylesheets name their colours
    outright and ignore it, which is what makes the result unreadable rather
    than merely dark: the tables set their cell backgrounds to white in code
    while the cell text colour comes from the palette, so it came out white on
    white. The window chrome, the dialogs and the calendar popup went dark
    around a light application.

    Pinning the scheme rather than switching to the Fusion style: this keeps
    the native Windows look everybody already has and changes nothing on a
    machine that was in light mode anyway.

    Must run before the first widget exists. Needs Qt 6.8, and is skipped
    rather than guessed at on anything older.
    """
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QGuiApplication

    hints = QGuiApplication.styleHints()
    if hasattr(hints, "setColorScheme"):
        hints.setColorScheme(Qt.ColorScheme.Light)


def _import_gui(result: dict, done: threading.Event) -> None:
    """Pull in the heavy GUI package off the main thread."""
    try:
        from src.gui.gui import run_app
        result["run_app"] = run_app
    except BaseException as exc:        # re-raised on the main thread below
        result["error"] = exc
    finally:
        done.set()


def main() -> None:
    claim_taskbar_identity()

    from PyQt6.QtGui import QIcon
    from PyQt6.QtWidgets import QApplication
    from src.assets import ICON_ICO, asset_path
    from src.splash import show_splash

    application = QApplication.instance() or QApplication(sys.argv)
    # Before the splash, which is the first widget there is.
    force_light_theme()
    icon = asset_path(ICON_ICO)
    if icon.exists():
        application.setWindowIcon(QIcon(str(icon)))
    splash = show_splash()

    result: dict = {}
    done = threading.Event()
    threading.Thread(target=_import_gui, args=(result, done),
                     daemon=True).start()

    # Keep painting while the import runs, so the splash actually animates.
    while not done.wait(0.01):
        application.processEvents()

    if "error" in result:
        splash.finish()
        raise result["error"]

    result["run_app"](app=application, splash=splash)


if __name__ == "__main__":
    main()
