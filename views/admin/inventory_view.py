"""
views/admin/inventory_view.py — ingredients, recipes and stock movements.

Three panes: ingredients (with low-stock flags), the recipe of the selected
product, and the stock movement ledger. Stock actions (receive / waste /
stocktake) all write a movement row, so the ledger is the source of truth.
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
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

import config
from controllers.admin_controller import AdminController, MenuError
from models.inventory import InventoryError, InventoryRepository
from views.widgets import Divider, Toast

logger = logging.getLogger(__name__)

__all__ = ["InventoryView", "StockActionDialog", "RecipeLineDialog"]


class StockActionDialog(QDialog):
    """Receive stock, record waste, or set a counted quantity."""

    def __init__(self, item, action: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.item = item
        self.action = action

        titles = {"receive": "توريد كمية", "waste": "تسجيل هدر", "count": "جرد المخزون"}
        self.setWindowTitle(titles.get(action, "حركة مخزون"))
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setModal(True)
        self.setMinimumWidth(440)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 20)
        root.setSpacing(12)

        title = QLabel(item.display_name)
        title.setObjectName("SectionTitle")
        root.addWidget(title)

        current = QLabel(f"الكمية الحالية: {item.quantity_label}")
        current.setObjectName("HintText")
        root.addWidget(current)

        form = QFormLayout()
        self.quantity_spin = QDoubleSpinBox()
        self.quantity_spin.setDecimals(2)
        self.quantity_spin.setMaximum(1_000_000)
        self.quantity_spin.setSuffix(f" {item.unit_label}")
        self.quantity_spin.setMinimumHeight(config.MIN_TOUCH_TARGET - 8)
        if action == "count":
            self.quantity_spin.setValue(item.current_qty)
        form.addRow("الكمية", self.quantity_spin)

        self.note_edit = QLineEdit()
        self.note_edit.setMinimumHeight(config.MIN_TOUCH_TARGET - 8)
        self.note_edit.setPlaceholderText("ملاحظة (اختياري)")
        form.addRow("ملاحظة", self.note_edit)
        root.addLayout(form)

        self.toast = Toast()
        root.addWidget(self.toast)

        buttons = QDialogButtonBox()
        save = buttons.addButton("حفظ", QDialogButtonBox.ButtonRole.AcceptRole)
        save.setObjectName("PrimaryAction")
        save.setMinimumHeight(config.MIN_TOUCH_TARGET)
        cancel = buttons.addButton("إلغاء", QDialogButtonBox.ButtonRole.RejectRole)
        cancel.setObjectName("GhostAction")
        cancel.setMinimumHeight(config.MIN_TOUCH_TARGET)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    @property
    def quantity(self) -> float:
        return float(self.quantity_spin.value())

    @property
    def note(self) -> str:
        return self.note_edit.text().strip()


class RecipeLineDialog(QDialog):
    """Add an ingredient to a product's recipe."""

    def __init__(self, items: list, product_name: str, modifiers: list | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("إضافة مكوّن للوصفة")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setModal(True)
        self.setMinimumWidth(480)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 20)
        root.setSpacing(12)

        title = QLabel(f"وصفة: {product_name}")
        title.setObjectName("SectionTitle")
        root.addWidget(title)

        form = QFormLayout()

        self.item_combo = QComboBox()
        for item in items:
            self.item_combo.addItem(f"{item.display_name} ({item.unit_label})", item.id)
        self.item_combo.setMinimumHeight(config.MIN_TOUCH_TARGET - 8)
        form.addRow("المادة", self.item_combo)

        self.quantity_spin = QDoubleSpinBox()
        self.quantity_spin.setDecimals(2)
        self.quantity_spin.setMaximum(100_000)
        self.quantity_spin.setValue(1)
        self.quantity_spin.setMinimumHeight(config.MIN_TOUCH_TARGET - 8)
        form.addRow("الكمية لكل حصة", self.quantity_spin)

        # Conditional rows: deduct this ingredient only when a modifier is chosen.
        self.modifier_combo = QComboBox()
        self.modifier_combo.addItem("دائماً (بغض النظر عن الخيارات)", None)
        for group, modifier in (modifiers or []):
            self.modifier_combo.addItem(f"{group.display_name}: {modifier.display_name}",
                                        modifier.id)
        self.modifier_combo.setMinimumHeight(config.MIN_TOUCH_TARGET - 8)
        form.addRow("مرتبط بخيار", self.modifier_combo)

        root.addLayout(form)

        hint = QLabel(
            "اختر خياراً عندما يجب خصم هذه المادة بدلاً من المادة الأساسية "
            "(مثال: حليب الشوفان بدل الحليب العادي)."
        )
        hint.setObjectName("HintText")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self.toast = Toast()
        root.addWidget(self.toast)

        buttons = QDialogButtonBox()
        save = buttons.addButton("إضافة", QDialogButtonBox.ButtonRole.AcceptRole)
        save.setObjectName("PrimaryAction")
        save.setMinimumHeight(config.MIN_TOUCH_TARGET)
        cancel = buttons.addButton("إلغاء", QDialogButtonBox.ButtonRole.RejectRole)
        cancel.setObjectName("GhostAction")
        cancel.setMinimumHeight(config.MIN_TOUCH_TARGET)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    @property
    def inventory_item_id(self) -> int:
        return int(self.item_combo.currentData())

    @property
    def quantity(self) -> float:
        return float(self.quantity_spin.value())

    @property
    def modifier_id(self) -> int | None:
        return self.modifier_combo.currentData()


class InventoryView(QWidget):
    """Ingredients + recipes + ledger."""

    def __init__(self, auth, controller: AdminController,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.auth = auth
        self.controller = controller
        self.inventory = InventoryRepository(controller.db)
        self._selected_item = None
        self._selected_product_id: int | None = None

        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self._build()
        self.reload()

    # -- construction ------------------------------------------------------ #
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        root.addWidget(self._build_summary())

        tabs = QTabWidget()
        tabs.addTab(self._build_ingredients_tab(), "المواد الخام")
        tabs.addTab(self._build_recipes_tab(), "الوصفات")
        tabs.addTab(self._build_ledger_tab(), "حركات المخزون")
        root.addWidget(tabs, 1)

        self.toast = Toast()
        root.addWidget(self.toast)

    def _build_summary(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("Card")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(16)

        self.summary_label = QLabel("")
        self.summary_label.setObjectName("SectionTitle")
        layout.addWidget(self.summary_label)
        layout.addStretch(1)

        self.low_stock_label = QLabel("")
        self.low_stock_label.setObjectName("WarningText")
        self.low_stock_label.setWordWrap(True)
        layout.addWidget(self.low_stock_label)
        return bar

    def _build_ingredients_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        for label, handler, obj in (
            ("+ مادة جديدة", self._add_item, "PrimaryAction"),
            ("توريد", lambda: self._stock_action("receive"), "GhostAction"),
            ("هدر", lambda: self._stock_action("waste"), "GhostAction"),
            ("جرد", lambda: self._stock_action("count"), "GhostAction"),
            ("إيقاف / تفعيل", self._toggle_item, "GhostAction"),
        ):
            button = QPushButton(label)
            button.setObjectName(obj)
            button.setMinimumHeight(config.MIN_TOUCH_TARGET)
            button.clicked.connect(handler)
            actions.addWidget(button)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.items_table = QTableWidget(0, 6)
        self.items_table.setHorizontalHeaderLabels(
            ["المادة", "الوحدة", "الكمية", "الحد الأدنى", "تكلفة الوحدة", "الحالة"]
        )
        self.items_table.verticalHeader().setVisible(False)
        self.items_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.items_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.items_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.items_table.setAlternatingRowColors(True)
        self.items_table.itemSelectionChanged.connect(self._on_item_selected)
        header = self.items_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 6):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.items_table, 1)
        return page

    def _build_recipes_tab(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)

        # product picker
        left = QFrame()
        left.setObjectName("Card")
        left.setFixedWidth(300)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 12, 12, 12)
        left_layout.setSpacing(8)
        left_layout.addWidget(self._label("اختر صنفاً", "SectionTitle"))

        self.product_search = QLineEdit()
        self.product_search.setPlaceholderText("ابحث…")
        self.product_search.setMinimumHeight(config.MIN_TOUCH_TARGET - 8)
        self.product_search.textChanged.connect(self._load_recipe_products)
        left_layout.addWidget(self.product_search)

        self.recipe_products = QTableWidget(0, 1)
        self.recipe_products.setHorizontalHeaderLabels(["الصنف"])
        self.recipe_products.verticalHeader().setVisible(False)
        self.recipe_products.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.recipe_products.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.recipe_products.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.recipe_products.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.recipe_products.itemSelectionChanged.connect(self._on_recipe_product_selected)
        left_layout.addWidget(self.recipe_products, 1)
        layout.addWidget(left)

        # recipe lines
        right = QFrame()
        right.setObjectName("Card")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(12, 12, 12, 12)
        right_layout.setSpacing(8)
        self.recipe_title = self._label("وصفة الصنف", "SectionTitle")
        right_layout.addWidget(self.recipe_title)

        self.recipe_summary = self._label("—", "HintText")
        self.recipe_summary.setWordWrap(True)
        right_layout.addWidget(self.recipe_summary)
        right_layout.addWidget(Divider())

        self.recipe_table = QTableWidget(0, 4)
        self.recipe_table.setHorizontalHeaderLabels(["المادة", "الكمية", "التكلفة", "شرط"])
        self.recipe_table.verticalHeader().setVisible(False)
        self.recipe_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.recipe_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.recipe_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.recipe_table.setAlternatingRowColors(True)
        header = self.recipe_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 4):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        right_layout.addWidget(self.recipe_table, 1)

        recipe_actions = QHBoxLayout()
        add = QPushButton("+ مكوّن")
        add.setObjectName("PrimaryAction")
        add.setMinimumHeight(config.MIN_TOUCH_TARGET)
        add.clicked.connect(self._add_recipe_line)
        remove = QPushButton("حذف المكوّن")
        remove.setObjectName("DangerAction")
        remove.setMinimumHeight(config.MIN_TOUCH_TARGET)
        remove.clicked.connect(self._remove_recipe_line)
        recipe_actions.addWidget(add, 2)
        recipe_actions.addWidget(remove, 1)
        right_layout.addLayout(recipe_actions)

        self.deduction_preview = self._label("", "HintText")
        self.deduction_preview.setWordWrap(True)
        right_layout.addWidget(self.deduction_preview)

        layout.addWidget(right, 1)
        return page

    def _build_ledger_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        filter_row = QHBoxLayout()
        filter_row.addWidget(self._label("حركات المخزون (الأحدث أولاً)", "SectionTitle"))
        filter_row.addStretch(1)
        self.reason_combo = QComboBox()
        self.reason_combo.addItem("كل الأسباب", None)
        for reason, label in ((config.STOCK_SALE, "بيع"), (config.STOCK_PURCHASE, "توريد"),
                              (config.STOCK_WASTE, "هدر"),
                              (config.STOCK_ADJUSTMENT, "تسوية"),
                              (config.STOCK_RETURN, "مرتجع")):
            self.reason_combo.addItem(label, reason)
        self.reason_combo.setMinimumHeight(config.MIN_TOUCH_TARGET - 8)
        self.reason_combo.currentIndexChanged.connect(self._load_ledger)
        filter_row.addWidget(self.reason_combo)
        layout.addLayout(filter_row)

        self.ledger_table = QTableWidget(0, 5)
        self.ledger_table.setHorizontalHeaderLabels(
            ["التاريخ", "المادة", "التغيير", "السبب", "ملاحظة"]
        )
        self.ledger_table.verticalHeader().setVisible(False)
        self.ledger_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.ledger_table.setAlternatingRowColors(True)
        header = self.ledger_table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        for column in (0, 2, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.ledger_table, 1)

        self.usage_label = self._label("", "HintText")
        self.usage_label.setWordWrap(True)
        layout.addWidget(self.usage_label)
        return page

    @staticmethod
    def _label(text: str, object_name: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName(object_name)
        return label

    # -- data -------------------------------------------------------------- #
    def reload(self) -> None:
        self._load_items()
        self._load_recipe_products()
        self._load_ledger()
        self._load_summary()

    def _load_summary(self) -> None:
        items = self.inventory.list_items()
        low = [i for i in items if i.is_low]
        value = self.inventory.stock_value_minor()
        self.summary_label.setText(
            f"المواد الخام: {len(items)}   |   قيمة المخزون التقديرية: "
            f"{config.format_money(value)}"
        )
        if low:
            names = "، ".join(f"{i.display_name} ({i.quantity_label})" for i in low[:4])
            more = f" و{len(low) - 4} أخرى" if len(low) > 4 else ""
            self.low_stock_label.setText(f"⚠ مواد منخفضة: {names}{more}")
            self.low_stock_label.setVisible(True)
        else:
            self.low_stock_label.setVisible(False)

    def _load_items(self) -> None:
        items = self.inventory.list_items(active_only=False)
        self.items_table.setRowCount(len(items))
        for row, item in enumerate(items):
            state = "موقوف" if not item.is_active else ("نفدت" if item.is_out
                                                        else ("منخفض" if item.is_low else "جيد"))
            values = [
                item.display_name,
                item.unit_label,
                f"{item.current_qty:g}",
                f"{item.min_qty:g}",
                config.format_money(int(round(item.cost_per_unit_minor))),
                state,
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setData(Qt.ItemDataRole.UserRole, item.id)
                if column:
                    cell.setTextAlignment(int(Qt.AlignmentFlag.AlignLeft
                                              | Qt.AlignmentFlag.AlignVCenter))
                self.items_table.setItem(row, column, cell)

    def _on_item_selected(self) -> None:
        rows = self.items_table.selectionModel().selectedRows()
        if not rows:
            return
        item_id = int(self.items_table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole))
        self._selected_item = self.inventory.get_item(item_id)

    def _load_recipe_products(self) -> None:
        products = self.controller.list_products(search=self.product_search.text())
        self.recipe_products.setRowCount(len(products))
        for row, product in enumerate(products):
            cell = QTableWidgetItem(product.display_name)
            cell.setData(Qt.ItemDataRole.UserRole, product.id)
            self.recipe_products.setItem(row, 0, cell)
        # Keep a row selected across reloads — setRowCount() clears the selection,
        # which would otherwise leave the recipe pane blank after a refresh.
        # setCurrentCell rather than selectRow (see menu_editor for why).
        if products and not self.recipe_products.selectionModel().selectedRows():
            self.recipe_products.setCurrentCell(0, 0)

    def _on_recipe_product_selected(self) -> None:
        rows = self.recipe_products.selectionModel().selectedRows()
        if not rows:
            return
        self._selected_product_id = int(
            self.recipe_products.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        )
        self._load_recipe()

    def _load_recipe(self) -> None:
        product_id = self._selected_product_id
        if product_id is None:
            return
        product = self.controller.get_product(product_id)
        lines = self.inventory.recipes_for_product(product_id)
        self.recipe_title.setText(f"وصفة: {product.display_name if product else product_id}")

        total = sum(line.cost_minor for line in lines if not line.is_conditional)
        self.recipe_summary.setText(
            f"عدد المكوّنات: {len(lines)}   |   تكلفة الحصة: {config.format_money(total)}"
            + (f"\nسعر البيع: {config.format_money(product.price_minor)} — "
               f"الهامش: {config.format_money(product.price_minor - total)}"
               if product else "")
        )

        self.recipe_table.setRowCount(len(lines))
        for row, line in enumerate(lines):
            values = [
                line.ingredient_name,
                line.quantity_label,
                config.format_money(line.cost_minor),
                line.modifier_name or "دائماً",
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setData(Qt.ItemDataRole.UserRole, line.id)
                if column:
                    cell.setTextAlignment(int(Qt.AlignmentFlag.AlignLeft
                                              | Qt.AlignmentFlag.AlignVCenter))
                self.recipe_table.setItem(row, column, cell)

        preview = self.inventory.preview_deduction(product_id, 1)
        self.deduction_preview.setText(
            "الخصم عند بيع حصة واحدة: "
            + ("، ".join(line.label for line in preview) if preview else "لا يوجد خصم")
        )

    def _load_ledger(self) -> None:
        reason = self.reason_combo.currentData()
        movements = self.inventory.movements(limit=200, reason=reason)
        self.ledger_table.setRowCount(len(movements))
        for row, movement in enumerate(movements):
            values = [
                movement.created_at,
                movement.ingredient_name,
                movement.change_label,
                movement.reason_label,
                movement.note,
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                if column == 2:
                    cell.setForeground(
                        __import__("PyQt6.QtGui", fromlist=["QColor"]).QColor(
                            "#C4593F" if movement.is_deduction else "#6E9C6B"
                        )
                    )
                if column:
                    cell.setTextAlignment(int(Qt.AlignmentFlag.AlignLeft
                                              | Qt.AlignmentFlag.AlignVCenter))
                self.ledger_table.setItem(row, column, cell)

        usage = self.inventory.consumption_since()
        if usage:
            parts = []
            for name, used, unit in usage[:6]:
                unit_label = {"g": "غ", "ml": "مل", "piece": "ق"}.get(unit, unit)
                parts.append(f"{name} {used:g} {unit_label}")
            self.usage_label.setText("إجمالي المستهلك من البيع: " + "، ".join(parts))
        else:
            self.usage_label.setText("لا توجد استهلاكات مسجلة بعد")

    # -- actions ------------------------------------------------------------ #
    def _add_item(self) -> None:
        from PyQt6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(self, "مادة جديدة", "اسم المادة")
        if not ok or not name.strip():
            return
        unit_labels = [("غرام", config.UNIT_GRAM), ("مل", config.UNIT_MILLILITER),
                       ("قطعة", config.UNIT_PIECE)]
        unit_name, ok = QInputDialog.getItem(
            self, "الوحدة", "وحدة القياس", [label for label, _ in unit_labels], 0, False
        )
        if not ok:
            return
        unit = next(u for label, u in unit_labels if label == unit_name)
        try:
            self.inventory.create_item(name.strip(), unit)
        except InventoryError as exc:
            self.toast.show_message(str(exc), "error", 5000)
            return
        self.reload()
        self.toast.show_message("تم إضافة المادة", "success", 3000)

    def _stock_action(self, action: str) -> None:
        if self._selected_item is None:
            self.toast.show_message("اختر مادة أولاً", "warning", 3000)
            return
        dialog = StockActionDialog(self._selected_item, action, parent=self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        user = getattr(self.auth, "current_user", None)
        user_id = getattr(user, "id", None)
        try:
            if action == "receive":
                self.inventory.receive_stock(self._selected_item.id, dialog.quantity,
                                             dialog.note, user_id)
            elif action == "waste":
                self.inventory.record_waste(self._selected_item.id, dialog.quantity,
                                            dialog.note, user_id)
            else:
                self.inventory.set_counted_stock(self._selected_item.id, dialog.quantity,
                                                 dialog.note, user_id)
        except InventoryError as exc:
            self.toast.show_message(str(exc), "error", 5000)
            return
        self.reload()
        self.toast.show_message("تم تسجيل الحركة", "success", 3000)

    def _toggle_item(self) -> None:
        if self._selected_item is None:
            self.toast.show_message("اختر مادة أولاً", "warning", 3000)
            return
        self.inventory.set_active(self._selected_item.id, not self._selected_item.is_active)
        self.reload()

    def _add_recipe_line(self) -> None:
        if self._selected_product_id is None:
            self.toast.show_message("اختر صنفاً أولاً", "warning", 3000)
            return
        items = self.inventory.list_items()
        if not items:
            self.toast.show_message("أضف مواد خام أولاً", "warning", 4000)
            return
        product = self.controller.get_product(self._selected_product_id)

        # every (group, modifier) pair, so a conditional row can be authored
        pairs = []
        for group in self.controller.list_modifier_groups(with_options=True):
            for modifier in group.options:
                pairs.append((group, modifier))

        dialog = RecipeLineDialog(items, product.display_name, pairs, parent=self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        try:
            self.inventory.add_recipe_line(self._selected_product_id,
                                           dialog.inventory_item_id,
                                           dialog.quantity, dialog.modifier_id)
        except InventoryError as exc:
            self.toast.show_message(str(exc), "error", 5000)
            return
        self._load_recipe()
        self.toast.show_message("تم إضافة المكوّن", "success", 3000)

    def _remove_recipe_line(self) -> None:
        rows = self.recipe_table.selectionModel().selectedRows()
        if not rows:
            self.toast.show_message("اختر مكوّناً", "warning", 3000)
            return
        recipe_id = int(self.recipe_table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole))
        self.inventory.remove_recipe_line(recipe_id)
        self._load_recipe()
        self.toast.show_message("تم حذف المكوّن", "success", 3000)
