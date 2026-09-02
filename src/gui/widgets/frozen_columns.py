"""Pin the leading columns of a table while the rest scrolls sideways.

A table with a column per measurement is always wider than its pane, and
scrolling it loses the one thing that says which row you are looking at. This
keeps the first few columns still.

    FrozenColumns(table).freeze_through("Sample location")

A second view over the SAME model, which is Qt's own approach: nothing is
copied, so the two cannot disagree about what a cell holds.

Three things make it behave like one table rather than two, and each was a bug
before it was one of these.

**It is transparent to the mouse.** Every click, hover and wheel goes straight
through to the table underneath, which is why hovering, picking a row and
double-clicking still work with no code here and none in the owning tab. It
works because the pinned half sits over the same ROWS at the same heights, and
the handlers a table like this attaches care about the row, not the column.
Forwarding the events instead meant the table fired Leave the moment the
pointer crossed onto the pinned half, so the hover outline flickered and the
half being pointed at was the half that stopped repainting.

**It borrows the table's stylesheet, rewritten.** Qt style sheets match on the
class actually named, so the ``QTableWidget::item`` rules that style the table
do not apply to a ``QTableView`` at all. Left alone, the pinned columns came
out with default padding, default font and no fill, right beside the styled
ones.

**It only appears once the table is scrolled.** At rest the columns underneath
are the same columns, perfectly aligned, so there is nothing to cover and
nothing that can be a pixel out.

The shared hover and picked outlines are repainted by whoever changes them,
through :meth:`repaint_rows`. Mirroring the table's paint events instead is a
loop: repainting a child that overlaps its parent dirties the parent under it,
which paints, which repaints the child, at one round per turn of the event
loop, forever.
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtWidgets import QAbstractItemView, QTableView, QTableWidget


class FrozenColumns(QTableView):
    """The leading columns of ``table``, held still while it scrolls."""

    def __init__(self, table: QTableWidget) -> None:
        super().__init__(table)
        self._table = table
        self._count = 0
        self._syncing = False

        self.setModel(table.model())
        # Never takes a mouse event, a key or the focus. The table underneath
        # handles all of it, and this only has to look right.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setFrameShape(QTableView.Shape.NoFrame)
        self.verticalHeader().setVisible(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Matched, not chosen: with different modes the two halves disagree
        # about what "scrolled by one" means and the rows drift apart.
        self.setVerticalScrollMode(table.verticalScrollMode())
        self.setAlternatingRowColors(table.alternatingRowColors())
        self.setShowGrid(table.showGrid())
        self.setStyleSheet(self._borrowed_style())

        table.viewport().stackUnder(self)

        header = table.horizontalHeader()
        header.sectionResized.connect(lambda *_: self.sync())
        header.sectionCountChanged.connect(lambda *_: self.sync())
        table.verticalHeader().sectionResized.connect(
            lambda row, _old, height: self.setRowHeight(row, height))
        table.verticalScrollBar().valueChanged.connect(
            self.verticalScrollBar().setValue)
        table.horizontalScrollBar().valueChanged.connect(
            lambda *_: self._place())
        table.viewport().installEventFilter(self)
        table.installEventFilter(self)
        self.hide()

    # ------------------------------------------------------------------
    def freeze_through(self, header: str) -> None:
        """Pin every column up to and including the one with this header.

        By header rather than by a count: which columns a sheet has depends on
        what it measures, so a fixed number would pin the wrong ones.
        """
        count = 0
        for column in range(self._table.columnCount()):
            item = self._table.horizontalHeaderItem(column)
            if item is not None and item.text() == header:
                count = column + 1
                break
        self._count = count
        self.sync()

    def repaint_rows(self) -> None:
        """Redraw the pinned half. Call beside repainting the table itself."""
        if self.isVisible():
            self.viewport().update()

    def sync(self) -> None:
        """Match the table again: which columns, how wide, how tall, where."""
        if self._syncing:
            return                      # a width we set must not start a round
        self._syncing = True
        try:
            columns = self._table.columnCount()
            if not self._count or columns <= self._count:
                self.hide()
                return

            for column in range(columns):
                frozen = column < self._count
                self.setColumnHidden(column, not frozen)
                if frozen:
                    self.setColumnWidth(column, self._table.columnWidth(column))
            for row in range(self._table.rowCount()):
                self.setRowHeight(row, self._table.rowHeight(row))

            self.setItemDelegate(self._table.itemDelegate())
            self.verticalScrollBar().setValue(
                self._table.verticalScrollBar().value())
            self._place()
        finally:
            self._syncing = False

    # ------------------------------------------------------------------
    def _borrowed_style(self) -> str:
        """The table's own stylesheet, made to match this class as well.

        A rule naming ``QTableWidget`` does not match a ``QTableView``, even
        though one derives from the other, so the rules have to be renamed to
        the base class to reach both.

        The table's own box is then undone. This is a panel INSIDE the table,
        not a table of its own, and inheriting the outer margin pushed its
        header ten pixels below the one it is supposed to sit on top of.
        """
        return self._table.styleSheet().replace("QTableWidget", "QTableView") + """
            QTableView {
                margin: 0px;
                border: none;
                border-right: 1px solid #C9CBCF;
                border-radius: 0px;
            }
        """

    def _place(self) -> None:
        """Cover the table's leading columns, but only once it has scrolled."""
        if not self._count or self._table.horizontalScrollBar().value() <= 0:
            self.hide()
            return

        width = sum(self._table.columnWidth(c) for c in range(self._count))
        viewport = self._table.viewport()
        header = self._table.horizontalHeader()
        if width <= 0 or viewport.width() <= 0:
            self.hide()
            return

        # One extra pixel for the separator down the right edge, or the
        # last pinned column loses a pixel to it.
        self.setGeometry(viewport.x(), header.y(),
                         min(width + 1, viewport.width()),
                         header.height() + viewport.height())
        self.show()
        self.raise_()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        """Follow the table's size."""
        if event.type() == QEvent.Type.Resize and watched in (
                self._table, self._table.viewport()):
            self._place()
        return False
