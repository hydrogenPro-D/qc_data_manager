"""The wall screen itself, and ``run_dashboard``.

Composition root, the same way ``MainWindow`` is for the app: it builds the
backend once, constructs one page per window, owns the rotation and the reread,
and is the only module here that talks to ``src.data_access``. The pages get
figures handed to them and nothing else.

Two things matter more here than in the app, because nobody is watching:

* **It must never stop.** A read that fails leaves the last good numbers on
  screen, says so in the header, and tries again at the next interval. It does
  not raise, and it does not clear the board.
* **It must never lie about being live.** A screen showing an hour-old number
  with no indication is worse than a blank one, so a stale read is labelled.
"""

from __future__ import annotations

import datetime as dt
import sys

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PyQt6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget

from src.data_access import build_backend

from ..data import dashboard_config as cfg
from ..data.dashboard_stats import WINDOWS, summarise_all
from . import palette as p
from .chrome import HeaderBar, RotationStrip
from .dashboard_page import DashboardPage
from .slide_deck import SlideDeck

WINDOW_TITLE = "HydrogenPro QC Live"
# After this long without a good read the header says so. Two intervals, so a
# single slow poll is not reported as an outage.
STALE_AFTER_MS = 2


class DashboardWindow(QMainWindow):
    """Full screen, three rotating windows onto the same electrode data."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)

        self.backend = build_backend()
        self._last_good: dt.datetime | None = None
        self._refresh_due = True

        backdrop = QWidget()
        backdrop.setObjectName("backdrop")
        backdrop.setStyleSheet(p.backdrop_style())

        self.header = HeaderBar()
        self.strip = RotationStrip()
        self.deck = SlideDeck(duration_ms=cfg.transition_ms())
        self._pages = [DashboardPage() for _ in WINDOWS]
        for page in self._pages:
            self.deck.add_page(page)
        self.deck.settled.connect(self._on_settled)

        layout = QVBoxLayout(backdrop)
        layout.setContentsMargins(18, 14, 18, 8)
        layout.setSpacing(14)
        layout.addWidget(self.header, 0)
        layout.addWidget(self.deck, 1)
        layout.addWidget(self.strip, 0)
        self.setCentralWidget(backdrop)

        self._sync_chrome()

        # Rotation and reread are separate clocks. The reread is far slower and
        # is deferred until the deck is still, see _on_settled.
        self._rotation = QTimer(self)
        self._rotation.setInterval(cfg.rotation_seconds() * 1000)
        self._rotation.timeout.connect(self.deck.advance)

        self._reread = QTimer(self)
        self._reread.setInterval(cfg.refresh_interval_ms())
        self._reread.timeout.connect(self._mark_due)

        # Escape to leave, since a full screen board with no title bar is
        # otherwise awkward to get out of on the machine driving the TV.
        for key in ("Esc", "Ctrl+Q"):
            QShortcut(QKeySequence(key), self, activated=self.close)
        # Right and left to drive it by hand, for anyone demonstrating it.
        QShortcut(QKeySequence("Right"), self, activated=self._step)

        # The first read happens once the window is up, so an unreachable
        # database shows an empty board with a warning rather than nothing.
        QTimer.singleShot(0, self._refresh)
        self._rotation.start()
        self._reread.start()
        self.strip.restart(self._rotation.interval())

    # ------------------------------------------------------------------
    def _step(self) -> None:
        """Advance by hand, and restart the clock so it does not jump twice."""
        self.deck.advance()
        self._rotation.start()
        self.strip.restart(self._rotation.interval())

    def _sync_chrome(self) -> None:
        """Point the header and the strip at whichever page is showing."""
        index = self.deck.current_index()
        self.header.set_title(WINDOWS[index][0] if index < len(WINDOWS) else "")
        self.strip.set_pages(self.deck.count(), index)

    def _on_settled(self, _index: int) -> None:
        """The deck has stopped moving: retitle, restart the bar, then read.

        The read is deliberately here rather than on its own timer firing
        whenever it likes. Reading blocks this thread for about a second on
        twelve thousand rows, and a second of frozen animation is exactly what
        makes a wall screen look broken. Between slides there is nothing moving
        to spoil.
        """
        self._sync_chrome()
        self.strip.restart(self._rotation.interval())
        if self._refresh_due:
            self._refresh()

    def _mark_due(self) -> None:
        """Ask for a reread at the next quiet moment."""
        self._refresh_due = True

    # ------------------------------------------------------------------
    def _refresh(self) -> None:
        """Re-read and refill all three pages. Never raises.

        Every window is refilled, not just the one on screen: the other two are
        seconds away from being shown, and refilling on arrival would put the
        read back inside the animation.
        """
        self._refresh_due = False
        try:
            # Once, on the first read. The vats are a join against the
            # process log, which is far bigger than the electrode list and
            # does not change once an electrode has been plated.
            if self.backend.vats_pending():
                self.backend.load_vats()
            frame = self.backend.get_metadata()
        except Exception as e:
            # Kept on screen deliberately. The numbers were true when they were
            # read, and the header now says how long ago that was.
            print(f"Dashboard refresh failed: {e}")
            self._mark_stale()
            return

        for page, stats in zip(self._pages,
                               summarise_all(frame, cfg.vat_rows())):
            page.show_stats(stats)
        self._last_good = dt.datetime.now()
        self.header.set_note("")

    def _mark_stale(self) -> None:
        """Say in the header how old the figures on screen are."""
        if self._last_good is None:
            self.header.set_note("No connection to the database")
            return
        minutes = int((dt.datetime.now() - self._last_good).total_seconds() // 60)
        threshold = (cfg.refresh_interval_ms() * STALE_AFTER_MS) / 60000
        if minutes >= max(1, threshold):
            self.header.set_note(f"Figures are {minutes} min old")


def run_dashboard() -> None:
    """Open the board full screen on the primary display and rotate forever."""
    app = QApplication(sys.argv)
    app.setApplicationName(WINDOW_TITLE)

    window = DashboardWindow()
    screen = QGuiApplication.primaryScreen()
    if screen is not None:
        window.setGeometry(screen.availableGeometry())
    window.showFullScreen()
    sys.exit(app.exec())
