"""
models/shift.py — cashier shifts and the figures behind a Z-Report.

Qt-free. A shift is one cashier's session at the till: it opens with a cash
float and closes with a counted drawer, and the difference between the two is
the over/short figure the owner actually cares about.

MONEY
    All amounts are integer minor units (whole rials for YER).
    expected_cash = opening_float + cash sales
    difference    = counted_cash - expected_cash      (negative = short)

Only one shift may be open at a time; the schema enforces it with a partial
unique index, and open_shift() surfaces a clear Arabic error rather than letting
a raw IntegrityError escape.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import config

logger = logging.getLogger(__name__)

__all__ = ["Shift", "ShiftRepository", "ShiftError", "ShiftTotals"]


class ShiftError(Exception):
    """Shift rule violation (Arabic message, safe to show the cashier)."""


# --------------------------------------------------------------------------- #
# Aggregated sales for one shift
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class ShiftTotals:
    """Every figure a Z-Report needs, computed in one pass over the orders."""

    order_count: int = 0
    gross_minor: int = 0            # sum of completed order totals
    cash_sales_minor: int = 0
    card_sales_minor: int = 0
    discount_minor: int = 0
    tax_minor: int = 0              # embedded tax (0 at a 0% rate)
    voided_count: int = 0
    first_order_at: str = ""
    last_order_at: str = ""
    top_items: list[tuple[str, int, int]] = field(default_factory=list)

    @property
    def average_order_minor(self) -> int:
        return int(self.gross_minor / self.order_count) if self.order_count else 0


# --------------------------------------------------------------------------- #
# Shift entity
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class Shift:
    id: int
    user_id: int
    status: str = config.SHIFT_OPEN
    opening_float_minor: int = 0
    expected_cash_minor: int = 0
    counted_cash_minor: int = 0
    difference_minor: int = 0
    total_sales_minor: int = 0
    cash_sales_minor: int = 0
    card_sales_minor: int = 0
    order_count: int = 0
    opened_at: str = ""
    closed_at: str | None = None
    z_report_path: str = ""
    notes: str = ""
    # joined, not stored
    cashier_name: str = ""

    @property
    def is_open(self) -> bool:
        return self.status == config.SHIFT_OPEN

    @property
    def difference_label(self) -> str:
        """
        Human over/short label, e.g. 'نقص 500 ر.ي' / 'زيادة 300 ر.ي' / 'مطابق'.

        The signed form alone ('−500 ر.ي') is ambiguous in a sentence — a
        cashier reading "فرق الصندوق −500" cannot tell whether that is money
        missing or extra — so the state word is part of the label.
        """
        if self.difference_minor == 0:
            return "مطابق"
        state = "زيادة" if self.difference_minor > 0 else "نقص"
        return f"{state} {config.format_money(abs(self.difference_minor))}"

    @property
    def difference_signed_label(self) -> str:
        """Compact signed form for tight spaces, e.g. '+500 ر.ي'."""
        from models.product import price_delta_label

        return price_delta_label(self.difference_minor) or config.format_money(0)

    @property
    def difference_state(self) -> str:
        """'مطابق' | 'زيادة' | 'نقص'."""
        if self.difference_minor == 0:
            return "مطابق"
        return "زيادة" if self.difference_minor > 0 else "نقص"

    @property
    def is_balanced(self) -> bool:
        return self.difference_minor == 0

    @classmethod
    def from_row(cls, row: Any) -> "Shift":
        keys = row.keys() if hasattr(row, "keys") else []
        get = lambda k, d=None: (row[k] if k in keys else d)  # noqa: E731
        return cls(
            id=int(row["id"]),
            user_id=int(row["user_id"]),
            status=str(get("status", config.SHIFT_OPEN)),
            opening_float_minor=int(get("opening_float_minor", 0) or 0),
            expected_cash_minor=int(get("expected_cash_minor", 0) or 0),
            counted_cash_minor=int(get("counted_cash_minor", 0) or 0),
            difference_minor=int(get("difference_minor", 0) or 0),
            total_sales_minor=int(get("total_sales_minor", 0) or 0),
            cash_sales_minor=int(get("cash_sales_minor", 0) or 0),
            card_sales_minor=int(get("card_sales_minor", 0) or 0),
            order_count=int(get("order_count", 0) or 0),
            opened_at=str(get("opened_at", "") or ""),
            closed_at=get("closed_at"),
            z_report_path=str(get("z_report_path", "") or ""),
            notes=str(get("notes", "") or ""),
            cashier_name=str(get("cashier_name", "") or ""),
        )


# --------------------------------------------------------------------------- #
# Repository
# --------------------------------------------------------------------------- #
_SELECT = """
    SELECT s.*, IFNULL(u.full_name, '') AS cashier_name
      FROM shifts s
      LEFT JOIN users u ON u.id = s.user_id
"""


class ShiftRepository:
    """All reads/writes on `shifts`, plus the sales aggregation for Z-Reports."""

    def __init__(self, db: Any = None) -> None:
        self._db = db

    @property
    def db(self):
        if self._db is None:
            from database.db_manager import get_db

            self._db = get_db()
        return self._db

    # -- reads ------------------------------------------------------------- #
    def get(self, shift_id: int) -> Shift | None:
        row = self.db.query_one(f"{_SELECT} WHERE s.id = ?", (int(shift_id),))
        return Shift.from_row(row) if row else None

    def get_open_shift(self) -> Shift | None:
        """The single currently-open shift, or None."""
        row = self.db.query_one(f"{_SELECT} WHERE s.status = ?", (config.SHIFT_OPEN,))
        return Shift.from_row(row) if row else None

    def has_open_shift(self) -> bool:
        return bool(
            self.db.query_value(
                "SELECT 1 FROM shifts WHERE status = ? LIMIT 1", (config.SHIFT_OPEN,)
            )
        )

    def list_shifts(self, limit: int = 30, user_id: int | None = None) -> list[Shift]:
        sql, params = _SELECT, []
        if user_id is not None:
            sql += " WHERE s.user_id = ?"
            params.append(int(user_id))
        sql += " ORDER BY s.id DESC LIMIT ?"
        params.append(int(limit))
        return [Shift.from_row(r) for r in self.db.query_all(sql, tuple(params))]

    def count(self) -> int:
        return self.db.scalar("SELECT COUNT(*) FROM shifts")

    # -- totals ------------------------------------------------------------ #
    def totals_for(self, shift_id: int, top_n: int = 5) -> ShiftTotals:
        """
        Aggregate a shift's sales. One query for the headline figures and one
        for the top sellers, rather than looping per order.
        """
        row = self.db.query_one(
            """
            SELECT
                COUNT(*)                                              AS order_count,
                IFNULL(SUM(total_minor), 0)                           AS gross_minor,
                IFNULL(SUM(CASE WHEN payment_method = 'cash'
                                THEN total_minor ELSE 0 END), 0)      AS cash_sales_minor,
                IFNULL(SUM(CASE WHEN payment_method = 'card'
                                THEN total_minor ELSE 0 END), 0)      AS card_sales_minor,
                IFNULL(SUM(discount_minor), 0)                        AS discount_minor,
                IFNULL(SUM(tax_minor), 0)                             AS tax_minor,
                IFNULL(MIN(created_at), '')                           AS first_order_at,
                IFNULL(MAX(created_at), '')                           AS last_order_at
              FROM orders
             WHERE shift_id = ? AND status = ?
            """,
            (int(shift_id), config.ORDER_STATUS_COMPLETED),
        )
        totals = ShiftTotals(
            order_count=int(row["order_count"] or 0),
            gross_minor=int(row["gross_minor"] or 0),
            cash_sales_minor=int(row["cash_sales_minor"] or 0),
            card_sales_minor=int(row["card_sales_minor"] or 0),
            discount_minor=int(row["discount_minor"] or 0),
            tax_minor=int(row["tax_minor"] or 0),
            first_order_at=str(row["first_order_at"] or ""),
            last_order_at=str(row["last_order_at"] or ""),
        )
        totals.voided_count = self.db.scalar(
            "SELECT COUNT(*) FROM orders WHERE shift_id = ? AND status = ?",
            (int(shift_id), config.ORDER_STATUS_VOIDED),
        )
        totals.top_items = [
            (str(r["name_ar"]), int(r["qty"] or 0), int(r["revenue"] or 0))
            for r in self.db.query_all(
                """
                SELECT oi.name_ar,
                       SUM(oi.quantity)       AS qty,
                       SUM(oi.line_total_minor) AS revenue
                  FROM order_items oi
                  JOIN orders o ON o.id = oi.order_id
                 WHERE o.shift_id = ? AND o.status = ?
                 GROUP BY oi.name_ar
                 ORDER BY qty DESC, revenue DESC
                 LIMIT ?
                """,
                (int(shift_id), config.ORDER_STATUS_COMPLETED, int(top_n)),
            )
        ]
        return totals

    def expected_cash(self, shift: Shift | ShiftTotals, opening_float_minor: int) -> int:
        """
        Cash that should be in the drawer.

        No cash refunds or payouts exist yet, so this is simply the float plus
        cash takings. When refunds arrive, subtract them here — the Z-Report
        reads this one function, so the change lands in one place.
        """
        cash = (shift.cash_sales_minor if isinstance(shift, ShiftTotals)
                else self.totals_for(shift.id).cash_sales_minor)
        return int(opening_float_minor) + int(cash)

    # -- writes ------------------------------------------------------------ #
    def open_shift(self, user_id: int, opening_float_minor: int, notes: str = "") -> Shift:
        """Open a shift. Fails clearly if one is already open."""
        if opening_float_minor < 0:
            raise ShiftError("رصيد البداية لا يمكن أن يكون سالباً")

        existing = self.get_open_shift()
        if existing is not None:
            raise ShiftError(
                f"يوجد وردية مفتوحة بالفعل (#{existing.id}) — أغلقها قبل فتح وردية جديدة"
            )

        try:
            with self.db.transaction():
                shift_id = self.db.insert(
                    """
                    INSERT INTO shifts (user_id, status, opening_float_minor, opened_at, notes)
                    VALUES (?, ?, ?, datetime('now','localtime'), ?)
                    """,
                    (int(user_id), config.SHIFT_OPEN, int(opening_float_minor), notes or ""),
                )
        except Exception as exc:
            # The partial unique index is the real guard; translate its error.
            if "idx_shifts_single_open" in str(exc) or "UNIQUE" in str(exc).upper():
                raise ShiftError("يوجد وردية مفتوحة بالفعل") from exc
            raise

        logger.info("تم فتح وردية #%s برصيد %s", shift_id,
                    config.format_money(opening_float_minor))
        shift = self.get(shift_id)
        assert shift is not None
        return shift

    def close_shift(self, shift_id: int, counted_cash_minor: int, notes: str = "") -> Shift:
        """Close a shift, snapshotting every Z-Report figure onto the row."""
        shift = self.get(shift_id)
        if shift is None:
            raise ShiftError("الوردية غير موجودة")
        if not shift.is_open:
            raise ShiftError("هذه الوردية مغلقة بالفعل")
        if counted_cash_minor < 0:
            raise ShiftError("المبلغ المعدود لا يمكن أن يكون سالباً")

        totals = self.totals_for(shift_id)
        expected = self.expected_cash(shift, shift.opening_float_minor)
        difference = int(counted_cash_minor) - expected

        with self.db.transaction():
            self.db.execute(
                """
                UPDATE shifts
                   SET status = ?, closed_at = datetime('now','localtime'),
                       expected_cash_minor = ?, counted_cash_minor = ?, difference_minor = ?,
                       total_sales_minor = ?, cash_sales_minor = ?, card_sales_minor = ?,
                       order_count = ?,
                       notes = CASE WHEN ? = '' THEN notes ELSE ? END
                 WHERE id = ?
                """,
                (
                    config.SHIFT_CLOSED, expected, int(counted_cash_minor), difference,
                    totals.gross_minor, totals.cash_sales_minor, totals.card_sales_minor,
                    totals.order_count, notes or "", notes or "", int(shift_id),
                ),
            )

        logger.info("تم إغلاق وردية #%s — فرق %s", shift_id, config.format_money(difference))
        closed = self.get(shift_id)
        assert closed is not None
        return closed

    def set_z_report_path(self, shift_id: int, path: str) -> None:
        self.db.execute(
            "UPDATE shifts SET z_report_path = ? WHERE id = ?", (str(path), int(shift_id))
        )

    def live_snapshot(self, shift_id: int) -> dict[str, Any]:
        """
        Current drawer state for an OPEN shift — used by the close dialog to show
        the cashier what the system expects before they count.
        """
        shift = self.get(shift_id)
        if shift is None:
            raise ShiftError("الوردية غير موجودة")
        totals = self.totals_for(shift_id)
        expected = self.expected_cash(shift, shift.opening_float_minor)
        return {
            "shift": shift,
            "totals": totals,
            "expected_cash_minor": expected,
            "opened_at": shift.opened_at,
            "cashier_name": shift.cashier_name,
        }

    def today_summary(self) -> dict[str, int]:
        """All completed sales today, across shifts (handy for the header)."""
        today = datetime.now().strftime("%Y-%m-%d")
        row = self.db.query_one(
            """
            SELECT COUNT(*) AS n, IFNULL(SUM(total_minor), 0) AS gross
              FROM orders
             WHERE status = ? AND date(created_at) = date(?)
            """,
            (config.ORDER_STATUS_COMPLETED, today),
        )
        return {"orders": int(row["n"] or 0), "gross_minor": int(row["gross"] or 0)}
