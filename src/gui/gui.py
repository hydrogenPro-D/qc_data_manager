"""MainWindow — the composition root, and ``run_app``.

The only place that knows how the pieces wire together: it builds the data
backend, constructs each tab injecting the callables it needs, hosts the tab
widget, and owns the auto-refresh timer. Tabs never construct each other and
never touch the backend directly — they only ever see the callables passed in
here, which is what keeps them testable with fakes.
"""

import sys

from PyQt6.QtWidgets import (
    QApplication,
    QLabel,
    QMainWindow,
    QWidget,
    QVBoxLayout,
)
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
import qtawesome as qta

from src.app_version import UNKNOWN, current_version
from src.data_access import app_config, build_backend
from .electrodes_gui import ElectrodesWidget
from .qc_board_gui import QcBoardWidget
from .sampling_gui import QcSamplingWidget
from .sem_edx_gui import SemEdxWidget
from .poll_worker import BackgroundJob, DeferredLoad, PollWorker
from .widgets.no_wheel_tabs import NoWheelTabWidget
from .styles.tab_styles import MAIN_TAB_STYLE
from .styles.tooltip_styles import TOOLTIP_STYLE

APP_TITLE = "QC Data Manager"
# Backstop only. The splash normally goes when the Electrodes read lands, a
# couple of seconds in; this is so a read that never lands cannot strand it.
SPLASH_HOLD_MS = 15000
TEST_DB_WARNING = "Connected to test database"
# Loud on purpose. Someone who does not notice this will believe a morning of
# decisions went to the real board.
TEST_DB_STYLE = ("color: #C81E1E; font-family: 'Segoe UI', Arial, sans-serif; "
                 "font-size: 13px; font-weight: 700; padding: 0 14px 0 8px;")
# Quiet: it is on screen for about a second and is not the point.
STARTING_STYLE = ("color: #94A3B8; font-family: 'Segoe UI', Arial, sans-serif; "
                  "font-size: 15px;")


def _seeded_on_first_use(build):
    """Wrap an update callback so its baseline query waits for the first poll.

    The baseline is "what we have already seen", so taking it at the first poll
    rather than at construction is if anything the more truthful moment: by
    then the deferred reads have landed and the tabs really have seen it.
    """
    holder = {}

    def call():
        if "callback" not in holder:
            holder["callback"] = build()
        return holder["callback"]()

    return call


class MainWindow(QMainWindow):
    """Single maximized window with a top tab bar; one tab per entity."""

    # Asks the worker thread to poll one entity. A signal rather than a direct
    # call, so the work actually happens on that thread.
    poll_requested = pyqtSignal(str)
    # Work the window does not need in order to open, run once it is up.
    deferred_requested = pyqtSignal(str)
    # One piece of work the operator is waiting on, off the UI thread so the
    # window keeps drawing while it runs.
    background_requested = pyqtSignal(object)

    def __init__(self) -> None:
        super().__init__()
        # Named rather than numbered when there is no release to name. Somebody
        # sending a screenshot of a bug is then telling you, without being
        # asked, whether they are on a release or running from source, and that
        # changes where to look first.
        version = current_version()
        title = APP_TITLE + ("  (dev build)" if version == UNKNOWN
                             else f"  v{version}")
        self.setWindowTitle(title)
        # The frame and nothing else. Everything expensive is in start(), so
        # the window is up a moment after the double click rather than a few
        # seconds later, which read to operators as nothing having happened.
        starting = QLabel("Starting")
        starting.setAlignment(Qt.AlignmentFlag.AlignCenter)
        starting.setStyleSheet(STARTING_STYLE)
        self.setCentralWidget(starting)
        self._splash = None

    # ------------------------------------------------------------------
    # Splash
    # ------------------------------------------------------------------
    def hold_splash(self, splash) -> None:
        """Keep the startup splash up until the first table has rows.

        The frame is on screen seconds before the Electrodes read lands, and an
        empty window reads to an operator as the app having failed to start.
        """
        self._splash = splash
        # A read that never lands must not leave the icon sitting there.
        QTimer.singleShot(SPLASH_HOLD_MS, self._release_splash)

    def _release_splash(self) -> None:
        """Fade the splash out. Safe to call more than once."""
        splash, self._splash = self._splash, None
        if splash is not None:
            splash.finish(self)

    def start(self) -> None:
        """Build the backend and the tabs, with the window already showing.

        Split out of __init__ purely so the frame can be painted first. This
        is still the blocking, fail-fast startup step it always was: run_app
        calls it inside the same try, so an unreachable database ends at the
        same titled dialog, only now with the window visible behind it.
        """
        # The single data dependency.
        self.backend = build_backend(
            db_install_confirm=self._confirm_driver_install,
            ask_image_root=self._ask_image_root)

        # Replaces the "Starting" label, which Qt deletes with the old central
        # widget.
        central_widget = QWidget()
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(0)

        self.tabs = NoWheelTabWidget()
        self.tabs.setStyleSheet(MAIN_TAB_STYLE)
        self._warn_if_not_production()

        # Built once and shared: each one seeds its watermark baseline when it
        # is created, so building a second copy per entity would cost a query
        # at startup and answer the same question.
        #
        # Built lazily, though: seeding is a query per entity, and the
        # Electrodes one alone was about 700ms of the window's startup. Nothing
        # reads a baseline until the first poll, five seconds later and on the
        # worker thread, so that is where the cost belongs.
        self._update_callbacks = {
            "electrodes": _seeded_on_first_use(self.backend.get_metadata_update_callback),
            "qc_board": _seeded_on_first_use(self.backend.get_qc_board_update_callback),
            "sem_edx": _seeded_on_first_use(self.backend.get_sem_edx_update_callback),
        }

        # --- Electrodes tab: wired entirely through injected callables ---
        # Reads dbo.MetaData (one row per produced electrode). Read-only for
        # now: the save callback lands here once write access exists.
        self.electrodes_widget = ElectrodesWidget(
            on_check_for_updates=self._update_callbacks["electrodes"],
            on_status_override=self._on_status_override,
        )
        # Read after the window is up, not before it. The 12k rows and their
        # status propagation are about 700ms, and nothing about opening the
        # window needs them. The tab shows a placeholder until they land.
        self.electrodes_widget.show_loading()
        self.tabs.addTab(self.electrodes_widget, "Electrodes")

        # --- QC tab: the board, sharing the backend's store so a decision here
        # immediately overrides the Electrodes tab's status.
        self.qc_board_widget = QcBoardWidget(
            board_store=self.backend.get_qc_board_store(),
            on_preview_finalise=self.backend.preview_finalise,
            on_finalise=self.backend.finalise_qc_decisions,
            on_undo=self.backend.undo_last_action,
            on_check_for_updates=self._update_callbacks["qc_board"],
        )
        # Its own board is already loaded (the backend read it while
        # connecting); the electrode list its add dialog offers arrives with
        # the deferred read, the same frame the Electrodes tab gets.
        self.qc_board_widget.board_changed.connect(self._on_board_changed)
        self.tabs.addTab(self.qc_board_widget, "QC")

        # --- SEM EDX tab: seeded from the Gen 4.1 workbook, then grown by
        # importing result folders. Independent of the board, SEM is done on a
        # small subset of electrodes.
        self.sem_edx_widget = SemEdxWidget(
            # Which quantities each generation's sheets hold, so every table
            # builds its columns from the workbook rather than from a list
            # written down in the GUI. Oldest generation first.
            layouts={generation: self.backend.get_sem_edx_layout(generation)
                     for generation in self.backend.get_sem_edx_generations()},
            on_get_measurements=self.backend.get_sem_edx_measurements,
            on_import_folder=self.backend.import_sem_edx_folder,
            # For a folder the parser makes nothing of: the numbers get typed
            # in, and the fields to type into come from the same layout the
            # table builds its columns from.
            on_add_sample=self.backend.add_sem_edx_sample,
            on_get_elements=self.backend.sem_edx_elements,
            on_update_sample=self.backend.update_sem_edx_sample,
            on_update_measurements=self.backend.update_sem_edx_measurements,
            on_delete_sample=self.backend.delete_sem_edx_sample,
            on_list_images=self.backend.list_sem_edx_images,
            # What is still sitting in the images library unimported, so the
            # operator picks from a list rather than navigating to it.
            on_list_import_candidates=self.backend.sem_edx_import_candidates,
            # Saving a sample is a write and a re-read, about a second. Handed
            # over whole so it can run off the UI thread.
            on_save_sample=self._save_sem_edx_sample,
            on_images_available=self.backend.sem_images_available,
            on_check_for_updates=self._update_callbacks["sem_edx"],
        )
        # Same again: the four sheets are about 560ms and the tab is not even
        # the visible one at launch.
        self.sem_edx_widget.show_loading()
        self.sem_edx_widget.data_changed.connect(self._on_sem_edx_changed)
        self.tabs.addTab(self.sem_edx_widget, "SEM EDX")

        # --- QC Sampling tab: how long the good and bad stretches run, and
        # what that says about how often to test. It reads nothing of its own:
        # the electrode frame arrives through _apply_electrodes, the same one
        # the table above and the board are given, so the statuses it measures
        # are the ones on screen. That also settles which database it is
        # looking at, which is whichever one the app is connected to.
        self.sampling_widget = QcSamplingWidget()
        self.sampling_widget.show_loading()
        self.tabs.addTab(self.sampling_widget, "QC Sampling")

        main_layout.addWidget(self.tabs, 1)
        central_widget.setLayout(main_layout)
        self.setCentralWidget(central_widget)

        self._start_polling()

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------
    def _start_polling(self) -> None:
        """Watch for other people's changes, from a worker thread.

        The query is a network round trip, so on the main thread it froze the
        window for a couple of hundred milliseconds every tick and stalled the
        tab crossfade part way through. Here only the finished result crosses
        back, and it is applied on the UI thread.

        Deliberately NOT triggered by a tab change: a switch should cost
        nothing, and the timer picks the change up within one interval anyway.
        """
        # No entry for the sampling tab: it has no watermark of its own and
        # nothing to poll for. It rides the electrodes reload instead, through
        # _apply_electrodes.
        self._widgets_by_key = {
            "electrodes": self.electrodes_widget,
            "qc_board": self.qc_board_widget,
            "sem_edx": self.sem_edx_widget,
        }
        # One poll at a time. A slow answer must not queue behind itself and
        # arrive as a burst of reloads.
        self._poll_in_flight = False

        self._poll_thread = QThread(self)
        self._poll_worker = PollWorker(self._update_callbacks)
        self._poll_worker.moveToThread(self._poll_thread)
        self.poll_requested.connect(self._poll_worker.poll)
        self._poll_worker.polled.connect(self._on_polled)

        # Everything the window does not need in order to open, on the same
        # thread. Together these were about 1.9 seconds of blank screen before
        # the first pixel; now the window is up and each tab fills in.
        self._deferred_worker = DeferredLoad({
            "electrodes": self.backend.get_metadata,
            "sem_edx": self.backend.get_all_sem_edx,
            "vats": self.backend.load_vats,
        })
        self._deferred_worker.moveToThread(self._poll_thread)
        self.deferred_requested.connect(self._deferred_worker.run)
        self._deferred_worker.loaded.connect(self._on_deferred)

        # Shares the poll thread, so a save and a poll cannot run at once:
        # both are queued slots on it and Qt runs them one after another.
        self._background_worker = BackgroundJob()
        self._background_worker.moveToThread(self._poll_thread)
        self.background_requested.connect(self._background_worker.run)
        self._background_worker.finished.connect(self._on_background_finished)
        # What to call with the answer. One at a time: the only caller is a
        # modal dialog, which cannot ask twice.
        self._background_done = None
        # Which tabs are still showing a placeholder, so the poll timer does
        # not ask about a table that has not had its first read.
        self._awaiting = {"electrodes", "sem_edx"}

        self._poll_thread.start()
        # THE VATS GO FIRST, ahead of the electrodes that are actually on
        # screen, because the statuses are computed FROM them. A scrap spreads
        # its doubt along the electrodes of one vat; with no vat on record it
        # falls back to the production sequence, which is a different rule
        # reaching different electrodes. Read second, they made half the table
        # change status a few seconds after it appeared, and the first version
        # was the wrong one. Better a longer placeholder than a confident
        # wrong answer.
        for key in ("vats", "electrodes", "sem_edx"):
            self.deferred_requested.emit(key)

        self._auto_refresh_timer = QTimer(self)
        self._auto_refresh_timer.setInterval(app_config.auto_refresh_interval_ms())
        self._auto_refresh_timer.timeout.connect(self._auto_refresh_tick)
        self._auto_refresh_timer.start()

    def _warn_if_not_production(self) -> None:
        """Say so, in the tab bar's corner, when this is not the real database.

        Only the QC data moves: the electrodes are read from the plant database
        either way, so the thing worth warning about is where writes land.
        """
        try:
            if not self.backend.is_test_database():
                return
        except Exception as e:
            print(f"Could not tell which database this is: {e}")
            return
        banner = QLabel(TEST_DB_WARNING)
        banner.setStyleSheet(TEST_DB_STYLE)
        banner.setToolTip(f"Writing to {self.backend.database_name()}")
        self.tabs.setCornerWidget(banner, Qt.Corner.TopRightCorner)

    # ------------------------------------------------------------------
    # Wiring helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _confirm_driver_install(driver_name: str, _installer_path) -> bool:
        """Ask the user (Yes/No) before launching the ODBC-driver installer.

        Runs on the main thread during startup, so a modal dialog is safe.
        """
        from PyQt6.QtWidgets import QMessageBox

        box = QMessageBox()
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Database Driver Required")
        box.setText(
            f'The SQL database driver "{driver_name}" is required to run the '
            f"application, but it is not installed."
        )
        box.setInformativeText(
            "Would you like to install it now?\n\n"
            "The installer will open and the application will continue once the "
            "installation is finished."
        )
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.Yes)
        return box.exec() == QMessageBox.StandardButton.Yes

    @staticmethod
    def _ask_image_root() -> str:
        """Ask where the SEM images library is, when the search cannot find it.

        Only reached on a machine that syncs it somewhere unusual, a different
        drive or not under the user profile, so it is a one-off per session
        rather than something an operator normally sees.
        """
        from PyQt6.QtWidgets import QFileDialog, QMessageBox

        QMessageBox.information(
            None, "SEM images not found",
            "The shared SEM images library could not be found on this "
            "computer.\n\n"
            "Please point to the 'Production SEM images' folder, the one that "
            "contains 'B productionline'.")
        return QFileDialog.getExistingDirectory(
            None, "Open the Production SEM images folder")

    def _apply_electrodes(self, frame, reload: bool = False) -> None:
        """One processed frame to both tabs that read it.

        The table repaints the rows that moved. The board re-indexes, because
        its add dialog splits electrodes by status and would otherwise keep
        offering the launch-time answer for the rest of the session.

        ``reload`` rebuilds the table instead of patching it, for the rare
        change that adds or drops a column rather than moving values.
        """
        if reload:
            self.electrodes_widget.load_data(frame)
        else:
            self.electrodes_widget.patch_statuses(frame)
        self.qc_board_widget.load_electrodes(frame)
        # Re-measured from the same frame. There is no patching to do here:
        # the run lengths are computed from the whole column, so one changed
        # status can move every figure on the tab.
        self._reload_sampling(frame)

    def _reload_sampling(self, frame) -> None:
        """Hand the sampling tab a new frame, unless somebody is using it.

        Held back while a range is being typed or a slider dragged: re-measuring
        rebuilds every table on the tab. The frame is still handed over, so the
        next Analyse picks it up rather than measuring the stale one.
        """
        try:
            if self.sampling_widget.is_busy():
                self.sampling_widget.frame = frame
                return
            self.sampling_widget.load_data(frame)
        except Exception as e:
            print(f"Refreshing the sampling tab failed: {e}")

    def _on_status_override(self, marking: str, status: str) -> None:
        """Set one electrode's status by hand, then repaint the changed rows."""
        try:
            self.backend.set_electrode_status(marking, status)
            self._apply_electrodes(self.backend.reprocess_metadata())
        except Exception as e:
            print(f"Setting the status of {marking} failed: {e}")

    def _on_board_changed(self) -> None:
        """A board move changed one electrode's status.

        Re-processing the cached read is cheap (no database round trip) and the
        tab repaints only the rows that actually differ — so this stays fast
        enough to run on every drop, and tab switches carry no backlog.
        """
        try:
            self._apply_electrodes(self.backend.reprocess_metadata())
        except Exception as e:
            print(f"Applying QC board change failed: {e}")

    def _reload_sem_edx(self, only: str = "") -> None:
        """Feed the electrode type sub tabs from the backend.

        One generation when the caller knows which changed, which is the usual
        case: a save touches one, and re-reading the others cost about 850ms
        of a wait the operator sat through after every edit.
        """
        wanted = str(only or "").strip()
        for generation in self.backend.get_sem_edx_generations():
            if wanted and generation != wanted:
                continue
            for electrode_type in self.backend.get_sem_edx_types(generation):
                self.sem_edx_widget.load_data(
                    self.backend.get_sem_edx(generation, electrode_type),
                    electrode_type, generation)

    def _on_background_finished(self, result, error) -> None:
        """Hand a background answer to whoever asked. Back on the UI thread."""
        done, self._background_done = self._background_done, None
        if callable(done):
            done(result, error)

    def _save_sem_edx_sample(self, generation, electrode, position,
                             values, measurements, done) -> None:
        """Write one sample and re-read its generation, off the UI thread.

        The write and the read go to the worker; only the rendering comes back
        here, since a widget may only be touched on this thread. That is what
        lets the sample window keep its spinner turning while it waits.
        """
        def work():
            changed = False
            if values:
                changed |= bool(self.backend.update_sem_edx_sample(
                    generation, electrode, position, values))
            if measurements:
                changed |= bool(self.backend.update_sem_edx_measurements(
                    generation, electrode, position, measurements))
            if not changed:
                return None
            return {electrode_type: self.backend.get_sem_edx(generation, electrode_type)
                    for electrode_type in self.backend.get_sem_edx_types(generation)}

        def rendered(frames, error):
            if error is None and frames:
                self.sem_edx_widget.apply_saved(generation, frames)
            done(error is None, error)

        self._background_done = rendered
        self.background_requested.emit(work)

    def _on_sem_edx_changed(self, generation: str = "") -> None:
        """An import or an edit changed the stored SEM results, so re-read them.

        Both electrode types reload, since an import can land on either, but
        only the generation named: nothing an edit does to one can change
        another. An empty name re-reads everything, for a caller that does not
        know which changed.
        """
        try:
            self._reload_sem_edx(generation)
        except Exception as e:
            print(f"Reloading the SEM EDX results failed: {e}")

    def _save_then_reload(self, save_fn, reload_fn):
        """Wrap a save callback so a successful write is followed by a re-read.

        A just-added row then lands in correct sort order with any
        server-generated fields. The reload is deferred to the next event-loop
        turn so it runs AFTER the tab's own local echo, which it replaces
        without a flash.
        """
        def wrapped(*args, **kwargs):
            result = save_fn(*args, **kwargs)

            def _reload():
                try:
                    reload_fn()
                except Exception as e:
                    print(f"Reload-after-save failed: {e}")

            QTimer.singleShot(0, _reload)
            return result

        return wrapped

    # ------------------------------------------------------------------
    # Auto-refresh
    # ------------------------------------------------------------------
    def _auto_refresh_tick(self) -> None:
        """Ask the worker about the visible tab only.

        A hidden tab is not worth a query: it is polled on the next tick after
        it becomes visible, and reloaded then.
        """
        if self._poll_in_flight:
            return
        widget = self.tabs.currentWidget()
        # The sampling tab has no watermark of its own: what it measures is the
        # electrode statuses, so that is what it asks about. Without this it is
        # in none of the polling keys and nothing is polled at all while it is
        # the tab on screen.
        if widget is self.sampling_widget:
            widget = self.electrodes_widget
        if widget is None or self._is_widget_busy(widget):
            return
        for key, candidate in self._widgets_by_key.items():
            if candidate is widget:
                # A tab still waiting for its first read has nothing to compare
                # against, and the answer would land on a table that is about
                # to be rebuilt anyway.
                if key in self._awaiting:
                    return
                self._poll_in_flight = True
                self.poll_requested.emit(key)
                return

    def _on_deferred(self, key: str, result) -> None:
        """Something the window opened without has arrived. On the UI thread.

        Always a full reload rather than a patch: the tab it is going to has
        nothing in it yet, and patching only ever rewrites the cells of columns
        the table already has, which the vats add one to.
        """
        # Dropped whether or not it worked: a read that failed is not going to
        # be retried, and leaving the tab out of the polling forever would mean
        # it never recovered.
        self._awaiting.discard(key)
        try:
            if key == "electrodes":
                if result is None:
                    self.electrodes_widget.hide_loading()
                    self.sampling_widget.hide_loading()
                else:
                    self._apply_electrodes(result, reload=True)
                # The visible tab now has its rows, so the splash has nothing
                # left to cover. After the table is built, not before, or the
                # icon would uncover an empty one.
                self._release_splash()
            elif key == "sem_edx":
                if not result:
                    self.sem_edx_widget.hide_loading()
                for generation, frames in (result or {}).items():
                    for electrode_type, frame in (frames or {}).items():
                        self.sem_edx_widget.load_data(frame, electrode_type, generation)
            elif key == "vats" and result:
                # Only once the table exists. At startup the vats land first
                # and the electrode read is still queued behind them, so it
                # picks them up by itself; reprocessing here would instead
                # fetch all 12k rows on the UI thread and freeze the window.
                if "electrodes" not in self._awaiting:
                    self._apply_electrodes(self.backend.reprocess_metadata(),
                                           reload=True)
        except Exception as e:
            print(f"Applying the deferred {key} read failed: {e}")

    def _on_polled(self, key: str, result) -> None:
        """Render what the worker found. Back on the UI thread."""
        self._poll_in_flight = False
        widget = self._widgets_by_key.get(key)
        # Skipped if the operator started editing while the answer was in
        # flight, since a reload would clear what they are typing.
        if widget is None or self._is_widget_busy(widget):
            return
        try:
            changed = widget.apply_external_updates(result)
        except Exception as e:
            print(f"Applying the {key} refresh failed: {e}")
            return

        # An electrodes poll re-reads the board too, since a live decision on
        # another machine changes what the Status column says. The QC tab draws
        # from that same store, so it has to be repainted from it, and re-indexed
        # from the frame: which electrodes exist and what their status is decides
        # what the add dialog offers.
        if changed and key == "electrodes":
            self.qc_board_widget.load_electrodes(result.get("data"))
            self._reload_sampling(result.get("data"))

    @staticmethod
    def _is_widget_busy(widget) -> bool:
        """True if the user is mid-entry/edit on this widget."""
        if getattr(widget, "editing_mode", False):
            return True
        entry_form = getattr(widget, "entry_form", None)
        grid_wrapper = getattr(entry_form, "grid_wrapper", None) if entry_form else None
        return bool(grid_wrapper is not None and grid_wrapper.isVisible())

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        """Stop the poller, then release backend resources."""
        self._auto_refresh_timer.stop()
        self._poll_thread.quit()
        # Bounded, so a poll still waiting on the network cannot hold the app
        # open. The connection goes with the process either way.
        self._poll_thread.wait(2000)
        try:
            self.backend.close()
        except Exception:
            pass
        super().closeEvent(event)


def _report_uncaught(kind, value, trace) -> None:
    """Show what went wrong and carry on, instead of the window disappearing.

    PyQt calls qFatal on an unhandled exception inside a slot, which kills the
    process with nothing on screen and nothing in a log: a misread folder or a
    bad row took the whole application with it. Installing a hook is the
    supported way to keep the window alive, and the operator gets a dialog
    naming the fault rather than a vanished app.
    """
    import traceback

    from PyQt6.QtWidgets import QMessageBox

    text = "".join(traceback.format_exception(kind, value, trace))
    print(text, file=sys.stderr)
    try:
        box = QMessageBox()
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Something went wrong")
        box.setText(f"{kind.__name__}: {value}")
        box.setDetailedText(text)
        box.exec()
    except Exception:
        pass            # a dialog about a dialog helps nobody


def run_app(app=None, splash=None):
    """Entry point: build the window, or show a titled error dialog and exit.

    main.py passes both: it builds the QApplication first so it can claim the
    taskbar identity and set the icon before any window exists, and puts the
    splash up before this module is even imported. Both are optional so
    run_app() still works on its own.
    """
    app = app or QApplication.instance() or QApplication(sys.argv)
    sys.excepthook = _report_uncaught
    try:
        # Only when nothing has set one, so main.py's icon stands. The flask is
        # the fallback for anything calling run_app directly.
        if app.windowIcon().isNull():
            from src.assets import ICON_ICO, asset_path
            from PyQt6.QtGui import QIcon

            icon = asset_path(ICON_ICO)
            app.setWindowIcon(QIcon(str(icon)) if icon.exists()
                              else qta.icon("fa6s.flask", color="#14b8a6"))
        # A tooltip is its own top level window, so it has to be styled here
        # rather than on the widget that owns it.
        app.setStyleSheet(TOOLTIP_STYLE)
        window = MainWindow()
        # One decision about which monitor, not two that happen to agree: the
        # splash picked the screen under the cursor, the window follows it.
        if splash is not None and splash.screen() is not None:
            window.setGeometry(splash.screen().availableGeometry())
        window.showMaximized()
        # Let the empty frame actually paint before the slow part starts.
        app.processEvents()
        if splash is not None:
            # Handed over rather than dismissed here: the window drops it once
            # the Electrodes table has rows, which is seconds after this.
            window.hold_splash(splash)
        window.start()
        sys.exit(app.exec())
    except FileNotFoundError as e:
        # Off the screen first: it is always on top and would float over the
        # dialog.
        if splash is not None:
            splash.dismiss()
        _fatal("Missing Required Files",
               f"The application could not find a required file:\n\n{e}\n\n"
               "Check that the 'config' folder sits next to the executable and "
               "contains config.json.")
    except Exception as e:
        if splash is not None:
            splash.dismiss()
        from src.data_access.db_manager import DriverNotInstalledError

        if isinstance(e, DriverNotInstalledError):
            _fatal("Database Driver Required",
                   f"{e}\n\nThe application reads from the SQL database and cannot "
                   "start without its ODBC driver.\n\nComplete the driver "
                   "installation, then start the application again.")
        _fatal("Failed to Initialize Application",
               f"Error: {e}\n\nCheck that:\n"
               "1. config/config.json is present and valid\n"
               "2. The configured backend is reachable\n"
               "3. You have permission to read these files")


def _fatal(title: str, message: str) -> None:
    """Show a titled error dialog and exit — never launch half-initialized."""
    from PyQt6.QtWidgets import QMessageBox

    QMessageBox.critical(None, title, f"{title}\n\n{message}")
    sys.exit(1)
