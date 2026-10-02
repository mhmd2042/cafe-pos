"""
views/cashier/modifier_dialog.py — quick touch modal for a product's options.

Design intent: a cashier picks a drink and gets the answer in one or two taps.
Every group is laid out with its options as large toggle tiles (>= 60px), the
product's required groups are pre-selected, and a free-text box handles the
"no foam, extra hot" requests.

RTL: the dialog is Arabic right-to-left, but price deltas inside option labels
are wrapped in Unicode isolates so "+500 ر.ي" is not reordered.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import config
from controllers.pos_controller import ModifierSelection
from models.order import SelectedModifier
from models.product import Modifier, ModifierGroup, Product, price_delta_label
from views.widgets import Toast

logger = logging.getLogger(__name__)

__all__ = ["ModifierDialog", "ModifierOptionButton"]


def isolate(text: str) -> str:
    """Wrap a Latin/digit run so RTL does not reorder it."""
    return f"\u2066{text}\u2069"


class ModifierOptionButton(QPushButton):
    """One selectable option tile inside a group."""

    def __init__(self, modifier: Modifier, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.modifier = modifier
        self.setCheckable(True)
        self.setObjectName("ModifierOption")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(64)

        # Single line, delta inline. A two-line label doubled every tile's height
        # and pushed milk/sugar below the fold — with six groups the cashier
        # would have to scroll for the two most common choices.
        name = modifier.display_name
        delta = modifier.price_label
        self.setText(f"{name}  {isolate(delta)}" if delta else name)
        self.setToolTip(f"{name} {delta}".strip())


class ModifierGroupBox(QFrame):
    """One group (الحجم / الحليب / السكر / إضافات / ملاحظات) with its options."""

    def __init__(self, group: ModifierGroup, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.group = group
        self.setObjectName("Card")
        self.buttons: list[ModifierOptionButton] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel(group.display_name)
        title.setObjectName("SectionTitle")
        header.addWidget(title)
        if group.is_required:
            badge = QLabel("مطلوب")
            badge.setObjectName("RoleBadge")
            header.addWidget(badge)
        header.addStretch(1)
        layout.addLayout(header)

        if group.is_text:
            # Free-text notes are handled by the dialog's own note field, so a
            # text group renders nothing here.
            layout.addWidget(self._hint("اكتب الملاحظة في خانة الملاحظات بالأسفل"))
            return

        grid = QGridLayout()
        grid.setSpacing(8)
        # Pack into 3 columns as soon as there are 4+ options: with six groups in
        # the dialog, a 2-column layout pushes milk and sugar below the fold and
        # makes the cashier scroll mid-order.
        columns = 3 if len(group.options) >= 4 else max(1, min(3, len(group.options)))
        for index, modifier in enumerate(group.options):
            button = ModifierOptionButton(modifier)
            button.setChecked(modifier.is_default)
            if group.is_single:
                button.clicked.connect(
                    lambda _=False, g=group, m=modifier: self._on_single(g, m)
                )
            else:
                button.clicked.connect(lambda _=False, b=button: self._on_multi(b))
            grid.addWidget(button, index // columns, index % columns)
            self.buttons.append(button)
        for column in range(columns):
            grid.setColumnStretch(column, 1)
        layout.addLayout(grid)

        if group.is_multi:
            layout.addWidget(self._hint(f"اختر حتى {group.max_select} خيارات"))

    @staticmethod
    def _hint(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("HintText")
        label.setWordWrap(True)
        return label

    # -- selection rules --------------------------------------------------- #
    def _on_single(self, group: ModifierGroup, chosen: Modifier) -> None:
        """Single-choice: radio behaviour, and a required group cannot be cleared."""
        for button in self.buttons:
            button.setChecked(button.modifier.id == chosen.id)
        self._update_styles()

    def _on_multi(self, button: ModifierOptionButton) -> None:
        """Multi-choice: cap at max_select, refuse to exceed rather than silently drop."""
        selected = [b for b in self.buttons if b.isChecked()]
        if len(selected) > self.group.max_select:
            button.setChecked(False)
            self._flash_limit()
            return
        self._update_styles()

    def _flash_limit(self) -> None:
        self.setProperty("limitHit", True)
        QTimer.singleShot(400, lambda: self.setProperty("limitHit", False))

    def _update_styles(self) -> None:
        for button in self.buttons:
            button.style().unpolish(button)
            button.style().polish(button)

    # -- results ----------------------------------------------------------- #
    def selected(self) -> list[Modifier]:
        return [b.modifier for b in self.buttons if b.isChecked()]

    def is_satisfied(self) -> bool:
        if self.group.is_text:
            return True
        count = len(self.selected())
        if self.group.is_required and count < max(1, self.group.min_select):
            return False
        if count > self.group.max_select:
            return False
        return True

    def problem(self) -> str:
        if self.is_satisfied():
            return ""
        return f"اختر {self.group.display_name} للمتابعة"


class ModifierDialog(QDialog):
    """
    Modal shown when a product has real choices.

    Usage:
        dialog = ModifierDialog(controller, product)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            selection = dialog.selection()
    """

    def __init__(self, controller, product: Product, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.product = product
        self.groups = controller.modifier_groups(product)
        self.group_boxes: list[ModifierGroupBox] = []

        self.setWindowTitle(f"خيارات — {product.display_name}")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setModal(True)
        self.setMinimumSize(760, 600)
        # Size to show size/milk/sugar without scrolling on a 768px-tall screen,
        # then cap to whatever the display actually offers.
        self.resize(780, 760)
        screen = self.screen()
        if screen is not None:
            available = screen.availableGeometry()
            self.resize(min(780, available.width() - 40),
                        min(760, available.height() - 40))

        self._build()

    # -- construction ------------------------------------------------------ #
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 20)
        root.setSpacing(14)

        root.addWidget(self._build_header())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        container.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.groups_layout = QVBoxLayout(container)
        self.groups_layout.setContentsMargins(0, 0, 8, 0)
        self.groups_layout.setSpacing(12)

        for group in self.groups:
            box = ModifierGroupBox(group)
            self.group_boxes.append(box)
            self.groups_layout.addWidget(box)

        if not self.groups:
            empty = QLabel("لا توجد خيارات لهذا الصنف")
            empty.setObjectName("HintText")
            self.groups_layout.addWidget(empty)

        self.groups_layout.addStretch(1)
        scroll.setWidget(container)
        root.addWidget(scroll, 1)

        root.addWidget(self._build_note_field())
        self.toast = Toast()
        root.addWidget(self.toast)
        root.addLayout(self._build_actions())

    def _build_header(self) -> QWidget:
        card = QFrame()
        card.setObjectName("CardRaised")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(12)

        name = QLabel(self.product.display_name)
        name.setObjectName("BrandTitle")
        name.setWordWrap(True)

        self.price_label = QLabel(config.format_money(self.product.price_minor))
        self.price_label.setObjectName("TotalValue")

        layout.addWidget(name, 1)
        layout.addWidget(self.price_label)
        return card

    def _build_note_field(self) -> QWidget:
        box = QFrame()
        box.setObjectName("Card")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        label = QLabel("ملاحظات (اختياري)")
        label.setObjectName("FieldLabel")

        self.note_edit = QLineEdit()
        self.note_edit.setPlaceholderText("مثال: بدون رغوة، ساخن جداً")
        self.note_edit.setMaxLength(120)
        self.note_edit.setMinimumHeight(config.MIN_TOUCH_TARGET - 12)
        self.note_edit.textChanged.connect(self._update_price)

        layout.addWidget(label)
        layout.addWidget(self.note_edit)
        return box

    def _build_actions(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(12)

        cancel = QPushButton("إلغاء")
        cancel.setObjectName("GhostAction")
        cancel.setMinimumHeight(config.MIN_TOUCH_TARGET)
        cancel.clicked.connect(self.reject)

        self.add_button = QPushButton("إضافة إلى الطلب")
        self.add_button.setObjectName("PrimaryAction")
        self.add_button.setMinimumHeight(config.MIN_TOUCH_TARGET)
        self.add_button.setDefault(True)
        self.add_button.clicked.connect(self._accept)

        row.addWidget(cancel, 1)
        row.addWidget(self.add_button, 2)
        return row

    # -- live pricing ------------------------------------------------------ #
    def selection(self) -> ModifierSelection:
        chosen: list[SelectedModifier] = []
        for box in self.group_boxes:
            for modifier in box.selected():
                chosen.append(
                    SelectedModifier(
                        group_id=box.group.id,
                        group_name=box.group.display_name,
                        modifier_id=modifier.id,
                        name=modifier.display_name,
                        price_minor=modifier.price_minor,
                    )
                )
        return ModifierSelection(modifiers=chosen, note=self.note_edit.text().strip())

    def unit_price_minor(self) -> int:
        """Product price plus every chosen delta — the tax-inclusive unit price."""
        return self.product.price_minor + sum(m.price_minor for m in self.selection().modifiers)

    def _update_price(self) -> None:
        self.price_label.setText(config.format_money(self.unit_price_minor()))

    # -- actions ----------------------------------------------------------- #
    def _accept(self) -> None:
        for box in self.group_boxes:
            problem = box.problem()
            if problem:
                self.toast.show_message(problem, "error", 4000)
                box.setFocus()
                return
        self.accept()
