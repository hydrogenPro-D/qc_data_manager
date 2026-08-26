"""Polling for other people's changes, off the UI thread.

Every few seconds the visible tab asks the database whether anything moved. Run
on the main thread that is a network round trip with the window frozen behind
it: measured against Azure it is around 190ms per poll, and 260ms when the
Electrodes tab adds its own watermark query. Long enough to stall the tab
crossfade part way through, which is what it looked like was at fault.

So the query runs here instead, on a worker thread, and only the result crosses
back. The split that makes that safe:

    worker thread   the query, and building the DataFrames from it
    UI thread       everything that touches a widget

Nothing in the fetch half touches Qt: it is pyodbc and pandas, both of which are
happy off the main thread as long as the connection is not shared, which is why
``DbManager`` keeps a connection per thread.

One poll is in flight at a time. A slow answer must not queue up behind itself
and arrive as a burst of reloads.
"""

from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot


class PollWorker(QObject):
    """Runs one tab's update callback and hands the result back by signal."""

    # (key, result). The result is whatever the backend's update callback
    # returned, normally {"has_updates": bool, "data": ...}.
    polled = pyqtSignal(str, object)

    def __init__(self, callbacks: dict[str, Callable]) -> None:
        super().__init__()
        self._callbacks = dict(callbacks)

    @pyqtSlot(str)
    def poll(self, key: str) -> None:
        """Ask one backend whether its entity changed.

        Always emits, even on failure, so the caller can let the next poll
        through rather than waiting for an answer that is never coming.
        """
        callback = self._callbacks.get(key)
        if not callable(callback):
            self.polled.emit(key, {})
            return
        try:
            self.polled.emit(key, callback() or {})
        except Exception as e:
            print(f"Auto-refresh error for {key}: {e}")
            self.polled.emit(key, {})


class DeferredLoad(QObject):
    """Runs one piece of backend work off the UI thread, once.

    For anything the window does not need in order to open. It shares the poll
    thread rather than starting its own, which also means it cannot overlap a
    poll: two queries at once on one connection is exactly what
    ``DbManager``'s per thread connections exist to avoid.
    """

    # (key, result). The result is whatever the work returned, or None if it
    # raised, so the caller can tell "nothing there" from "never answered".
    loaded = pyqtSignal(str, object)

    def __init__(self, work: dict[str, Callable]) -> None:
        super().__init__()
        self._work = dict(work)

    @pyqtSlot(str)
    def run(self, key: str) -> None:
        job = self._work.get(key)
        if not callable(job):
            self.loaded.emit(key, None)
            return
        try:
            self.loaded.emit(key, job())
        except Exception as e:
            print(f"Deferred load failed for {key}: {e}")
            self.loaded.emit(key, None)
