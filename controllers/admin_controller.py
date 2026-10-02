"""
controllers/admin_controller.py — everything the Admin role can do.

Qt-free. Three jobs:
    1. Menu management: categories, products, prices, modifier groups/options.
    2. Reporting: daily/monthly sales, top items, peak hours, exports.
    3. Settings: tax, cafe identity, printer and backup configuration.

Every write is permission-checked through AuthController and audited, so the
`audit_log` is a real record of who changed a price and when.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Sequence

import config

logger = logging.getLogger(__name__)

__all__ = [
    "AdminController",
    "SalesReport",
    "MenuError",
    "ReportsDir",
]

ReportsDir = config.LOG_DIR / "reports"


class MenuError(Exception):
    """Menu edit rejected (Arabic message, safe to show the admin)."""


# --------------------------------------------------------------------------- #
# Report containers
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class SalesReport:
    """Everything one reporting period needs, computed in a handful of queries."""

    start: str = ""
    end: str = ""
    label: str = ""
    order_count: int = 0
    gross_minor: int = 0
    cash_minor: int = 0
    card_minor: int = 0
    discount_minor: int = 0
    tax_minor: int = 0
    voided_count: int = 0
    avg_order_minor: int = 0
    daily: list[dict[str, Any]] = field(default_factory=list)     # per-day series
    top_items: list[dict[str, Any]] = field(default_factory=list)
    by_category: list[dict[str, Any]] = field(default_factory=list)
    peak_hours: list[dict[str, Any]] = field(default_factory=list)
    by_payment: list[dict[str, Any]] = field(default_factory=list)
    cogs_minor: int = 0

    @property
    def gross_margin_minor(self) -> int:
        return self.gross_minor - self.cogs_minor

    @property
    def margin_percent(self) -> float:
        return (self.gross_margin_minor / self.gross_minor * 100.0) if self.gross_minor else 0.0

    @property
    def busiest_hour(self) -> dict[str, Any] | None:
        return max(self.peak_hours, key=lambda h: h["orders"], default=None)


# --------------------------------------------------------------------------- #
# Controller
# --------------------------------------------------------------------------- #
class AdminController:
    """Menu CRUD, reporting and settings for the admin role."""

    def __init__(self, auth: Any = None, db: Any = None) -> None:
        self.auth = auth
        self._db = db

    @property
    def db(self):
        if self._db is None:
            from database.db_manager import get_db

            self._db = get_db()
        return self._db

    # -- permissions ------------------------------------------------------- #
    def _require(self, capability: str) -> None:
        if self.auth is None:
            return
        try:
            self.auth.require(capability)
        except Exception as exc:
            raise MenuError(str(exc)) from exc

    def can(self, capability: str) -> bool:
        return bool(self.auth is None or self.auth.can(capability))

    def _audit(self, action: str, entity: str, entity_id: int | None, details: str = "") -> None:
        user = getattr(self.auth, "current_user", None)
        try:
            self.db.execute(
                """
                INSERT INTO audit_log (user_id, action, entity, entity_id, details)
                VALUES (?, ?, ?, ?, ?)
                """,
                (getattr(user, "id", None), action, entity, entity_id, details[:500]),
            )
        except Exception as exc:  # pragma: no cover
            logger.debug("تعذّر كتابة سجل التدقيق: %s", exc)

    # ------------------------------------------------------------------ #
    # CATEGORIES
    # ------------------------------------------------------------------ #
    def list_categories(self, active_only: bool = False) -> list[Any]:
        from models.product import ProductRepository

        return ProductRepository(self.db).list_categories(active_only=active_only)

    def create_category(self, name_ar: str, name_en: str = "", color: str = "",
                        sort_order: int | None = None) -> int:
        self._require("manage_menu")
        name = str(name_ar or "").strip()
        if not name:
            raise MenuError("اسم التصنيف مطلوب")
        if self.db.query_value(
            "SELECT 1 FROM categories WHERE name_ar = ?", (name,)
        ):
            raise MenuError("يوجد تصنيف بنفس الاسم")
        if sort_order is None:
            sort_order = self.db.scalar("SELECT IFNULL(MAX(sort_order), 0) + 1 FROM categories")
        category_id = self.db.insert(
            "INSERT INTO categories (name_ar, name_en, color, sort_order) VALUES (?, ?, ?, ?)",
            (name, name_en or "", color or "", int(sort_order)),
        )
        self._audit("category_created", "categories", category_id, name)
        return category_id

    def update_category(self, category_id: int, **fields: Any) -> None:
        self._require("manage_menu")
        allowed = {"name_ar", "name_en", "color", "icon", "sort_order", "is_active"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return
        assignments = ", ".join(f"{k} = ?" for k in updates)
        self.db.execute(
            f"UPDATE categories SET {assignments} WHERE id = ?",
            tuple(list(updates.values()) + [int(category_id)]),
        )
        self._audit("category_updated", "categories", category_id,
                    ", ".join(f"{k}={v}" for k, v in updates.items()))

    def delete_category(self, category_id: int) -> None:
        """
        Delete only when empty. The schema uses ON DELETE RESTRICT for a reason:
        silently orphaning 10 products would be worse than refusing.
        """
        self._require("manage_menu")
        count = self.db.scalar(
            "SELECT COUNT(*) FROM products WHERE category_id = ?", (int(category_id),)
        )
        if count:
            raise MenuError(
                f"لا يمكن حذف التصنيف — يحتوي على {count} صنف. انقل الأصناف أو أوقف التصنيف."
            )
        self.db.execute("DELETE FROM categories WHERE id = ?", (int(category_id),))
        self._audit("category_deleted", "categories", category_id)

    # ------------------------------------------------------------------ #
    # PRODUCTS
    # ------------------------------------------------------------------ #
    def list_products(self, category_id: int | None = None, search: str = "",
                      active_only: bool = False) -> list[Any]:
        from models.product import ProductRepository

        return ProductRepository(self.db).list_products(
            category_id, search=search, active_only=active_only
        )

    def get_product(self, product_id: int) -> Any:
        from models.product import ProductRepository

        return ProductRepository(self.db).get_product(product_id)

    @staticmethod
    def parse_price(text: str) -> int:
        """
        Parse a price the admin typed into integer minor units.

        Accepts '2,500', '2500', '2500.00', Arabic-Indic digits. Rejects
        anything else rather than silently storing 0.
        """
        raw = str(text or "").strip()
        raw = raw.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
        raw = raw.replace(",", "").replace("٬", "").replace(" ", "")
        if not raw:
            raise MenuError("السعر مطلوب")
        try:
            value = Decimal(raw)
        except (InvalidOperation, ValueError) as exc:
            raise MenuError(f"سعر غير صالح: {text}") from exc
        if value < 0:
            raise MenuError("السعر لا يمكن أن يكون سالباً")
        return config.to_minor(value)

    def create_product(self, category_id: int, name_ar: str, price_minor: int,
                       name_en: str = "", cost_minor: int = 0, sku: str = "",
                       track_inventory: bool = True, sort_order: int | None = None) -> int:
        self._require("manage_menu")
        name = str(name_ar or "").strip()
        if not name:
            raise MenuError("اسم الصنف مطلوب")
        if not self.db.query_value("SELECT 1 FROM categories WHERE id = ?", (int(category_id),)):
            raise MenuError("التصنيف غير موجود")
        if price_minor < 0:
            raise MenuError("السعر لا يمكن أن يكون سالباً")
        if sort_order is None:
            sort_order = self.db.scalar(
                "SELECT IFNULL(MAX(sort_order), 0) + 1 FROM products WHERE category_id = ?",
                (int(category_id),),
            )
        product_id = self.db.insert(
            """
            INSERT INTO products (category_id, name_ar, name_en, sku, price_minor,
                                  cost_minor, track_inventory, sort_order)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (int(category_id), name, name_en or "", sku or None, int(price_minor),
             int(cost_minor), int(bool(track_inventory)), int(sort_order)),
        )
        self._audit("product_created", "products", product_id,
                    f"{name} price={price_minor}")
        return product_id

    def update_product(self, product_id: int, **fields: Any) -> None:
        """Update a product. Price changes are audited with before/after values."""
        self._require("manage_menu")
        allowed = {"category_id", "name_ar", "name_en", "sku", "price_minor",
                   "cost_minor", "color", "sort_order", "is_active", "track_inventory"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return

        before = self.db.query_one(
            "SELECT name_ar, price_minor FROM products WHERE id = ?", (int(product_id),)
        )
        if before is None:
            raise MenuError("الصنف غير موجود")
        if "price_minor" in updates and int(updates["price_minor"]) < 0:
            raise MenuError("السعر لا يمكن أن يكون سالباً")

        assignments = ", ".join(f"{k} = ?" for k in updates)
        self.db.execute(
            f"UPDATE products SET {assignments}, updated_at = datetime('now','localtime') "
            "WHERE id = ?",
            tuple(list(updates.values()) + [int(product_id)]),
        )

        detail = ", ".join(f"{k}={v}" for k, v in updates.items())
        if "price_minor" in updates:
            detail = (f"price {before['price_minor']} -> {updates['price_minor']} "
                      f"({before['name_ar']})")
        self._audit("product_updated", "products", product_id, detail)

    def set_product_active(self, product_id: int, active: bool) -> None:
        self.update_product(product_id, is_active=int(bool(active)))
        self._audit("product_enabled" if active else "product_disabled",
                    "products", product_id)

    def delete_product(self, product_id: int) -> None:
        """
        Products are never hard-deleted: order_items reference them and history
        must stay readable. Disabling is the supported operation.
        """
        self._require("manage_menu")
        sold = self.db.scalar(
            "SELECT COUNT(*) FROM order_items WHERE product_id = ?", (int(product_id),)
        )
        if sold:
            self.set_product_active(product_id, False)
            raise MenuError(
                f"هذا الصنف بِيع {sold} مرة — لا يمكن حذفه، تم إيقافه بدلاً من ذلك"
            )
        self.db.execute("DELETE FROM recipes WHERE product_id = ?", (int(product_id),))
        self.db.execute("DELETE FROM product_modifier_groups WHERE product_id = ?",
                        (int(product_id),))
        self.db.execute("DELETE FROM products WHERE id = ?", (int(product_id),))
        self._audit("product_deleted", "products", product_id)

    # ------------------------------------------------------------------ #
    # MODIFIERS
    # ------------------------------------------------------------------ #
    def list_modifier_groups(self, with_options: bool = True) -> list[Any]:
        from models.product import Modifier, ModifierGroup

        rows = self.db.query_all(
            "SELECT * FROM modifier_groups ORDER BY sort_order, id"
        )
        groups: list[ModifierGroup] = []
        for row in rows:
            options: list[Modifier] = []
            if with_options:
                options = [
                    Modifier.from_row(r)
                    for r in self.db.query_all(
                        "SELECT * FROM modifiers WHERE group_id = ? ORDER BY sort_order, id",
                        (int(row["id"]),),
                    )
                ]
            groups.append(ModifierGroup.from_row(row, options))
        return groups

    def create_modifier_group(self, name_ar: str, group_type: str = "single",
                              is_required: bool = False, max_select: int = 1) -> int:
        self._require("manage_modifiers")
        name = str(name_ar or "").strip()
        if not name:
            raise MenuError("اسم مجموعة الخيارات مطلوب")
        if group_type not in ("single", "multi", "text"):
            raise MenuError(f"نوع مجموعة غير معروف: {group_type}")
        sort_order = self.db.scalar("SELECT IFNULL(MAX(sort_order), 0) + 1 FROM modifier_groups")
        group_id = self.db.insert(
            """
            INSERT INTO modifier_groups (name_ar, group_type, is_required, min_select,
                                         max_select, sort_order)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (name, group_type, int(bool(is_required)), 1 if is_required else 0,
             int(max_select), int(sort_order)),
        )
        self._audit("modifier_group_created", "modifier_groups", group_id, name)
        return group_id

    def update_modifier_group(self, group_id: int, **fields: Any) -> None:
        self._require("manage_modifiers")
        allowed = {"name_ar", "name_en", "group_type", "is_required", "min_select",
                   "max_select", "sort_order", "is_active"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return
        assignments = ", ".join(f"{k} = ?" for k in updates)
        self.db.execute(
            f"UPDATE modifier_groups SET {assignments} WHERE id = ?",
            tuple(list(updates.values()) + [int(group_id)]),
        )
        self._audit("modifier_group_updated", "modifier_groups", group_id,
                    ", ".join(f"{k}={v}" for k, v in updates.items()))

    def delete_modifier_group(self, group_id: int) -> None:
        self._require("manage_modifiers")
        self.db.execute("DELETE FROM product_modifier_groups WHERE group_id = ?", (int(group_id),))
        self.db.execute("DELETE FROM modifiers WHERE group_id = ?", (int(group_id),))
        self.db.execute("DELETE FROM modifier_groups WHERE id = ?", (int(group_id),))
        self._audit("modifier_group_deleted", "modifier_groups", group_id)

    def create_modifier(self, group_id: int, name_ar: str, price_minor: int = 0,
                        is_default: bool = False) -> int:
        self._require("manage_modifiers")
        name = str(name_ar or "").strip()
        if not name:
            raise MenuError("اسم الخيار مطلوب")
        if not self.db.query_value("SELECT 1 FROM modifier_groups WHERE id = ?", (int(group_id),)):
            raise MenuError("مجموعة الخيارات غير موجودة")
        sort_order = self.db.scalar(
            "SELECT IFNULL(MAX(sort_order), 0) + 1 FROM modifiers WHERE group_id = ?",
            (int(group_id),),
        )
        modifier_id = self.db.insert(
            """
            INSERT INTO modifiers (group_id, name_ar, price_minor, is_default, sort_order)
            VALUES (?, ?, ?, ?, ?)
            """,
            (int(group_id), name, int(price_minor), int(bool(is_default)), int(sort_order)),
        )
        self._audit("modifier_created", "modifiers", modifier_id,
                    f"{name} delta={price_minor}")
        return modifier_id

    def update_modifier(self, modifier_id: int, **fields: Any) -> None:
        self._require("manage_modifiers")
        allowed = {"name_ar", "name_en", "price_minor", "is_default", "sort_order", "is_active"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return
        assignments = ", ".join(f"{k} = ?" for k in updates)
        self.db.execute(
            f"UPDATE modifiers SET {assignments} WHERE id = ?",
            tuple(list(updates.values()) + [int(modifier_id)]),
        )
        self._audit("modifier_updated", "modifiers", modifier_id,
                    ", ".join(f"{k}={v}" for k, v in updates.items()))

    def delete_modifier(self, modifier_id: int) -> None:
        self._require("manage_modifiers")
        self.db.execute("DELETE FROM recipes WHERE modifier_id = ?", (int(modifier_id),))
        self.db.execute("DELETE FROM modifiers WHERE id = ?", (int(modifier_id),))
        self._audit("modifier_deleted", "modifiers", modifier_id)

    def groups_for_product(self, product_id: int) -> list[int]:
        return [
            int(r["group_id"])
            for r in self.db.query_all(
                "SELECT group_id FROM product_modifier_groups WHERE product_id = ? "
                "ORDER BY sort_order",
                (int(product_id),),
            )
        ]

    def set_product_groups(self, product_id: int, group_ids: Sequence[int]) -> None:
        """Replace a product's modifier groups (used by the editor's checklist)."""
        self._require("manage_modifiers")

        # Validate up front: without this a missing product or group surfaces as
        # a raw sqlite3.IntegrityError instead of something an admin can act on.
        if not self.db.query_value("SELECT 1 FROM products WHERE id = ?", (int(product_id),)):
            raise MenuError("الصنف غير موجود")
        for group_id in group_ids:
            if not self.db.query_value("SELECT 1 FROM modifier_groups WHERE id = ?",
                                       (int(group_id),)):
                raise MenuError(f"مجموعة الخيارات غير موجودة: {group_id}")

        with self.db.transaction():
            self.db.execute("DELETE FROM product_modifier_groups WHERE product_id = ?",
                            (int(product_id),))
            for index, group_id in enumerate(group_ids, start=1):
                self.db.execute(
                    "INSERT OR IGNORE INTO product_modifier_groups "
                    "(product_id, group_id, sort_order) VALUES (?, ?, ?)",
                    (int(product_id), int(group_id), index),
                )
        self._audit("product_modifiers_set", "products", product_id,
                    f"groups={list(group_ids)}")

    # ------------------------------------------------------------------ #
    # REPORTS
    # ------------------------------------------------------------------ #
    @staticmethod
    def today() -> str:
        return date.today().strftime("%Y-%m-%d")

    @staticmethod
    def month_start(when: date | None = None) -> str:
        when = when or date.today()
        return when.replace(day=1).strftime("%Y-%m-%d")

    def _cogs_for_period(self, start: str, end: str) -> int:
        """
        Cost of goods sold: recipe cost per unit × quantity, for base rows only.

        Approximate by design — it uses the ingredient cost captured in the
        recipe at report time, not a historical snapshot, so it moves if an
        admin edits an ingredient price. Noted in the README.
        """
        row = self.db.query_one(
            """
            SELECT IFNULL(SUM(
                     oi.quantity * IFNULL((
                         SELECT SUM(r.quantity * i.cost_per_unit_minor)
                           FROM recipes r
                           JOIN inventory_items i ON i.id = r.inventory_item_id
                          WHERE r.product_id = oi.product_id AND r.modifier_id IS NULL
                     ), 0)
                   ), 0) AS cogs
              FROM order_items oi
              JOIN orders o ON o.id = oi.order_id
             WHERE o.status = ? AND date(o.created_at) BETWEEN date(?) AND date(?)
            """,
            (config.ORDER_STATUS_COMPLETED, start, end),
        )
        return int(round(float(row["cogs"] or 0)))

    def sales_report(self, start: str | None = None, end: str | None = None,
                     label: str = "", top_n: int = 10) -> SalesReport:
        """Full report for a period. All figures come from completed orders."""
        start = start or self.today()
        end = end or start
        report = SalesReport(start=start, end=end, label=label or f"{start} → {end}")

        head = self.db.query_one(
            """
            SELECT COUNT(*) AS n,
                   IFNULL(SUM(total_minor), 0) AS gross,
                   IFNULL(SUM(CASE WHEN payment_method='cash' THEN total_minor ELSE 0 END), 0) AS cash,
                   IFNULL(SUM(CASE WHEN payment_method='card' THEN total_minor ELSE 0 END), 0) AS card,
                   IFNULL(SUM(discount_minor), 0) AS discount,
                   IFNULL(SUM(tax_minor), 0) AS tax
              FROM orders
             WHERE status = ? AND date(created_at) BETWEEN date(?) AND date(?)
            """,
            (config.ORDER_STATUS_COMPLETED, start, end),
        )
        report.order_count = int(head["n"] or 0)
        report.gross_minor = int(head["gross"] or 0)
        report.cash_minor = int(head["cash"] or 0)
        report.card_minor = int(head["card"] or 0)
        report.discount_minor = int(head["discount"] or 0)
        report.tax_minor = int(head["tax"] or 0)
        report.avg_order_minor = (
            int(report.gross_minor / report.order_count) if report.order_count else 0
        )
        report.voided_count = self.db.scalar(
            "SELECT COUNT(*) FROM orders WHERE status = ? "
            "AND date(created_at) BETWEEN date(?) AND date(?)",
            (config.ORDER_STATUS_VOIDED, start, end),
        )
        report.cogs_minor = self._cogs_for_period(start, end)

        # Per-day series — zero-filled so the chart has no gaps.
        rows = {
            str(r["day"]): r
            for r in self.db.query_all(
                """
                SELECT date(created_at) AS day, COUNT(*) AS n,
                       IFNULL(SUM(total_minor), 0) AS gross
                  FROM orders
                 WHERE status = ? AND date(created_at) BETWEEN date(?) AND date(?)
                 GROUP BY date(created_at)
                """,
                (config.ORDER_STATUS_COMPLETED, start, end),
            )
        }
        cursor = datetime.strptime(start, "%Y-%m-%d").date()
        last = datetime.strptime(end, "%Y-%m-%d").date()
        while cursor <= last:
            key = cursor.strftime("%Y-%m-%d")
            row = rows.get(key)
            report.daily.append({
                "day": key,
                "orders": int(row["n"]) if row else 0,
                "gross_minor": int(row["gross"]) if row else 0,
            })
            cursor += timedelta(days=1)

        report.top_items = [
            {
                "name": str(r["name_ar"]),
                "qty": int(r["qty"] or 0),
                "revenue_minor": int(r["revenue"] or 0),
            }
            for r in self.db.query_all(
                """
                SELECT oi.name_ar, SUM(oi.quantity) AS qty,
                       SUM(oi.line_total_minor) AS revenue
                  FROM order_items oi
                  JOIN orders o ON o.id = oi.order_id
                 WHERE o.status = ? AND date(o.created_at) BETWEEN date(?) AND date(?)
                 GROUP BY oi.name_ar
                 ORDER BY qty DESC, revenue DESC
                 LIMIT ?
                """,
                (config.ORDER_STATUS_COMPLETED, start, end, int(top_n)),
            )
        ]

        report.by_category = [
            {
                "name": str(r["category"]),
                "qty": int(r["qty"] or 0),
                "revenue_minor": int(r["revenue"] or 0),
            }
            for r in self.db.query_all(
                """
                SELECT IFNULL(c.name_ar, 'غير مصنّف') AS category,
                       SUM(oi.quantity) AS qty,
                       SUM(oi.line_total_minor) AS revenue
                  FROM order_items oi
                  JOIN orders o ON o.id = oi.order_id
                  LEFT JOIN products p ON p.id = oi.product_id
                  LEFT JOIN categories c ON c.id = p.category_id
                 WHERE o.status = ? AND date(o.created_at) BETWEEN date(?) AND date(?)
                 GROUP BY c.id
                 ORDER BY revenue DESC
                """,
                (config.ORDER_STATUS_COMPLETED, start, end),
            )
        ]

        # Peak hours — all 24 buckets, zero-filled, so the chart reads correctly.
        hour_rows = {
            int(r["hour"]): r
            for r in self.db.query_all(
                """
                SELECT CAST(strftime('%H', created_at) AS INTEGER) AS hour,
                       COUNT(*) AS n, IFNULL(SUM(total_minor), 0) AS gross
                  FROM orders
                 WHERE status = ? AND date(created_at) BETWEEN date(?) AND date(?)
                 GROUP BY hour
                """,
                (config.ORDER_STATUS_COMPLETED, start, end),
            )
        }
        report.peak_hours = [
            {
                "hour": hour,
                "label": f"{hour:02d}:00",
                "orders": int(hour_rows[hour]["n"]) if hour in hour_rows else 0,
                "gross_minor": int(hour_rows[hour]["gross"]) if hour in hour_rows else 0,
            }
            for hour in range(24)
        ]

        report.by_payment = [
            {"method": "cash", "label": "نقداً", "count": 0, "total_minor": report.cash_minor},
            {"method": "card", "label": "بطاقة", "count": 0, "total_minor": report.card_minor},
        ]
        for entry in self.db.query_all(
            """
            SELECT payment_method, COUNT(*) AS n
              FROM orders
             WHERE status = ? AND date(created_at) BETWEEN date(?) AND date(?)
             GROUP BY payment_method
            """,
            (config.ORDER_STATUS_COMPLETED, start, end),
        ):
            for item in report.by_payment:
                if item["method"] == entry["payment_method"]:
                    item["count"] = int(entry["n"] or 0)

        return report

    def daily_report(self, day: str | None = None) -> SalesReport:
        day = day or self.today()
        return self.sales_report(day, day, label=f"تقرير يوم {day}")

    def monthly_report(self, year: int | None = None, month: int | None = None) -> SalesReport:
        today = date.today()
        year = year or today.year
        month = month or today.month
        start = date(year, month, 1)
        end = (date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)) - timedelta(days=1)
        return self.sales_report(
            start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"),
            label=f"تقرير شهر {year}-{month:02d}",
        )

    def orders_in_period(self, start: str, end: str, limit: int = 1000) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self.db.query_all(
                "SELECT * FROM orders WHERE date(created_at) BETWEEN date(?) AND date(?) "
                "ORDER BY id DESC LIMIT ?",
                (start, end, int(limit)),
            )
        ]

    def shift_history(self, limit: int = 50) -> list[Any]:
        from models.shift import ShiftRepository

        return ShiftRepository(self.db).list_shifts(limit)

    # ------------------------------------------------------------------ #
    # EXPORTS
    # ------------------------------------------------------------------ #
    def export_report_csv(self, report: SalesReport) -> Path:
        """Machine-readable export: a summary block, then each table."""
        ReportsDir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        path = ReportsDir / f"sales_{report.start}_{report.end}_{stamp}.csv"

        # utf-8-sig: Excel on Windows shows Arabic correctly only with the BOM.
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["التقرير", report.label])
            writer.writerow(["من", report.start, "إلى", report.end])
            writer.writerow([])
            writer.writerow(["ملخص", "القيمة (وحدة صغرى)"])
            for key, value in (
                ("عدد الطلبات", report.order_count),
                ("طلبات ملغاة", report.voided_count),
                ("إجمالي المبيعات", report.gross_minor),
                ("نقداً", report.cash_minor),
                ("بطاقة", report.card_minor),
                ("الخصومات", report.discount_minor),
                ("الضريبة", report.tax_minor),
                ("متوسط الطلب", report.avg_order_minor),
                ("تكلفة المبيعات", report.cogs_minor),
                ("الربح الإجمالي", report.gross_margin_minor),
            ):
                writer.writerow([key, value])

            writer.writerow([])
            writer.writerow(["المبيعات اليومية", "عدد الطلبات", "الإجمالي"])
            for row in report.daily:
                writer.writerow([row["day"], row["orders"], row["gross_minor"]])

            writer.writerow([])
            writer.writerow(["الأصناف الأكثر مبيعاً", "الكمية", "الإيراد"])
            for row in report.top_items:
                writer.writerow([row["name"], row["qty"], row["revenue_minor"]])

            writer.writerow([])
            writer.writerow(["ساعات الذروة", "عدد الطلبات", "الإجمالي"])
            for row in report.peak_hours:
                if row["orders"]:
                    writer.writerow([row["label"], row["orders"], row["gross_minor"]])

            writer.writerow([])
            writer.writerow(["حسب التصنيف", "الكمية", "الإيراد"])
            for row in report.by_category:
                writer.writerow([row["name"], row["qty"], row["revenue_minor"]])

        self._audit("report_exported_csv", "reports", None, str(path))
        config.secure_file(path)
        return path

    def export_report_html(self, report: SalesReport) -> Path:
        """
        Print-ready HTML report.

        Chosen over a PDF library on purpose: reportlab would need an Arabic
        font embedded and registered, whereas an HTML file renders Arabic with
        the system fonts and any browser can print it to PDF — offline, with no
        extra dependency. The admin gets a real document either way.
        """
        ReportsDir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        path = ReportsDir / f"sales_{report.start}_{report.end}_{stamp}.html"

        def money(minor: int) -> str:
            return config.format_money(minor)

        max_daily = max((r["gross_minor"] for r in report.daily), default=0) or 1
        max_hour = max((h["orders"] for h in report.peak_hours), default=0) or 1

        rows_daily = "".join(
            f"<tr><td>{r['day']}</td><td>{r['orders']}</td><td>{money(r['gross_minor'])}</td>"
            f"<td><div class='bar' style='width:{r['gross_minor'] / max_daily * 100:.1f}%'></div></td></tr>"
            for r in report.daily
        )
        rows_top = "".join(
            f"<tr><td>{r['name']}</td><td>{r['qty']}</td><td>{money(r['revenue_minor'])}</td></tr>"
            for r in report.top_items
        )
        rows_hours = "".join(
            f"<tr><td>{h['label']}</td><td>{h['orders']}</td>"
            f"<td><div class='bar' style='width:{h['orders'] / max_hour * 100:.1f}%'></div></td></tr>"
            for h in report.peak_hours if h["orders"]
        )
        rows_cat = "".join(
            f"<tr><td>{r['name']}</td><td>{r['qty']}</td><td>{money(r['revenue_minor'])}</td></tr>"
            for r in report.by_category
        )

        html = f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<title>{report.label}</title>
<style>
  :root {{ --ink:#2A231D; --muted:#6E6154; --accent:#B77A33; --line:#E2D6C5; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: "Noto Naskh Arabic","Cairo","Tajawal","Segoe UI",sans-serif;
         margin: 0; padding: 32px; background:#F7F2EA; color: var(--ink); }}
  .sheet {{ max-width: 900px; margin: 0 auto; background:#fff; border:1px solid var(--line);
            border-radius: 16px; padding: 28px 32px; }}
  h1 {{ font-size: 26px; margin: 0 0 4px; }}
  .sub {{ color: var(--muted); margin-bottom: 20px; }}
  .cards {{ display:grid; grid-template-columns: repeat(4, 1fr); gap:12px; margin: 18px 0 26px; }}
  .card {{ border:1px solid var(--line); border-radius:12px; padding:12px 14px; background:#FBF7F1; }}
  .card .k {{ font-size:12px; color:var(--muted); }}
  .card .v {{ font-size:19px; font-weight:700; margin-top:4px; }}
  .card .v.accent {{ color: var(--accent); }}
  h2 {{ font-size:17px; margin: 22px 0 8px; border-right: 4px solid var(--accent);
        padding-right: 10px; }}
  table {{ width:100%; border-collapse: collapse; font-size:14px; }}
  th, td {{ padding: 7px 10px; border-bottom:1px solid var(--line); text-align: right; }}
  th {{ background:#F1E9DD; color:var(--muted); font-size:13px; }}
  .bar {{ height: 10px; background: var(--accent); border-radius: 5px; min-width:2px; }}
  footer {{ margin-top: 26px; color: var(--muted); font-size: 12px; text-align:center; }}
  @media print {{ body {{ background:#fff; padding:0; }} .sheet {{ border:none; }} }}
</style>
</head>
<body>
<div class="sheet">
  <h1>{report.label}</h1>
  <div class="sub">من {report.start} إلى {report.end}</div>

  <div class="cards">
    <div class="card"><div class="k">عدد الطلبات</div><div class="v">{report.order_count}</div></div>
    <div class="card"><div class="k">إجمالي المبيعات</div><div class="v accent">{money(report.gross_minor)}</div></div>
    <div class="card"><div class="k">متوسط الطلب</div><div class="v">{money(report.avg_order_minor)}</div></div>
    <div class="card"><div class="k">الربح الإجمالي</div><div class="v">{money(report.gross_margin_minor)}</div></div>
    <div class="card"><div class="k">نقداً</div><div class="v">{money(report.cash_minor)}</div></div>
    <div class="card"><div class="k">بطاقة</div><div class="v">{money(report.card_minor)}</div></div>
    <div class="card"><div class="k">الخصومات</div><div class="v">{money(report.discount_minor)}</div></div>
    <div class="card"><div class="k">هامش الربح</div><div class="v">{report.margin_percent:.1f}%</div></div>
  </div>

  <h2>المبيعات اليومية</h2>
  <table><thead><tr><th>اليوم</th><th>الطلبات</th><th>الإجمالي</th><th></th></tr></thead>
  <tbody>{rows_daily}</tbody></table>

  <h2>الأصناف الأكثر مبيعاً</h2>
  <table><thead><tr><th>الصنف</th><th>الكمية</th><th>الإيراد</th></tr></thead>
  <tbody>{rows_top}</tbody></table>

  <h2>ساعات الذروة</h2>
  <table><thead><tr><th>الساعة</th><th>الطلبات</th><th></th></tr></thead>
  <tbody>{rows_hours}</tbody></table>

  <h2>حسب التصنيف</h2>
  <table><thead><tr><th>التصنيف</th><th>الكمية</th><th>الإيراد</th></tr></thead>
  <tbody>{rows_cat}</tbody></table>

  <footer>أُنشئ في {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} — {config.APP_NAME}</footer>
</div>
</body>
</html>"""
        path.write_text(html, encoding="utf-8")
        self._audit("report_exported_html", "reports", None, str(path))
        config.secure_file(path)
        return path

    # ------------------------------------------------------------------ #
    # SETTINGS
    # ------------------------------------------------------------------ #
    def settings(self) -> dict[str, str]:
        return self.db.get_all_settings()

    def update_settings(self, values: dict[str, Any]) -> None:
        self._require("manage_settings")
        for key, value in values.items():
            self.db.set_setting(key, value)
        self._audit("settings_updated", "settings", None,
                    ", ".join(f"{k}={v}" for k, v in values.items()))

    def set_tax_rate(self, rate_text: str) -> Decimal:
        """Accept '0', '0.15', '15%'. Prices stay tax-inclusive either way."""
        raw = str(rate_text or "").strip().replace("%", "")
        try:
            rate = Decimal(raw)
        except (InvalidOperation, ValueError) as exc:
            raise MenuError(f"نسبة ضريبة غير صالحة: {rate_text}") from exc
        if rate < 0 or rate > 1:
            raise MenuError("نسبة الضريبة يجب أن تكون بين 0 و 1 (مثال: 0.15 لـ 15%)")
        self.update_settings({"tax_rate": str(rate)})
        return rate

    def backup_status(self) -> dict[str, Any]:
        from controllers.backup_controller import BackupController

        return BackupController(self.db).status()

    def printer_status(self) -> dict[str, Any]:
        from services.printer_service import PrinterService

        return PrinterService(self.db).status()
