"""
views/admin/menu_editor.py — category / product / modifier CRUD.

Layout: category rail, product table, and an editor panel for the selected
product (including which modifier groups it offers and its recipe cost).
Every save goes through AdminController, so it is permission-checked and audited.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import config
from controllers.admin_controller import AdminController, MenuError
from views.widgets import Divider, Toast

logger = logging.getLogger(__name__)

__all__ = ["MenuEditorView", "ProductDialog", "ModifierDialog"]


# --------------------------------------------------------------------------- #
# Product add/edit dialog
# --------------------------------------------------------------------------- #
class ProductDialog(QDialog):
    """Add or edit one menu item."""

    def __init__(self, controller: AdminController, categories: list, product=None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.product = product
        self.categories = categories

        self.setWindowTitle("تعديل صنف" if product else "إضافة صنف")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setModal(True)
        self.setMinimumWidth(520)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 20)
        root.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(10)

        self.name_edit = QLineEdit(product.name_ar if product else "")
        self.name_edit.setMinimumHeight(config.MIN_TOUCH_TARGET - 8)
        form.addRow("الاسم (عربي)", self.name_edit)

        self.name_en_edit = QLineEdit(product.name_en if product else "")
        self.name_en_edit.setMinimumHeight(config.MIN_TOUCH_TARGET - 8)
        form.addRow("الاسم (إنجليزي)", self.name_en_edit)

        self.category_combo = QComboBox()
        for category in categories:
            self.category_combo.addItem(category.display_name, category.id)
        if product is not None:
            index = self.category_combo.findData(product.category_id)
            if index >= 0:
                self.category_combo.setCurrentIndex(index)
        self.category_combo.setMinimumHeight(config.MIN_TOUCH_TARGET - 8)
        form.addRow("التصنيف", self.category_combo)

        # Prices are entered in whole rials; stored as integer minor units.
        self.price_spin = QDoubleSpinBox()
        self.price_spin.setDecimals(config.MONEY_DECIMALS)
        self.price_spin.setMaximum(10_000_000)
        self.price_spin.setSingleStep(100)
        self.price_spin.setSuffix(f" {config.CURRENCY_SYMBOL}")
        self.price_spin.setMinimumHeight(config.MIN_TOUCH_TARGET - 8)
        if product is not None:
            self.price_spin.setValue(float(config.from_minor(product.price_minor)))
        form.addRow("السعر (شامل الضريبة)", self.price_spin)

        self.cost_spin = QDoubleSpinBox()
        self.cost_spin.setDecimals(config.MONEY_DECIMALS)
        self.cost_spin.setMaximum(10_000_000)
        self.cost_spin.setSingleStep(100)
        self.cost_spin.setSuffix(f" {config.CURRENCY_SYMBOL}")
        self.cost_spin.setMinimumHeight(config.MIN_TOUCH_TARGET - 8)
        if product is not None:
            self.cost_spin.setValue(float(config.from_minor(product.cost_minor)))
        form.addRow("التكلفة التقديرية", self.cost_spin)

        self.sku_edit = QLineEdit(product.sku or "" if product else "")
        self.sku_edit.setMinimumHeight(config.MIN_TOUCH_TARGET - 8)
        form.addRow("الرمز (SKU)", self.sku_edit)

        self.track_check = QCheckBox("خصم المخزون عند البيع")
        self.track_check.setChecked(product.track_inventory if product else True)
        form.addRow("", self.track_check)

        self.active_check = QCheckBox("الصنف مُفعّل")
        self.active_check.setChecked(product.is_active if product else True)
        form.addRow("", self.active_check)

        root.addLayout(form)

        self.margin_label = QLabel("")
        self.margin_label.setObjectName("HintText")
        root.addWidget(self.margin_label)
        self.price_spin.valueChanged.connect(self._update_margin)
        self.cost_spin.valueChanged.connect(self._update_margin)
        self._update_margin()

        self.toast = Toast()
        root.addWidget(self.toast)

        buttons = QDialogButtonBox()
        save = buttons.addButton("حفظ", QDialogButtonBox.ButtonRole.AcceptRole)
        save.setObjectName("PrimaryAction")
        save.setMinimumHeight(config.MIN_TOUCH_TARGET)
        cancel = buttons.addButton("إلغاء", QDialogButtonBox.ButtonRole.RejectRole)
        cancel.setObjectName("GhostAction")
        cancel.setMinimumHeight(config.MIN_TOUCH_TARGET)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _update_margin(self) -> None:
        price = config.to_minor(self.price_spin.value())
        cost = config.to_minor(self.cost_spin.value())
        margin = price - cost
        percent = (margin / price * 100.0) if price else 0.0
        self.margin_label.setText(
            f"هامش الربح: {config.format_money(margin)} ({percent:.1f}%)"
        )

    def _save(self) -> None:
        price = config.to_minor(self.price_spin.value())
        cost = config.to_minor(self.cost_spin.value())
        payload = {
            "category_id": int(self.category_combo.currentData()),
            "name_ar": self.name_edit.text().strip(),
            "name_en": self.name_en_edit.text().strip(),
            "sku": self.sku_edit.text().strip() or None,
            "price_minor": price,
            "cost_minor": cost,
            "track_inventory": int(self.track_check.isChecked()),
            "is_active": int(self.active_check.isChecked()),
        }
        try:
            if self.product is None:
                self.controller.create_product(**payload)
            else:
                self.controller.update_product(self.product.id, **payload)
        except MenuError as exc:
            self.toast.show_message(str(exc), "error", 5000)
            return
        except Exception as exc:
            logger.exception("تعذّر حفظ الصنف")
            self.toast.show_message(f"تعذّر الحفظ: {exc}", "error", 5000)
            return
        self.accept()


# --------------------------------------------------------------------------- #
# Modifier add/edit dialog
# --------------------------------------------------------------------------- #
class ModifierDialog(QDialog):
    """Add or edit one modifier option inside a group."""

    def __init__(self, controller: AdminController, group, modifier=None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.group = group
        self.modifier = modifier

        self.setWindowTitle("تعديل خيار" if modifier else "إضافة خيار")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setModal(True)
        self.setMinimumWidth(440)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 20)
        root.setSpacing(12)

        form = QFormLayout()
        self.name_edit = QLineEdit(modifier.name_ar if modifier else "")
        self.name_edit.setMinimumHeight(config.MIN_TOUCH_TARGET - 8)
        form.addRow("الاسم", self.name_edit)

        self.price_spin = QDoubleSpinBox()
        self.price_spin.setDecimals(config.MONEY_DECIMALS)
        self.price_spin.setRange(-100_000, 100_000)
        self.price_spin.setSingleStep(100)
        self.price_spin.setSuffix(f" {config.CURRENCY_SYMBOL}")
        self.price_spin.setMinimumHeight(config.MIN_TOUCH_TARGET - 8)
        if modifier is not None:
            self.price_spin.setValue(float(config.from_minor(modifier.price_minor)))
        form.addRow("فرق السعر", self.price_spin)
        root.addLayout(form)

        hint = QLabel("استخدم قيمة سالبة للحجم الأصغر (مثال: −300).")
        hint.setObjectName("HintText")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self.default_check = QCheckBox("الخيار الافتراضي")
        self.default_check.setChecked(modifier.is_default if modifier else False)
        root.addWidget(self.default_check)

        self.active_check = QCheckBox("مُفعّل")
        self.active_check.setChecked(modifier.is_active if modifier else True)
        root.addWidget(self.active_check)

        self.toast = Toast()
        root.addWidget(self.toast)

        buttons = QDialogButtonBox()
        save = buttons.addButton("حفظ", QDialogButtonBox.ButtonRole.AcceptRole)
        save.setObjectName("PrimaryAction")
        save.setMinimumHeight(config.MIN_TOUCH_TARGET)
        cancel = buttons.addButton("إلغاء", QDialogButtonBox.ButtonRole.RejectRole)
        cancel.setObjectName("GhostAction")
        cancel.setMinimumHeight(config.MIN_TOUCH_TARGET)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _save(self) -> None:
        payload = {
            "name_ar": self.name_edit.text().strip(),
            "price_minor": config.to_minor(self.price_spin.value()),
            "is_default": int(self.default_check.isChecked()),
            "is_active": int(self.active_check.isChecked()),
        }
        try:
            if self.modifier is None:
                self.controller.create_modifier(self.group.id, **payload)
            else:
                self.controller.update_modifier(self.modifier.id, **payload)
        except MenuError as exc:
            self.toast.show_message(str(exc), "error", 5000)
            return
        except Exception as exc:
            logger.exception("تعذّر حفظ الخيار")
            self.toast.show_message(f"تعذّر الحفظ: {exc}", "error", 5000)
            return
        self.accept()


# --------------------------------------------------------------------------- #
# Main editor
# --------------------------------------------------------------------------- #
class MenuEditorView(QWidget):
    """Three-pane menu editor."""

    def __init__(self, auth, controller: AdminController,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.auth = auth
        self.controller = controller
        self._category_id: int | None = None
        self._selected_product = None
        self._category_buttons: dict[int, QPushButton] = {}

        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self._build()
        self.reload()

    # -- construction ------------------------------------------------------ #
    def _build(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        root.addWidget(self._build_category_rail())
        root.addWidget(self._build_products_panel(), 1)
        root.addWidget(self._build_editor_panel())

    def _build_category_rail(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("Card")
        panel.setFixedWidth(230)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        title = QLabel("التصنيفات")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)
        layout.addWidget(Divider())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        self.category_layout = QVBoxLayout(container)
        self.category_layout.setContentsMargins(0, 0, 0, 0)
        self.category_layout.setSpacing(6)
        self.category_layout.addStretch(1)
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

        add = QPushButton("+ تصنيف جديد")
        add.setObjectName("PrimaryAction")
        add.setMinimumHeight(config.MIN_TOUCH_TARGET)
        add.clicked.connect(self._add_category)
        layout.addWidget(add)

        edit = QPushButton("تعديل التصنيف")
        edit.setObjectName("GhostAction")
        edit.setMinimumHeight(config.MIN_TOUCH_TARGET)
        edit.clicked.connect(self._edit_category)
        layout.addWidget(edit)

        remove = QPushButton("حذف التصنيف")
        remove.setObjectName("DangerAction")
        remove.setMinimumHeight(config.MIN_TOUCH_TARGET)
        remove.clicked.connect(self._delete_category)
        layout.addWidget(remove)

        self.toast = Toast()
        layout.addWidget(self.toast)
        return panel

    def _build_products_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("Card")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        header = QHBoxLayout()
        self.products_title = QLabel("الأصناف")
        self.products_title.setObjectName("SectionTitle")
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("ابحث…")
        self.search_edit.setMinimumHeight(config.MIN_TOUCH_TARGET - 8)
        self.search_edit.setMaximumWidth(240)
        self.search_edit.textChanged.connect(self._load_products)
        self.show_disabled = QCheckBox("إظهار غير المُفعّلة")
        self.show_disabled.stateChanged.connect(self._load_products)
        header.addWidget(self.products_title)
        header.addStretch(1)
        header.addWidget(self.show_disabled)
        header.addWidget(self.search_edit)
        layout.addLayout(header)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["الصنف", "السعر", "التكلفة", "الهامش", "الحالة"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.itemSelectionChanged.connect(self._on_product_selected)
        header_view = self.table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 5):
            header_view.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        add = QPushButton("+ صنف جديد")
        add.setObjectName("PrimaryAction")
        add.setMinimumHeight(config.MIN_TOUCH_TARGET)
        add.clicked.connect(self._add_product)
        edit = QPushButton("تعديل")
        edit.setObjectName("GhostAction")
        edit.setMinimumHeight(config.MIN_TOUCH_TARGET)
        edit.clicked.connect(self._edit_product)
        toggle = QPushButton("إيقاف / تفعيل")
        toggle.setObjectName("GhostAction")
        toggle.setMinimumHeight(config.MIN_TOUCH_TARGET)
        toggle.clicked.connect(self._toggle_product)
        actions.addWidget(add, 2)
        actions.addWidget(edit, 1)
        actions.addWidget(toggle, 1)
        layout.addLayout(actions)
        return panel

    def _build_editor_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("Card")
        panel.setFixedWidth(330)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        self.editor_title = QLabel("اختر صنفاً")
        self.editor_title.setObjectName("SectionTitle")
        self.editor_title.setWordWrap(True)
        layout.addWidget(self.editor_title)
        layout.addWidget(Divider())

        self.editor_summary = QLabel("—")
        self.editor_summary.setObjectName("HintText")
        self.editor_summary.setWordWrap(True)
        layout.addWidget(self.editor_summary)

        groups_label = QLabel("مجموعات الخيارات المتاحة لهذا الصنف")
        groups_label.setObjectName("FieldLabel")
        groups_label.setWordWrap(True)
        layout.addWidget(groups_label)

        self.groups_list = QListWidget()
        self.groups_list.setMinimumHeight(190)
        layout.addWidget(self.groups_list)

        save_groups = QPushButton("حفظ مجموعات الخيارات")
        save_groups.setObjectName("GhostAction")
        save_groups.setMinimumHeight(config.MIN_TOUCH_TARGET)
        save_groups.clicked.connect(self._save_groups)
        layout.addWidget(save_groups)

        layout.addWidget(Divider())
        mods_label = QLabel("مجموعات الخيارات في النظام")
        mods_label.setObjectName("FieldLabel")
        layout.addWidget(mods_label)

        self.modifier_groups_list = QListWidget()
        self.modifier_groups_list.setMinimumHeight(150)
        self.modifier_groups_list.itemSelectionChanged.connect(self._load_modifier_options)
        layout.addWidget(self.modifier_groups_list)

        self.modifier_options_list = QListWidget()
        self.modifier_options_list.setMinimumHeight(150)
        layout.addWidget(self.modifier_options_list)

        mod_actions = QHBoxLayout()
        add_mod = QPushButton("+ خيار")
        add_mod.setObjectName("GhostAction")
        add_mod.setMinimumHeight(52)
        add_mod.clicked.connect(self._add_modifier)
        edit_mod = QPushButton("تعديل")
        edit_mod.setObjectName("GhostAction")
        edit_mod.setMinimumHeight(52)
        edit_mod.clicked.connect(self._edit_modifier)
        del_mod = QPushButton("حذف")
        del_mod.setObjectName("DangerAction")
        del_mod.setMinimumHeight(52)
        del_mod.clicked.connect(self._delete_modifier)
        mod_actions.addWidget(add_mod)
        mod_actions.addWidget(edit_mod)
        mod_actions.addWidget(del_mod)
        layout.addLayout(mod_actions)

        layout.addStretch(1)
        return panel

    # -- data -------------------------------------------------------------- #
    def reload(self) -> None:
        self._load_modifier_groups()
        self._load_categories()
        self._load_products()

    def _load_categories(self) -> None:
        while self.category_layout.count() > 1:
            item = self.category_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._category_buttons.clear()

        # Groups must be loaded before a product is selected, because selecting a
        # product builds its modifier-group checklist from self._groups.
        if not getattr(self, "_groups", None):
            self._load_modifier_groups()

        categories = self.controller.list_categories(active_only=False)
        for category in categories:
            button = QPushButton(category.label)
            button.setObjectName("CategoryTab")
            button.setCheckable(True)
            button.setMinimumHeight(60)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, cid=category.id: self._select_category(cid))
            self.category_layout.insertWidget(self.category_layout.count() - 1, button)
            self._category_buttons[category.id] = button

        all_button = QPushButton("كل الأصناف")
        all_button.setObjectName("CategoryTab")
        all_button.setCheckable(True)
        all_button.setMinimumHeight(60)
        all_button.clicked.connect(lambda: self._select_category(None))
        self.category_layout.insertWidget(self.category_layout.count() - 1, all_button)
        self._category_buttons[-1] = all_button

        self._select_category(categories[0].id if categories else None)

    def _select_category(self, category_id: int | None) -> None:
        self._category_id = category_id
        for key, button in self._category_buttons.items():
            button.setChecked(key == category_id or (category_id is None and key == -1))
        self._load_products()

    def _load_products(self) -> None:
        products = self.controller.list_products(
            self._category_id,
            search=self.search_edit.text(),
            active_only=not self.show_disabled.isChecked(),
        )
        self.table.setRowCount(len(products))
        money = config.format_money
        for row, product in enumerate(products):
            margin = product.margin_minor
            values = [
                product.display_name,
                money(product.price_minor),
                money(product.cost_minor),
                money(margin),
                "مُفعّل" if product.is_active else "موقوف",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, product.id)
                if column:
                    item.setTextAlignment(int(Qt.AlignmentFlag.AlignLeft
                                              | Qt.AlignmentFlag.AlignVCenter))
                self.table.setItem(row, column, item)
        self.products_title.setText(f"الأصناف — {len(products)}")

        # Select the first row so the editor panel is populated on open. Without
        # this the editor shows its "اختر صنفاً" placeholder and the whole screen
        # looks broken until the admin clicks something.
        #
        # setCurrentCell, not selectRow: QTableWidget.selectRow() is a no-op here
        # (verified — the selection stays empty), while setCurrentCell triggers
        # the selection and the itemSelectionChanged signal we depend on.
        if products and not self.table.selectionModel().selectedRows():
            self.table.setCurrentCell(0, 0)

    def _load_modifier_groups(self) -> None:
        self.modifier_groups_list.clear()
        self._groups = self.controller.list_modifier_groups(with_options=True)
        for group in self._groups:
            label = f"{group.display_name}  ({len(group.options)})"
            if group.is_required:
                label += "  • مطلوب"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, group.id)
            self.modifier_groups_list.addItem(item)
        if self._groups:
            self.modifier_groups_list.setCurrentRow(0)

    def _load_modifier_options(self) -> None:
        self.modifier_options_list.clear()
        group = self._current_group()
        if group is None:
            return
        for option in group.options:
            label = option.display_name
            if option.price_minor:
                label += f"   ({option.price_label})"
            if option.is_default:
                label += "  ★"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, option.id)
            self.modifier_options_list.addItem(item)

    def _current_group(self):
        item = self.modifier_groups_list.currentItem()
        if item is None:
            return None
        group_id = item.data(Qt.ItemDataRole.UserRole)
        return next((g for g in self._groups if g.id == group_id), None)

    def _current_modifier(self):
        group = self._current_group()
        item = self.modifier_options_list.currentItem()
        if group is None or item is None:
            return None
        modifier_id = item.data(Qt.ItemDataRole.UserRole)
        return next((m for m in group.options if m.id == modifier_id), None)

    # -- product selection -------------------------------------------------- #
    def _selected_product_id(self) -> int | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        item = self.table.item(rows[0].row(), 0)
        return int(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def _on_product_selected(self) -> None:
        product_id = self._selected_product_id()
        if product_id is None:
            return
        product = self.controller.get_product(product_id)
        self._selected_product = product
        if product is None:
            return

        from models.inventory import InventoryRepository

        inventory = InventoryRepository(self.controller.db)
        recipe_cost = inventory.product_cost_minor(product.id)
        self.editor_title.setText(product.display_name)
        self.editor_summary.setText(
            f"السعر: {config.format_money(product.price_minor)} (شامل الضريبة)\n"
            f"تكلفة الوصفة: {config.format_money(recipe_cost)}\n"
            f"الهامش الفعلي: {config.format_money(product.price_minor - recipe_cost)}\n"
            f"خصم المخزون: {'نعم' if product.track_inventory else 'لا'}"
        )

        # checklist of modifier groups
        self.groups_list.clear()
        attached = set(self.controller.groups_for_product(product.id))
        for group in self._groups:
            item = QListWidgetItem(group.display_name)
            item.setData(Qt.ItemDataRole.UserRole, group.id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if group.id in attached else Qt.CheckState.Unchecked
            )
            self.groups_list.addItem(item)

    # -- category actions --------------------------------------------------- #
    def _add_category(self) -> None:
        from PyQt6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(self, "تصنيف جديد", "اسم التصنيف")
        if not ok or not name.strip():
            return
        try:
            self.controller.create_category(name.strip())
        except MenuError as exc:
            self.toast.show_message(str(exc), "error", 5000)
            return
        self._load_categories()
        self.toast.show_message("تم إضافة التصنيف", "success", 3000)

    def _edit_category(self) -> None:
        from PyQt6.QtWidgets import QInputDialog

        if self._category_id is None:
            self.toast.show_message("اختر تصنيفاً أولاً", "warning", 3000)
            return
        current = next((c for c in self.controller.list_categories(False)
                        if c.id == self._category_id), None)
        if current is None:
            return
        name, ok = QInputDialog.getText(self, "تعديل التصنيف", "اسم التصنيف",
                                        text=current.name_ar)
        if not ok or not name.strip():
            return
        try:
            self.controller.update_category(self._category_id, name_ar=name.strip())
        except MenuError as exc:
            self.toast.show_message(str(exc), "error", 5000)
            return
        self._load_categories()
        self.toast.show_message("تم تحديث التصنيف", "success", 3000)

    def _delete_category(self) -> None:
        from PyQt6.QtWidgets import QMessageBox

        if self._category_id is None:
            return
        confirm = QMessageBox.question(self, "حذف التصنيف", "هل تريد حذف هذا التصنيف؟")
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            self.controller.delete_category(self._category_id)
        except MenuError as exc:
            self.toast.show_message(str(exc), "error", 6000)
            return
        self._load_categories()
        self.toast.show_message("تم حذف التصنيف", "success", 3000)

    # -- product actions ---------------------------------------------------- #
    def _add_product(self) -> None:
        categories = self.controller.list_categories(active_only=False)
        if not categories:
            self.toast.show_message("أضف تصنيفاً أولاً", "warning", 4000)
            return
        dialog = ProductDialog(self.controller, categories, None, parent=self)
        if dialog.exec() == dialog.DialogCode.Accepted:
            self._load_products()
            self._load_categories()
            self.toast.show_message("تم إضافة الصنف", "success", 3000)

    def _edit_product(self) -> None:
        product = self._selected_product
        if product is None:
            self.toast.show_message("اختر صنفاً أولاً", "warning", 3000)
            return
        categories = self.controller.list_categories(active_only=False)
        dialog = ProductDialog(self.controller, categories, product, parent=self)
        if dialog.exec() == dialog.DialogCode.Accepted:
            self._load_products()
            self._on_product_selected()
            self.toast.show_message("تم تحديث الصنف", "success", 3000)

    def _toggle_product(self) -> None:
        product = self._selected_product
        if product is None:
            self.toast.show_message("اختر صنفاً أولاً", "warning", 3000)
            return
        try:
            self.controller.set_product_active(product.id, not product.is_active)
        except MenuError as exc:
            self.toast.show_message(str(exc), "error", 5000)
            return
        self._load_products()
        self._on_product_selected()
        self.toast.show_message(
            "تم تفعيل الصنف" if not product.is_active else "تم إيقاف الصنف",
            "success", 3000,
        )

    def _save_groups(self) -> None:
        product = self._selected_product
        if product is None:
            return
        group_ids = [
            self.groups_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.groups_list.count())
            if self.groups_list.item(i).checkState() == Qt.CheckState.Checked
        ]
        try:
            self.controller.set_product_groups(product.id, group_ids)
        except MenuError as exc:
            self.toast.show_message(str(exc), "error", 5000)
            return
        self.toast.show_message("تم حفظ مجموعات الخيارات", "success", 3000)

    # -- modifier actions --------------------------------------------------- #
    def _add_modifier(self) -> None:
        group = self._current_group()
        if group is None:
            self.toast.show_message("اختر مجموعة خيارات", "warning", 3000)
            return
        dialog = ModifierDialog(self.controller, group, None, parent=self)
        if dialog.exec() == dialog.DialogCode.Accepted:
            self._load_modifier_groups()
            self._load_modifier_options()

    def _edit_modifier(self) -> None:
        group = self._current_group()
        modifier = self._current_modifier()
        if group is None or modifier is None:
            self.toast.show_message("اختر خياراً", "warning", 3000)
            return
        dialog = ModifierDialog(self.controller, group, modifier, parent=self)
        if dialog.exec() == dialog.DialogCode.Accepted:
            self._load_modifier_groups()
            self._load_modifier_options()

    def _delete_modifier(self) -> None:
        modifier = self._current_modifier()
        if modifier is None:
            self.toast.show_message("اختر خياراً", "warning", 3000)
            return
        try:
            self.controller.delete_modifier(modifier.id)
        except MenuError as exc:
            self.toast.show_message(str(exc), "error", 5000)
            return
        self._load_modifier_groups()
        self._load_modifier_options()
        self.toast.show_message("تم حذف الخيار", "success", 3000)
