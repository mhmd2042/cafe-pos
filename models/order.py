"""
models/order.py — the live cart, tax-inclusive totals, and order persistence.

Qt-free: the cart panel renders this, it does not own it. That keeps the money
maths testable without a display, which matters more here than anywhere else in
the app.

MONEY RULES
    * Every amount is an integer in minor units (whole rials for YER).
    * Prices are TAX-INCLUSIVE. A line total is
          (product price + Σ modifier deltas) × quantity
      and the order total is the sum of line totals minus discount. Tax is
      *derived* from the total for reporting only — never added at checkout.
    * Nothing here uses float. Rounding happens once, in config, via Decimal.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Sequence

import config

logger = logging.getLogger(__name__)

__all__ = [
    "SelectedModifier",
    "CartLine",
    "Cart",
    "OrderRecord",
    "OrderRepository",
    "CartError",
]


class CartError(Exception):
    """Invalid cart operation (empty cart, bad quantity, ...). Arabic message."""


# --------------------------------------------------------------------------- #
# A chosen modifier, snapshotted
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class SelectedModifier:
    """
    One modifier as chosen on a specific line.

    Snapshot semantics: the name and price are copied at selection time, so
    renaming or repricing a modifier later never rewrites historical receipts.
    """

    group_id: int
    group_name: str
    modifier_id: int | None
    name: str
    price_minor: int = 0

    @property
    def label(self) -> str:
        from models.product import price_delta_label

        delta = price_delta_label(self.price_minor)
        return f"{self.name} {delta}".strip()

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "group": self.group_name,
            "modifier_id": self.modifier_id,
            "name": self.name,
            "price_minor": self.price_minor,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SelectedModifier":
        return cls(
            group_id=int(data.get("group_id") or 0),
            group_name=str(data.get("group") or ""),
            modifier_id=data.get("modifier_id"),
            name=str(data.get("name") or ""),
            price_minor=int(data.get("price_minor") or 0),
        )


# --------------------------------------------------------------------------- #
# Cart line
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class CartLine:
    product_id: int | None
    name_ar: str
    name_en: str = ""
    base_price_minor: int = 0
    quantity: int = 1
    modifiers: list[SelectedModifier] = field(default_factory=list)
    note: str = ""
    line_id: int = 0                     # local, for widget identity only

    # -- money ------------------------------------------------------------- #
    @property
    def modifiers_total_minor(self) -> int:
        return sum(m.price_minor for m in self.modifiers)

    @property
    def unit_price_minor(self) -> int:
        return self.base_price_minor + self.modifiers_total_minor

    @property
    def line_total_minor(self) -> int:
        return self.unit_price_minor * max(0, self.quantity)

    # -- display ----------------------------------------------------------- #
    @property
    def display_name(self) -> str:
        return self.name_ar or self.name_en

    def modifiers_summary(self) -> str:
        """'كبير • حليب شوفان • بدون سكر' — skips zero-priced noise? No: shows all."""
        return " • ".join(m.name for m in self.modifiers)

    def detail_lines(self) -> list[str]:
        parts: list[str] = []
        summary = self.modifiers_summary()
        if summary:
            parts.append(summary)
        if self.note:
            parts.append(f"ملاحظة: {self.note}")
        return parts

    def signature(self) -> tuple:
        """
        Identity for line merging: same product, same options, same note.
        Deliberately excludes quantity so re-tapping a drink bumps the count.
        """
        mods = tuple(sorted((m.group_id, m.modifier_id or 0, m.price_minor) for m in self.modifiers))
        return (self.product_id, mods, self.note.strip())

    def to_order_item(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "name_ar": self.name_ar,
            "name_en": self.name_en,
            "unit_price_minor": self.unit_price_minor,
            "quantity": self.quantity,
            "line_total_minor": self.line_total_minor,
            "modifiers_json": json.dumps([m.to_dict() for m in self.modifiers], ensure_ascii=False),
            "note": self.note,
        }


# --------------------------------------------------------------------------- #
# Cart
# --------------------------------------------------------------------------- #
class Cart:
    """
    The live order. Pure Python — no Qt, no SQL.

    `total_minor` is what the customer pays; `tax_minor` is the portion already
    inside it. With the configured 0% rate tax is 0 and no tax line is shown.
    """

    def __init__(self, order_type: str = config.ORDER_TYPE_DINE_IN) -> None:
        self.lines: list[CartLine] = []
        self.order_type = order_type if order_type in config.ORDER_TYPES else config.ORDER_TYPE_DINE_IN
        self.discount_minor = 0
        self.note = ""
        self._next_line_id = 1

    # -- state ------------------------------------------------------------- #
    @property
    def is_empty(self) -> bool:
        return not self.lines

    @property
    def line_count(self) -> int:
        return len(self.lines)

    @property
    def item_count(self) -> int:
        return sum(line.quantity for line in self.lines)

    @property
    def subtotal_minor(self) -> int:
        return sum(line.line_total_minor for line in self.lines)

    @property
    def total_minor(self) -> int:
        return max(0, self.subtotal_minor - self.discount_minor)

    @property
    def tax_minor(self) -> int:
        """Embedded tax portion of the total (reporting only)."""
        rate = self._tax_rate()
        return config.extract_tax_from_inclusive(self.total_minor, rate)

    @property
    def net_minor(self) -> int:
        return self.total_minor - self.tax_minor

    def _tax_rate(self):
        from decimal import Decimal

        try:
            from database.db_manager import get_db

            raw = get_db().get_setting("tax_rate", str(config.DEFAULT_TAX_RATE))
            return Decimal(str(raw))
        except Exception:
            return config.DEFAULT_TAX_RATE

    # -- mutation ---------------------------------------------------------- #
    def add(
        self,
        product: Any,
        modifiers: Sequence[SelectedModifier] = (),
        note: str = "",
        quantity: int = 1,
    ) -> CartLine:
        """Add a product; merges into an identical existing line when enabled."""
        quantity = max(1, int(quantity))
        line = CartLine(
            product_id=int(product.id),
            name_ar=product.name_ar,
            name_en=product.name_en,
            base_price_minor=int(product.price_minor),
            quantity=quantity,
            modifiers=list(modifiers),
            note=(note or "").strip(),
        )

        if config.CART_MERGE_IDENTICAL_LINES:
            signature = line.signature()
            for existing in self.lines:
                if existing.signature() == signature:
                    existing.quantity += quantity
                    return existing

        line.line_id = self._next_line_id
        self._next_line_id += 1
        self.lines.append(line)
        return line

    def find(self, line_id: int) -> CartLine | None:
        return next((line for line in self.lines if line.line_id == line_id), None)

    def increment(self, line_id: int, by: int = 1) -> CartLine | None:
        line = self.find(line_id)
        if line is not None:
            line.quantity = max(1, line.quantity + int(by))
        return line

    def decrement(self, line_id: int) -> CartLine | None:
        """Decrement; at quantity 1 the line is removed (one-tap behaviour)."""
        line = self.find(line_id)
        if line is None:
            return None
        line.quantity -= 1
        if line.quantity <= 0:
            self.lines.remove(line)
            return None
        return line

    def set_quantity(self, line_id: int, quantity: int) -> CartLine | None:
        line = self.find(line_id)
        if line is None:
            return None
        if quantity <= 0:
            self.lines.remove(line)
            return None
        line.quantity = int(quantity)
        return line

    def remove(self, line_id: int) -> bool:
        line = self.find(line_id)
        if line is None:
            return False
        self.lines.remove(line)
        return True

    def clear(self) -> None:
        self.lines.clear()
        self.discount_minor = 0
        self.note = ""

    def set_discount(self, amount_minor: int) -> None:
        """Discount is capped at the subtotal so the total can never go negative."""
        self.discount_minor = max(0, min(int(amount_minor), self.subtotal_minor))

    def set_order_type(self, order_type: str) -> None:
        if order_type in config.ORDER_TYPES:
            self.order_type = order_type

    # -- reporting --------------------------------------------------------- #
    def summary(self) -> dict[str, int]:
        return {
            "lines": self.line_count,
            "items": self.item_count,
            "subtotal_minor": self.subtotal_minor,
            "discount_minor": self.discount_minor,
            "tax_minor": self.tax_minor,
            "net_minor": self.net_minor,
            "total_minor": self.total_minor,
        }

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Cart lines={self.line_count} items={self.item_count} total={self.total_minor}>"


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class OrderRecord:
    id: int
    order_number: str
    total_minor: int
    tax_minor: int
    paid_minor: int
    change_minor: int
    payment_method: str
    order_type: str
    created_at: str

    @property
    def total_label(self) -> str:
        return config.format_money(self.total_minor)


class OrderRepository:
    """Writes completed orders and reads them back for receipts/reprints."""

    def __init__(self, db: Any = None) -> None:
        self._db = db

    @property
    def db(self):
        if self._db is None:
            from database.db_manager import get_db

            self._db = get_db()
        return self._db

    # -- numbering --------------------------------------------------------- #
    def next_order_number(self, when: datetime | None = None, prefix: str = "") -> str:
        """
        Daily sequence: 20261002-0001, 20261002-0002, ...

        MUST be called inside the checkout transaction. The caller holds
        BEGIN IMMEDIATE, so the read-then-insert cannot interleave with another
        till and produce a duplicate (order_number is UNIQUE regardless).
        """
        when = when or datetime.now()
        date_part = when.strftime("%Y%m%d")
        pattern = f"{prefix}{date_part}-%"
        last = self.db.query_value(
            "SELECT order_number FROM orders WHERE order_number LIKE ? "
            "ORDER BY order_number DESC LIMIT 1",
            (pattern,),
        )
        sequence = 1
        if last:
            try:
                sequence = int(str(last).rsplit("-", 1)[1]) + 1
            except (IndexError, ValueError):
                sequence = self.db.scalar(
                    "SELECT COUNT(*) FROM orders WHERE order_number LIKE ?", (pattern,)
                ) + 1
        return config.ORDER_NUMBER_FORMAT.format(date=f"{prefix}{date_part}", seq=sequence)

    # -- write ------------------------------------------------------------- #
    def save(
        self,
        cart: Cart,
        *,
        user_id: int | None,
        shift_id: int | None = None,
        payment_method: str = config.PAYMENT_CASH,
        paid_minor: int | None = None,
    ) -> OrderRecord:
        """
        Persist a completed sale atomically: order header, its items and the
        payment row either all land or none do.
        """
        if cart.is_empty:
            raise CartError("لا يمكن إتمام طلب فارغ")
        if payment_method not in config.PAYMENT_METHODS:
            raise CartError(f"طريقة دفع غير معروفة: {payment_method}")

        total = cart.total_minor
        paid = int(paid_minor if paid_minor is not None else total)
        if payment_method == config.PAYMENT_CASH and paid < total:
            raise CartError("المبلغ المدفوع أقل من الإجمالي")
        if payment_method == config.PAYMENT_CARD:
            paid = total                       # a card is always exact
        change = max(0, paid - total)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with self.db.transaction():
            order_number = self.next_order_number()
            order_id = self.db.insert(
                """
                INSERT INTO orders (order_number, shift_id, user_id, order_type, status,
                                    subtotal_minor, discount_minor, total_minor, tax_minor,
                                    payment_method, paid_minor, change_minor, note, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_number, shift_id, user_id, cart.order_type,
                    config.ORDER_STATUS_COMPLETED,
                    cart.subtotal_minor, cart.discount_minor, total, cart.tax_minor,
                    payment_method, paid, change, cart.note, now,
                ),
            )
            for line in cart.lines:
                item = line.to_order_item()
                self.db.execute(
                    """
                    INSERT INTO order_items (order_id, product_id, name_ar, name_en,
                                             unit_price_minor, quantity, line_total_minor,
                                             modifiers_json, note)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        order_id, item["product_id"], item["name_ar"], item["name_en"],
                        item["unit_price_minor"], item["quantity"], item["line_total_minor"],
                        item["modifiers_json"], item["note"],
                    ),
                )
            self.db.execute(
                "INSERT INTO payments (order_id, method, amount_minor) VALUES (?, ?, ?)",
                (order_id, payment_method, paid),
            )

        logger.info("تم حفظ الطلب %s بإجمالي %s", order_number, config.format_money(total))
        return OrderRecord(
            id=order_id,
            order_number=order_number,
            total_minor=total,
            tax_minor=cart.tax_minor,
            paid_minor=paid,
            change_minor=change,
            payment_method=payment_method,
            order_type=cart.order_type,
            created_at=now,
        )

    def mark_printed(self, order_id: int) -> None:
        self.db.execute(
            "UPDATE orders SET printed_at = datetime('now','localtime') WHERE id = ?",
            (int(order_id),),
        )

    # -- read -------------------------------------------------------------- #
    def get(self, order_id: int) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM orders WHERE id = ?", (int(order_id),))
        if row is None:
            return None
        order = dict(row)
        order["items"] = [
            dict(r)
            for r in self.db.query_all(
                "SELECT * FROM order_items WHERE order_id = ? ORDER BY id", (int(order_id),)
            )
        ]
        return order

    def get_by_number(self, order_number: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT id FROM orders WHERE order_number = ?", (str(order_number),)
        )
        return self.get(int(row["id"])) if row else None

    def item_modifiers(self, item: dict[str, Any]) -> list[SelectedModifier]:
        """Decode an order_item's modifiers_json back into objects."""
        try:
            raw = json.loads(item.get("modifiers_json") or "[]")
        except (TypeError, ValueError):
            return []
        return [SelectedModifier.from_dict(d) for d in raw if isinstance(d, dict)]

    def list_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self.db.query_all(
                "SELECT * FROM orders WHERE status = ? ORDER BY id DESC LIMIT ?",
                (config.ORDER_STATUS_COMPLETED, int(limit)),
            )
        ]

    def sales_total(self, day: str | None = None) -> int:
        """Completed sales total for a day (defaults to today)."""
        day = day or datetime.now().strftime("%Y-%m-%d")
        return self.db.scalar(
            "SELECT IFNULL(SUM(total_minor), 0) FROM orders "
            "WHERE status = ? AND date(created_at) = date(?)",
            (config.ORDER_STATUS_COMPLETED, day),
        )
