"""The rotating deck: pages that slide sideways, one after another, forever.

A horizontal slide rather than the app's crossfade. A crossfade says "this
replaced that", which is right for a tab somebody clicked; a slide says "here
comes the next one", which is what an unattended screen is doing. It also gives
the eye a direction to follow, so a person walking past can tell the board is
advancing on its own rather than reacting to someone at a keyboard.

Pages are children of the deck, positioned by hand. There is exactly one
animation group, reused, and one invariant: **only the current page is left on
screen when nothing is running**. Building an animation per rotation left
stopped ones stranded mid-slide when a rotation interrupted its predecessor,
and on a screen that rotates every few seconds for weeks that accumulates.
"""

from __future__ import annotations

from PyQt6.QtCore import (
    QEasingCurve, QParallelAnimationGroup, QPropertyAnimation, QRect, pyqtSignal,
)
from PyQt6.QtWidgets import QWidget


class SlideDeck(QWidget):
    """Holds the pages and slides between them on demand."""

    # Emitted when a slide has finished and the deck is still again. The window
    # uses it to do its reading while nothing is moving.
    settled = pyqtSignal(int)

    def __init__(self, parent=None, duration_ms: int = 620) -> None:
        super().__init__(parent)
        self.duration_ms = duration_ms
        self._pages: list[QWidget] = []
        self._index = 0
        self._sliding = False

        self._group = QParallelAnimationGroup(self)
        self._group.finished.connect(self._on_finished)

    # ------------------------------------------------------------------
    def add_page(self, page: QWidget) -> None:
        """Adopt a page. The first one added is the one shown."""
        page.setParent(self)
        self._pages.append(page)
        if len(self._pages) == 1:
            page.setGeometry(self.rect())
            page.show()
        else:
            page.setGeometry(self._offscreen(1))
            page.hide()

    def pages(self) -> list[QWidget]:
        return list(self._pages)

    def current_index(self) -> int:
        return self._index

    def count(self) -> int:
        return len(self._pages)

    # ------------------------------------------------------------------
    def _offscreen(self, direction: int) -> QRect:
        """A page's resting place just past one edge of the deck."""
        rect = self.rect()
        return QRect(rect.x() + direction * rect.width(), rect.y(),
                     rect.width(), rect.height())

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        """Re-place every page. Only the current one sits inside the frame."""
        super().resizeEvent(event)
        if self._sliding:
            # Mid slide the animation owns the geometry, and writing to it here
            # would snap the pages to their end states halfway through.
            return
        for index, page in enumerate(self._pages):
            page.setGeometry(self.rect() if index == self._index
                             else self._offscreen(1))

    # ------------------------------------------------------------------
    def advance(self) -> None:
        """Slide to the next page, wrapping at the end."""
        if len(self._pages) > 1:
            self.go_to((self._index + 1) % len(self._pages))

    def go_to(self, index: int) -> None:
        """Slide to one page. Ignored while a slide is already running.

        Ignored rather than queued: a rotation that fires during a slide means
        the screen is running slower than its own interval, and stacking the
        backlog would make it worse rather than catching up.
        """
        if self._sliding or not self._pages:
            return
        index %= len(self._pages)
        if index == self._index:
            return

        leaving, arriving = self._pages[self._index], self._pages[index]
        # Always leftward, so the deck reads as one strip advancing rather than
        # as pages shuffling back and forth.
        arriving.setGeometry(self._offscreen(1))
        arriving.show()
        arriving.raise_()

        self._group.clear()
        for page, end in ((leaving, self._offscreen(-1)),
                          (arriving, self.rect())):
            animation = QPropertyAnimation(page, b"geometry", self._group)
            animation.setDuration(self.duration_ms)
            # Eased both ends. A slide that starts abruptly reads as a jump,
            # and one that stops abruptly reads as a crash into the edge.
            animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
            animation.setStartValue(page.geometry())
            animation.setEndValue(end)
            self._group.addAnimation(animation)

        self._sliding = True
        self._index = index
        self._group.start()

    def _on_finished(self) -> None:
        """Tidy up: everything but the current page goes back off screen."""
        self._sliding = False
        for index, page in enumerate(self._pages):
            if index == self._index:
                page.setGeometry(self.rect())
                page.show()
            else:
                page.setGeometry(self._offscreen(1))
                page.hide()
        self.settled.emit(self._index)
