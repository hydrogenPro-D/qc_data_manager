# Crossfading between tabs in PyQt6

A drop-in `QTabWidget` that dissolves between pages instead of swapping them in
one frame. Roughly 200ms, no dependencies beyond PyQt6.

It looks trivial and is not, because three Qt behaviours get in the way. Each
one produced a visible bug before it was handled, and they are described below
the code so a reimplementation does not rediscover them.

---

## How it works

The obvious approach, fading the outgoing page out and the incoming page in,
does not work. By the time Qt tells you the tab changed, the old page is already
hidden, and animating two opacities against each other makes the window
background flash through the gap in the middle.

What works instead:

1. **On the click**, while the old page is still on screen, photograph it into a
   `QPixmap` and put that picture in a label on top.
2. **Let Qt switch** the tab normally. The new page appears at full opacity
   immediately, hidden behind the photograph.
3. **Fade the photograph out.** Only one thing animates, and the new page is
   fully painted underneath the whole time, so nothing bleeds through.

There is exactly **one** label and **one** animation, reused for every switch,
which is what makes the interrupted case safe.

---

## The code

```python
"""A QTabWidget that crossfades between pages."""
from __future__ import annotations

from PyQt6.QtWidgets import QGraphicsOpacityEffect, QLabel, QTabWidget
from PyQt6.QtCore import Qt, QPoint, QPropertyAnimation, QEasingCurve, QRect


class CrossfadeTabWidget(QTabWidget):
    """Dissolves a photograph of the outgoing page over the incoming one.

    One overlay and one animation, reused for every switch, with one invariant:
    the overlay is visible only while the animation is running.

    Set ``fade_ms`` to 0 to switch instantly.
    """

    FADE_MS = 200

    def __init__(self, parent=None, fade_ms: int = FADE_MS) -> None:
        super().__init__(parent)
        self.fade_ms = fade_ms

        self._overlay: QLabel | None = None
        self._animation: QPropertyAnimation | None = None
        # Set on the click, consumed by the change that follows it.
        self._pending = False

        self.tabBarClicked.connect(self._photograph_current)
        self.currentChanged.connect(self._fade_to_current)

    def _build_overlay(self) -> QLabel:
        """The one label every switch reuses, and the effect it fades with."""
        overlay = QLabel(self)
        overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        overlay.hide()
        overlay.setGraphicsEffect(QGraphicsOpacityEffect(overlay))

        animation = QPropertyAnimation(overlay.graphicsEffect(), b"opacity", self)
        animation.setStartValue(1.0)
        animation.setEndValue(0.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        # Hidden rather than deleted, so the next switch has it ready and there
        # is never a stray widget to account for.
        animation.finished.connect(overlay.hide)

        self._overlay, self._animation = overlay, animation
        return overlay

    def _photograph_current(self, index: int) -> None:
        """Copy the page about to be left, while it is still on screen."""
        current = self.currentWidget()
        if not self.fade_ms or index == self.currentIndex() or current is None:
            return

        overlay = self._overlay or self._build_overlay()
        overlay.setPixmap(current.grab())
        # The page lives in the internal stack, so map its corner to us. Taken
        # fresh every time, so a resize since the last switch is accounted for.
        overlay.setGeometry(QRect(current.mapTo(self, QPoint(0, 0)),
                                  current.size()))
        self._pending = True

    def _fade_to_current(self, _index: int) -> None:
        """Dissolve the photograph over the fully opaque new page."""
        pending, self._pending = self._pending, False
        if self._animation is None or self._overlay is None:
            return

        # Restarting resets the effect to its start value, so an interrupted
        # fade cannot leave the overlay part transparent for the next switch.
        self._animation.stop()
        if not pending or not self.fade_ms or self.currentWidget() is None:
            # Nothing to dissolve. The invariant says hide it.
            self._overlay.hide()
            return

        self._overlay.raise_()
        self._overlay.show()
        self._animation.setDuration(self.fade_ms)
        self._animation.start()
```

Use it exactly like `QTabWidget`:

```python
tabs = CrossfadeTabWidget()
tabs.addTab(first_page, "First")
tabs.addTab(second_page, "Second")

nested = CrossfadeTabWidget(fade_ms=0)   # instant, for a strip that should not fade
```

---

## The three things that will bite you

### 1. A hidden widget does not render, so photograph on the click

`currentChanged` fires **after** Qt has hidden the outgoing page, and
`widget.grab()` on a hidden widget returns blank or stale pixels. So the
photograph has to be taken from `tabBarClicked`, which fires first, while the
page is still visible.

Consequence: a tab changed in **code** (`setCurrentIndex` from your own logic)
never gets a photograph and switches instantly. That is usually what you want,
and it is why `_pending` exists rather than assuming every change was a click.

### 2. `QPropertyAnimation.stop()` does not emit `finished`

This is the one that produces the worst bug, and it contradicts what most people
assume. Qt emits `finished` only when an animation **reaches its end**. Calling
`stop()` part way through emits nothing.

So this is broken:

```python
# BROKEN: the label survives if the animation is ever stopped early.
animation.finished.connect(photograph.deleteLater)
```

**Symptom:** switch tabs faster than the fade and a half transparent copy of an
old page stays on screen forever, like a watermark. One per interrupted switch,
and they accumulate. With `WA_TransparentForMouseEvents` set they do not block
clicks, so it reads as a rendering glitch rather than a broken widget.

**Fix:** do not tie cleanup to `finished` at all. Reuse one overlay, so there is
nothing to clean up. An interrupted switch simply re-points the label it already
has.

If you must create a label per switch, connect `stateChanged` instead and
dispose when it leaves `Running`. Reuse is still better: it removes the failure
mode instead of handling it.

### 3. Restart, do not recreate, or the fade starts part way through

Because the overlay is reused, its opacity effect is wherever the last
interrupted fade left it, say 0.35. Calling `start()` on the same
`QPropertyAnimation` resets it to `startValue`, which is why the code stops and
restarts one animation rather than building a new one. Miss this and every
switch after an interrupted one begins already half faded.

---

## Geometry

`setGeometry(QRect(current.mapTo(self, QPoint(0, 0)), current.size()))` maps the
page's corner into the tab widget's coordinates, because the page is a child of
the internal `QStackedWidget`, not of the `QTabWidget`. Measure it on **every**
switch: a per switch label that is never re-measured is also the wrong size
after the window is resized.

---

## Worth pinning with tests

Two are enough to protect the design, and both fail against a naive
implementation:

```python
def test_switching_faster_than_the_fade_leaks_nothing():
    for index in (1, 2, 0, 1, 2):
        tabs._photograph_current(index)
        tabs.setCurrentIndex(index)
        assert len([c for c in tabs.children() if isinstance(c, QLabel)]) <= 1


def test_an_interrupted_fade_restarts_from_opaque():
    tabs._photograph_current(1); tabs.setCurrentIndex(1)
    tabs._animation.setCurrentTime(150)          # part way through
    tabs._photograph_current(2); tabs.setCurrentIndex(2)
    assert tabs._overlay.graphicsEffect().opacity() == 1.0
```

Run them headless with `QT_QPA_PLATFORM=offscreen`. Call `tabs.show()` first,
since `grab()` needs a realised widget.

---

## Where not to use it

* **Canvases that redraw themselves**, matplotlib in particular. Photographing
  one mid redraw captures a partial frame.
* **Strips nested inside something that already fades.** If switching an outer
  tab also changes an inner one, two dissolves run over a single click and it
  reads as a stutter. Pass `fade_ms=0` to the inner strip. Note that nesting on
  its own is fine: two strips that never change together can both fade, because
  only one of them animates per click.
