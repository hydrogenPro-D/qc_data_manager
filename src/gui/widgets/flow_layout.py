"""A layout that wraps onto a second row when the window is too narrow.

Qt has no such layout in the box. A row of small fixed size widgets, a control
bar for instance, is wider than the window on a laptop screen: with a
``QHBoxLayout`` the right hand end is simply cut off, and there is no scrollbar
to reach it because the bar is not in a scroll area.

Everything here is the standard flow layout: lay items left to right, and when
the next one will not fit, start a new line. One row on a wide screen, two on a
narrow one, and nothing ever clipped.
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect, QSize, Qt
from PyQt6.QtWidgets import QLayout, QSizePolicy

# A widget carrying this property is a separator between groups rather than a
# control. It is dropped when it would fall at the end or the start of a row,
# where a hairline separates nothing and reads as a stray tick.
DIVIDER = "flowDivider"


def is_divider(item) -> bool:
    widget = item.widget() if item else None
    return bool(widget and widget.property(DIVIDER))


class FlowLayout(QLayout):
    """Left to right, wrapping to a new row as needed."""

    def __init__(self, parent=None, spacing: int = 6,
                 line_spacing: int = 8) -> None:
        super().__init__(parent)
        self._items: list = []
        self._spacing = spacing
        self._line_spacing = line_spacing
        self.setContentsMargins(0, 0, 0, 0)

    # --- the five QLayout has to have -------------------------------------
    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):
        return (self._items.pop(index) if 0 <= index < len(self._items)
                else None)

    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)

    # --- sizing ------------------------------------------------------------
    def hasHeightForWidth(self) -> bool:
        """The whole point: the height depends on how much width there is."""
        return True

    def heightForWidth(self, width: int) -> int:
        return self._arrange(QRect(0, 0, width, 0), place=False)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._arrange(rect, place=True)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        """The widest single item, not the whole row.

        Deliberately: claiming the row's full width as a minimum is what makes
        a layout refuse to shrink, and refusing to shrink is the bug this
        class exists to fix.
        """
        size = QSize(0, 0)
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(),
                            margins.top() + margins.bottom())

    # --- the arranging itself ---------------------------------------------
    def _arrange(self, rect: QRect, place: bool) -> int:
        """Lay the items out in ``rect``, or just measure. Returns the height."""
        margins = self.contentsMargins()
        inner = rect.adjusted(margins.left(), margins.top(),
                              -margins.right(), -margins.bottom())
        x, y, row_height = inner.x(), inner.y(), 0
        trailing = None
        for item in self._items:
            hint = item.sizeHint()
            divider = is_divider(item)
            if x + hint.width() > inner.right() and row_height:
                # A separator that ended the row now separates nothing.
                if trailing is not None and place:
                    _collapse(*trailing)
                x = inner.x()
                y += row_height + self._line_spacing
                row_height = 0
                if divider:
                    # And one that would start the row separates nothing
                    # either. Placed at no width rather than hidden: toggling
                    # visibility from inside a layout pass invites a loop.
                    if place:
                        _collapse(item, QPoint(x, y), hint.height())
                    trailing = None
                    continue
            if place:
                item.setGeometry(QRect(QPoint(x, y), hint))
            trailing = (item, QPoint(x, y), hint.height()) if divider else None
            x += hint.width() + self._spacing
            row_height = max(row_height, hint.height())
        return y + row_height - rect.y() + margins.bottom()


def _collapse(item, at: QPoint, height: int) -> None:
    """Give an item no width, so it takes no room and paints nothing."""
    item.setGeometry(QRect(at, QSize(0, height)))


def flow_host(spacing: int = 6, line_spacing: int = 8, parent=None):
    """A widget holding a ``FlowLayout``, ready to have widgets added to it.

    The size policy matters: without ``setHeightForWidth`` the parent layout
    never asks how tall the wrapped rows are and the second row is cut off.
    """
    from PyQt6.QtWidgets import QWidget

    host = QWidget(parent)
    layout = FlowLayout(host, spacing=spacing, line_spacing=line_spacing)
    policy = QSizePolicy(QSizePolicy.Policy.Expanding,
                         QSizePolicy.Policy.Preferred)
    policy.setHeightForWidth(True)
    host.setSizePolicy(policy)
    return host, layout
