"""
models/product.py — menu entities and read access: categories, products,
modifier groups and their options.

Qt-free by design (the product grid renders these, it does not define them).

Money: every price is an integer in minor units and is TAX-INCLUSIVE — the
number the customer pays. A modifier's `price_minor` is a *delta* added to the
product price (and may be negative, e.g. a small size).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

import config

logger = logging.getLogger(__name__)

__all__ = [
    "Category",
    "Product",
    "Modifier",
    "ModifierGroup",
    "ProductRepository",
    "price_delta_label",
]


def price_delta_label(price_minor: int) -> str:
    """'+500 ر.ي' / '−300 ر.ي' / '' for a zero delta."""
    if not price_minor:
        return ""
    sign = "+" if price_minor > 0 else "−"
    return f"{sign}{config.format_money(abs(price_minor))}"


# --------------------------------------------------------------------------- #
# Entities
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class Category:
    id: int
    name_ar: str
    name_en: str = ""
    icon: str = ""
    color: str = ""
    sort_order: int = 0
    is_active: bool = True
    product_count: int = 0

    @property
    def display_name(self) -> str:
        return self.name_ar or self.name_en

    @property
    def label(self) -> str:
        """
        Rail label: name plus how many items it holds.

        The count is always shown, including (0) — hiding it made an empty
        category look like a different kind of button next to its neighbours.
        """
        return f"{self.display_name}  ({self.product_count})"

    @classmethod
    def from_row(cls, row: Any) -> "Category":
        keys = row.keys() if hasattr(row, "keys") else []
        return cls(
            id=int(row["id"]),
            name_ar=str(row["name_ar"] or ""),
            name_en=str(row["name_en"] or ""),
            icon=str(row["icon"] or "") if "icon" in keys else "",
            color=str(row["color"] or "") if "color" in keys else "",
            sort_order=int(row["sort_order"] or 0) if "sort_order" in keys else 0,
            is_active=bool(row["is_active"]) if "is_active" in keys else True,
            product_count=int(row["product_count"] or 0) if "product_count" in keys else 0,
        )


@dataclass(slots=True)
class Modifier:
    id: int
    group_id: int
    name_ar: str
    name_en: str = ""
    price_minor: int = 0
    is_default: bool = False
    sort_order: int = 0
    is_active: bool = True

    @property
    def display_name(self) -> str:
        return self.name_ar or self.name_en

    @property
    def price_label(self) -> str:
        return price_delta_label(self.price_minor)

    @classmethod
    def from_row(cls, row: Any) -> "Modifier":
        return cls(
            id=int(row["id"]),
            group_id=int(row["group_id"]),
            name_ar=str(row["name_ar"] or ""),
            name_en=str(row["name_en"] or ""),
            price_minor=int(row["price_minor"] or 0),
            is_default=bool(row["is_default"]),
            sort_order=int(row["sort_order"] or 0),
            is_active=bool(row["is_active"]),
        )


@dataclass(slots=True)
class ModifierGroup:
    """A set of choices shown as one block in the modifier dialog."""

    id: int
    name_ar: str
    name_en: str = ""
    group_type: str = "single"          # single | multi | text
    is_required: bool = False
    min_select: int = 0
    max_select: int = 1
    sort_order: int = 0
    is_active: bool = True
    modifiers: list[Modifier] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        return self.name_ar or self.name_en

    @property
    def is_text(self) -> bool:
        return self.group_type == "text"

    @property
    def is_multi(self) -> bool:
        return self.group_type == "multi"

    @property
    def is_single(self) -> bool:
        return self.group_type == "single"

    @property
    def options(self) -> list[Modifier]:
        return [m for m in self.modifiers if m.is_active]

    def default_selection(self) -> list[Modifier]:
        """Pre-ticked options: the flagged default, or the first option when the
        group is required and nothing is flagged."""
        flagged = [m for m in self.options if m.is_default]
        if flagged:
            return flagged[: max(1, self.max_select)]
        if self.is_required and self.is_single and self.options:
            return [self.options[0]]
        return []

    @classmethod
    def from_row(cls, row: Any, modifiers: Sequence[Modifier] = ()) -> "ModifierGroup":
        return cls(
            id=int(row["id"]),
            name_ar=str(row["name_ar"] or ""),
            name_en=str(row["name_en"] or ""),
            group_type=str(row["group_type"] or "single"),
            is_required=bool(row["is_required"]),
            min_select=int(row["min_select"] or 0),
            max_select=int(row["max_select"] or 1),
            sort_order=int(row["sort_order"] or 0),
            is_active=bool(row["is_active"]),
            modifiers=list(modifiers),
        )


@dataclass(slots=True)
class Product:
    id: int
    category_id: int
    name_ar: str
    name_en: str = ""
    sku: str | None = None
    price_minor: int = 0            # tax-inclusive
    cost_minor: int = 0
    color: str = ""
    sort_order: int = 0
    is_active: bool = True
    track_inventory: bool = True

    @property
    def display_name(self) -> str:
        return self.name_ar or self.name_en

    @property
    def price_label(self) -> str:
        return config.format_money(self.price_minor)

    @property
    def margin_minor(self) -> int:
        """Admin-only figure. Never rendered in the cashier POS view."""
        return self.price_minor - self.cost_minor

    @classmethod
    def from_row(cls, row: Any) -> "Product":
        keys = row.keys() if hasattr(row, "keys") else []
        get = lambda k, d=None: (row[k] if k in keys else d)  # noqa: E731
        return cls(
            id=int(row["id"]),
            category_id=int(row["category_id"]),
            name_ar=str(row["name_ar"] or ""),
            name_en=str(row["name_en"] or ""),
            sku=get("sku"),
            price_minor=int(row["price_minor"] or 0),
            cost_minor=int(get("cost_minor", 0) or 0),
            color=str(get("color", "") or ""),
            sort_order=int(get("sort_order", 0) or 0),
            is_active=bool(get("is_active", 1)),
            track_inventory=bool(get("track_inventory", 1)),
        )


# --------------------------------------------------------------------------- #
# Repository
# --------------------------------------------------------------------------- #
_PRODUCT_COLUMNS = (
    "id, category_id, name_ar, name_en, sku, price_minor, cost_minor, color, "
    "sort_order, is_active, track_inventory"
)


class ProductRepository:
    """Read access to the menu. Menu *editing* belongs to the admin (Phase 5)."""

    def __init__(self, db: Any = None) -> None:
        self._db = db

    @property
    def db(self):
        if self._db is None:
            from database.db_manager import get_db

            self._db = get_db()
        return self._db

    # -- categories -------------------------------------------------------- #
    def list_categories(self, active_only: bool = True) -> list[Category]:
        sql = """
            SELECT c.id, c.name_ar, c.name_en, c.icon, c.color, c.sort_order, c.is_active,
                   (SELECT COUNT(*) FROM products p
                     WHERE p.category_id = c.id AND p.is_active = 1) AS product_count
              FROM categories c
        """
        if active_only:
            sql += " WHERE c.is_active = 1"
        sql += " ORDER BY c.sort_order, c.id"
        return [Category.from_row(r) for r in self.db.query_all(sql)]

    def get_category(self, category_id: int) -> Category | None:
        row = self.db.query_one(
            "SELECT id, name_ar, name_en, icon, color, sort_order, is_active "
            "FROM categories WHERE id = ?",
            (int(category_id),),
        )
        return Category.from_row(row) if row else None

    # -- products ---------------------------------------------------------- #
    def list_products(
        self,
        category_id: int | None = None,
        *,
        search: str = "",
        active_only: bool = True,
    ) -> list[Product]:
        clauses: list[str] = []
        params: list[Any] = []
        if category_id is not None:
            clauses.append("category_id = ?")
            params.append(int(category_id))
        if active_only:
            clauses.append("is_active = 1")
        if search and search.strip():
            # Match either language; LIKE is case-insensitive for ASCII in SQLite
            # and the seeded names are Arabic, so both are searched.
            needle = f"%{search.strip()}%"
            clauses.append("(name_ar LIKE ? OR name_en LIKE ? OR IFNULL(sku,'') LIKE ?)")
            params.extend([needle, needle, needle])

        sql = f"SELECT {_PRODUCT_COLUMNS} FROM products"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY sort_order, id"
        return [Product.from_row(r) for r in self.db.query_all(sql, tuple(params))]

    def get_product(self, product_id: int) -> Product | None:
        row = self.db.query_one(
            f"SELECT {_PRODUCT_COLUMNS} FROM products WHERE id = ?", (int(product_id),)
        )
        return Product.from_row(row) if row else None

    def count_products(self, active_only: bool = True) -> int:
        sql = "SELECT COUNT(*) FROM products" + (" WHERE is_active = 1" if active_only else "")
        return self.db.scalar(sql)

    # -- modifiers --------------------------------------------------------- #
    def modifier_groups_for_product(self, product_id: int) -> list[ModifierGroup]:
        """
        Groups attached to a product, each with its options.

        Two queries (groups, then all options) rather than one per group — a
        touch POS must not do N+1 round trips on every tile tap.
        """
        group_rows = self.db.query_all(
            """
            SELECT g.id, g.name_ar, g.name_en, g.group_type, g.is_required,
                   g.min_select, g.max_select, g.sort_order, g.is_active
              FROM modifier_groups g
              JOIN product_modifier_groups pg ON pg.group_id = g.id
             WHERE pg.product_id = ? AND g.is_active = 1
             ORDER BY pg.sort_order, g.sort_order
            """,
            (int(product_id),),
        )
        if not group_rows:
            return []

        group_ids = [int(r["id"]) for r in group_rows]
        placeholders = ",".join("?" * len(group_ids))
        option_rows = self.db.query_all(
            f"""
            SELECT id, group_id, name_ar, name_en, price_minor, is_default,
                   sort_order, is_active
              FROM modifiers
             WHERE group_id IN ({placeholders}) AND is_active = 1
             ORDER BY sort_order, id
            """,
            tuple(group_ids),
        )

        by_group: dict[int, list[Modifier]] = {gid: [] for gid in group_ids}
        for row in option_rows:
            by_group[int(row["group_id"])].append(Modifier.from_row(row))

        return [
            ModifierGroup.from_row(r, by_group.get(int(r["id"]), [])) for r in group_rows
        ]

    def has_modifiers(self, product_id: int) -> bool:
        return bool(
            self.db.scalar(
                "SELECT COUNT(*) FROM product_modifier_groups WHERE product_id = ?",
                (int(product_id),),
            )
        )

    # -- admin writes (used by the Phase-5 menu editor, handy in tests) ----- #
    def set_price(self, product_id: int, price_minor: int) -> None:
        self.db.execute(
            "UPDATE products SET price_minor = ?, updated_at = datetime('now','localtime') "
            "WHERE id = ?",
            (int(price_minor), int(product_id)),
        )

    def set_active(self, product_id: int, active: bool) -> None:
        self.db.execute(
            "UPDATE products SET is_active = ?, updated_at = datetime('now','localtime') "
            "WHERE id = ?",
            (int(bool(active)), int(product_id)),
        )
