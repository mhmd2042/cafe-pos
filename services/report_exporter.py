"""
services/report_exporter.py — Z-Reports and CSV exports.

Writes to logs/reports/ (and mirrors the Z-Report next to the backups so it
travels with the database copy to the external drive).

Two formats per Z-Report:
    z_report_<shift>_<stamp>.txt   — printable, plain text
    z_report_<shift>_<stamp>.csv   — machine-readable, for the owner's spreadsheet

Everything is written with UTF-8 + BOM for the CSV, because Excel on Windows
mangles Arabic without it.
"""

from __future__ import annotations

import csv
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import config
from models.shift import Shift, ShiftTotals

logger = logging.getLogger(__name__)

__all__ = ["ReportExporter"]

REPORTS_DIR = config.LOG_DIR / "reports"


class ReportExporter:
    """Turns shift figures into files. Never raises for a missing folder."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = Path(directory or REPORTS_DIR)

    # -- helpers ----------------------------------------------------------- #
    def _ensure(self) -> None:
        config.secure_dir(self.directory)

    def _secure(self, path: Path) -> Path:
        """Reports contain revenue figures — keep them owner-only."""
        config.secure_file(path)
        return path

    @staticmethod
    def _money(minor: int) -> str:
        return config.format_money(minor, symbol=False)

    @staticmethod
    def _stamp() -> str:
        return datetime.now().strftime("%Y-%m-%d_%H%M%S")

    def _path(self, kind: str, shift_id: int, suffix: str) -> Path:
        return self.directory / f"{kind}_{shift_id:04d}_{self._stamp()}.{suffix}"

    # -- Z-Report ---------------------------------------------------------- #
    def z_report_text(self, shift: Shift, totals: ShiftTotals) -> str:
        """Human-readable end-of-day summary for one shift."""
        width = 44

        def rule(char: str = "-") -> str:
            return char * width

        def kv(label: str, value: str, pad: int = 0) -> str:
            gap = max(1, width - len(label) - len(value) - pad)
            return f"{label}{' ' * gap}{value}"

        out: list[str] = []
        out.append(rule("="))
        out.append("تقرير إغلاق الوردية (Z-Report)".center(width))
        out.append(rule("="))
        out.append(kv("رقم الوردية", f"#{shift.id}"))
        out.append(kv("الكاشير", shift.cashier_name or "-"))
        out.append(kv("فُتحت", shift.opened_at or "-"))
        out.append(kv("أُغلقت", shift.closed_at or "-"))
        out.append(rule())

        out.append("المبيعات")
        out.append(kv("  عدد الطلبات", str(totals.order_count)))
        if totals.voided_count:
            out.append(kv("  طلبات ملغاة", str(totals.voided_count)))
        out.append(kv("  إجمالي المبيعات", self._money(totals.gross_minor)))
        out.append(kv("  منها نقداً", self._money(totals.cash_sales_minor)))
        out.append(kv("  منها بطاقة", self._money(totals.card_sales_minor)))
        if totals.discount_minor:
            out.append(kv("  الخصومات", self._money(totals.discount_minor)))
        if totals.tax_minor:
            out.append(kv("  منها ضريبة", self._money(totals.tax_minor)))
        if totals.order_count:
            out.append(kv("  متوسط الطلب", self._money(totals.average_order_minor)))
        if totals.first_order_at:
            out.append(kv("  أول طلب", totals.first_order_at))
        out.append(kv("  آخر طلب", totals.last_order_at or "-"))
        out.append(rule())

        if totals.top_items:
            out.append("الأكثر مبيعاً")
            for name, qty, revenue in totals.top_items:
                out.append(kv(f"  {name}", f"{qty} × {self._money(revenue)}"))
            out.append(rule())

        out.append("الصندوق")
        out.append(kv("  رصيد البداية", self._money(shift.opening_float_minor)))
        out.append(kv("  المبيعات النقدية", self._money(shift.cash_sales_minor)))
        out.append(kv("  المتوقع في الصندوق", self._money(shift.expected_cash_minor)))
        out.append(kv("  المعدود فعلياً", self._money(shift.counted_cash_minor)))
        out.append(kv("  الفرق", shift.difference_label))
        out.append(rule("="))

        if shift.notes:
            out.append(f"ملاحظات: {shift.notes}")
        out.append(f"أُنشئ في {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        out.append("")
        return "\n".join(out)

    def export_z_report(self, shift: Shift, totals: ShiftTotals) -> Path:
        """Write the Z-Report as .txt (+ a copy beside the backups) and .csv."""
        self._ensure()
        text_path = self._path("z_report", shift.id, "txt")
        text_path.write_text(self.z_report_text(shift, totals), encoding="utf-8")

        self.export_shift_csv(shift, totals)

        # A copy next to the backups travels with the database to the USB drive.
        try:
            mirror_dir = Path(config.LOCAL_BACKUP_DIR) / "reports"
            config.secure_dir(mirror_dir)
            mirrored = mirror_dir / text_path.name
            mirrored.write_text(text_path.read_text(encoding="utf-8"), encoding="utf-8")
            config.secure_file(mirrored)
        except OSError as exc:  # pragma: no cover
            logger.debug("تعذّر نسخ تقرير الوردية إلى مجلد النسخ: %s", exc)

        logger.info("تم إنشاء تقرير الوردية: %s", text_path)
        return self._secure(text_path)

    def export_shift_csv(self, shift: Shift, totals: ShiftTotals) -> Path:
        """One-row-per-shift CSV, suitable for appending to an owner's sheet."""
        self._ensure()
        path = self._path("z_report", shift.id, "csv")
        rows: list[Iterable[Any]] = [
            ["shift_id", shift.id],
            ["cashier", shift.cashier_name],
            ["opened_at", shift.opened_at],
            ["closed_at", shift.closed_at or ""],
            ["order_count", totals.order_count],
            ["voided_count", totals.voided_count],
            ["gross_minor", totals.gross_minor],
            ["cash_sales_minor", totals.cash_sales_minor],
            ["card_sales_minor", totals.card_sales_minor],
            ["discount_minor", totals.discount_minor],
            ["tax_minor", totals.tax_minor],
            ["opening_float_minor", shift.opening_float_minor],
            ["expected_cash_minor", shift.expected_cash_minor],
            ["counted_cash_minor", shift.counted_cash_minor],
            ["difference_minor", shift.difference_minor],
            ["notes", shift.notes],
        ]
        # utf-8-sig so Excel opens Arabic correctly instead of mojibake.
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["field", "value"])
            writer.writerows(rows)
            writer.writerow([])
            writer.writerow(["top_item", "qty", "revenue_minor"])
            for name, qty, revenue in totals.top_items:
                writer.writerow([name, qty, revenue])
        return self._secure(path)

    # -- generic CSV (admin reports, Phase 5 hook) ------------------------- #
    def export_rows_csv(self, name: str, headers: list[str], rows: list[Iterable[Any]]) -> Path:
        self._ensure()
        path = self.directory / f"{name}_{self._stamp()}.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(headers)
            writer.writerows(rows)
        return self._secure(path)

    def export_orders_csv(self, orders: list[dict[str, Any]], name: str = "orders") -> Path:
        headers = ["order_number", "created_at", "order_type", "payment_method",
                   "status", "subtotal_minor", "discount_minor", "tax_minor",
                   "total_minor", "paid_minor", "change_minor"]
        rows = [[o.get(h, "") for h in headers] for o in orders]
        return self.export_rows_csv(name, headers, rows)

    def list_reports(self, limit: int = 20) -> list[Path]:
        if not self.directory.is_dir():
            return []
        files = sorted(self.directory.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
        return [f for f in files if f.is_file()][:limit]
