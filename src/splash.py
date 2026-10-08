"""Startup splash: the app icon, gently pulsing, while the GUI loads.

Deliberately light. It imports only Qt and src.assets, never the GUI package,
because its whole job is to be on screen BEFORE that package is imported, and
that import alone is about 1.4 seconds. It then stays up, pulsing, until the
Electrodes table has its rows, which the window tells it.

Living under src/gui/ would defeat it: importing anything from that package
pulls in the heavy module and the splash would appear milliseconds before the
thing it was meant to cover.
"""
from __future__ import annotations

import sys

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QCursor, QGuiApplication, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from src.assets import ICON_PNG, asset_path

ICON_SIZE = 190
PULSE_TICK_MS = 33          # about 30 fps, so the fade looks smooth
PULSE_PERIOD = 48           # ticks per dim, bright, dim cycle, about 1.6 s
MIN_OPACITY = 0.35

# A floor on the closing fade, so a splash dismissed early does not simply cut
# out. It costs nothing: by then the event loop is running and the window is
# already usable underneath.
FADE_MIN_TICKS = 8


class StartupSplash(QWidget):
    """Frameless, transparent window showing the icon until the app is ready."""

    def __init__(self) -> None:
        # SplashScreen rather than Tool: Windows gives tool windows a border
        # and a shadow, which reads as a grey card around the artwork.
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.SplashScreen)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        # Children must not paint a panel of their own.
        self.setStyleSheet("background: transparent; border: none;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(0)

        self._source = QPixmap(str(asset_path(ICON_PNG)))
        if not self._source.isNull():
            self._source = self._source.scaled(
                ICON_SIZE, ICON_SIZE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)

        self._icon = QLabel()
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon.setFixedSize(ICON_SIZE, ICON_SIZE)
        layout.addWidget(self._icon)

        self.adjustSize()
        self._centre()

        self._frame = 0
        self._closing = False
        self._closing_from = 1.0
        self._closing_ticks = 0
        self._fade_ticks = FADE_MIN_TICKS
        self._apply(MIN_OPACITY)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(PULSE_TICK_MS)

    # -- painting ---------------------------------------------------------
    def _apply(self, opacity: float) -> None:
        """Redraw the icon at this opacity.

        The pixmap is faded, not the window: setWindowOpacity on a translucent
        frameless window composites badly on Windows, greying the artwork and
        showing a panel edge.
        """
        opacity = max(0.0, min(1.0, opacity))
        if self._source.isNull():
            return
        faded = QPixmap(self._source.size())
        faded.fill(QColor(0, 0, 0, 0))
        painter = QPainter(faded)
        painter.setOpacity(opacity)
        painter.drawPixmap(0, 0, self._source)
        painter.end()
        self._icon.setPixmap(faded)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._strip_window_chrome()

    def _strip_window_chrome(self) -> None:
        """Qt paints nothing at the edges, Windows does.

        It rounds frameless windows and gives them a border and a shadow. Both
        off. Has to run on a shown window, since there is no handle before
        that, and is a no-op elsewhere.
        """
        if sys.platform != "win32":
            return
        try:
            import ctypes
            handle = int(self.winId())
            dwm = ctypes.windll.dwmapi
            NCRENDERING_POLICY = 2          # DWMNCRP_DISABLED kills the shadow
            CORNER_PREFERENCE = 33          # DWMWCP_DONOTROUND squares it off
            for attribute, value in ((NCRENDERING_POLICY, 1),
                                     (CORNER_PREFERENCE, 1)):
                dwm.DwmSetWindowAttribute(
                    handle, attribute,
                    ctypes.byref(ctypes.c_int(value)),
                    ctypes.sizeof(ctypes.c_int))
        except Exception:
            pass        # cosmetic only

    def _centre(self) -> None:
        """Centre on the screen holding the cursor.

        The main window is pinned to this screen afterwards, so the two cannot
        land on different monitors.
        """
        screen = (QGuiApplication.screenAt(QCursor.pos())
                  or QApplication.primaryScreen())
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(area.center().x() - self.width() // 2,
                  area.center().y() - self.height() // 2)

    # -- animation --------------------------------------------------------
    def _tick(self) -> None:
        if self._closing:
            self._fade_out()
            return
        self._frame = (self._frame + 1) % PULSE_PERIOD
        self._apply(self._pulse_opacity(self._frame))

    def _pulse_opacity(self, frame: int) -> float:
        """A triangle wave, which reads as breathing. A square one blinks."""
        half = PULSE_PERIOD / 2
        strength = 1.0 - abs(frame - half) / half    # 0 at the ends, 1 mid
        return MIN_OPACITY + (1.0 - MIN_OPACITY) * strength

    def _fade_out(self) -> None:
        """Ride the pulse down to nothing rather than vanishing mid-breath."""
        self._closing_ticks += 1
        progress = self._closing_ticks / self._fade_ticks
        if progress >= 1.0:
            self._timer.stop()
            self.close()
            self.deleteLater()
            return
        self._apply(self._closing_from * (1.0 - progress))

    # -- lifecycle --------------------------------------------------------
    def finish(self, window=None) -> None:
        """Begin closing, fading out rather than cutting off.

        A fast startup would otherwise make the splash blink.
        """
        if window is not None:
            window.raise_()
            window.activateWindow()
        if self._closing:
            return
        self._closing = True
        self._closing_from = max(MIN_OPACITY, self._pulse_opacity(self._frame))
        self._closing_ticks = 0
        # Finish the breath: however far through the cycle we are, take the
        # rest of it to fade away, with a floor so it is never an abrupt cut.
        remaining = PULSE_PERIOD - (self._frame % PULSE_PERIOD)
        self._fade_ticks = max(FADE_MIN_TICKS, remaining)

    def dismiss(self) -> None:
        """Off the screen at once, no fade.

        For the failure path only. The splash stays on top of everything, so a
        startup error dialog would open behind it.
        """
        self._timer.stop()
        self._closing = True
        self.close()
        self.deleteLater()


def show_splash() -> StartupSplash:
    """Put the splash on screen immediately and return it."""
    splash = StartupSplash()
    splash.show()
    # Paint it now: the caller is about to block for over a second.
    QApplication.processEvents()
    return splash
