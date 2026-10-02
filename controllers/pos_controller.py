"""
controllers/pos_controller.py — the cashier's till logic.

Qt-free: the POS view owns widgets and calls into this; this owns the rules and
never imports Qt. That keeps cart maths, checkout and printing testable headlessly.

Responsibilities
    * Load the menu (categories + products + modifiers).
    * Drive the cart: add with modifiers, +/- quantity, delete, clear.
    * Checkout: validate payment, persist the order, print, hand back a receipt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import config
from models.order import Cart, CartError, OrderRecord, OrderRepository, SelectedModifier
from models.product import Modifier, ModifierGroup, Product, ProductRepository
from services.printer_service import PrinterService, PrintResult, ReceiptData

logger = logging.getLogger(__name__)

__all__ = ["PosController", "CheckoutResult", "ModifierSelection"]


# --------------------------------------------------------------------------- #
# Result objects
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class ModifierSelection:
    """What the modifier dialog returns for one product."""

    modifiers: list[SelectedModifier] = field(default_factory=list)
    note: str = ""

    @property
    def has_choices(self) -> bool:
        return bool(self.modifiers or self.note.strip())


@dataclass(slots=True)
class CheckoutResult:
    ok: bool
    message: str = ""
    order: OrderRecord | None = None
    print_result: PrintResult | None = None
    deducted: list[Any] | None = None      # inventory DeductionLine list

    @property
    def needs_printer_notice(self) -> bool:
        return bool(self.print_result and self.print_result.needs_attention)

    @property
    def deducted_summary(self) -> str:
        """'حبوب قهوة 18 غ، حليب 150 مل' — for a soft notice or a debug log."""
        if not self.deducted:
            return ""
        return "، ".join(line.label for line in self.deducted)


# --------------------------------------------------------------------------- #
# Controller
# --------------------------------------------------------------------------- #
class PosController:
    """Bridges the POS view to the menu, the cart and the printer."""

    def __init__(
        self,
        auth: Any = None,
        product_repo: ProductRepository | None = None,
        order_repo: OrderRepository | None = None,
        printer: PrinterService | None = None,
    ) -> None:
        self.auth = auth
        self.products = product_repo or ProductRepository()
        self.orders = order_repo or OrderRepository()
        self.printer = printer or PrinterService()
        self.cart = Cart()

    # -- menu -------------------------------------------------------------- #
    def load_categories(self) -> list[Any]:
        return self.products.list_categories(active_only=True)

    def load_products(self, category_id: int | None = None, search: str = "") -> list[Product]:
        return self.products.list_products(category_id, search=search, active_only=True)

    def modifier_groups(self, product: Product) -> list[ModifierGroup]:
        return self.products.modifier_groups_for_product(product.id)

    def needs_modifier_dialog(self, product: Product) -> bool:
        """
        Only interrupt the cashier when there is a real choice to make.

        A product whose only group is the optional free-text notes box is added
        straight to the cart — an extra modal for every water bottle would slow
        the queue down for no benefit.
        """
        groups = self.modifier_groups(product)
        return any(not g.is_text for g in groups)

    # -- cart -------------------------------------------------------------- #
    def add_product(
        self,
        product: Product,
        selection: ModifierSelection | None = None,
        quantity: int = 1,
    ) -> Any:
        selection = selection or ModifierSelection()
        return self.cart.add(product, selection.modifiers, selection.note, quantity)

    def increment(self, line_id: int) -> None:
        self.cart.increment(line_id)

    def decrement(self, line_id: int) -> None:
        self.cart.decrement(line_id)

    def set_quantity(self, line_id: int, quantity: int) -> None:
        self.cart.set_quantity(line_id, quantity)

    def remove_line(self, line_id: int) -> None:
        self.cart.remove(line_id)

    def clear_cart(self) -> None:
        self.cart.clear()

    def set_order_type(self, order_type: str) -> None:
        self.cart.set_order_type(order_type)

    def toggle_order_type(self) -> str:
        """Flip dine-in ↔ takeaway (single-flow ordering, one tap)."""
        new = (config.ORDER_TYPE_TAKEAWAY
               if self.cart.order_type == config.ORDER_TYPE_DINE_IN
               else config.ORDER_TYPE_DINE_IN)
        self.cart.set_order_type(new)
        return new

    # -- checkout ---------------------------------------------------------- #
    def checkout(
        self,
        payment_method: str = config.PAYMENT_CASH,
        paid_minor: int | None = None,
        *,
        print_receipt: bool = True,
        shift_id: int | None = None,
    ) -> CheckoutResult:
        """
        Persist the sale and print. The cart is only cleared after the order is
        safely in the database, so a printer failure can never lose a sale.
        """
        if self.cart.is_empty:
            return CheckoutResult(ok=False, message="السلة فارغة — أضف صنفاً أولاً")

        if self.auth is not None and not self.auth.can("take_payment"):
            return CheckoutResult(ok=False, message="لا تملك صلاحية تحصيل الدفع")

        user = getattr(self.auth, "current_user", None)
        try:
            order = self.orders.save(
                self.cart,
                user_id=getattr(user, "id", None),
                shift_id=shift_id,
                payment_method=payment_method,
                paid_minor=paid_minor,
            )
        except CartError as exc:
            return CheckoutResult(ok=False, message=str(exc))
        except Exception as exc:
            logger.exception("فشل حفظ الطلب")
            return CheckoutResult(ok=False, message=f"تعذّر حفظ الطلب: {exc}")

        print_result: PrintResult | None = None
        if print_receipt:
            try:
                print_result = self.print_order(order.id)
            except Exception as exc:  # the sale is already saved — never undo it
                logger.exception("فشلت الطباعة")
                print_result = PrintResult(
                    ok=False, message="تعذّرت الطباعة", error=str(exc)
                )
            if print_result.printed:
                self.orders.mark_printed(order.id)

        # Inventory deduction runs AFTER the order is committed, in its own
        # transaction, and swallows its own errors: a stock-table problem must
        # never cost the café a sale. A failed deduction is logged and reported
        # as a warning, not as a failed sale.
        deducted = self._deduct_inventory(order.id, user_id=getattr(user, "id", None))

        self.cart.clear()

        message = f"تم إتمام الطلب {order.order_number}"
        if print_result is not None and print_result.fell_back:
            message += " — " + print_result.message
        if deducted is None:
            message += " — تعذّر خصم المخزون تلقائياً (راجع سجل الأخطاء)"
        return CheckoutResult(ok=True, message=message, order=order,
                              print_result=print_result, deducted=deducted)

    def _deduct_inventory(self, order_id: int, *, user_id: int | None = None):
        """Best-effort stock deduction. Returns the lines, or None on failure."""
        try:
            from models.inventory import InventoryRepository

            return InventoryRepository(self.orders.db).deduct_for_order(
                order_id, user_id=user_id
            )
        except Exception:
            logger.exception("تعذّر خصم المخزون للطلب #%s", order_id)
            return None

    # -- receipts ---------------------------------------------------------- #
    def receipt_data(self, order_id: int) -> ReceiptData | None:
        order = self.orders.get(order_id)
        if order is None:
            return None
        user = getattr(self.auth, "current_user", None)
        return ReceiptData.from_order(
            order,
            self.printer.settings(),
            modifiers_of=self.orders.item_modifiers,
            cashier_name=getattr(user, "display_name", ""),
        )

    def print_order(self, order_id: int) -> PrintResult:
        data = self.receipt_data(order_id)
        if data is None:
            return PrintResult(ok=False, message="الطلب غير موجود")
        return self.printer.print_receipt(data)

    def reprint_last(self) -> PrintResult:
        """Reprint the most recent completed order (paper jams happen)."""
        recent = self.orders.list_recent(limit=1)
        if not recent:
            return PrintResult(ok=False, message="لا توجد طلبات لإعادة طباعتها")
        return self.print_order(int(recent[0]["id"]))

    # -- convenience for the UI -------------------------------------------- #
    @property
    def totals(self) -> dict[str, int]:
        return self.cart.summary()

    @property
    def total_label(self) -> str:
        return config.format_money(self.cart.total_minor)

    def today_sales_label(self) -> str:
        return config.format_money(self.orders.sales_total())

    def suggested_cash_amounts(self) -> list[int]:
        """
        Quick-tender buttons: the configured round amounts plus the exact total,
        each rounded up to a tidy figure.
        """
        total = self.cart.total_minor
        amounts: list[int] = []
        for amount in config.QUICK_CASH_AMOUNTS:
            if amount >= total:
                amounts.append(amount)
        if total > 0:
            step = 500 if total >= 2000 else 100
            rounded = ((total + step - 1) // step) * step
            if rounded not in amounts:
                amounts.insert(0, rounded)
            if total not in amounts:
                amounts.insert(0, total)
        # de-duplicate, keep order, cap the row
        seen: set[int] = set()
        unique = [a for a in amounts if not (a in seen or seen.add(a))]
        return unique[:5]
