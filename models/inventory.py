"""
models/inventory.py — raw ingredients, recipes and automatic deduction.

The spec's example: a Cappuccino sale must deduct 18 g of coffee beans and
150 ml of milk. This module is what makes that happen, and what stops stock
figures drifting away from reality.

HOW A RECIPE WORKS
    `recipes` maps a product to an ingredient with a quantity per single serving:
        product 4 (كابتشينو) + ingredient 1 (حبوب قهوة) + 18 g
    A recipe row may also carry `modifier_id`. Those rows are *conditional*:
        product 4 + ingredient 3 (حليب شوفان) + 200 ml, modifier 12 (حليب شوفان)
    so choosing oat milk deducts oat milk instead of the default cow's milk.

    Deduction therefore runs in two passes per order line:
        1. base rows   (modifier_id IS NULL)
        2. extra rows  (modifier_id IN the line's chosen modifiers)

UNITS AND MONEY
    Quantities are REAL (18 g, 200 ml, 1 piece) — they are measures, not money.
    `cost_per_unit_minor` is a RATE (minor units per gram/ml/piece), so it is also
    REAL; any money figure derived from it is rounded to an int before it is used
    as an amount. Amounts are never stored as floats.

FAILURE POLICY
    Deduction never blocks a sale. `deduct_for_order()` is called after the order
    has been committed, in its own transaction, and swallows its own errors —
    a stock-table problem must never cost the café a sale.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Sequence

import config

logger = logging.getLogger(__name__)

__all__ = [
    "InventoryItem",
    "RecipeLine",
    "StockMovement",
    "DeductionLine",
    "InventoryRepository",
    "InventoryError",
]


class InventoryError(Exception):
    """Inventory rule violation (Arabic message)."""


# --------------------------------------------------------------------------- #
# Entities
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class InventoryItem:
    id: int
    name_ar: str
    name_en: str = ""
    unit: str = config.UNIT_GRAM
    current_qty: float = 0.0
    min_qty: float = 0.0
    cost_per_unit_minor: float = 0.0
    supplier: str = ""
    is_active: bool = True
    updated_at: str = ""

    @property
    def display_name(self) -> str:
        return self.name_ar or self.name_en

    @property
    def unit_label(self) -> str:
        return {"g": "غرام", "ml": "مل", "piece": "قطعة"}.get(self.unit, self.unit)

    @property
    def quantity_label(self) -> str:
        return f"{self.current_qty:g} {self.unit_label}"

    @property
    def is_low(self) -> bool:
        """At or below the reorder threshold."""
        return self.min_qty > 0 and self.current_qty <= self.min_qty

    @property
    def is_out(self) -> bool:
        return self.current_qty <= 0

    @property
    def stock_value_minor(self) -> int:
        """Approximate value of what is on the shelf, in minor units."""
        return int(round(max(0.0, self.current_qty) * max(0.0, self.cost_per_unit_minor)))

    @classmethod
    def from_row(cls, row: Any) -> "InventoryItem":
        keys = row.keys() if hasattr(row, "keys") else []
        get = lambda k, d=None: (row[k] if k in keys else d)  # noqa: E731
        return cls(
            id=int(row["id"]),
            name_ar=str(row["name_ar"] or ""),
            name_en=str(row["name_en"] or ""),
            unit=str(get("unit", config.UNIT_GRAM)),
            current_qty=float(get("current_qty", 0) or 0),
            min_qty=float(get("min_qty", 0) or 0),
            cost_per_unit_minor=float(get("cost_per_unit_minor", 0) or 0),
            supplier=str(get("supplier", "") or ""),
            is_active=bool(get("is_active", 1)),
            updated_at=str(get("updated_at", "") or ""),
        )


@dataclass(slots=True)
class RecipeLine:
    """One ingredient used by one product (optionally only for one modifier)."""

    id: int
    product_id: int
    inventory_item_id: int
    quantity: float
    modifier_id: int | None = None
    # joined
    ingredient_name: str = ""
    unit: str = config.UNIT_GRAM
    product_name: str = ""
    modifier_name: str = ""
    modifier_group_id: int | None = None
    modifier_is_default: bool = False
    cost_per_unit_minor: float = 0.0

    @property
    def unit_label(self) -> str:
        return {"g": "غرام", "ml": "مل", "piece": "قطعة"}.get(self.unit, self.unit)

    @property
    def quantity_label(self) -> str:
        return f"{self.quantity:g} {self.unit_label}"

    @property
    def is_conditional(self) -> bool:
        return self.modifier_id is not None

    @property
    def group_id(self) -> int | None:
        """The modifier group this conditional row belongs to."""
        return self.modifier_group_id

    @property
    def is_default(self) -> bool:
        """True when this row's modifier is the group's default option."""
        return bool(self.modifier_is_default)

    @property
    def cost_minor(self) -> int:
        return int(round(self.quantity * max(0.0, self.cost_per_unit_minor)))

    @classmethod
    def from_row(cls, row: Any) -> "RecipeLine":
        keys = row.keys() if hasattr(row, "keys") else []
        get = lambda k, d=None: (row[k] if k in keys else d)  # noqa: E731
        return cls(
            id=int(row["id"]),
            product_id=int(row["product_id"]),
            inventory_item_id=int(row["inventory_item_id"]),
            quantity=float(row["quantity"] or 0),
            modifier_id=get("modifier_id"),
            ingredient_name=str(get("ingredient_name", "") or ""),
            unit=str(get("unit", config.UNIT_GRAM)),
            product_name=str(get("product_name", "") or ""),
            modifier_name=str(get("modifier_name", "") or ""),
            modifier_group_id=get("modifier_group_id"),
            modifier_is_default=bool(get("modifier_is_default", 0)),
            cost_per_unit_minor=float(get("cost_per_unit_minor", 0) or 0),
        )


@dataclass(slots=True)
class StockMovement:
    id: int
    inventory_item_id: int
    change_qty: float
    reason: str
    order_id: int | None = None
    user_id: int | None = None
    note: str = ""
    created_at: str = ""
    ingredient_name: str = ""
    unit: str = config.UNIT_GRAM

    @property
    def is_deduction(self) -> bool:
        return self.change_qty < 0

    @property
    def change_label(self) -> str:
        sign = "" if self.change_qty < 0 else "+"
        unit = {"g": "غ", "ml": "مل", "piece": "ق"}.get(self.unit, self.unit)
        return f"{sign}{self.change_qty:g} {unit}"

    @property
    def reason_label(self) -> str:
        return {
            config.STOCK_SALE: "بيع",
            config.STOCK_PURCHASE: "توريد",
            config.STOCK_WASTE: "هدر",
            config.STOCK_ADJUSTMENT: "تسوية",
            config.STOCK_RETURN: "مرتجع",
        }.get(self.reason, self.reason)

    @classmethod
    def from_row(cls, row: Any) -> "StockMovement":
        keys = row.keys() if hasattr(row, "keys") else []
        get = lambda k, d=None: (row[k] if k in keys else d)  # noqa: E731
        return cls(
            id=int(row["id"]),
            inventory_item_id=int(row["inventory_item_id"]),
            change_qty=float(row["change_qty"] or 0),
            reason=str(get("reason", "")),
            order_id=get("order_id"),
            user_id=get("user_id"),
            note=str(get("note", "") or ""),
            created_at=str(get("created_at", "") or ""),
            ingredient_name=str(get("ingredient_name", "") or ""),
            unit=str(get("unit", config.UNIT_GRAM)),
        )


@dataclass(slots=True)
class DeductionLine:
    """What one sale consumed — returned so the UI/receipt can report it."""

    inventory_item_id: int
    name: str
    quantity: float
    unit: str

    @property
    def label(self) -> str:
        unit = {"g": "غ", "ml": "مل", "piece": "قطعة"}.get(self.unit, self.unit)
        return f"{self.name} {self.quantity:g} {unit}"


# --------------------------------------------------------------------------- #
# Repository
# --------------------------------------------------------------------------- #
class InventoryRepository:
    """Reads/writes ingredients, recipes and stock movements; performs deduction."""

    def __init__(self, db: Any = None) -> None:
        self._db = db

    @property
    def db(self):
        if self._db is None:
            from database.db_manager import get_db

            self._db = get_db()
        return self._db

    # -- ingredients ------------------------------------------------------- #
    def list_items(self, active_only: bool = True, search: str = "") -> list[InventoryItem]:
        clauses, params = [], []
        if active_only:
            clauses.append("is_active = 1")
        if search.strip():
            needle = f"%{search.strip()}%"
            clauses.append("(name_ar LIKE ? OR name_en LIKE ? OR supplier LIKE ?)")
            params.extend([needle, needle, needle])
        sql = "SELECT * FROM inventory_items"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY name_ar COLLATE NOCASE"
        return [InventoryItem.from_row(r) for r in self.db.query_all(sql, tuple(params))]

    def get_item(self, item_id: int) -> InventoryItem | None:
        row = self.db.query_one("SELECT * FROM inventory_items WHERE id = ?", (int(item_id),))
        return InventoryItem.from_row(row) if row else None

    def low_stock_items(self) -> list[InventoryItem]:
        return [i for i in self.list_items() if i.is_low]

    def stock_value_minor(self) -> int:
        return sum(i.stock_value_minor for i in self.list_items())

    def create_item(self, name_ar: str, unit: str, current_qty: float = 0.0,
                    min_qty: float = 0.0, cost_per_unit_minor: float = 0.0,
                    name_en: str = "", supplier: str = "") -> int:
        if not str(name_ar).strip():
            raise InventoryError("اسم المادة مطلوب")
        if unit not in config.UNITS:
            raise InventoryError(f"وحدة غير معروفة: {unit}")
        return self.db.insert(
            """
            INSERT INTO inventory_items (name_ar, name_en, unit, current_qty, min_qty,
                                         cost_per_unit_minor, supplier)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (str(name_ar).strip(), name_en or "", unit, float(current_qty),
             float(min_qty), float(cost_per_unit_minor), supplier or ""),
        )

    def update_item(self, item_id: int, **fields: Any) -> None:
        allowed = {"name_ar", "name_en", "unit", "min_qty", "cost_per_unit_minor",
                   "supplier", "is_active"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return
        assignments = ", ".join(f"{k} = ?" for k in updates)
        params = list(updates.values()) + [int(item_id)]
        self.db.execute(
            f"UPDATE inventory_items SET {assignments}, "
            "updated_at = datetime('now','localtime') WHERE id = ?",
            tuple(params),
        )

    def set_active(self, item_id: int, active: bool) -> None:
        self.update_item(item_id, is_active=int(bool(active)))

    # -- stock movements --------------------------------------------------- #
    def adjust_stock(self, item_id: int, change_qty: float, reason: str,
                     *, order_id: int | None = None, user_id: int | None = None,
                     note: str = "") -> None:
        """
        Apply a stock movement. Must be called inside a transaction when it is
        part of a larger operation.
        """
        if reason not in config.STOCK_REASONS:
            raise InventoryError(f"سبب حركة مخزون غير معروف: {reason}")
        self.db.execute(
            """
            INSERT INTO stock_movements (inventory_item_id, change_qty, reason,
                                         order_id, user_id, note)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (int(item_id), float(change_qty), reason, order_id, user_id, note or ""),
        )
        self.db.execute(
            """
            UPDATE inventory_items
               SET current_qty = current_qty + ?,
                   updated_at = datetime('now','localtime')
             WHERE id = ?
            """,
            (float(change_qty), int(item_id)),
        )

    def receive_stock(self, item_id: int, quantity: float, note: str = "",
                      user_id: int | None = None) -> None:
        """Delivery received: add to stock."""
        if quantity <= 0:
            raise InventoryError("الكمية المستلمة يجب أن تكون أكبر من صفر")
        with self.db.transaction():
            self.adjust_stock(item_id, float(quantity), config.STOCK_PURCHASE,
                              user_id=user_id, note=note)

    def record_waste(self, item_id: int, quantity: float, note: str = "",
                     user_id: int | None = None) -> None:
        if quantity <= 0:
            raise InventoryError("كمية الهدر يجب أن تكون أكبر من صفر")
        with self.db.transaction():
            self.adjust_stock(item_id, -float(quantity), config.STOCK_WASTE,
                              user_id=user_id, note=note)

    def set_counted_stock(self, item_id: int, counted: float, note: str = "",
                          user_id: int | None = None) -> float:
        """Stocktake: move current_qty to the counted figure. Returns the delta."""
        item = self.get_item(item_id)
        if item is None:
            raise InventoryError("المادة غير موجودة")
        if counted < 0:
            raise InventoryError("الكمية لا يمكن أن تكون سالبة")
        delta = float(counted) - item.current_qty
        with self.db.transaction():
            self.adjust_stock(item_id, delta, config.STOCK_ADJUSTMENT,
                              user_id=user_id, note=note or "جرد")
        return delta

    def movements(self, item_id: int | None = None, limit: int = 50,
                  reason: str | None = None) -> list[StockMovement]:
        sql = """
            SELECT m.*, IFNULL(i.name_ar, '') AS ingredient_name,
                   IFNULL(i.unit, 'g') AS unit
              FROM stock_movements m
              LEFT JOIN inventory_items i ON i.id = m.inventory_item_id
        """
        clauses, params = [], []
        if item_id is not None:
            clauses.append("m.inventory_item_id = ?")
            params.append(int(item_id))
        if reason:
            clauses.append("m.reason = ?")
            params.append(reason)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY m.id DESC LIMIT ?"
        params.append(int(limit))
        return [StockMovement.from_row(r) for r in self.db.query_all(sql, tuple(params))]

    def consumption_since(self, since: str | None = None) -> list[tuple[str, float, str]]:
        """Total consumed per ingredient (for the usage report)."""
        sql = """
            SELECT i.name_ar AS name, IFNULL(i.unit, 'g') AS unit,
                   IFNULL(SUM(-m.change_qty), 0) AS used
              FROM stock_movements m
              JOIN inventory_items i ON i.id = m.inventory_item_id
             WHERE m.reason = ?
        """
        params: list[Any] = [config.STOCK_SALE]
        if since:
            sql += " AND date(m.created_at) >= date(?)"
            params.append(since)
        sql += " GROUP BY i.id ORDER BY used DESC"
        return [(str(r["name"]), float(r["used"] or 0), str(r["unit"]))
                for r in self.db.query_all(sql, tuple(params))]

    # -- recipes ----------------------------------------------------------- #
    def recipes_for_product(self, product_id: int) -> list[RecipeLine]:
        return [
            RecipeLine.from_row(r)
            for r in self.db.query_all(
                """
                SELECT r.*, i.name_ar AS ingredient_name, i.unit,
                       i.cost_per_unit_minor,
                       IFNULL(m.name_ar, '') AS modifier_name,
                       m.group_id AS modifier_group_id,
                       IFNULL(m.is_default, 0) AS modifier_is_default
                  FROM recipes r
                  JOIN inventory_items i ON i.id = r.inventory_item_id
                  LEFT JOIN modifiers m ON m.id = r.modifier_id
                 WHERE r.product_id = ?
                 ORDER BY r.modifier_id IS NOT NULL, i.name_ar
                """,
                (int(product_id),),
            )
        ]

    def add_recipe_line(self, product_id: int, inventory_item_id: int, quantity: float,
                        modifier_id: int | None = None) -> int:
        if quantity <= 0:
            raise InventoryError("كمية المكوّن يجب أن تكون أكبر من صفر")
        if not self.db.query_value("SELECT 1 FROM products WHERE id = ?", (int(product_id),)):
            raise InventoryError("الصنف غير موجود")
        if not self.db.query_value("SELECT 1 FROM inventory_items WHERE id = ?",
                                   (int(inventory_item_id),)):
            raise InventoryError("المادة غير موجودة")
        try:
            return self.db.insert(
                """
                INSERT INTO recipes (product_id, inventory_item_id, quantity, modifier_id)
                VALUES (?, ?, ?, ?)
                """,
                (int(product_id), int(inventory_item_id), float(quantity), modifier_id),
            )
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                raise InventoryError("هذا المكوّن مضاف بالفعل لهذا الصنف") from exc
            if "FOREIGN KEY" in str(exc).upper():
                raise InventoryError("الصنف أو المادة أو الخيار غير موجود") from exc
            raise

    def update_recipe_line(self, recipe_id: int, quantity: float) -> None:
        if quantity <= 0:
            raise InventoryError("كمية المكوّن يجب أن تكون أكبر من صفر")
        self.db.execute("UPDATE recipes SET quantity = ? WHERE id = ?",
                        (float(quantity), int(recipe_id)))

    def remove_recipe_line(self, recipe_id: int) -> None:
        self.db.execute("DELETE FROM recipes WHERE id = ?", (int(recipe_id),))

    def product_cost_minor(self, product_id: int) -> int:
        """Recipe cost of one serving (base rows only). Approximate COGS."""
        return sum(line.cost_minor for line in self.recipes_for_product(product_id)
                   if not line.is_conditional)

    def products_without_recipes(self) -> list[tuple[int, str]]:
        """Items that track inventory but have no recipe — a data-quality report."""
        return [
            (int(r["id"]), str(r["name_ar"]))
            for r in self.db.query_all(
                """
                SELECT p.id, p.name_ar
                  FROM products p
                 WHERE p.is_active = 1 AND p.track_inventory = 1
                   AND NOT EXISTS (SELECT 1 FROM recipes r WHERE r.product_id = p.id)
                 ORDER BY p.name_ar
                """
            )
        ]

    # -- deduction --------------------------------------------------------- #
    def deduct_for_order(self, order_id: int, *, user_id: int | None = None) -> list[DeductionLine]:
        """
        Deduct every ingredient consumed by an order.

        Reads the order's own items (not the live cart), so the deduction always
        matches what was actually sold and printed.

        Returns the lines deducted. Raises InventoryError only for programming
        errors; the caller (PosController) swallows failures so a stock problem
        can never lose a sale.
        """
        items = self.db.query_all(
            """
            SELECT oi.product_id, oi.quantity, oi.modifiers_json
              FROM order_items oi
             WHERE oi.order_id = ?
            """,
            (int(order_id),),
        )
        if not items:
            return []

        import json

        consumed: dict[int, DeductionLine] = {}

        for item in items:
            product_id = item["product_id"]
            if product_id is None:
                continue          # product deleted since the sale; nothing to map
            qty = int(item["quantity"] or 1)

            chosen_modifier_ids: set[int] = set()
            try:
                for entry in json.loads(item["modifiers_json"] or "[]"):
                    if isinstance(entry, dict) and entry.get("modifier_id") is not None:
                        chosen_modifier_ids.add(int(entry["modifier_id"]))
            except (TypeError, ValueError):
                pass

            # Split the recipe: base rows always fire, conditional rows are
            # grouped so a "pick one of these" group (milk type) can fall back
            # to its default when the cashier chose nothing.
            recipe = self.recipes_for_product(int(product_id))
            conditional_by_group: dict[int, list[RecipeLine]] = {}
            for line in recipe:
                if line.is_conditional and line.group_id:
                    conditional_by_group.setdefault(line.group_id, []).append(line)

            applicable: list[RecipeLine] = []
            chosen_groups: set[int] = set()

            # Split the recipe: base rows always fire, conditional rows are grouped
        # so a "pick one of these" group (milk type) can fall back to its
        # default when the cashier chose nothing.
        recipe = self.recipes_for_product(int(product_id))
        conditional_by_group: dict[int, list[RecipeLine]] = {}
        for line in recipe:
            if line.is_conditional and line.group_id:
                conditional_by_group.setdefault(line.group_id, []).append(line)

        applicable: list[RecipeLine] = []
        chosen_groups: set[int] = set()

        for line in recipe:
            # Base rows (modifier_id IS NULL) always apply.
            if not line.is_conditional:
                applicable.append(line)
                continue
            # Conditional rows apply when their modifier was chosen.
            if line.modifier_id in chosen_modifier_ids:
                applicable.append(line)
                chosen_groups.add(line.group_id)

        # For any modifier group that has conditional rows but where the cashier
        # picked nothing, fall back to that group's default option — otherwise a
        # Latte added without opening the dialog would deduct no milk at all.
        for group_id, group_rows in conditional_by_group.items():
            if group_id in chosen_groups:
                continue
            default_rows = [r for r in group_rows if r.is_default]
            if default_rows:
                applicable.extend(default_rows)

        for line in applicable:
            total = line.quantity * qty
            key = line.inventory_item_id
            if key in consumed:
                consumed[key].quantity += total
            else:
                consumed[key] = DeductionLine(
                    inventory_item_id=key,
                    name=line.ingredient_name,
                    quantity=total,
                    unit=line.unit,
                )

        if not consumed:
            return []

        lines = list(consumed.values())
        with self.db.transaction():
            for line in lines:
                self.adjust_stock(
                    line.inventory_item_id, -line.quantity, config.STOCK_SALE,
                    order_id=order_id, user_id=user_id,
                    note="خصم تلقائي عند البيع",
                )

        logger.info("تم خصم %s مكوّناً للطلب #%s", len(lines), order_id)
        return lines

    def preview_deduction(self, product_id: int, quantity: int = 1,
                          modifier_ids: Sequence[int] = ()) -> list[DeductionLine]:
        """What a sale *would* consume — used by the recipe editor preview."""
        chosen = {int(m) for m in modifier_ids}
        recipe = self.recipes_for_product(int(product_id))

        conditional_by_group: dict[int, list[RecipeLine]] = {}
        for line in recipe:
            if line.is_conditional and line.group_id:
                conditional_by_group.setdefault(line.group_id, []).append(line)

        applicable: list[RecipeLine] = []
        chosen_groups: set[int] = set()
        for line in recipe:
            if not line.is_conditional:
                applicable.append(line)
            elif line.modifier_id in chosen:
                applicable.append(line)
                chosen_groups.add(line.group_id)
        for group_id, rows in conditional_by_group.items():
            if group_id in chosen_groups:
                continue
            defaults = [r for r in rows if r.is_default]
            if defaults:
                applicable.extend(defaults)

        totals: dict[int, DeductionLine] = {}
        for line in applicable:
            amount = line.quantity * max(1, int(quantity))
            existing = totals.get(line.inventory_item_id)
            if existing:
                existing.quantity += amount
            else:
                totals[line.inventory_item_id] = DeductionLine(
                    line.inventory_item_id, line.ingredient_name, amount, line.unit
                )
        return list(totals.values())

    def theoretical_stock(self, since: str | None = None) -> list[dict[str, Any]]:
        """
        What stock *should* be, from movements — compared against current_qty to
        expose drift. Any difference means something changed stock without a
        movement row (a bug, or a manual edit outside the app).
        """
        rows = self.db.query_all(
            """
            SELECT i.id, i.name_ar, i.unit, i.current_qty,
                   IFNULL(SUM(m.change_qty), 0) AS moved
              FROM inventory_items i
              LEFT JOIN stock_movements m ON m.inventory_item_id = i.id
             GROUP BY i.id
             ORDER BY i.name_ar
            """
        )
        out: list[dict[str, Any]] = []
        for row in rows:
            current = float(row["current_qty"] or 0)
            moved = float(row["moved"] or 0)
            out.append({
                "id": int(row["id"]),
                "name": str(row["name_ar"]),
                "unit": str(row["unit"]),
                "current_qty": current,
                "moved_qty": moved,
                "drift": round(current - moved, 6),
            })
        return out
