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

from PyQt6.QtCore import Qt, QObject, QPointF, QRectF, QSize, QTimer
from PyQt6.QtGui import (QBrush, QColor, QCursor, QFont, QFontMetrics,
                         QPainter, QPen, QPolygonF)
from PyQt6.QtWidgets import (QApplication, QStyle, QStyledItemDelegate,
                             QStyleOptionViewItem)

from src.data_access.electrodes.electrode_columns import FAILED_CRACK
from .coupon_icon import GAP as COUPON_GAP, draw_coupons, icon_label

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


def blend(base: str, towards: str, amount: float) -> QColor:
    """``base`` moved ``amount`` of the way to ``towards``, 0 to 1.

    For animation: a colour that has to be worked out on every paint cannot be
    a constant, and mixing two named colours keeps the palette honest instead
    of inventing a third one nobody chose.
    """
    first, second = QColor(base), QColor(towards)
    share = max(0.0, min(1.0, amount))
    return QColor(
        round(first.red() + (second.red() - first.red()) * share),
        round(first.green() + (second.green() - first.green()) * share),
        round(first.blue() + (second.blue() - first.blue()) * share))


def _foreground_of(index) -> QColor:
    """The cell's ForegroundRole as a QColor, defaulting to the body text colour."""
    value = index.data(Qt.ItemDataRole.ForegroundRole)
    if value is None:
        return QColor(DEFAULT_TEXT)
    return value.color() if isinstance(value, QBrush) else QColor(value)


# How long a freshly changed row stays lit, and how often it steps down.
FLASH_MS = 1500
FLASH_TICK_MS = 60


class RowFlasher(QObject):
    """Lights rows on one table and fades them back out.

    The wash itself is painted by RowHighlight; this only drives it. Lives here
    rather than in a tab because two tabs want it now, and a second copy would
    drift from the first the moment either was tuned.
    """

    def __init__(self, highlight: RowHighlight, table, parent=None) -> None:
        super().__init__(parent)
        self._highlight = highlight
        self._table = table
        self._timer = QTimer(self)
        self._timer.setInterval(FLASH_TICK_MS)
        self._timer.timeout.connect(self._fade)

    def flash(self, rows) -> None:
        """Light these row indexes at full strength. Empty does nothing."""
        lit = {int(row): 1.0 for row in (rows or [])}
        if not lit:
            return
        self._highlight.flashing = lit
        self._timer.start()
        self._repaint()

    def _fade(self) -> None:
        """Step every lit row down, and stop once the last one is out."""
        step = FLASH_TICK_MS / FLASH_MS
        faded = {row: strength - step
                 for row, strength in self._highlight.flashing.items()
                 if strength - step > 0}
        self._highlight.flashing = faded
        if not faded:
            self._timer.stop()
        self._repaint()

    def _repaint(self) -> None:
        if self._table is not None:
            self._table.viewport().update()


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


class RowTintDelegate(QStyledItemDelegate):
    """An ordinary cell with a colour worked out on every paint behind it.

    For a row that has to breathe: the tint cannot be written into the item,
    because that is set once when the table is filled and this changes forty
    times a second.
    """

    def __init__(self, fill_for, parent=None) -> None:
        super().__init__(parent)
        self._fill_for = fill_for

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        colour = self._fill_for(index.row()) if self._fill_for else None
        if colour is None:
            super().paint(painter, option, index)
            return
        # The cell as the style draws it, the tint over its background, the
        # words over the tint. Handing the colour to the style instead does
        # nothing: a table with a QTableWidget::item rule paints its own.
        option = QStyleOptionViewItem(option)
        self.initStyleOption(option, index)
        widget = option.widget
        style = widget.style() if widget is not None else QApplication.style()
        text = str(option.text or "")
        option.text = ""
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem,
                          option, painter, widget)
        painter.fillRect(option.rect, colour)
        where = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText,
                                     option, widget)
        margin = style.pixelMetric(QStyle.PixelMetric.PM_FocusFrameHMargin,
                                   option, widget) + 1
        where = where.adjusted(margin, 0, -margin, 0)
        painter.save()
        painter.setFont(option.font)
        painter.setPen(QPen(_foreground_of(index)))
        painter.drawText(
            where, int(option.displayAlignment),
            QFontMetrics(option.font).elidedText(
                text, Qt.TextElideMode.ElideRight, where.width()))
        painter.restore()


class CouponTextDelegate(QStyledItemDelegate):
    """A cell's own text, with the coupon icon drawn after it.

    For a read only list, where the request state is words rather than a badge
    and there are no buttons to hit. The words come first: the column is read
    down as a column of states, and an icon in front of some of them indented
    those rows out of line with the rest.
    """

    SIZE = 22
    # What the icon adds to a column, for whoever is working out its width.
    EXTRA = SIZE + COUPON_GAP

    def __init__(self, coupons_for, parent=None, fill_for=None) -> None:
        super().__init__(parent)
        self._coupons_for = coupons_for
        # The same per-paint tint RowTintDelegate applies, so a breathing row
        # breathes across this column too rather than stopping at its edge.
        self._fill_for = fill_for

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        coupons = self._coupons_for(index.row()) if self._coupons_for else ""
        has_icon = bool(icon_label(coupons))
        tint = self._fill_for(index.row()) if self._fill_for else None
        if not has_icon and tint is None:
            super().paint(painter, option, index)
            return
        # The cell drawn as Qt would draw it but with the text held back, so
        # the background, the selection and above all the left edge of the
        # words are whatever the rows without an icon already use.
        option = QStyleOptionViewItem(option)
        self.initStyleOption(option, index)
        widget = option.widget
        style = widget.style() if widget is not None else QApplication.style()
        text = str(option.text or "")
        option.text = ""
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem,
                          option, painter, widget)
        # OVER what the style just drew, not handed to it. A table carrying a
        # QTableWidget::item rule paints its own background and ignores the
        # brush on the option, which is the same trap RowFillDelegate exists
        # for, so the tint goes on afterwards and the text goes on top.
        if tint is not None:
            painter.fillRect(option.rect, tint)
        where = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText,
                                     option, widget)
        # The same inset Qt applies inside that rect before it draws the
        # words. Without it the painted rows sat three pixels left of the
        # plain ones, which is small and completely visible in a column read
        # top to bottom.
        margin = style.pixelMetric(QStyle.PixelMetric.PM_FocusFrameHMargin,
                                   option, widget) + 1
        where = where.adjusted(margin, 0, -margin, 0)
        metrics = QFontMetrics(option.font)
        room = self.EXTRA if has_icon else 0
        drawn = metrics.elidedText(text, Qt.TextElideMode.ElideRight,
                                   max(0, where.width() - room))
        # THE WORDS AND THE ICON MOVE AS ONE BLOCK, placed the way the cell
        # asked its text to be placed. Drawing the words at the left of a
        # centred column put the rows carrying an icon out of line with the
        # rows that carry none, which is the thing this ordering was meant to
        # fix in the first place.
        width = metrics.horizontalAdvance(drawn) + room
        left = self._block_left(where, width, option.displayAlignment)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setFont(option.font)
        painter.setPen(QPen(_foreground_of(index)))
        painter.drawText(
            QRectF(left, float(where.top()), float(width), float(where.height())),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            drawn)
        # Immediately after the words rather than out at the cell's right
        # edge, the same as the icon beside a badge in the table next to this.
        if has_icon:
            size = float(self.SIZE)
            draw_coupons(painter,
                         QRectF(left + width - size,
                                QRectF(option.rect).center().y() - size / 2,
                                size, size), coupons)
        painter.restore()

    @staticmethod
    def _block_left(where, width: float, alignment) -> float:
        """Where the words and icon start, for the cell's own alignment."""
        if alignment & Qt.AlignmentFlag.AlignHCenter:
            return where.left() + max(0.0, where.width() - width) / 2
        if alignment & Qt.AlignmentFlag.AlignRight:
            return where.right() - width
        return float(where.left())


class RequestActionDelegate(QStyledItemDelegate):
    """The QC request column: a badge, and the buttons that change it.

    Painted rather than built from widgets. There are thirteen thousand rows
    and a QPushButton in each would cost thirteen thousand widgets to show
    three of them; the table already paints its badges, so the buttons are
    painted beside them and a click is worked out from where it landed.

    Three shapes, one per situation:

    * no request, so there is something to ask for. A quiet outline that wakes
      up under the cursor, because a column of thirteen thousand live buttons
      is a column nobody can read.
    * a request this application has to answer, so a tick and a cross beside
      the badge.
    * anything else, which is a badge and nothing to do.
    """

    # Matches StatusPillDelegate, so the two columns line up.
    RADIUS = 3
    PADDING_X = 14
    PADDING_Y = 4
    MIN_WIDTH = 96
    TEXT_CUSHION = 8

    # How far the answerable badge's fill travels towards its outline at
    # the top of the breath. Gentle on purpose: this runs all day beside
    # somebody working, and anything stronger becomes a thing to ignore.
    PULSE_DEPTH = 0.30

    ACTION_SIZE = 20
    ACTION_GAP = 6
    APPROVE = "approve"
    DECLINE = "decline"
    REQUEST = "request"

    ASK_LABEL = "Request for QC"
    # Asleep, and awake under the cursor. The resting grey was two shades
    # lighter and all but vanished on the coloured rows, worst on a failure's
    # crimson, where the button it has to offer was the hardest to read.
    ASK_IDLE = "#5F6B7A"
    ASK_LIVE = "#0B7268"
    # And it rests on its own surface rather than straight on the row. A button
    # cannot be legible on six different washes without one, and a faint fill
    # keeps it looking secondary where plain dark text would not.
    ASK_REST_FILL = QColor(255, 255, 255, 205)
    APPROVE_INK = "#0B7268"
    DECLINE_INK = "#8E1537"

    # The coupon disc drawn after the badge. Big enough that five holes are
    # five holes rather than a texture, which is the whole point of it.
    COUPON_SIZE = 24

    def __init__(self, style_for_value, parent=None,
                 answerable: str = "", highlight=None,
                 coupons_for=None, display_text=None,
                 animated: str = "", askable=None, pulse=None) -> None:
        """Args:
            style_for_value: maps a cell's text to the badge style.
            answerable: the one label that gets a tick and a cross beside it.
            highlight: the row wash, painted the same as the other columns.
            coupons_for: ``row -> coupons``, what this request asks be cut.
                Drawn immediately to the RIGHT of the badge, so it reads as
                belonging to it; an electrode nobody has asked for draws
                nothing, so the column stays quiet.
            display_text: the cell's text as it should read THIS INSTANT, for a
                badge that animates. Resolved on every paint rather than written
                into the cell, which is set once when the table is built.
            askable: ``row -> bool``, whether this one can still be asked
                for. False draws no button and answers no click, so a row
                nobody could fulfil offers nothing rather than offering
                something that would be refused.
            pulse: ``() -> float`` between 0 and 1, the breath the answerable
                badge is drawn at this instant. The one badge waiting on
                somebody here is the one worth noticing across a room.
        """
        super().__init__(parent)
        self._style_for_value = style_for_value
        self._answerable = answerable
        self._highlight = highlight
        self._coupons_for = coupons_for
        self._display_text = display_text
        self._askable = askable
        self._pulse = pulse
        # The one state whose badge grows dots, so only its pill reserves room
        # for them.
        self._animated = animated

    def _coupon_room(self, coupons: str) -> float:
        """What the icon takes to the right of the badge, 0 where there is none."""
        return (self.COUPON_SIZE + COUPON_GAP
                if icon_label(coupons) else 0.0)

    def _coupon_rect(self, pill: QRectF, coupons: str) -> QRectF:
        """The icon, immediately after the badge and centred on it."""
        size = float(self.COUPON_SIZE)
        return QRectF(pill.right() + COUPON_GAP,
                      pill.center().y() - size / 2, size, size)

    # -- what is where ----------------------------------------------------
    def can_ask(self, row=None) -> bool:
        """Whether an empty cell on this row offers anything."""
        if self._askable is None or row is None:
            return True
        return bool(self._askable(row))

    def action_at(self, rect: QRectF, point, text: str,
                  coupons: str = "", row=None) -> str:
        """Which button this click landed on, or "" for none.

        The same arithmetic as paint, so the two cannot drift: anything that
        moves a button moves its target with it. [row] is wanted only for the
        ask button, which some rows do not get.
        """
        label = str(text or "").strip()
        if not label:
            return (self.REQUEST if self.can_ask(row)
                    and self._ask_rect(rect).contains(point) else "")
        if label != self._answerable:
            return ""
        approve, decline = self._answer_rects(
            rect, self._widest(self._drawn(label), label), coupons)
        if approve.contains(point):
            return self.APPROVE
        if decline.contains(point):
            return self.DECLINE
        return ""

    def _ask_font(self) -> QFont:
        """The same font a badge uses.

        It was smaller for a while, on the theory that an offer should sit
        back behind the facts. Side by side it just looked like a badge that
        had come out wrong. Same size, same weight; the colour is what says it
        is a button rather than a state.
        """
        return self._font()

    def _ask_rect(self, rect: QRectF) -> QRectF:
        """The ask button, the size and shape a badge in this row would be."""
        return self._pill_rect(rect, self.ASK_LABEL)

    # What the dots can grow to. Named here rather than imported from the tab:
    # the delegate has to reserve the room, and a delegate that asked a tab how
    # long its animation gets would be the wrong way round.
    ANIMATION_ROOM = "..."

    def _widest(self, label: str, state: str) -> str:
        """The label at its longest frame, for measuring a badge that animates.

        The pill is sized to this and drawn with the frame showing now, so it
        does not resize three times a second, which reads as a flicker rather
        than as progress. Only the one state that animates is padded; widening
        every badge to fit dots they never grow would leave the column loose.
        """
        return (label + self.ANIMATION_ROOM if state and state == self._animated
                else label)

    def _drawn(self, text: str) -> str:
        """The label as painted, glyph and all, so widths agree everywhere."""
        style = self._style_for_value(text)
        glyph = getattr(style, "glyph", "") if style is not None else ""
        return f"{glyph}  {text}" if glyph else text

    def _answer_rects(self, rect: QRectF, label: str,
                      coupons: str = "") -> tuple:
        """The tick and the cross, after the badge and after its icon.

        The order along the row is badge, icon, cross, tick, so the icon stays
        next to the thing it describes and the buttons stay at the end where a
        thumb finds them.
        """
        size = float(self.ACTION_SIZE)
        icon = self._coupon_room(coupons)
        both = 2 * size + self.ACTION_GAP
        pill = self._pill_rect(rect, label,
                               reserve=icon + both + self.ACTION_GAP)
        top = pill.center().y() - size / 2
        left = pill.right() + icon + self.ACTION_GAP
        return (QRectF(left + size + self.ACTION_GAP, top, size, size),
                QRectF(left, top, size, size))

    def _pill_rect(self, rect: QRectF, label: str, reserve: float = 0.0) -> QRectF:
        metrics = QFontMetrics(self._font())
        width = min(max(float(self.MIN_WIDTH),
                        metrics.horizontalAdvance(label) + 2 * self.PADDING_X
                        + self.TEXT_CUSHION),
                    max(0.0, rect.width() - 8 - reserve))
        height = float(metrics.height() + 2 * self.PADDING_Y)
        return QRectF(rect.left() + (rect.width() - reserve - width) / 2,
                      rect.top() + (rect.height() - height) / 2, width, height)

    def _font(self) -> QFont:
        font = QFont()
        font.setBold(True)
        font.setPointSizeF(8.0)
        return font

    # -- painting ---------------------------------------------------------
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        text = str(index.data() or "").strip()
        background = _background_of(index)
        painter.save()
        painter.fillRect(option.rect, background if background
                         else option.palette.base())
        if self._highlight is not None:
            self._highlight.paint(painter, option, index)
        painter.restore()

        # The stored value decides the style and whether there are buttons;
        # the animated one is only what gets drawn. Looking the style up by the
        # animated text found nothing the moment a dot appeared.
        shown = self._display_text(text) if self._display_text else text
        coupons = (self._coupons_for(index.row()) if self._coupons_for
                   else "")
        rect = QRectF(option.rect)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setFont(self._font())
        if not text:
            # Nothing at all where there is nothing to ask for. An empty
            # cell says that better than a button that would be refused.
            if self.can_ask(index.row()):
                self._paint_ask(painter, rect, hovered)
        else:
            self._paint_badge(painter, rect, text, shown, coupons)
        painter.restore()

    def _paint_ask(self, painter: QPainter, rect: QRectF, hovered: bool) -> None:
        colour = QColor(self.ASK_LIVE if hovered else self.ASK_IDLE)
        button = self._ask_rect(rect)
        painter.setFont(self._ask_font())
        painter.setBrush(QBrush(QColor("#E6F6F6")) if hovered
                         else QBrush(self.ASK_REST_FILL))
        painter.setPen(QPen(colour, 1.2))
        painter.drawRoundedRect(button, self.RADIUS, self.RADIUS)
        painter.setPen(QPen(colour))
        # Elided, like every other badge here. Drawn whole it spilled out of
        # both ends of its own outline on a narrow screen, which read as a
        # broken button rather than as a squeezed one.
        painter.drawText(
            button, Qt.AlignmentFlag.AlignCenter,
            painter.fontMetrics().elidedText(
                self.ASK_LABEL, Qt.TextElideMode.ElideRight,
                int(button.width()) - self.PADDING_X))

    def _paint_badge(self, painter: QPainter, rect: QRectF, text: str,
                     shown: str | None = None, coupons: str = "") -> None:
        """``text`` is the stored state, ``shown`` what to draw for it."""
        shown = text if shown is None else shown
        style = self._style_for_value(text)
        if style is None:
            painter.setPen(QPen(QColor("#1F1F1F")))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, shown)
            return

        answerable = text == self._answerable
        # THE ICON DOES NOT MOVE THE BADGE OFF CENTRE, the same rule the failure
        # badges follow: a column of pills that do not line up reads worse than
        # one where a few have something beside them. Only the buttons, which
        # are pressed rather than read, take room off the centring.
        reserve = (2 * self.ACTION_SIZE + 2 * self.ACTION_GAP
                   + self._coupon_room(coupons) if answerable else 0.0)
        # Measured with the glyph, because that is what gets drawn. Measuring
        # the bare text left the tick taking room the elide had not budgeted
        # for, and "Approved, waiting" came out cut short.
        # Measured at the longest frame the animation reaches, so the pill does
        # not resize three times a second; drawn at the frame showing now.
        label = f"{style.glyph}  {text}" if style.glyph else text
        drawn = f"{style.glyph}  {shown}" if style.glyph else shown
        pill = self._pill_rect(rect, self._widest(label, text), reserve)
        # The answerable badge breathes: its own fill drifting towards its own
        # outline colour and back. Only the fill moves, so the words keep the
        # contrast they were chosen for and the badge never changes size.
        fill = QColor(style.background) if style.background else None
        if answerable and fill is not None and self._pulse is not None:
            fill = blend(style.background, style.color,
                         self.PULSE_DEPTH * float(self._pulse()))
        painter.setBrush(QBrush(fill) if fill is not None
                         else Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(style.color), 1.2))
        painter.drawRoundedRect(pill, self.RADIUS, self.RADIUS)
        painter.setPen(QPen(QColor(style.text or style.color)))
        metrics = painter.fontMetrics()
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter,
                         metrics.elidedText(drawn, Qt.TextElideMode.ElideRight,
                                            int(pill.width()) - 2 * self.PADDING_X))
        draw_coupons(painter, self._coupon_rect(pill, coupons), coupons)
        if answerable:
            approve, decline = self._answer_rects(
                rect, self._widest(label, text), coupons)
            under = self._cursor_in(rect)
            self._paint_tick(painter, approve,
                             under is not None and approve.contains(under))
            self._paint_cross(painter, decline,
                              under is not None and decline.contains(under))

    def _cursor_in(self, rect: QRectF):
        """Where the cursor is in this cell, or None if it is not in it.

        A delegate is told the cell is hovered but not whereabouts, and these
        are two buttons in one cell. Read from the view the same way the click
        is, so what lights up is what a click would hit.
        """
        view = self.parent()
        viewport = getattr(view, "viewport", None)
        if viewport is None:
            return None
        point = QPointF(viewport().mapFromGlobal(QCursor.pos()))
        return point if rect.contains(point) else None

    def _paint_tick(self, painter: QPainter, disc: QRectF,
                    hovered: bool = False) -> None:
        self._disc(painter, disc, self.APPROVE_INK, hovered)
        pen = QPen(QColor("#FFFFFF"))
        pen.setWidthF(max(1.6, disc.height() * 0.12))
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        left, top, size = disc.left(), disc.top(), disc.height()
        painter.drawPolyline(QPolygonF([
            QPointF(left + size * 0.28, top + size * 0.52),
            QPointF(left + size * 0.44, top + size * 0.68),
            QPointF(left + size * 0.74, top + size * 0.32)]))

    def _paint_cross(self, painter: QPainter, disc: QRectF,
                     hovered: bool = False) -> None:
        self._disc(painter, disc, self.DECLINE_INK, hovered)
        pen = QPen(QColor("#FFFFFF"))
        pen.setWidthF(max(1.6, disc.height() * 0.12))
        painter.setPen(pen)
        left, top, size = disc.left(), disc.top(), disc.height()
        painter.drawLine(QPointF(left + size * 0.32, top + size * 0.32),
                         QPointF(left + size * 0.68, top + size * 0.68))
        painter.drawLine(QPointF(left + size * 0.68, top + size * 0.32),
                         QPointF(left + size * 0.32, top + size * 0.68))

    @staticmethod
    def _disc(painter: QPainter, disc: QRectF, colour: str,
              hovered: bool = False) -> None:
        """The coloured circle, and what it does under the cursor.

        A flat disc reads as a status dot rather than as something to press, so
        the one being pointed at grows a ring and darkens. The ring is drawn
        outside the disc, so the two sit still while one of them lights up:
        resizing the disc itself made the pair jiggle as the cursor crossed.
        """
        ink = QColor(colour)
        if hovered:
            halo = QColor(ink)
            halo.setAlpha(70)
            painter.setPen(QPen(halo, 3.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(disc.adjusted(-2.5, -2.5, 2.5, 2.5))
            ink = ink.darker(125)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(ink))
        painter.drawEllipse(disc)

    def _known_labels(self) -> tuple:
        """Every label this column can hold, for measuring the column once.

        Qt samples the first thousand rows when it sizes a column, and the
        longest state is easily absent from those: 13,000 electrodes and a
        handful of requests.
        """
        return tuple(getattr(self, "_labels", ()) or (self._answerable,))

    def set_labels(self, labels) -> None:
        """Every state this column can show, so the width is not guessed."""
        self._labels = tuple(labels)

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:
        """Wide enough for the longest badge AND the two buttons beside it."""
        metrics = QFontMetrics(self._font())
        widest = max(metrics.horizontalAdvance(self._drawn(self._answerable or "")),
                     *(metrics.horizontalAdvance(self._drawn(label))
                       for label in self._known_labels()),
                     metrics.horizontalAdvance(self.ASK_LABEL),
                     self.MIN_WIDTH)
        needed = (widest + 2 * self.PADDING_X + self.TEXT_CUSHION
                  + 2 * self.ACTION_SIZE + 3 * self.ACTION_GAP + 16)
        return QSize(int(needed), max(metrics.height() + 2 * self.PADDING_Y + 6, 30))


class StatusPillDelegate(QStyledItemDelegate):
    """Draws the cell's text as a rounded, bordered badge instead of plain text."""

    # Barely softened. A radius anywhere near half the badge's height reads as
    # a capsule, which is the shape every generated interface reaches for;
    # just enough to take the hard corner off looks like something somebody
    # drew on purpose.
    RADIUS = 3
    PADDING_X = 14
    PADDING_Y = 4
    MIN_WIDTH = 96
    # Cushion between the text and the pill's inner edge. Without it the elide
    # budget lands exactly on the text advance and rounding trims a character.
    TEXT_CUSHION = 8

    # The failure badges. A filled disc each, the height of the pill's text,
    # drawn to the right of it.
    BADGE_SIZE = 17
    BADGE_GAP = 5
    # Room kept for badges whether or not this row has any, so every pill in
    # the column lands in the same place. Reserving only what a row needs put
    # the badged rows' pills a few pixels left of the rest, and a column of
    # badges that do not line up reads as worse than no badges.
    BADGE_SLOTS = 2
    BADGE_FILL = "#C81E1E"
    BADGE_INK = "#FFFFFF"

    def __init__(self, style_for_value: Callable[[str], Optional[PillStyle]],
                 parent=None, right_gutter: int = 0, widest_label: str = "",
                 highlight: Optional[RowHighlight] = None,
                 width_text: Optional[Callable[[str], str]] = None,
                 display_text: Optional[Callable[[str], str]] = None,
                 badges_for: Optional[Callable[[int], tuple]] = None) -> None:
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
        self._badges_for = badges_for

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
        badges = tuple(self._badges_for(index.row())) if self._badges_for else ()
        # THE BADGES DO NOT MOVE THE PILL. Taking their room off the centring
        # budget put a badged row's pill a few pixels left of every other one,
        # and a column of pills that do not line up reads worse than no badges
        # at all. The pill is centred on the cell and the badges hang off its
        # right; sizeHint is what makes sure there is room for them.
        available = max(0, option.rect.width() - 8 - self._right_gutter)
        measured = self._width_text(label) if self._width_text else label
        wanted = (metrics.horizontalAdvance(measured) + 2 * self.PADDING_X
                  + self.TEXT_CUSHION)
        width = min(max(self.MIN_WIDTH, wanted), available)
        height = metrics.height() + 2 * self.PADDING_Y

        # Centred in what's left after the gutter, so a hover control on the
        # right never lands on top of the badge.
        pill = QRectF(
            option.rect.left()
            + (option.rect.width() - self._right_gutter - width) / 2,
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
        self._paint_badges(painter, option, pill, badges)
        painter.restore()

    def _paint_badges(self, painter: QPainter, option: QStyleOptionViewItem,
                      pill: QRectF, badges: tuple) -> None:
        """A disc per reason, in the room reserved for them beside the pill.

        Drawn rather than loaded: the artwork is two shapes, and a file on disk
        would have to be found at runtime, shipped by the build and scaled for
        the zoom. The rest of this application draws its icons too.
        """
        if not badges:
            return
        size = self.BADGE_SIZE
        top = pill.center().y() - size / 2
        left = pill.right() + self.BADGE_GAP
        # Never past the cell's edge, however narrow the column is dragged.
        room = option.rect.right() - 2 - len(badges) * (size + self.BADGE_GAP)
        left = min(left, max(float(option.rect.left()), float(room)))
        for code in badges:
            disc = QRectF(left, top, size, size)
            painter.setBrush(QBrush(QColor(self.BADGE_FILL)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(disc)
            if code == FAILED_CRACK:
                self._paint_crack(painter, disc)
            else:
                self._paint_initials(painter, disc, code.upper())
            left += size + self.BADGE_GAP

    def _paint_initials(self, painter: QPainter, disc: QRectF, text: str) -> None:
        """Two letters, sized to the disc rather than to the table's font."""
        font = QFont(painter.font())
        font.setBold(True)
        font.setPointSizeF(max(5.5, disc.height() * 0.42))
        painter.setFont(font)
        painter.setPen(QPen(QColor(self.BADGE_INK)))
        painter.drawText(disc, Qt.AlignmentFlag.AlignCenter, text)

    def _paint_crack(self, painter: QPainter, disc: QRectF) -> None:
        """A split down the disc, which is what a cracked one looks like.

        A zig-zag rather than a straight line: a straight one reads as a slash,
        which already means "not applicable" everywhere else.
        """
        inset = disc.height() * 0.22
        top, bottom = disc.top() + inset, disc.bottom() - inset
        middle = disc.center().x()
        step = disc.width() * 0.16
        crack = QPolygonF([
            QPointF(middle - step * 0.4, top),
            QPointF(middle + step, top + (bottom - top) * 0.32),
            QPointF(middle - step, top + (bottom - top) * 0.62),
            QPointF(middle + step * 0.5, bottom),
        ])
        pen = QPen(QColor(self.BADGE_INK))
        pen.setWidthF(max(1.4, disc.height() * 0.11))
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPolyline(crack)

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
        # NO ROOM ADDED FOR THE BADGES. The column is already sized for its
        # longest label, and the rows that carry badges are never the rows
        # with that label: a failure is a short word and the long one is a
        # wait. So centring a short pill in a column sized for a long one
        # leaves more than enough either side, and padding on top only made
        # the whole column wider than it needed to be.
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
