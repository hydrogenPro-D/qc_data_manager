"""Filter bar: dropdowns + a date filter + search, above a filtered table.

Domain-neutral — the owning tab declares which dropdowns exist and fills their
options from the data; this widget only reports the current selection. Every
filter is independent, so they combine.

The date filter has three modes: everything, a single month picked from the
months present in the data, or an explicit from/to range. Whichever is active,
``date_bounds()`` returns one inclusive ``(start, end)`` pair (``None`` for open
ends), so callers never branch on the mode.
"""

from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass

from PyQt6.QtCore import Qt, QDate, QEvent, QPointF, QTimer, pyqtSignal
from PyQt6.QtGui import (QFontMetrics, QMouseEvent, QStandardItem,
                         QStandardItemModel)
import qtawesome as qta
from PyQt6.QtWidgets import (
    QApplication, QCalendarWidget, QWidget, QHBoxLayout, QVBoxLayout, QLabel,
    QPushButton,
    QLineEdit, QStackedWidget, QComboBox,
)

from ..styles.button_styles import QUIET_BUTTON_STYLE
from .no_scroll_inputs import NoScrollComboBox, NoScrollDateEdit
from .table_delegates import TickDelegate

_ALL = "__all__"

# FIVE, NOT FOURTEEN. At the height of these fields a radius near half of it
# makes every one of them a capsule, which is the shape a generated interface
# reaches for and it showed. Enough to take the hard corner off and no more.
_COMBO_STYLE = """
    QComboBox {
        background-color: #FFFFFF;
        border: 1px solid #D1D5DB;
        border-radius: 5px;
        padding: 4px 8px;
        font-family: 'Segoe UI', Arial, sans-serif;
        font-size: 12px;
        font-weight: 600;
        color: #374151;
        /* Only wide enough to be clickable. What it actually needs is set per
           control from the text on display, see CheckableFilterCombo. */
        min-width: 34px;
    }
    QComboBox:hover { border: 1px solid #9CA3AF; }
    QComboBox:focus { border: 1.5px solid #55BDBD; }
    /* Beside the right padding, not inside it: whatever this takes is width
       the text cannot use, which is why it is counted in CLOSED_PADDING. */
    QComboBox::drop-down {
        subcontrol-origin: padding;
        subcontrol-position: center right;
        width: 18px;
        border: none;
    }
    /* No arrow asset ships with the app, so draw one out of borders. */
    QComboBox::down-arrow {
        width: 0px; height: 0px;
        border-left: 4px solid transparent;
        border-right: 4px solid transparent;
        border-top: 5px solid #6B7280;
        margin-right: 5px;
    }
    QComboBox::down-arrow:on { top: 1px; }
    QComboBox QAbstractItemView {
        border: 1px solid #E1E1E1;
        background-color: #FFFFFF;
        selection-background-color: #E6F6F6;
        selection-color: #1F1F1F;
        outline: none;
    }
"""

_MODE_BUTTON_STYLE = """
    QPushButton {
        background-color: #FFFFFF;
        color: #4B5563;
        border: 1px solid #D1D5DB;
        border-radius: 5px;
        padding: 4px 12px;
        font-family: 'Segoe UI', Arial, sans-serif;
        font-size: 12px;
        font-weight: 600;
    }
    QPushButton:hover { border: 1px solid #9CA3AF; }
    QPushButton:checked {
        background-color: #E6F6F6;
        color: #0F766E;
        border: 1.5px solid #55BDBD;
    }
"""

_DATE_EDIT_STYLE = """
    QDateEdit {
        background-color: #FFFFFF;
        border: 1px solid #D1D5DB;
        border-radius: 5px;
        /* Room on the right for the calendar icon, which sits over the field. */
        padding: 4px 26px 4px 10px;
        font-family: 'Segoe UI', Arial, sans-serif;
        font-size: 12px;
        color: #374151;
        min-width: 104px;
    }
    QDateEdit:hover { border: 1px solid #9CA3AF; }
    QDateEdit:focus { border: 1.5px solid #55BDBD; }
    /* Kept, and kept wide enough to click, because it is the only thing Qt
       opens the popup from. The arrow is dropped: the icon replaces it. */
    QDateEdit::drop-down {
        subcontrol-origin: padding;
        subcontrol-position: center right;
        border: none;
        background: transparent;
        width: 24px;
    }
    QDateEdit::down-arrow { image: none; width: 0px; height: 0px; }
"""

# The popup. Without this its header renders as two blank buttons between the
# arrows, so the calendar shows a month without saying which one.
_CALENDAR_STYLE = """
    QCalendarWidget QWidget#qt_calendar_navigationbar {
        background-color: #F1F5F5;
        border-bottom: 1px solid #E1E7E7;
    }
    QCalendarWidget QToolButton {
        color: #17302D;
        font-family: 'Segoe UI', Arial, sans-serif;
        font-size: 12px;
        font-weight: 600;
        background-color: transparent;
        border: none;
        padding: 4px 10px;
        margin: 2px;
    }
    QCalendarWidget QToolButton:hover { background-color: #E0F0EE; }
    QCalendarWidget QToolButton::menu-indicator { image: none; width: 0px; }
    QCalendarWidget QSpinBox {
        font-size: 12px;
        color: #17302D;
        background-color: #FFFFFF;
        border: 1px solid #D1D5DB;
    }
    QCalendarWidget QAbstractItemView:enabled {
        font-family: 'Segoe UI', Arial, sans-serif;
        font-size: 12px;
        color: #1F2937;
        background-color: #FFFFFF;
        selection-background-color: #55BDBD;
        selection-color: #FFFFFF;
        outline: none;
    }
    QCalendarWidget QAbstractItemView:disabled { color: #B6C2C0; }
"""

_SEARCH_STYLE = """
    QLineEdit {
        background-color: #FFFFFF;
        border: 1px solid #E1E1E1;
        border-radius: 5px;
        padding: 5px 12px;
        font-family: 'Segoe UI', Arial, sans-serif;
        font-size: 12px;
        color: #1F1F1F;
        min-width: 112px;
    }
    QLineEdit:focus { border: 1.5px solid #55BDBD; }
"""

_LABEL_STYLE = "color: #6B6B6B; font-size: 12px; font-weight: 600;"

# The shared quiet button, which this one is the original of.
_CLEAR_BUTTON_STYLE = QUIET_BUTTON_STYLE

# The search box reserves room for a phrase; a marking is six characters, and
# the search box's min-width would otherwise decide the range fields' minimum
# and keep the whole strip wider than the window.
_RANGE_INPUT_STYLE = _SEARCH_STYLE.replace("min-width: 112px;", "min-width: 30px;")


@dataclass
class FilterDropdown:
    """One value-filter dropdown. Options are filled from the data later."""

    key: str
    label: str
    all_label: str = "All"


class DatePickerEdit(NoScrollDateEdit):
    """A date field that opens its calendar wherever you click.

    Qt only opens the popup from the drop-down strip at the right edge, around
    twenty pixels of a hundred pixel field, which on a tablet is a target
    nobody hits. A press anywhere is forwarded there instead.

    The icon is a child widget rather than a stylesheet image: a stylesheet
    needs a file on disk, and this application draws its icons from a font.
    """

    ICON = 14

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._forwarding = False
        self.setCalendarPopup(True)
        self.setStyleSheet(_DATE_EDIT_STYLE)

        self._icon = QLabel(self)
        self._icon.setPixmap(
            qta.icon("fa6s.calendar-days", color="#6B7280").pixmap(self.ICON, self.ICON))
        # Transparent to the mouse, so a click on it reaches the field below and
        # is forwarded like any other.
        self._icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._icon.resize(self.ICON, self.ICON)

        calendar = self.calendarWidget()
        if calendar is not None:
            calendar.setStyleSheet(_CALENDAR_STYLE)
            calendar.setVerticalHeaderFormat(
                QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)

    def _place_icon(self) -> None:
        self._icon.move(self.width() - self.ICON - 9,
                        (self.height() - self.ICON) // 2)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        self._place_icon()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        """Also placed here: the date range page starts hidden, and a widget
        that is never shown is never resized either."""
        super().showEvent(event)
        self._place_icon()

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        """Send the press to the drop-down, wherever it actually landed."""
        if self._forwarding or not self.calendarPopup():
            super().mousePressEvent(event)
            return
        self._forwarding = True
        try:
            at = QPointF(self.width() - 12, self.height() / 2)
            for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
                QApplication.sendEvent(self, QMouseEvent(
                    kind, at, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.NoModifier))
        finally:
            self._forwarding = False


class CheckableFilterCombo(NoScrollComboBox):
    """A dropdown whose options are ticked rather than picked one at a time.

    Nothing ticked means everything, which is what the old single-value "All"
    entry meant and is the state an operator expects to start in.

    ROW 0 IS THE LABEL, not an option. A combo shows whichever row is current,
    so one hidden row holds the summary text and stays current no matter what
    is ticked below it.

    The popup does not close on a tick. Choosing three vats would otherwise be
    three trips through the list.
    """

    changed = pyqtSignal()

    # What a row costs besides its text: the tick and its margins, as
    # TickDelegate lays them out, plus the right margin and room for a
    # scrollbar so the last character is never clipped by one appearing.
    POPUP_PADDING = (TickDelegate.LEFT + TickDelegate.SIZE + TickDelegate.GAP
                     + 6 + 26)
    # And on the closed combo, every pixel the text cannot have: the 8px
    # padding either side from _COMBO_STYLE, the 18px arrow strip, which sits
    # beside the padding rather than inside it, the border, and a little slack
    # so nothing is elided.
    CLOSED_PADDING = 8 + 8 + 18 + 2 + 4

    def __init__(self, all_label: str = "All", parent=None) -> None:
        super().__init__(parent)
        self._all_label = all_label
        self._ticked = qta.icon("fa6s.square-check", color="#0C7C73")
        self._unticked = qta.icon("fa6.square", color="#C9CDD3")

        self.setModel(QStandardItemModel(self))
        self.setItemDelegate(TickDelegate(self._ticked, self._unticked, self))
        summary = QStandardItem(all_label)
        summary.setFlags(Qt.ItemFlag.NoItemFlags)
        self.model().appendRow(summary)
        self.view().setRowHidden(0, True)
        self.view().viewport().installEventFilter(self)
        self._show_summary()

    def sizeHint(self):  # noqa: N802 (Qt naming)
        """As wide as the text on display, not as wide as the longest option.

        A combo's own hint fits its widest item, so the Status filter reserved
        room for "Tested and Released" while showing the word "All", and six of
        them together pushed the bar past the window. The popup is widened
        separately by _fit_popup, so the closed control never needed that room.
        """
        hint = super().sizeHint()
        hint.setWidth(self.minimumWidth())
        return hint

    def minimumSizeHint(self):  # noqa: N802 (Qt naming)
        """Same answer, or a layout would fall back to the widest item again."""
        return self.sizeHint()

    # ---- the options ---------------------------------------------------
    def set_options(self, options: list) -> None:
        """Refill from the data, keeping whatever is still on offer ticked."""
        kept = set(self.checked())
        model = self.model()
        model.removeRows(1, model.rowCount() - 1)
        for option in options:
            item = QStandardItem(str(option))
            item.setCheckState(Qt.CheckState.Checked if str(option) in kept
                               else Qt.CheckState.Unchecked)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            model.appendRow(item)
        self._fit_popup()
        self._show_summary()

    def _fit_popup(self) -> None:
        """Widen the popup to whatever its longest option needs.

        The combo itself is sized by the filter bar and stays narrow, and Qt
        gives the popup the same width, which cut longer electrode types off
        halfway. Only the list grows; the closed combo is untouched.
        """
        model = self.model()
        metrics = QFontMetrics(self.view().font())
        widest = max((metrics.horizontalAdvance(model.item(row).text())
                      for row in range(1, model.rowCount())), default=0)
        self.view().setMinimumWidth(widest + self.POPUP_PADDING)

    def checked(self) -> list:
        """Everything ticked, in the order it is offered."""
        model = self.model()
        return [model.item(row).text() for row in range(1, model.rowCount())
                if model.item(row).checkState() == Qt.CheckState.Checked]

    def set_checked(self, values) -> None:
        """Tick exactly these, and say so: setting a filter is a filter change
        whether a person clicked it or the code did."""
        wanted = {str(v) for v in values}
        model = self.model()
        for row in range(1, model.rowCount()):
            item = model.item(row)
            item.setCheckState(Qt.CheckState.Checked if item.text() in wanted
                               else Qt.CheckState.Unchecked)
        self._show_summary()
        self.changed.emit()

    # ---- ticking -------------------------------------------------------
    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt naming)
        """Toggle on release and swallow it, so the popup stays open."""
        if obj is self.view().viewport() and event.type() == QEvent.Type.MouseButtonRelease:
            index = self.view().indexAt(event.position().toPoint())
            if index.isValid() and index.row() > 0:
                item = self.model().item(index.row())
                item.setCheckState(
                    Qt.CheckState.Unchecked
                    if item.checkState() == Qt.CheckState.Checked
                    else Qt.CheckState.Checked)
                self._show_summary()
                self._paint_now()
                self.changed.emit()
                return True
        return super().eventFilter(obj, event)

    def _paint_now(self) -> None:
        """Draw the tick immediately, before whatever the change sets off.

        ``update()`` only asks for a repaint, and the request waits in the
        queue behind the filtering the change is about to run. On a big table
        that is long enough to see: the box the operator just clicked stays
        blank until the rows have finished, which reads as a missed click
        rather than as a slow one. ``repaint()`` paints there and then.

        The popup is its own window, so it is painted separately from the
        closed control underneath it.
        """
        self.view().viewport().repaint()
        self.repaint()

    def _show_summary(self) -> None:
        """Row 0 says what is ticked, since that is the row on display."""
        chosen = self.checked()
        if not chosen:
            text = self._all_label
        elif len(chosen) == 1:
            text = chosen[0]
        else:
            text = f"{len(chosen)} chosen"
        self.model().item(0).setText(text)
        self.setCurrentIndex(0)
        self._fit_closed()

    def _fit_closed(self) -> None:
        """Width enough for the text on display, and no more.

        A combo sizes itself on first show and then keeps that width, so a
        longer value picked later was elided. Grown to fit what it displays now,
        which also shrinks back when the filter is cleared.
        """
        metrics = QFontMetrics(self.font())
        text = self.model().item(0).text()
        self.setMinimumWidth(metrics.horizontalAdvance(text) + self.CLOSED_PADDING)
        self.updateGeometry()

    def changeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        """Measure again when the font changes, not only when the text does.

        The caller applies the stylesheet after constructing this, and the
        stylesheet is what sets the font it paints with. Measured once in the
        constructor, every combo was sized against whatever font it happened to
        have at the time, and the ones the owning tab never refilled kept it and
        rendered "All" as "Al".
        """
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._fit_closed()


class DateRangeWidget(QWidget):
    """A plain from/to range over a second date column.

    Deliberately not a DateFilterWidget: no All or Month, and each end is
    optional. An end left on "Any" does not bound that side, and with both on
    "Any" the filter is off, which is how it stays out of the way until
    somebody wants it. Its own caption is what tells it apart from the date
    filter beside it.
    """

    changed = pyqtSignal()

    def __init__(self, caption: str = "Date", parent=None) -> None:
        super().__init__(parent)
        # Which field each calendar popup belongs to, so an unset one can be
        # opened on a useful month. Keyed by the popup, which is what raises
        # the event.
        self._calendars: dict = {}
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        title = QLabel(caption)
        title.setStyleSheet(_LABEL_STYLE)
        layout.addWidget(title, 0)

        self.start_edit = self._edit()
        self.end_edit = self._edit()
        to_label = QLabel("to")
        to_label.setStyleSheet(_LABEL_STYLE)

        clear = QPushButton("Any")
        clear.setCursor(Qt.CursorShape.PointingHandCursor)
        clear.setStyleSheet(_MODE_BUTTON_STYLE)
        clear.setToolTip("Stop filtering on this date")
        clear.clicked.connect(self.reset)

        layout.addWidget(self.start_edit, 0)
        layout.addWidget(to_label, 0)
        layout.addWidget(self.end_edit, 0)
        layout.addWidget(clear, 0)
        self.setLayout(layout)

    def _edit(self) -> DatePickerEdit:
        """One end of the range, showing "Any" until it is set.

        The minimum date doubles as the empty value, which is Qt's way of
        giving a date field a blank state at all.
        """
        edit = DatePickerEdit()
        edit.setDisplayFormat("dd.MM.yyyy")
        edit.setMinimumDate(QDate(1900, 1, 1))
        edit.setSpecialValueText("Any")
        edit.setDate(edit.minimumDate())
        edit.dateChanged.connect(lambda _d: self.changed.emit())
        calendar = edit.calendarWidget()
        if calendar is not None:
            self._calendars[calendar] = edit
            calendar.installEventFilter(self)
        return edit

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt naming)
        """Open an unset field on this month rather than on the year 1900.

        The empty value IS the minimum date, so left alone Qt opens the popup
        four centuries ago and every date is a scroll away.
        """
        if event.type() == QEvent.Type.Show and obj in self._calendars:
            edit = self._calendars[obj]
            if edit.date() == edit.minimumDate():
                today = QDate.currentDate()
                obj.setCurrentPage(today.year(), today.month())
        return super().eventFilter(obj, event)

    def range(self) -> tuple:
        """``(start, end)`` as dates, either side None when left on Any."""
        def value(edit: DatePickerEdit):
            date = edit.date()
            return None if date == edit.minimumDate() else date.toPyDate()
        return value(self.start_edit), value(self.end_edit)

    def is_set(self) -> bool:
        """Whether this filter is bounding anything at all."""
        return any(end is not None for end in self.range())

    def reset(self) -> None:
        """Both ends back to Any, and one redraw for the pair."""
        for edit in (self.start_edit, self.end_edit):
            edit.blockSignals(True)
            edit.setDate(edit.minimumDate())
            edit.blockSignals(False)
        self.changed.emit()


class CurrentPageStack(QStackedWidget):
    """As wide as the page it is showing, not as its widest page.

    A ``QStackedWidget`` reserves room for every page at once, so in All mode
    the date filter was holding the width of two date pickers and the word
    between them, none of which is on screen. On a laptop that invisible 290
    pixels is the difference between the filter strip fitting and scrolling
    sideways with the search box off the end of it.
    """

    def sizeHint(self):  # noqa: N802 (Qt naming)
        page = self.currentWidget()
        return page.sizeHint() if page is not None else super().sizeHint()

    def minimumSizeHint(self):  # noqa: N802 (Qt naming)
        page = self.currentWidget()
        return (page.minimumSizeHint() if page is not None
                else super().minimumSizeHint())


class DateFilterWidget(QWidget):
    """All / single month / explicit range, collapsed into one (start, end)."""

    changed = pyqtSignal()

    MODE_ALL = "all"
    MODE_MONTH = "month"
    MODE_RANGE = "range"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._mode = self.MODE_ALL
        self._buttons: dict[str, QPushButton] = {}
        self.init_ui()

    def init_ui(self) -> None:
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        caption = QLabel("Date")
        caption.setStyleSheet(_LABEL_STYLE)
        layout.addWidget(caption, 0)

        for mode, text in ((self.MODE_ALL, "All"),
                           (self.MODE_MONTH, "Month"),
                           (self.MODE_RANGE, "Range")):
            button = QPushButton(text)
            button.setCheckable(True)
            button.setChecked(mode == self._mode)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(_MODE_BUTTON_STYLE)
            button.clicked.connect(lambda _c, m=mode: self._set_mode(m))
            self._buttons[mode] = button
            layout.addWidget(button, 0)

        # Only the active mode's input is shown; "All" gets an empty page.
        self._inputs = CurrentPageStack()
        self._inputs.addWidget(QWidget())          # MODE_ALL
        self._inputs.addWidget(self._month_page())  # MODE_MONTH
        self._inputs.addWidget(self._range_page())  # MODE_RANGE
        layout.addWidget(self._inputs, 0)

        self.setLayout(layout)

    def _month_page(self) -> QWidget:
        self.month_combo = NoScrollComboBox()
        self.month_combo.setStyleSheet(_COMBO_STYLE)
        # Capped, and with the trailing stretch below, so the combo keeps its
        # own width rather than spreading across the page it sits on.
        self.month_combo.setMaximumWidth(150)
        self.month_combo.currentIndexChanged.connect(lambda _i: self.changed.emit())

        page = QWidget()
        page_layout = QHBoxLayout()
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.addWidget(self.month_combo, 0)
        page_layout.addStretch(1)
        page.setLayout(page_layout)
        return page

    def _range_page(self) -> QWidget:
        today = QDate.currentDate()
        self.start_edit = DatePickerEdit()
        self.end_edit = DatePickerEdit()
        for edit, default in ((self.start_edit, today.addMonths(-1)), (self.end_edit, today)):
            edit.setDisplayFormat("dd.MM.yyyy")
            edit.setDate(default)
            edit.dateChanged.connect(lambda _d: self.changed.emit())

        to_label = QLabel("to")
        to_label.setStyleSheet(_LABEL_STYLE)

        page = QWidget()
        page_layout = QHBoxLayout()
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(6)
        page_layout.addWidget(self.start_edit)
        page_layout.addWidget(to_label)
        page_layout.addWidget(self.end_edit)
        page.setLayout(page_layout)
        return page

    def reset(self) -> None:
        """Back to All, the mode that filters nothing."""
        self._set_mode(self.MODE_ALL)

    def _set_mode(self, mode: str) -> None:
        self._mode = mode
        for key, button in self._buttons.items():
            button.setChecked(key == mode)
        self._inputs.setCurrentIndex(
            {self.MODE_ALL: 0, self.MODE_MONTH: 1, self.MODE_RANGE: 2}[mode])
        # The stack is now as wide as the page it shows, so switching pages
        # changes how much of the row this filter wants. Nothing asks again on
        # its own.
        self._inputs.updateGeometry()
        self.updateGeometry()
        self.changed.emit()

    def set_month_options(self, months: list[tuple[int, int]]) -> None:
        """Offer only the (year, month) pairs the data actually contains, newest
        first — so no selection can produce an empty table."""
        current = self.month_combo.currentData()
        self.month_combo.blockSignals(True)
        self.month_combo.clear()
        for year, month in months:
            self.month_combo.addItem(
                f"{calendar.month_name[month]} {year}", (year, month))
        if current is not None:
            index = self.month_combo.findData(current)
            if index >= 0:
                self.month_combo.setCurrentIndex(index)
        self.month_combo.blockSignals(False)

    def bounds(self) -> tuple[dt.date | None, dt.date | None]:
        """Inclusive (start, end); ``None`` means open-ended."""
        if self._mode == self.MODE_MONTH:
            selected = self.month_combo.currentData()
            if selected is None:
                return None, None
            year, month = selected
            last_day = calendar.monthrange(year, month)[1]
            return dt.date(year, month, 1), dt.date(year, month, last_day)

        if self._mode == self.MODE_RANGE:
            start = self.start_edit.date().toPyDate()
            end = self.end_edit.date().toPyDate()
            # Tolerate a backwards range rather than showing nothing.
            return (start, end) if start <= end else (end, start)

        return None, None


class NumberRangeWidget(QWidget):
    """A from/to pair over the number inside an identifier.

    The prefix is optional on the way in, so "B14144", "b14144" and "14144" are
    all the same electrode. Operators read markings with the letter and type
    them that way; making them strip it would be a rule to remember for nothing.
    """

    changed = pyqtSignal()

    WIDTH = 124

    def __init__(self, caption: str = "Electrode", parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        layout.addWidget(self._caption(caption), 0)

        # Both ends named, so which field is which is readable without having
        # to click into one. The fields themselves are then left blank.
        self.low_edit = self._edit()
        self.high_edit = self._edit()
        for widget in (self._caption("from"), self.low_edit,
                       self._caption("to"), self.high_edit):
            layout.addWidget(widget, 0)
        self.setLayout(layout)

    @staticmethod
    def _caption(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(_LABEL_STYLE)
        return label

    def _edit(self) -> QLineEdit:
        edit = QLineEdit()
        edit.setFixedWidth(self.WIDTH)
        edit.setClearButtonEnabled(True)
        edit.setStyleSheet(_RANGE_INPUT_STYLE)
        # On editing finished rather than per keystroke: half a marking is a
        # number too, so filtering as you type would jump to B1 then B14 then
        # B141 before landing anywhere useful.
        edit.editingFinished.connect(self.changed.emit)
        return edit

    @staticmethod
    def _number(text: str) -> int | None:
        """The digits in what was typed, or None if there were none."""
        digits = "".join(c for c in str(text) if c.isdigit())
        return int(digits) if digits else None

    def bounds(self) -> tuple[int | None, int | None]:
        """``(low, high)`` inclusive, either side open when left blank.

        Returned exactly as typed, including a low above a high. That range
        holds nothing and the table shows nothing, which is the honest answer:
        quietly swapping the two would hide the typo behind results that look
        right, and the reader would never learn they had asked the wrong thing.
        """
        return (self._number(self.low_edit.text()),
                self._number(self.high_edit.text()))

    def clear(self) -> None:
        """Both ends blank, which is no filter at all."""
        for edit in (self.low_edit, self.high_edit):
            edit.clear()

    def describe(self, prefix: str = "B") -> str:
        """The range in words, for a report that lists what was filtered."""
        low, high = self.bounds()
        if low is None and high is None:
            return ""
        if low is not None and high is not None:
            return f"{prefix}{low} to {prefix}{high}"
        return f"from {prefix}{low}" if low is not None else f"up to {prefix}{high}"


class FilterBarWidget(QWidget):
    """A row of dropdown filters, a date filter, a search box and a result count."""

    changed = pyqtSignal()

    SEARCH_DEBOUNCE_MS = 250
    SEARCH_WIDTH = 150
    # A caption belongs to the control beside it, so it sits closer to that than
    # the gap between one filter and the next. Uniform spacing read as twelve
    # separate things on the row rather than six pairs.
    CAPTION_GAP = 5

    def __init__(self, dropdowns: list[FilterDropdown], parent=None,
                 with_date: bool = True, with_search: bool = True,
                 search_placeholder: str = "Search",
                 count_noun: str = "electrodes",
                 number_range: str = "", stacked: bool = False,
                 with_clear: bool = False,
                 extra_date_range: str = "") -> None:
        super().__init__(parent)
        self._dropdowns = dropdowns
        self._count_noun = count_noun
        self._combos: dict[str, QComboBox] = {}
        self.date_filter: DateFilterWidget | None = None
        self.search_input: QLineEdit | None = None
        # Only the tabs that ask for it: a caption turns it on and names it.
        self._number_caption = number_range
        self.number_range: NumberRangeWidget | None = None
        # A second date range over a different column, named by its caption.
        self._extra_date_caption = extra_date_range
        self.extra_date_range: DateRangeWidget | None = None
        # Where it belongs while there is room, and whether it has been pushed
        # off that row. See _reflow.
        self._extra_home: tuple[QHBoxLayout, int] | None = None
        # The widgets that drop to a line of their own together, in row order.
        self._wrapping: list = []
        self._overflow_row: QWidget | None = None
        self._wrapped = False
        # The same for the count, the search box and Clear all filters, which
        # ride at the right of the first row. They describe the filters rather
        # than being filters, so when that row cannot hold everything they are
        # the ones that move.
        self._trailing: list = []
        self._trailing_home: tuple[QHBoxLayout, int] | None = None
        self._trailing_row: QWidget | None = None
        self._trailing_wrapped = False
        # One row per group rather than one long strip. Past four dropdowns and
        # a range a single row is wider than the window, and a filter strip that
        # scrolls sideways can hide a filter that is switched on.
        self._stacked = stacked
        self._with_clear = with_clear
        self.clear_button: QPushButton | None = None

        # Coalesces keystrokes so a big table re-renders once, not per character.
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(self.SEARCH_DEBOUNCE_MS)
        self._search_timer.timeout.connect(self._relay)

        self.init_ui(with_date, with_search, search_placeholder)

    def init_ui(self, with_date: bool, with_search: bool, search_placeholder: str) -> None:
        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        rows: list[QHBoxLayout] = []

        def row() -> QHBoxLayout:
            line = QHBoxLayout()
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(10)
            outer.addLayout(line)
            rows.append(line)
            return line

        first = row()
        # Under the first row rather than at the bottom of the bar: the count
        # and the search belong with the filters they describe, and read as
        # something else entirely sitting below the date ranges.
        self._trailing_row = QWidget()
        trailing_layout = QHBoxLayout(self._trailing_row)
        trailing_layout.setContentsMargins(0, 0, 0, 0)
        trailing_layout.setSpacing(10)
        outer.addWidget(self._trailing_row)
        self._trailing_row.hide()
        # Its own stretch, and deliberately NOT in ``rows``: that list is what
        # add_trailing and add_before_extra_range index into by row number.
        trailing_layout.addStretch(1)

        for dropdown in self._dropdowns:
            combo = CheckableFilterCombo(dropdown.all_label)
            combo.setStyleSheet(_COMBO_STYLE)
            combo.changed.connect(self._relay)
            self._combos[dropdown.key] = combo
            first.addWidget(self._captioned(dropdown.label, combo), 0)

        # Stacked, the ranges drop to a second line together; otherwise they
        # follow on. Two lines rather than three: the electrode range and the
        # date range are the same kind of question and read as a pair.
        second = row() if self._stacked else first
        if self._number_caption:
            self.number_range = NumberRangeWidget(self._number_caption)
            self.number_range.changed.connect(self._relay)
            second.addWidget(self.number_range, 0)

        if with_date:
            self.date_filter = DateFilterWidget()
            self.date_filter.changed.connect(self._relay)
            second.addWidget(self.date_filter, 0)

        # Last on the row, after the date filter it must not be mistaken for.
        if self._extra_date_caption:
            self.extra_date_range = DateRangeWidget(self._extra_date_caption)
            self.extra_date_range.changed.connect(self._relay)
            self._extra_home = (second, second.count())
            second.addWidget(self.extra_date_range, 0)
            self._wrapping = [self.extra_date_range]

            # Somewhere to put it when the row is too narrow to hold it. Hidden
            # until then, so it costs no height on a wide screen.
            self._overflow_row = QWidget()
            overflow = QHBoxLayout(self._overflow_row)
            overflow.setContentsMargins(0, 0, 0, 0)
            overflow.setSpacing(10)
            outer.addWidget(self._overflow_row)
            self._overflow_row.hide()
            rows.append(overflow)

        for line in rows:
            line.addStretch(1)

        # The count and the search go at the right of the top line, where the
        # eye lands after reading across the filters. Clear all goes at the
        # right of the last one, after everything it undoes.
        self.count_label = QLabel("")
        self.count_label.setStyleSheet(_LABEL_STYLE)
        # After the stretch, which is what holds the group at the right of the
        # row, and the index it goes back to when it comes home from its own
        # line.
        self._trailing_home = (first, first.count())
        first.addWidget(self.count_label, 0)
        self._trailing.append(self.count_label)

        if with_search:
            self.search_input = QLineEdit()
            # Capped: it is searching for a marking, six characters, and left
            # to its own hint it took more of the row than any filter.
            self.search_input.setMaximumWidth(self.SEARCH_WIDTH)
            self.search_input.setPlaceholderText(f"🔍  {search_placeholder}")
            self.search_input.setClearButtonEnabled(True)
            self.search_input.setStyleSheet(_SEARCH_STYLE)
            self.search_input.textChanged.connect(lambda _t: self._search_timer.start())
            first.addWidget(self.search_input, 0)
            self._trailing.append(self.search_input)

        if self._with_clear:
            self.clear_button = QPushButton("Clear all filters")
            self.clear_button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.clear_button.setStyleSheet(_CLEAR_BUTTON_STYLE)
            self.clear_button.clicked.connect(self.clear_all)
            first.addWidget(self.clear_button, 0)
            self._trailing.append(self.clear_button)

        # Kept so an owning tab can hang something off the end of a row, see
        # add_trailing.
        self._rows = rows
        self.setLayout(outer)

    @classmethod
    def _captioned(cls, label: str, widget) -> QWidget:
        """One filter: its caption and its control, kept together."""
        caption = QLabel(label)
        caption.setStyleSheet(_LABEL_STYLE)
        group = QWidget()
        layout = QHBoxLayout(group)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(cls.CAPTION_GAP)
        layout.addWidget(caption, 0)
        layout.addWidget(widget, 0)
        return group

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._reflow()

    def _reflow(self) -> None:
        """Drop a full row's travelling group onto a line of its own.

        Qt squeezes the fields rather than letting the row overflow, and an
        electrode number typed into a 56 pixel box cannot be read back. Nothing
        here is allowed to shrink, so the row that cannot fit gets a second
        line instead. On a wide screen the line is hidden and costs nothing.

        Two groups travel, on two different rows, so they do not interact: the
        extra date range off the ranges row, and the count, the search box and
        Clear all filters off the filters row. The second only on a bar built
        to be more than one line: the tabs with a single row pin their strip to
        the height it had when they built it, and a line appearing under it
        would be a line nobody can see.
        """
        self._wrapped = self._settle(self._wrapping, self._extra_home,
                                     self._overflow_row, self._wrapped)
        if self._stacked:
            self._trailing_wrapped = self._settle(
                self._trailing, self._trailing_home, self._trailing_row,
                self._trailing_wrapped)

    def _settle(self, group: list, home_at, holder, wrapped: bool) -> bool:
        """Move one group to its own line, or bring it home. Returns where."""
        if not group or home_at is None or holder is None:
            return wrapped
        home, index = home_at
        # Measured WITHOUT the travelling group, then with it added back, so
        # the answer does not depend on which row the group is sitting in.
        # Otherwise moving it changes the measurement that moved it, and it
        # flaps between the two.
        needed = self._natural_width(home, group) + sum(
            widget.sizeHint().width() + home.spacing() for widget in group)
        margin = self.contentsMargins()
        available = self.width() - margin.left() - margin.right()
        now = needed > available
        if now == wrapped:
            return wrapped

        overflow = holder.layout()
        # THE WHOLE GROUP MOVES, in its own order. Anything put ahead of the
        # extra range belongs with it: left behind, it ends up alone at the end
        # of a row it has nothing to do with.
        for position, widget in enumerate(group):
            if now:
                home.removeWidget(widget)
                overflow.insertWidget(position, widget)
            else:
                overflow.removeWidget(widget)
                home.insertWidget(index + position, widget)
        holder.setVisible(now)
        return now

    def _natural_width(self, row: QHBoxLayout, group: list) -> int:
        """What a row would take if nothing in it were squeezed.

        The widgets' own hints rather than the layout's, which already reflects
        the squeezing this exists to prevent. The travelling group is left out
        wherever it currently sits; the caller adds it back.
        """
        total = 0
        counted = 0
        for position in range(row.count()):
            item = row.itemAt(position)
            widget = item.widget()
            if widget is not None and widget in group:
                continue
            # A spacer counts for what it reserves, which is nothing for the
            # stretch and the inset for a trailing control's dead space.
            total += (widget.sizeHint().width() if widget is not None
                      else item.sizeHint().width())
            counted += 1
        return total + row.spacing() * max(0, counted - 1)

    def add_before_extra_range(self, widget) -> None:
        """Put a widget just ahead of the extra date range, and keep it there.

        It joins the group that wraps, so when the range drops to a line of its
        own this goes with it and stays immediately in front. Parked at the
        right edge instead, it read as belonging to nothing, and once the range
        wrapped away it was left alone at the end of the row above.
        """
        if self._extra_home is None:
            self._rows[1].addWidget(widget, 0)
            return
        row, index = self._extra_home
        row.insertWidget(index, widget)
        self._wrapping.insert(0, widget)

    def add_trailing(self, row: int, widget, right_inset: int = 0) -> None:
        """Put a widget at the right end of one row, held clear of the edge.

        ``right_inset`` is dead space after it, which is how a control can be
        lined up with something narrower than this bar: the Electrodes tab
        insets its zoom by the width of the panel beside the table, so the two
        share a right edge.
        """
        if not 0 <= row < len(self._rows):
            return
        self._rows[row].addWidget(widget, 0)
        if right_inset:
            self._rows[row].addSpacing(right_inset)

    def _relay(self) -> None:
        """Paint the control that was just used, then start the filtering.

        Everything downstream of ``changed`` runs synchronously, so until it
        returns Qt has not drawn anything: the pressed button, the ticked box
        or the cleared field would all appear only once the table had caught
        up. Painting first costs nothing and makes the click feel answered.

        And a look at the rows on the way past: the date filter is as wide as
        the mode it is showing, so picking Range makes it want room it did not
        want a moment ago, and no resize happens to notice that.
        """
        self._reflow()
        self.repaint()
        self.changed.emit()

    def clear_all(self) -> None:
        """Every filter back to showing everything, and one redraw for the lot.

        Each control is silenced while it is reset, so a bar with six filters on
        does not re-render the table six times on its way back to empty.
        """
        for combo in self._combos.values():
            combo.blockSignals(True)
            combo.set_checked([])
            combo.blockSignals(False)
        for widget in (self.number_range, self.date_filter,
                       self.extra_date_range, self.search_input):
            if widget is None:
                continue
            widget.blockSignals(True)
            if isinstance(widget, (DateFilterWidget, DateRangeWidget)):
                widget.reset()
            else:
                widget.clear()
            widget.blockSignals(False)
        # The search box debounces, so a pending redraw would otherwise land
        # just after this one and undo nothing twice.
        self._search_timer.stop()
        self._relay()

    # ---- options / values ---------------------------------------------
    def set_options(self, key: str, options: list[str]) -> None:
        """Refill one dropdown from the data, keeping what is still ticked."""
        combo = self._combos.get(key)
        if combo is not None:
            combo.set_options(options)

    def values(self, key: str) -> list[str]:
        """Everything ticked in one dropdown. Empty means no filter at all."""
        combo = self._combos.get(key)
        return combo.checked() if combo is not None else []

    def value(self, key: str) -> str | None:
        """The one ticked value, or None. For callers that only allow one."""
        chosen = self.values(key)
        return chosen[0] if len(chosen) == 1 else None

    def active(self) -> dict:
        """``{label: [ticked values]}`` for the dropdowns that are filtering.

        Labels rather than keys, so a caller can put it in front of somebody.
        """
        chosen = {dropdown.label: self.values(dropdown.key)
                  for dropdown in self._dropdowns
                  if self.values(dropdown.key)}
        span = self.number_range.describe() if self.number_range else ""
        if span:
            chosen[self._number_caption] = [span]
        return chosen

    def set_month_options(self, months: list[tuple[int, int]]) -> None:
        if self.date_filter is not None:
            self.date_filter.set_month_options(months)

    def date_bounds(self) -> tuple[dt.date | None, dt.date | None]:
        return self.date_filter.bounds() if self.date_filter else (None, None)

    def number_bounds(self) -> tuple[int | None, int | None]:
        return self.number_range.bounds() if self.number_range else (None, None)

    def search_text(self) -> str:
        return self.search_input.text().strip() if self.search_input else ""

    def set_result_count(self, shown: int, total: int) -> None:
        """Show how much of the dataset survived the filters."""
        self.count_label.setText(
            f"{shown:,} of {total:,}".replace(",", " ") if shown != total
            else f"{total:,} {self._count_noun}".replace(",", " "))
