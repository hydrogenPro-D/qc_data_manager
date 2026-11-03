"""Which coupons QC wants: the settings dialog, and the per request choice.

Two things, both about the same three values. The settings say what each reason
for a request is worth, standing until somebody changes them. The choice asks
for one request, which is the hand raised case: the operator is already saying
this electrode in particular, so they say what they want from it too.

No backend here. The owning tab injects callables, the same as every other
widget in this folder.
"""

from __future__ import annotations

from PyQt6.QtCore import QRectF, QSize, Qt
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import (
    QDialog, QFormLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
    QWidget,
)

from src.data_access.qc_requests.coupon_columns import (
    ALL_FIVE,
    COUPON_LABELS,
    NONE,
    ORDER,
    REASON_LABELS,
    REASONS,
    normalize,
)
from .coupon_icon import coupon_icon, draw_coupons
from .error_dialogs import ConfirmDialog, build_header
from .no_scroll_inputs import NoScrollComboBox
from ..styles.button_styles import (
    PRIMARY_BUTTON_STYLE,
    SECONDARY_BUTTON_STYLE,
    apply_modern_shadow,
)
from ..styles.input_styles import COMBO_BOX_STYLE

HEADER_COLOR = "#14B8A6"
# Big enough to tell five holes from one at a glance, small enough to sit on a
# dropdown row without stretching it.
ICON_IN_LIST = 20


def coupon_combo(selected: str = ALL_FIVE,
                 with_nothing: bool = True) -> NoScrollComboBox:
    """The choices, in order of how much they ask for.

    Least first, so the list reads as a scale rather than as unrelated options.
    The stored value rides along as the item's data, so the labels can be
    reworded without touching what is written to the database.

    NOTHING IS NOT ALWAYS A CHOICE. As a setting it means a reason QC does not
    want coupons from, which is a real answer. As an answer to "shall we ask
    for this electrode" it is not: no coupons is no test, the procedure raises
    no request for it, and an operator who picks it has pressed a button that
    does nothing. So the dialogs that raise or agree a request leave it out.
    """
    combo = NoScrollComboBox()
    combo.setStyleSheet(COMBO_BOX_STYLE)
    combo.setMinimumHeight(34)
    # Enough for the longest label, so the three rows line up and the list does
    # not resize as the choice changes.
    combo.setMinimumWidth(190)
    # The picture on every choice, so the list is read as three shapes rather
    # than three sentences. The bare disc stands for Nothing: a picker with one
    # blank row reads as a missing icon rather than as an answer.
    combo.setIconSize(QSize(ICON_IN_LIST, ICON_IN_LIST))
    offered = [value for value in ORDER
               if with_nothing or value != NONE]
    for value in offered:
        combo.addItem(coupon_icon(value, ICON_IN_LIST),
                      COUPON_LABELS[value], value)
    wanted = normalize(selected)
    # A selection that is no longer on the list falls to the least it can ask
    # for, which is the nearest thing to what was asked for.
    combo.setCurrentIndex(offered.index(wanted) if wanted in offered else 0)
    return combo


class CouponPreview(QWidget):
    """The drawing for whatever is currently chosen, beside the dropdown.

    A picture of the thing being set, so the three words in the list are read
    once and the shape thereafter. Nothing is drawn for Nothing, which is the
    honest picture of asking for no coupons.
    """

    SIZE = 34

    def __init__(self, combo: NoScrollComboBox, parent=None) -> None:
        super().__init__(parent)
        self._combo = combo
        self.setFixedSize(self.SIZE, self.SIZE)
        combo.currentIndexChanged.connect(lambda _i: self.update())

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        draw_coupons(painter, QRectF(1, 1, self.SIZE - 2, self.SIZE - 2),
                     chosen_coupons(self._combo), show_empty=True)
        painter.end()


def chosen_coupons(combo: NoScrollComboBox) -> str:
    """What the operator picked, as the database spells it."""
    return normalize(combo.currentData())


def ask_with_coupons(parent, title: str, question: str, confirm_text: str,
                     selected: str = ALL_FIVE) -> tuple[bool, str]:
    """A confirmation that also asks what to cut. ``(agreed, coupons)``.

    The dropdown sits inside the confirmation rather than in a dialog of its
    own: it is one decision, and splitting it in two makes the operator agree
    to something before being told what they are agreeing to.

    Nothing is not offered here. Asking for an electrode and wanting nothing
    cut from it is not a request, and the procedure would refuse it anyway.
    """
    holder = QWidget()
    row = QHBoxLayout(holder)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(10)
    caption = QLabel("Coupons")
    caption.setStyleSheet("color: #4B5563; font-size: 13px; font-weight: 600;")
    combo = coupon_combo(selected, with_nothing=False)
    row.addWidget(caption, 0)
    row.addWidget(combo, 1)

    dialog = ConfirmDialog(parent, title=title, question=question,
                           confirm_text=confirm_text, extra=holder)
    agreed = dialog.exec() == QDialog.DialogCode.Accepted
    return agreed, chosen_coupons(combo)


class CouponSettingsDialog(QDialog):
    """What QC wants cut, per reason a request can be raised.

    THE THIRD CHOICE IS NOT A SMALLER VERSION OF THE OTHER TWO. Setting a
    reason to Nothing turns it off: no request is raised for it, and around a
    failure the electrodes that would have been queued for testing are not
    queued either. Three dropdowns and nothing else, so that is written here
    rather than on screen.
    """

    def __init__(self, parent=None, settings: dict | None = None,
                 on_save=None) -> None:
        """Args:
            settings: ``{reason: coupons}`` as it stands now.
            on_save: ``(reason, coupons) -> bool``, called once per setting
                that the operator actually changed.
        """
        super().__init__(parent)
        self._on_save = on_save
        self._settings = dict(settings or {})
        self._combos: dict[str, NoScrollComboBox] = {}

        self.setWindowTitle("Coupon settings")
        self.setMinimumWidth(480)
        self.setStyleSheet("QDialog { background-color: #FFFFFF; }")

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(14)
        # The dropdowns take what they need and no more. Left to expand they
        # took the whole row and the captions beside them came out zero pixels
        # wide, so the dialog was three unlabelled lists.
        form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        for reason in REASONS:
            combo = coupon_combo(self._settings.get(reason, ALL_FIVE))
            self._combos[reason] = combo
            caption = QLabel(REASON_LABELS[reason])
            caption.setStyleSheet(
                "color: #1F1F1F; font-size: 13px; font-weight: 600;")
            # The dropdown and a picture of what it currently says.
            field = QWidget()
            beside = QHBoxLayout(field)
            beside.setContentsMargins(0, 0, 0, 0)
            beside.setSpacing(10)
            beside.addWidget(combo, 0)
            beside.addWidget(CouponPreview(combo), 0)
            form.addRow(caption, field)

        cancel = QPushButton("Cancel")
        cancel.setMinimumHeight(38)
        cancel.setStyleSheet(SECONDARY_BUTTON_STYLE)
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        cancel.setDefault(True)

        save = QPushButton("Save")
        save.setMinimumHeight(38)
        save.setMinimumWidth(120)
        save.setStyleSheet(PRIMARY_BUTTON_STYLE)
        save.setCursor(Qt.CursorShape.PointingHandCursor)
        apply_modern_shadow(save)
        save.clicked.connect(self._save)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(cancel, 0)
        buttons.addWidget(save, 0)

        body = QVBoxLayout()
        body.setContentsMargins(24, 22, 24, 20)
        body.setSpacing(18)
        body.addLayout(form)
        body.addLayout(buttons)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(build_header("⚙", "Coupon settings", HEADER_COLOR))
        layout.addLayout(body, 1)
        self.setLayout(layout)

    def values(self) -> dict[str, str]:
        """What the dropdowns currently say."""
        return {reason: chosen_coupons(combo)
                for reason, combo in self._combos.items()}

    def _save(self) -> None:
        """Write the ones that moved, then close.

        Only the ones that moved: each is a procedure call, and writing all
        three every time would stamp an edit on settings nobody touched.
        """
        if self._on_save is not None:
            for reason, coupons in self.values().items():
                if coupons != normalize(self._settings.get(reason, ALL_FIVE)):
                    self._on_save(reason, coupons)
        self.accept()
