"""
views/cashier/pos_main.py — the cashier's till screen.

Three zones (see config.POS_LAYOUT_MIRROR_RTL for why the sides are mirrored):
    RIGHT  : category rail (large toggle buttons, one per category)
    CENTER : product grid, a wrapping flow of >=150x96 touch tiles
    LEFT   : live order cart (lines, quantity steppers, totals, checkout)

The whole screen is Arabic RTL. The product grid and cart keep the app
direction; only the numeric keypads inside dialogs opt out to LTR.

Responsiveness: the product grid is a FlowLayout, so it reflows to as many
columns as the window allows while every tile stays above the touch minimum.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
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
from controllers.pos_controller import PosController
from models.order import CartLine
from models.product import Product
from views.cashier.cash_dialog import PaymentDialog
from views.cashier.modifier_dialog import ModifierDialog
from views.widgets import Divider, FlowLayout, QuantityStepper, Toast

logger = logging.getLogger(__name__)

__all__ = ["PosMainView", "ProductTile", "CartLineWidget"]

isolate = lambda text: f"\u2066{text}\u2069"  # noqa: E731


# --------------------------------------------------------------------------- #
# Product tile
# --------------------------------------------------------------------------- #
class ProductTile(QPushButton):
    """One menu item: name + tax-inclusive price, sized for a finger."""

    def __init__(self, product: Product, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.product = product
        self.setObjectName("ProductTile")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumSize(config.PRODUCT_TILE_MIN_WIDTH, config.PRODUCT_TILE_MIN_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setText(f"{product.display_name}\n{isolate(product.price_label)}")
        self.setToolTip(f"{product.display_name} — {product.price_label}")
        if not product.is_active:
            self.setEnabled(False)


# --------------------------------------------------------------------------- #
# Cart line
# --------------------------------------------------------------------------- #
class CartLineWidget(QFrame):
    """One cart row: name, chosen options, quantity stepper, line total, delete."""

    increment = pyqtSignal(int)
    decrement = pyqtSignal(int)
    remove = pyqtSignal(int)

    def __init__(self, line: CartLine, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.line = line
        self.setObjectName("CartLine")

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 10)
        root.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(10)

        name = QLabel(line.display_name)
        name.setObjectName("CartLineName")
        name.setWordWrap(True)

        total = QLabel(config.format_money(line.line_total_minor))
        total.setObjectName("MoneyValue")

        delete = QPushButton("✕ حذف")
        delete.setObjectName("LineDelete")
        delete.setCursor(Qt.CursorShape.PointingHandCursor)
        delete.setToolTip("حذف الصنف")
        delete.clicked.connect(lambda: self.remove.emit(line.line_id))

        top.addWidget(name, 1)
        top.addWidget(total)
        top.addWidget(delete)
        root.addLayout(top)

        details = line.detail_lines()
        if details:
            detail = QLabel(" • ".join(details))
            detail.setObjectName("HintText")
            detail.setWordWrap(True)
            root.addWidget(detail)

        bottom = QHBoxLayout()
        bottom.setSpacing(10)

        stepper = QuantityStepper(line.line_id, line.quantity)
        stepper.increment_requested.connect(self.increment.emit)
        stepper.decrement_requested.connect(self.decrement.emit)

        unit = QLabel(f"الوحدة: {config.format_money(line.unit_price_minor)}")
        unit.setObjectName("HintText")

        bottom.addWidget(stepper)
        bottom.addStretch(1)
        bottom.addWidget(unit)
        root.addLayout(bottom)


# --------------------------------------------------------------------------- #
# Main POS view
# --------------------------------------------------------------------------- #
class PosMainView(QWidget):
    """Category rail + product grid + live cart."""

    checkout_completed = pyqtSignal(object)      # OrderRecord
    close_shift_requested = pyqtSignal()
    logout_requested = pyqtSignal()

    def __init__(self, auth, controller: PosController | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.auth = auth
        self.controller = controller or PosController(auth=auth)
        self._category_id: int | None = None
        self._category_buttons: dict[int, QPushButton] = {}
        self.shift = None

        self.setObjectName("PosMainView")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self._build()
        self.reload_menu()
        self.refresh_cart()

    # -- shift ------------------------------------------------------------- #
    def set_shift(self, shift) -> None:
        """
        Attach the open shift. The POS refuses to take money without one, which
        is what makes the mandatory open-shift step real rather than cosmetic.
        """
        self.shift = shift
        if shift is not None:
            self.shift_button.setText(f"وردية #{shift.id}")
            self.shift_button.setToolTip(
                f"فُتحت {shift.opened_at} — رصيد البداية "
                f"{config.format_money(shift.opening_float_minor)}"
            )
        else:
            self.shift_button.setText("لا توجد وردية")
            self.shift_button.setToolTip("لا توجد وردية مفتوحة")
        self.refresh_cart()

    # -- construction ------------------------------------------------------ #
    def _build(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(14)

        rail = self._build_category_rail()
        center = self._build_product_area()
        cart = self._build_cart_panel()

        # Widgets are added in reading order: rail, grid, cart.
        #
        # Under the app-wide RTL direction that puts the rail at the reading
        # start (right) and the cart on the far left — the correct Arabic mirror
        # of the spec's "categories left / cart right", which was written for an
        # LTR screen. Set POS_LAYOUT_MIRROR_RTL = False to force the literal
        # LTR placement instead.
        if not config.POS_LAYOUT_MIRROR_RTL:
            root.setLayoutDirection(Qt.LayoutDirection.LeftToRight)

        root.addWidget(rail)
        root.addWidget(center, 1)
        root.addWidget(cart)

    def _build_category_rail(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("Card")
        panel.setFixedWidth(config.CATEGORY_RAIL_WIDTH)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        title = QLabel("الأصناف")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        self.category_layout = QVBoxLayout(container)
        self.category_layout.setContentsMargins(0, 0, 0, 0)
        self.category_layout.setSpacing(8)
        self.category_layout.addStretch(1)
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)
        return panel

    def _build_product_area(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("Card")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(10)

        self.products_title = QLabel("القائمة")
        self.products_title.setObjectName("SectionTitle")

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("ابحث عن صنف…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMinimumHeight(config.MIN_TOUCH_TARGET - 8)
        self.search_edit.setMaximumWidth(320)
        self.search_edit.textChanged.connect(self._on_search)

        header.addWidget(self.products_title)
        header.addStretch(1)
        header.addWidget(self.search_edit)
        layout.addLayout(header)
        layout.addWidget(Divider())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        self.product_flow = FlowLayout(container, margin=0, spacing=config.PRODUCT_GRID_SPACING)
        self.product_container = container
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

        self.toast = Toast()
        layout.addWidget(self.toast)
        return panel

    def _build_cart_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("Card")
        panel.setFixedWidth(config.CART_PANEL_WIDTH)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel("الطلب الحالي")
        title.setObjectName("SectionTitle")
        self.order_type_button = QPushButton("محلي")
        self.order_type_button.setObjectName("OrderTypeChip")
        self.order_type_button.setMinimumHeight(46)
        self.order_type_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.order_type_button.setToolTip("تبديل بين محلي وسفري")
        self.order_type_button.clicked.connect(self._toggle_order_type)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.order_type_button)
        layout.addLayout(header)

        # Shift banner: the cashier can always see which shift they are selling
        # into, and close it from here at end of day.
        shift_row = QHBoxLayout()
        shift_row.setSpacing(8)
        self.shift_button = QPushButton("لا توجد وردية")
        self.shift_button.setObjectName("OrderTypeChip")
        self.shift_button.setMinimumHeight(46)
        self.shift_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.shift_button.clicked.connect(self.close_shift_requested.emit)
        self.shift_button.setToolTip("إغلاق الوردية وتوليد تقرير Z")
        shift_row.addWidget(self.shift_button)
        shift_row.addStretch(1)
        layout.addLayout(shift_row)
        layout.addWidget(Divider())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.cart_container = QWidget()
        self.cart_layout = QVBoxLayout(self.cart_container)
        self.cart_layout.setContentsMargins(0, 0, 6, 0)
        self.cart_layout.setSpacing(8)
        self.cart_layout.addStretch(1)
        scroll.setWidget(self.cart_container)
        layout.addWidget(scroll, 1)

        self.empty_hint = QLabel("السلة فارغة\nاختر صنفاً من القائمة")
        self.empty_hint.setObjectName("HintText")
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty_hint)

        layout.addWidget(Divider())

        totals = QGridLayout()
        totals.setSpacing(6)
        self.subtotal_label = QLabel("0")
        self.subtotal_label.setObjectName("MoneyValue")
        self.discount_label = QLabel("0")
        self.discount_label.setObjectName("MoneyValue")
        self.tax_row_label = QLabel("منها ضريبة")
        self.tax_row_label.setObjectName("FieldLabel")
        self.tax_value_label = QLabel("0")
        self.tax_value_label.setObjectName("MoneyValue")

        totals.addWidget(self._field("المجموع"), 0, 0)
        totals.addWidget(self.subtotal_label, 0, 1, alignment=Qt.AlignmentFlag.AlignLeft)
        totals.addWidget(self._field("الخصم"), 1, 0)
        totals.addWidget(self.discount_label, 1, 1, alignment=Qt.AlignmentFlag.AlignLeft)
        totals.addWidget(self.tax_row_label, 2, 0)
        totals.addWidget(self.tax_value_label, 2, 1, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addLayout(totals)

        total_row = QHBoxLayout()
        total_caption = QLabel("الإجمالي")
        total_caption.setObjectName("SectionTitle")
        self.total_label = QLabel("0")
        self.total_label.setObjectName("TotalValue")
        total_row.addWidget(total_caption)
        total_row.addStretch(1)
        total_row.addWidget(self.total_label)
        layout.addLayout(total_row)

        self.checkout_button = QPushButton("إتمام الدفع")
        self.checkout_button.setObjectName("PrimaryAction")
        self.checkout_button.setMinimumHeight(72)
        self.checkout_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.checkout_button.clicked.connect(self.open_payment)
        layout.addWidget(self.checkout_button)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        clear = QPushButton("إفراغ")
        clear.setObjectName("GhostAction")
        clear.setMinimumHeight(52)
        clear.clicked.connect(self._clear_cart)
        reprint = QPushButton("إعادة طباعة")
        reprint.setObjectName("GhostAction")
        reprint.setMinimumHeight(52)
        reprint.setToolTip("إعادة طباعة آخر إيصال")
        reprint.clicked.connect(self._reprint_last)
        actions.addWidget(clear, 1)
        actions.addWidget(reprint, 1)
        layout.addLayout(actions)
        return panel

    @staticmethod
    def _field(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("FieldLabel")
        return label

    # -- menu -------------------------------------------------------------- #
    def reload_menu(self) -> None:
        """(Re)load categories and the product grid. Safe to call after edits."""
        while self.category_layout.count() > 1:
            item = self.category_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._category_buttons.clear()

        categories = self.controller.load_categories()
        for category in categories:
            button = QPushButton(category.label)
            button.setObjectName("CategoryTab")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setMinimumHeight(68)
            button.clicked.connect(lambda _=False, cid=category.id: self.select_category(cid))
            self.category_layout.insertWidget(self.category_layout.count() - 1, button)
            self._category_buttons[category.id] = button

        all_button = QPushButton(f"الكل  ({self.controller.products.count_products()})")
        all_button.setObjectName("CategoryTab")
        all_button.setCheckable(True)
        all_button.setMinimumHeight(68)
        all_button.setCursor(Qt.CursorShape.PointingHandCursor)
        all_button.clicked.connect(lambda: self.select_category(None))
        self.category_layout.insertWidget(self.category_layout.count() - 1, all_button)
        self._category_buttons[-1] = all_button

        self.select_category(categories[0].id if categories else None)

    def select_category(self, category_id: int | None) -> None:
        self._category_id = category_id
        for key, button in self._category_buttons.items():
            button.setChecked((key == category_id) or (category_id is None and key == -1))
        self.search_edit.clear()
        self.refresh_products()

    def refresh_products(self) -> None:
        while self.product_flow.count():
            item = self.product_flow.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        products = self.controller.load_products(self._category_id, self.search_edit.text())
        for product in products:
            tile = ProductTile(product)
            tile.clicked.connect(lambda _=False, p=product: self.on_product_tapped(p))
            self.product_flow.addWidget(tile)

        self.products_title.setText(
            f"القائمة — {len(products)} صنف" if products else "القائمة — لا نتائج"
        )
        self.product_container.updateGeometry()

    def _on_search(self, text: str) -> None:
        if text.strip():
            self._category_id = None
            for key, button in self._category_buttons.items():
                button.setChecked(key == -1)
        self.refresh_products()

    # -- ordering ---------------------------------------------------------- #
    def on_product_tapped(self, product: Product) -> None:
        """Add straight to the cart, or open the modifier dialog when needed."""
        try:
            if self.controller.needs_modifier_dialog(product):
                selection = self._ask_modifiers(product)
                if selection is None:          # cancelled
                    return
                self.controller.add_product(product, selection)
            else:
                self.controller.add_product(product)
        except Exception as exc:
            logger.exception("تعذّرت إضافة الصنف")
            self.toast.show_message(f"تعذّرت إضافة الصنف: {exc}", "error", 5000)
            return

        self.refresh_cart()
        self.toast.show_message(f"أُضيف {product.display_name}", "success", 1200)

    def _ask_modifiers(self, product: Product):
        """
        Ask for options. Returns a ModifierSelection, or None if cancelled.

        Split out so a headless test can answer without driving a modal exec()
        (which would block forever with no user).
        """
        dialog = ModifierDialog(self.controller, product, parent=self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return None
        return dialog.selection()

    def _toggle_order_type(self) -> None:
        order_type = self.controller.toggle_order_type()
        label = "محلي" if order_type == config.ORDER_TYPE_DINE_IN else "سفري"
        self.order_type_button.setText(label)
        self.toast.show_message(f"نوع الطلب: {label}", "info", 1500)

    def _on_increment(self, line_id: int) -> None:
        self.controller.increment(line_id)
        self.refresh_cart()

    def _on_decrement(self, line_id: int) -> None:
        self.controller.decrement(line_id)
        self.refresh_cart()

    def _on_remove(self, line_id: int) -> None:
        self.controller.remove_line(line_id)
        self.refresh_cart()

    def _clear_cart(self) -> None:
        if self.controller.cart.is_empty:
            return
        self.controller.clear_cart()
        self.refresh_cart()
        self.toast.show_message("تم إفراغ السلة", "info", 1500)

    # -- cart rendering ---------------------------------------------------- #
    def refresh_cart(self) -> None:
        while self.cart_layout.count() > 1:
            item = self.cart_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        cart = self.controller.cart
        for line in cart.lines:
            widget = CartLineWidget(line)
            widget.increment.connect(self._on_increment)
            widget.decrement.connect(self._on_decrement)
            widget.remove.connect(self._on_remove)
            self.cart_layout.insertWidget(self.cart_layout.count() - 1, widget)

        self.empty_hint.setVisible(cart.is_empty)

        totals = cart.summary()
        self.subtotal_label.setText(config.format_money(totals["subtotal_minor"]))
        self.discount_label.setText(config.format_money(totals["discount_minor"]))
        self.tax_value_label.setText(config.format_money(totals["tax_minor"]))
        self.total_label.setText(config.format_money(totals["total_minor"]))

        # A 0% VAT rate means the tax row would always read "0" — hide it rather
        # than confuse the cashier with a meaningless line.
        show_tax = totals["tax_minor"] > 0
        self.tax_row_label.setVisible(show_tax)
        self.tax_value_label.setVisible(show_tax)

        self.checkout_button.setEnabled(not cart.is_empty and self.shift is not None)
        if self.shift is None:
            self.checkout_button.setText("افتح وردية أولاً")
        elif cart.is_empty:
            self.checkout_button.setText("إتمام الدفع")
        else:
            self.checkout_button.setText(
                f"إتمام الدفع — {config.format_money(cart.total_minor)}"
            )

    # -- payment ----------------------------------------------------------- #
    def open_payment(self) -> None:
        cart = self.controller.cart
        if cart.is_empty:
            self.toast.show_message("السلة فارغة — أضف صنفاً أولاً", "warning", 3000)
            return

        dialog = PaymentDialog(cart.total_minor, parent=self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return

        self._complete_checkout(dialog.method, dialog.paid_minor)

    def _complete_checkout(self, method: str, paid_minor: int) -> None:
        """
        Separated from open_payment so a headless test can complete a sale
        without driving the modal dialog.
        """
        if self.shift is None:
            self.toast.show_message(
                "لا يمكن البيع بدون وردية مفتوحة — افتح وردية أولاً", "warning", 5000
            )
            return

        result = self.controller.checkout(method, paid_minor, shift_id=self.shift.id)
        self.refresh_cart()

        if not result.ok:
            self.toast.show_message(result.message, "error", 6000)
            return

        kind = "warning" if result.needs_printer_notice else "success"
        self.toast.show_message(result.message, kind, 6000)
        if result.order is not None:
            self.checkout_completed.emit(result.order)

    def _reprint_last(self) -> None:
        try:
            result = self.controller.reprint_last()
        except Exception as exc:
            logger.exception("فشلت إعادة الطباعة")
            self.toast.show_message(f"تعذّرت إعادة الطباعة: {exc}", "error", 5000)
            return
        self.toast.show_message(
            result.message, "warning" if result.needs_attention else "success", 5000
        )

    # -- lifecycle --------------------------------------------------------- #
    def focus_search(self) -> None:
        self.search_edit.setFocus()
        self.search_edit.selectAll()
