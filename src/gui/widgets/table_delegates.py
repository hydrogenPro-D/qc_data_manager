"""Painted table decorations: full-row fills, a left accent bar, status pills.

Qt ignores a QTableWidgetItem's background brush as soon as the table carries a
``QTableWidget::item`` stylesheet rule — the stylesheet wins and the cell paints
its default colour. So rows that need a real fill are painted here instead,
which also gives us the rounded badge and the edge stripe that plain items
can't express. All of it is domain-neutral: the owning tab supplies colours
through callbacks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from PyQt6.QtCore import Qt, QPointF, QRectF, QSize
from PyQt6.QtGui import QColor, QPainter, QPen, QBrush, QFont, QFontMetrics
from PyQt6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

BAR_WIDTH = 4
DEFAULT_TEXT = "#1F1F1F"
_PADDING = 10


@dataclass
class PillStyle:
    """How one status value is drawn as a badge."""

    color: str                    # border, and the text unless `text` is set
    background: str = "#FFFFFF"
    glyph: str = ""               # optional leading mark, e.g. "✓" / "!"
    text: str | None = None       # override for a solid-filled badge


@dataclass
class RowHighlight:
    """Which row the pointer is over, and which one was clicked.

    An outline rather than a fill: rows here are already coloured by status, and
    a second wash of colour on top would fight with the one that carries meaning.
    Shared by every delegate on the table, since a row is drawn one cell at a
    time and each has to know it is part of an outlined row.
    """

    hover_row: int = -1
    picked_row: int = -1
    last_column: int = -1
    hover_color: str = "#9AD5D5"
    picked_color: str = "#14B8A6"
    hover_width: int = 1
    picked_width: int = 2
    # ``{row: how strongly it is still lit}``, 1.0 down to 0. Rows somebody
    # else changed, so a status moving under the operator is noticed without a
    # dialog interrupting them.
    flashing: dict = field(default_factory=dict)
    flash_color: str = "#14B8A6"

    def clear(self) -> None:
        self.hover_row = -1
        self.picked_row = -1
        self.flashing = {}

    def flash_fill(self, row: int) -> Optional[QColor]:
        """The wash over a row that just changed, or None once it has faded."""
        strength = self.flashing.get(row, 0.0)
        if strength <= 0:
            return None
        colour = QColor(self.flash_color)
        colour.setAlphaF(min(1.0, max(0.0, strength)) * 0.35)
        return colour

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        """The row's outline, as much of it as this cell owns."""
        row = index.row()
        wash = self.flash_fill(row)
        if wash is not None:
            painter.save()
            painter.fillRect(option.rect, wash)
            painter.restore()

        if row == self.picked_row:
            color, width = self.picked_color, self.picked_width
        elif row == self.hover_row:
            color, width = self.hover_color, self.hover_width
        else:
            return

        painter.save()
        pen = QPen(QColor(color), width)
        pen.setCosmetic(True)
        painter.setPen(pen)

        # Inset by half the pen, or half of a thick line falls outside the cell
        # and the neighbouring row paints over it.
        rect = QRectF(option.rect)
        inset = width / 2.0
        top, bottom = rect.top() + inset, rect.bottom() - inset
        painter.drawLine(QPointF(rect.left(), top), QPointF(rect.right(), top))
        painter.drawLine(QPointF(rect.left(), bottom), QPointF(rect.right(), bottom))
        # The ends of the row close it off; the joins between cells stay open.
        if index.column() == 0:
            painter.drawLine(QPointF(rect.left() + inset, top),
                             QPointF(rect.left() + inset, bottom))
        if index.column() == self.last_column:
            painter.drawLine(QPointF(rect.right() - inset, top),
                             QPointF(rect.right() - inset, bottom))
        painter.restore()


def _background_of(index) -> Optional[QColor]:
    """The cell's BackgroundRole as a QColor, if the tab set one."""
    value = index.data(Qt.ItemDataRole.BackgroundRole)
    if value is None:
        return None
    return value.color() if isinstance(value, QBrush) else QColor(value)


def _foreground_of(index) -> QColor:
    """The cell's ForegroundRole as a QColor, defaulting to the body text colour."""
    value = index.data(Qt.ItemDataRole.ForegroundRole)
    if value is None:
        return QColor(DEFAULT_TEXT)
    return value.color() if isinstance(value, QBrush) else QColor(value)


class RowHighlightDelegate(QStyledItemDelegate):
    """Ordinary cell painting, with the row outline drawn over it.

    For tables that are happy with how Qt draws their cells and only want the
    hover and click outline. Tables that paint their own fills take a
    ``RowHighlight`` directly instead.
    """

    def __init__(self, highlight: RowHighlight, parent=None) -> None:
        super().__init__(parent)
        self._highlight = highlight

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        super().paint(painter, option, index)
        self._highlight.paint(painter, option, index)


class RowFillDelegate(QStyledItemDelegate):
    """Fills the cell with its BackgroundRole colour, then draws the text.

    Optionally paints a coloured bar down the left edge of one column, giving
    each row a status stripe without spending a whole column on it.
    """

    def __init__(
        self,
        accent_for_row: Optional[Callable[[int], Optional[str]]] = None,
        accent_column: int = 0,
        parent=None,
        highlight: Optional[RowHighlight] = None,
    ) -> None:
        super().__init__(parent)
        self._accent_for_row = accent_for_row
        self._accent_column = accent_column
        self._highlight = highlight

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        painter.save()

        background = _background_of(index)
        painter.fillRect(option.rect, background if background else option.palette.base())
        # Under the stripe and the text: the outline marks the row, it does not
        # replace what the row says.
        if self._highlight is not None:
            self._highlight.paint(painter, option, index)

        left_padding = _PADDING
        if self._accent_for_row is not None and index.column() == self._accent_column:
            color = self._accent_for_row(index.row())
            if color:
                painter.fillRect(
                    option.rect.left(), option.rect.top(), BAR_WIDTH, option.rect.height(),
                    QColor(color))
            left_padding += BAR_WIDTH

        text = str(index.data() or "")
        if text:
            font = index.data(Qt.ItemDataRole.FontRole)
            painter.setFont(font if isinstance(font, QFont) else option.font)
            painter.setPen(QPen(_foreground_of(index)))

            alignment = index.data(Qt.ItemDataRole.TextAlignmentRole)
            alignment = int(alignment) if alignment is not None else int(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

            rect = option.rect.adjusted(left_padding, 0, -_PADDING, 0)
            painter.drawText(rect, alignment, painter.fontMetrics().elidedText(
                text, Qt.TextElideMode.ElideRight, rect.width()))

        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:
        """Report the padding this delegate actually draws with.

        The default hint measures the text alone, so content sizing produced
        columns a couple of dozen pixels too narrow and the text came out
        elided even with room to spare.
        """
        hint = super().sizeHint(option, index)
        padding = 2 * _PADDING
        if self._accent_for_row is not None and index.column() == self._accent_column:
            padding += BAR_WIDTH
        return QSize(hint.width() + padding, max(hint.height(), 28))


class LoadingCellDelegate(RowFillDelegate):
    """A row fill that shows a pulsing bar where a value has not arrived yet.

    A column that is genuinely still loading is worth showing as loading. Left
    out until its data lands, it appears from nowhere a second or two after the
    window opens, which reads as a glitch rather than as progress.

    It extends the row fill rather than replacing it so the column keeps the
    hover outline and the poll flash like every other one.
    """

    BAR_HEIGHT = 8
    BAR_INSET = 14

    def __init__(self, pulse: Callable[[], float], *args, **kwargs) -> None:
        """``pulse`` gives 0 to 1, how bright the bar is now, or below zero for
        "nothing is pending, draw the cell normally"."""
        super().__init__(*args, **kwargs)
        self._pulse = pulse

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        super().paint(painter, option, index)
        strength = self._pulse()
        if strength < 0 or str(index.data() or "").strip():
            return

        painter.save()
        colour = QColor(self._placeholder_color)
        colour.setAlphaF(0.16 + 0.24 * min(1.0, max(0.0, strength)))
        bar = QRectF(option.rect.left() + self.BAR_INSET,
                     option.rect.center().y() - self.BAR_HEIGHT / 2,
                     max(0.0, option.rect.width() - 2 * self.BAR_INSET),
                     float(self.BAR_HEIGHT))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(colour))
        painter.drawRoundedRect(bar, 3, 3)
        painter.restore()

    _placeholder_color = "#94A3B8"


class StatusPillDelegate(QStyledItemDelegate):
    """Draws the cell's text as a rounded, bordered badge instead of plain text."""

    # Softened corners, not a capsule: at the badge's height a radius near
    # half of it reads as a pill and crowds the text at the ends.
    RADIUS = 6
    PADDING_X = 14
    PADDING_Y = 4
    MIN_WIDTH = 96
    # Cushion between the text and the pill's inner edge. Without it the elide
    # budget lands exactly on the text advance and rounding trims a character.
    TEXT_CUSHION = 8

    def __init__(self, style_for_value: Callable[[str], Optional[PillStyle]],
                 parent=None, right_gutter: int = 0, widest_label: str = "",
                 highlight: Optional[RowHighlight] = None,
                 width_text: Optional[Callable[[str], str]] = None,
                 display_text: Optional[Callable[[str], str]] = None) -> None:
        """Args:
            style_for_value: Maps a cell's text to the badge style to draw.
            right_gutter: Pixels kept clear on the right of the cell, room for a
                hover control that would otherwise sit on the badge.
            widest_label: The longest label this column can ever hold. Qt only
                samples the first ~1000 rows when content sizing, so a long
                badge further down would otherwise never be measured and the
                column would come out too narrow for it.
            width_text: Maps a label to the text its badge should be *measured*
                against, when that differs from what is drawn. An animated
                label is measured at its longest frame, or the badge would
                resize on every tick and read as a flicker.
            display_text: Maps a cell's value to what the badge actually draws,
                resolved on every paint. An animation has to live here rather
                than in the cell: the cell's text is written once when the table
                is built, and repainting it would draw the same thing forever.
        """
        super().__init__(parent)
        self._style_for_value = style_for_value
        self._right_gutter = right_gutter
        self._widest_label = widest_label
        self._highlight = highlight
        self._width_text = width_text
        self._display_text = display_text

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        text = str(index.data() or "").strip()
        if self._display_text is not None:
            text = self._display_text(text)
        style = self._style_for_value(text)

        # Row fill underneath, whether or not this value has a badge.
        background = _background_of(index)
        painter.save()
        painter.fillRect(option.rect, background if background else option.palette.base())
        if self._highlight is not None:
            self._highlight.paint(painter, option, index)
        painter.restore()

        if not text or style is None:
            return

        label = f"{style.glyph}  {text}" if style.glyph else text
        font = QFont(option.font)
        font.setBold(True)
        font.setPointSizeF(max(7.5, option.font.pointSizeF() - 0.5))

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setFont(font)

        metrics = painter.fontMetrics()
        available = max(0, option.rect.width() - 8 - self._right_gutter)
        measured = self._width_text(label) if self._width_text else label
        wanted = (metrics.horizontalAdvance(measured) + 2 * self.PADDING_X
                  + self.TEXT_CUSHION)
        width = min(max(self.MIN_WIDTH, wanted), available)
        height = metrics.height() + 2 * self.PADDING_Y

        # Centred in what's left after the gutter, so a hover control on the
        # right never lands on top of the badge.
        pill = QRectF(
            option.rect.left() + (option.rect.width() - self._right_gutter - width) / 2,
            option.rect.top() + (option.rect.height() - height) / 2,
            width, height,
        )

        painter.setBrush(QBrush(QColor(style.background)))
        painter.setPen(QPen(QColor(style.color), 1.2))
        painter.drawRoundedRect(pill, self.RADIUS, self.RADIUS)

        painter.setPen(QPen(QColor(style.text or style.color)))
        painter.drawText(
            pill, Qt.AlignmentFlag.AlignCenter,
            metrics.elidedText(label, Qt.TextElideMode.ElideRight,
                               int(width) - 2 * self.PADDING_X))
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:
        """Wide enough for the badge *and* the gutter.

        Measured with the same bold, glyph-prefixed label that ``paint`` draws:
        the default hint uses the regular font and the bare text, which comes
        out narrower than the badge and leaves it elided.
        """
        text = str(index.data() or "").strip()
        style = self._style_for_value(text)
        if not text or style is None:
            return super().sizeHint(option, index)

        metrics = QFontMetrics(self._label_font(option))
        needed = max(self._label_width(metrics, self._label(style, text)),
                     self._label_width(metrics, self._widest_label),
                     self.MIN_WIDTH + 16 + self._right_gutter)
        height = metrics.height() + 2 * self.PADDING_Y + 6
        return QSize(needed, max(height, 30))

    def _label_font(self, option: QStyleOptionViewItem) -> QFont:
        """The font ``paint`` draws the badge label with."""
        font = QFont(option.font)
        font.setBold(True)
        font.setPointSizeF(max(7.5, option.font.pointSizeF() - 0.5))
        return font

    @staticmethod
    def _label(style: PillStyle, text: str) -> str:
        return f"{style.glyph}  {text}" if style.glyph else text

    def _label_width(self, metrics: QFontMetrics, label: str) -> int:
        """Cell width a label needs: the pill, the gutter and the 8px margin
        ``paint`` keeps between pill and cell edge, plus a little slack so
        rounding can't shave the last character off."""
        if not label:
            return 0
        return (metrics.horizontalAdvance(label) + 2 * self.PADDING_X
                + self.TEXT_CUSHION + self._right_gutter + 8 + 6)


class TickDelegate(QStyledItemDelegate):
    """Draws a row of the pick list: its tick, its hover tint and its text.

    All three by hand. A QListWidget carrying a ``::item`` stylesheet rule
    hands its rows to the stylesheet style, and that drew the decoration only
    for the row under the pointer: a ticked electrode looked unticked the
    moment the pointer moved off it. Qt's own check indicator is no better,
    having no glyph of its own for a stylesheet to colour.
    """

    SIZE = 15           # the tick, square
    LEFT = 8            # from the row's left edge to the tick
    GAP = 8             # from the tick to the text

    def __init__(self, ticked, unticked, parent=None) -> None:
        super().__init__(parent)
        self._ticked = ticked.pixmap(self.SIZE, self.SIZE)
        self._unticked = unticked.pixmap(self.SIZE, self.SIZE)

    def paint(self, painter, option, index) -> None:
        painter.save()
        if option.state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(option.rect, QColor("#F3FAFA"))
        else:
            painter.fillRect(option.rect, QColor("#FFFFFF"))

        state = index.data(Qt.ItemDataRole.CheckStateRole)
        ticked = state == Qt.CheckState.Checked or state == Qt.CheckState.Checked.value
        top = option.rect.top() + (option.rect.height() - self.SIZE) // 2
        painter.drawPixmap(option.rect.left() + self.LEFT, top,
                           self._ticked if ticked else self._unticked)

        painter.setPen(QPen(QColor("#1F1F1F")))
        text = option.rect.adjusted(self.LEFT + self.SIZE + self.GAP, 0, -6, 0)
        painter.drawText(text, int(Qt.AlignmentFlag.AlignLeft
                                   | Qt.AlignmentFlag.AlignVCenter),
                         str(index.data() or ""))
        painter.restore()

    def sizeHint(self, option, index) -> QSize:
        hint = super().sizeHint(option, index)
        return QSize(hint.width() + self.LEFT + self.SIZE + self.GAP,
                     max(hint.height(), 26))
