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

from PyQt6.QtCore import Qt, QDate, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QLabel, QPushButton, QLineEdit, QStackedWidget, QComboBox,
)

from .no_scroll_inputs import NoScrollComboBox, NoScrollDateEdit

_ALL = "__all__"

_COMBO_STYLE = """
    QComboBox {
        background-color: #FFFFFF;
        border: 1px solid #D1D5DB;
        border-radius: 14px;
        padding: 4px 10px;
        font-family: 'Segoe UI', Arial, sans-serif;
        font-size: 12px;
        font-weight: 600;
        color: #374151;
        min-width: 104px;
    }
    QComboBox:hover { border: 1px solid #9CA3AF; }
    QComboBox:focus { border: 1.5px solid #55BDBD; }
    QComboBox::drop-down {
        subcontrol-origin: padding;
        subcontrol-position: center right;
        width: 20px;
        border: none;
    }
    /* No arrow asset ships with the app, so draw one out of borders. */
    QComboBox::down-arrow {
        width: 0px; height: 0px;
        border-left: 4px solid transparent;
        border-right: 4px solid transparent;
        border-top: 5px solid #6B7280;
        margin-right: 6px;
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
        border-radius: 13px;
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
        border-radius: 13px;
        padding: 4px 8px;
        font-family: 'Segoe UI', Arial, sans-serif;
        font-size: 12px;
        color: #374151;
        min-width: 92px;
    }
    QDateEdit:focus { border: 1.5px solid #55BDBD; }
    QDateEdit::drop-down { border: none; width: 16px; }
"""

_SEARCH_STYLE = """
    QLineEdit {
        background-color: #FFFFFF;
        border: 1px solid #E1E1E1;
        border-radius: 14px;
        padding: 5px 12px;
        font-family: 'Segoe UI', Arial, sans-serif;
        font-size: 12px;
        color: #1F1F1F;
        min-width: 140px;
    }
    QLineEdit:focus { border: 1.5px solid #55BDBD; }
"""

_LABEL_STYLE = "color: #6B6B6B; font-size: 12px; font-weight: 600;"


@dataclass
class FilterDropdown:
    """One value-filter dropdown. Options are filled from the data later."""

    key: str
    label: str
    all_label: str = "All"


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
        self._inputs = QStackedWidget()
        self._inputs.addWidget(QWidget())          # MODE_ALL
        self._inputs.addWidget(self._month_page())  # MODE_MONTH
        self._inputs.addWidget(self._range_page())  # MODE_RANGE
        layout.addWidget(self._inputs, 0)

        self.setLayout(layout)

    def _month_page(self) -> QWidget:
        self.month_combo = NoScrollComboBox()
        self.month_combo.setStyleSheet(_COMBO_STYLE)
        # The stack is as wide as its widest page (the two date pickers), so
        # without a trailing stretch the combo would spread across all of it.
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
        self.start_edit = NoScrollDateEdit()
        self.end_edit = NoScrollDateEdit()
        for edit, default in ((self.start_edit, today.addMonths(-1)), (self.end_edit, today)):
            edit.setDisplayFormat("dd.MM.yyyy")
            edit.setCalendarPopup(True)
            edit.setDate(default)
            edit.setStyleSheet(_DATE_EDIT_STYLE)
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

    def _set_mode(self, mode: str) -> None:
        self._mode = mode
        for key, button in self._buttons.items():
            button.setChecked(key == mode)
        self._inputs.setCurrentIndex(
            {self.MODE_ALL: 0, self.MODE_MONTH: 1, self.MODE_RANGE: 2}[mode])
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


class FilterBarWidget(QWidget):
    """A row of dropdown filters, a date filter, a search box and a result count."""

    changed = pyqtSignal()

    SEARCH_DEBOUNCE_MS = 250

    def __init__(self, dropdowns: list[FilterDropdown], parent=None,
                 with_date: bool = True, with_search: bool = True,
                 search_placeholder: str = "Search",
                 count_noun: str = "electrodes") -> None:
        super().__init__(parent)
        self._dropdowns = dropdowns
        self._count_noun = count_noun
        self._combos: dict[str, QComboBox] = {}
        self.date_filter: DateFilterWidget | None = None
        self.search_input: QLineEdit | None = None

        # Coalesces keystrokes so a big table re-renders once, not per character.
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(self.SEARCH_DEBOUNCE_MS)
        self._search_timer.timeout.connect(self.changed.emit)

        self.init_ui(with_date, with_search, search_placeholder)

    def init_ui(self, with_date: bool, with_search: bool, search_placeholder: str) -> None:
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        for dropdown in self._dropdowns:
            caption = QLabel(dropdown.label)
            caption.setStyleSheet(_LABEL_STYLE)
            combo = NoScrollComboBox()
            combo.setStyleSheet(_COMBO_STYLE)
            combo.addItem(dropdown.all_label, _ALL)
            combo.currentIndexChanged.connect(lambda _i: self.changed.emit())
            self._combos[dropdown.key] = combo
            layout.addWidget(caption, 0)
            layout.addWidget(combo, 0)

        if with_date:
            self.date_filter = DateFilterWidget()
            self.date_filter.changed.connect(self.changed.emit)
            layout.addWidget(self.date_filter, 0)

        layout.addStretch(1)

        self.count_label = QLabel("")
        self.count_label.setStyleSheet(_LABEL_STYLE)
        layout.addWidget(self.count_label, 0)

        if with_search:
            self.search_input = QLineEdit()
            self.search_input.setPlaceholderText(f"🔍  {search_placeholder}")
            self.search_input.setClearButtonEnabled(True)
            self.search_input.setStyleSheet(_SEARCH_STYLE)
            self.search_input.textChanged.connect(lambda _t: self._search_timer.start())
            layout.addWidget(self.search_input, 0)

        self.setLayout(layout)

    # ---- options / values ---------------------------------------------
    def set_options(self, key: str, options: list[str]) -> None:
        """Refill one dropdown from the data, keeping the current selection."""
        combo = self._combos.get(key)
        if combo is None:
            return
        current = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        dropdown = next(d for d in self._dropdowns if d.key == key)
        combo.addItem(dropdown.all_label, _ALL)
        for option in options:
            combo.addItem(str(option), str(option))
        index = combo.findData(current)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

    def value(self, key: str) -> str | None:
        """The selected value, or ``None`` when the dropdown is on "All"."""
        combo = self._combos.get(key)
        if combo is None:
            return None
        data = combo.currentData()
        return None if data == _ALL or data is None else str(data)

    def set_month_options(self, months: list[tuple[int, int]]) -> None:
        if self.date_filter is not None:
            self.date_filter.set_month_options(months)

    def date_bounds(self) -> tuple[dt.date | None, dt.date | None]:
        return self.date_filter.bounds() if self.date_filter else (None, None)

    def search_text(self) -> str:
        return self.search_input.text().strip() if self.search_input else ""

    def set_result_count(self, shown: int, total: int) -> None:
        """Show how much of the dataset survived the filters."""
        self.count_label.setText(
            f"{shown:,} of {total:,}".replace(",", " ") if shown != total
            else f"{total:,} {self._count_noun}".replace(",", " "))
