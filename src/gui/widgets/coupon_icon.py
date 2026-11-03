"""The coupon icon: a disc with the wanted coupons taken out of it.

Straight from PDM's `COUPON_ICON_RECIPE.md`, constants and all, because the
whole point is that the two applications draw the same thing. Change nothing
here without changing it there.

THE HOLES ARE HOLES. One path holds the circle and the squares and is filled
with the even odd rule, so whatever is behind shows through and the icon still
reads on a coloured row. Drawing the squares on top would need them to match
every row colour this table paints.

Painted rather than loaded from an SVG: no Qt SVG plugin, nothing to ship beside
the executable, and it stays sharp at any row height.
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (QColor, QFontMetrics, QIcon, QPainter, QPainterPath,
                         QPen, QPixmap)

from src.data_access.qc_requests.coupon_columns import (ALL_FIVE, TOP_RIGHT,
                                                        normalize)

DISC = "#CBD5E1"
EDGE = "#94A3B8"
COUPON = 0.19       # coupon side, as a fraction of the diameter
OFFSET = 0.48       # coupon centre, as a fraction of the radius

# Beside the icon in a cell. They do not repeat the word coupon: the column it
# sits in already carries it.
ICON_LABELS = {TOP_RIGHT: "Top right", ALL_FIVE: "All five"}
# Room between the disc and the words.
GAP = 6


def coupon_centres(disc: QRectF, pattern: str) -> list:
    """Where the holes go, as fractions of the disc, so it holds at any size."""
    middle = disc.center()
    step = disc.width() / 2 * OFFSET
    if pattern == TOP_RIGHT:
        return [QPointF(middle.x() + step, middle.y() - step)]
    if pattern == ALL_FIVE:
        return [middle,
                QPointF(middle.x() - step, middle.y() - step),
                QPointF(middle.x() + step, middle.y() - step),
                QPointF(middle.x() - step, middle.y() + step),
                QPointF(middle.x() + step, middle.y() + step)]
    return []


def coupon_path(disc: QRectF, pattern: str) -> QPainterPath:
    """The disc with its coupons taken out, at any size."""
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.OddEvenFill)
    path.addEllipse(disc)
    side = disc.width() * COUPON
    for centre in coupon_centres(disc, pattern):
        path.addRect(QRectF(centre.x() - side / 2, centre.y() - side / 2,
                            side, side))
    return path


def draw_coupons(painter: QPainter, disc: QRectF, pattern: str,
                 show_empty: bool = False) -> None:
    """The icon alone. Nothing at all for a pattern that asks for nothing.

    ``show_empty`` draws the bare disc for that case instead, with no coupons
    taken out of it. Wanted where the three choices are being OFFERED, a picker
    with one blank row reads as a missing picture rather than as an answer; not
    wanted in a table, where a column of discs on rows nobody has asked for is
    noise. See PDM's recipe, which draws nothing in its column for the same
    reason.
    """
    if not show_empty and not coupon_centres(disc, normalize(pattern)):
        return
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(QPen(QColor(EDGE), 1))
    painter.setBrush(QColor(DISC))
    painter.drawPath(coupon_path(disc, normalize(pattern)))
    painter.restore()


def coupon_pixmap(pattern: str, size: int = 22) -> QPixmap:
    """The icon as a pixmap, for anywhere that wants a QIcon rather than paint.

    The bare disc is drawn for Nothing: this exists for the pickers, where all
    three choices have to look like choices.
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    draw_coupons(painter, QRectF(1, 1, size - 2, size - 2), pattern,
                 show_empty=True)
    painter.end()
    return pixmap


def coupon_icon(pattern: str, size: int = 22) -> QIcon:
    """The same, as a QIcon, for a combo box item."""
    return QIcon(coupon_pixmap(pattern, size))


def icon_label(pattern: str) -> str:
    """The words beside the icon, or "" where there is no icon."""
    return ICON_LABELS.get(normalize(pattern), "")


def labelled_width(metrics: QFontMetrics, pattern: str, size: int) -> int:
    """What the icon and its words take together, for sizing a column.

    Zero where nothing is asked for: an electrode nobody has requested draws
    nothing, so the column stays quiet in a list where most rows are ordinary.
    """
    label = icon_label(pattern)
    if not label:
        return 0
    return size + GAP + metrics.horizontalAdvance(label)


def draw_labelled(painter: QPainter, rect: QRectF, pattern: str,
                  size: int, colour: str = "#0F172A") -> None:
    """The icon and its words, the pair left aligned in ``rect``.

    A shape on its own has to be learnt; the words settle it at once, which is
    the reason PDM draws both and why this does too. The label is elided when
    the column is squeezed, so a narrow cell loses the words rather than
    spilling them over the next one.
    """
    label = icon_label(pattern)
    if not label:
        return
    painter.save()
    top = rect.top() + (rect.height() - size) / 2
    draw_coupons(painter, QRectF(rect.left(), top, size, size), pattern)
    text_left = rect.left() + size + GAP
    room = int(max(0.0, rect.right() - text_left))
    painter.setPen(QPen(QColor(colour)))
    painter.drawText(
        QRectF(text_left, rect.top(), room, rect.height()),
        int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
        painter.fontMetrics().elidedText(label, Qt.TextElideMode.ElideRight,
                                         room))
    painter.restore()
