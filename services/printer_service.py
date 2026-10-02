"""
services/printer_service.py — ESC/POS thermal receipt printing with a graceful
offline fallback.

WHY RASTER, NOT TEXT
    Thermal printers render text with their own built-in font (usually CP437),
    which has no Arabic glyphs. Sending UTF-8 Arabic to the printer produces
    garbage or nothing. The reliable offline approach is to lay the receipt out
    in Qt with the same Arabic font the app uses, rasterise it to a 1-bit image
    at the printer's dot width, and send it with the ESC/POS raster command
    (GS v 0). This also gives us real RTL shaping for free.

GRACEFUL DEGRADATION (spec rule: never crash when no printer is attached)
    1. No backend configured / not installed  → fall back
    2. Backend present but unreachable        → fall back, report why
    3. Fallback writes two artefacts to logs/receipts/:
         receipt_<order>_<stamp>.txt   — plain-text copy, always readable
         receipt_<order>_<stamp>.png   — exactly what the printer would have got
    The caller gets a PrintResult describing which path was taken, so the UI can
    show a soft notice instead of an error.

Backends are probed in order and can be forced with the `printer_backend`
setting: "auto" (default), "escpos", "cups", "windows", "file".
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import config

logger = logging.getLogger(__name__)

__all__ = ["ReceiptData", "PrintResult", "PrinterService", "get_printer_service"]

# --------------------------------------------------------------------------- #
# Data handed to the renderer
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class ReceiptData:
    """Everything a receipt needs, already formatted. No DB access in the renderer."""

    cafe_name: str = "المقهى"
    cafe_phone: str = ""
    cafe_address: str = ""
    tax_number: str = ""
    order_number: str = ""
    order_type: str = config.ORDER_TYPE_DINE_IN
    cashier_name: str = ""
    created_at: str = ""
    lines: list[dict[str, Any]] = field(default_factory=list)
    subtotal_minor: int = 0
    discount_minor: int = 0
    tax_minor: int = 0
    total_minor: int = 0
    payment_method: str = config.PAYMENT_CASH
    paid_minor: int = 0
    change_minor: int = 0
    footer: str = ""
    width_dots: int = config.RECEIPT_DOTS_80MM

    @property
    def order_type_label(self) -> str:
        return "محلي" if self.order_type == config.ORDER_TYPE_DINE_IN else "سفري"

    @property
    def payment_label(self) -> str:
        return "نقداً" if self.payment_method == config.PAYMENT_CASH else "بطاقة"

    @property
    def show_tax(self) -> bool:
        """A 0% rate means there is nothing meaningful to print."""
        return self.tax_minor > 0

    @classmethod
    def from_order(cls, order: dict[str, Any], settings: dict[str, str],
                   items: list[dict[str, Any]] | None = None,
                   modifiers_of=None, cashier_name: str = "") -> "ReceiptData":
        """Build from an order row (as returned by OrderRepository.get)."""
        items = items if items is not None else order.get("items", [])
        lines: list[dict[str, Any]] = []
        for item in items:
            mods = []
            if modifiers_of is not None:
                mods = [m.name for m in modifiers_of(item)]
            elif item.get("modifiers_json"):
                import json

                try:
                    mods = [d.get("name", "") for d in json.loads(item["modifiers_json"])]
                except (TypeError, ValueError):
                    mods = []
            lines.append(
                {
                    "name": item.get("name_ar") or item.get("name_en") or "",
                    "quantity": int(item.get("quantity") or 1),
                    "unit_price_minor": int(item.get("unit_price_minor") or 0),
                    "line_total_minor": int(item.get("line_total_minor") or 0),
                    "modifiers": [m for m in mods if m],
                    "note": item.get("note") or "",
                }
            )

        width_mm = int(settings.get("receipt_width_mm", config.RECEIPT_WIDTH_MM) or 80)
        return cls(
            cafe_name=settings.get("cafe_name", "المقهى"),
            cafe_phone=settings.get("cafe_phone", ""),
            cafe_address=settings.get("cafe_address", ""),
            tax_number=settings.get("tax_number", ""),
            order_number=str(order.get("order_number") or ""),
            order_type=str(order.get("order_type") or config.ORDER_TYPE_DINE_IN),
            cashier_name=cashier_name,
            created_at=str(order.get("created_at") or ""),
            lines=lines,
            subtotal_minor=int(order.get("subtotal_minor") or 0),
            discount_minor=int(order.get("discount_minor") or 0),
            tax_minor=int(order.get("tax_minor") or 0),
            total_minor=int(order.get("total_minor") or 0),
            payment_method=str(order.get("payment_method") or config.PAYMENT_CASH),
            paid_minor=int(order.get("paid_minor") or 0),
            change_minor=int(order.get("change_minor") or 0),
            footer=settings.get("receipt_footer", ""),
            width_dots=(config.RECEIPT_DOTS_58MM if width_mm <= 58
                        else config.RECEIPT_DOTS_80MM),
        )


@dataclass(slots=True)
class PrintResult:
    """Outcome of a print attempt. `ok` is True even on a clean fallback."""

    ok: bool
    printed: bool = False               # went to a physical printer
    fell_back: bool = False             # written to files instead
    backend: str = ""
    text_path: Path | None = None
    image_path: Path | None = None
    message: str = ""
    error: str = ""

    @property
    def needs_attention(self) -> bool:
        """True when the cashier should be told something (printer missing/failed)."""
        return self.fell_back or not self.ok


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #
class PrinterService:
    """Renders receipts and sends them to a printer, or to disk when there is none."""

    def __init__(self, db: Any = None) -> None:
        self._db = db

    @property
    def db(self):
        if self._db is None:
            from database.db_manager import get_db

            self._db = get_db()
        return self._db

    # -- settings ---------------------------------------------------------- #
    def settings(self) -> dict[str, str]:
        try:
            return self.db.get_all_settings()
        except Exception as exc:  # pragma: no cover - settings are best-effort
            logger.debug("تعذّر قراءة الإعدادات: %s", exc)
            return {}

    def is_enabled(self) -> bool:
        return str(self.settings().get("printer_enabled", "0")).strip() in ("1", "true", "yes")

    def configured_backend(self) -> str:
        return (self.settings().get("printer_backend", "auto") or "auto").strip().lower()

    def printer_name(self) -> str:
        return (self.settings().get("printer_name", "") or "").strip()

    # -- backend probes ---------------------------------------------------- #
    @staticmethod
    def _has_escpos_library() -> bool:
        try:
            import escpos  # noqa: F401

            return True
        except Exception:
            return False

    @staticmethod
    def _cups_available() -> bool:
        return bool(shutil.which("lp"))

    @staticmethod
    def _windows_spooler_available() -> bool:
        return sys.platform.startswith("win")

    def detect_backend(self) -> str:
        """
        Pick a transport. Returns one of:
            escpos | cups | windows | file
        "file" is the always-available fallback.
        """
        forced = self.configured_backend()
        if forced in ("escpos", "cups", "windows", "file"):
            return forced
        # auto
        if self._has_escpos_library() and self.printer_name():
            return "escpos"
        if self._cups_available() and self.printer_name():
            return "cups"
        if self._windows_spooler_available() and self.printer_name():
            return "windows"
        return "file"

    def status(self) -> dict[str, Any]:
        """Small health summary for the admin settings screen (Phase 5)."""
        return {
            "enabled": self.is_enabled(),
            "backend": self.detect_backend(),
            "printer_name": self.printer_name(),
            "escpos_installed": self._has_escpos_library(),
            "cups_available": self._cups_available(),
            "fallback_dir": str(config.RECEIPTS_DIR),
        }

    # -- public API -------------------------------------------------------- #
    def print_receipt(self, data: ReceiptData) -> PrintResult:
        """
        Print a receipt, falling back to files. Never raises: a till must keep
        selling even if the printer is unplugged, out of paper, or on fire.
        """
        config.ensure_directories()
        text = self.render_text(data)

        # Always write the text copy — it is the audit trail and costs nothing.
        text_path = self._write_text(text, data)

        if not self.is_enabled():
            return PrintResult(
                ok=True, fell_back=True, backend="file",
                text_path=text_path,
                message="الطابعة غير مُفعّلة — تم حفظ نسخة من الإيصال",
            )

        backend = self.detect_backend()
        if backend == "file":
            return PrintResult(
                ok=True, fell_back=True, backend="file", text_path=text_path,
                message="لا توجد طابعة متصلة — تم حفظ الإيصال كملف",
            )

        try:
            image_path = self.render_image(data)
        except Exception as exc:
            logger.exception("فشل تحويل الإيصال إلى صورة")
            return PrintResult(
                ok=True, fell_back=True, backend=backend, text_path=text_path,
                message="تعذّر تجهيز الإيصال للطباعة — تم حفظ نسخة",
                error=str(exc),
            )

        try:
            if backend == "escpos":
                self._send_escpos(data, image_path)
            elif backend == "cups":
                self._send_cups(image_path)
            elif backend == "windows":
                self._send_windows(data, image_path)
            else:  # pragma: no cover - detect_backend never returns anything else
                raise RuntimeError(f"backend غير مدعوم: {backend}")
        except Exception as exc:
            logger.warning("فشلت الطباعة عبر %s: %s", backend, exc)
            return PrintResult(
                ok=True, fell_back=True, backend=backend, text_path=text_path,
                image_path=image_path,
                message="تعذّرت الطباعة — تم حفظ نسخة من الإيصال",
                error=str(exc),
            )

        return PrintResult(
            ok=True, printed=True, backend=backend,
            text_path=text_path, image_path=image_path,
            message="تم إرسال الإيصال إلى الطابعة",
        )

    def test_print(self) -> PrintResult:
        """Send a small test receipt (admin button)."""
        data = ReceiptData(
            cafe_name=self.settings().get("cafe_name", "المقهى"),
            order_number="TEST",
            created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            lines=[{"name": "إيصال تجريبي", "quantity": 1, "unit_price_minor": 0,
                    "line_total_minor": 0, "modifiers": [], "note": ""}],
            total_minor=0,
            footer="اختبار الطابعة",
        )
        return self.print_receipt(data)

    # -- rendering --------------------------------------------------------- #
    @staticmethod
    def _width_chars(width_dots: int) -> int:
        return (config.RECEIPT_WIDTH_CHARS_58MM if width_dots <= config.RECEIPT_DOTS_58MM
                else config.RECEIPT_WIDTH_CHARS_80MM)

    def render_text(self, data: ReceiptData) -> str:
        """Plain-text receipt (fallback artefact + debugging aid)."""
        from decimal import Decimal

        width = self._width_chars(data.width_dots)

        def money(minor: int) -> str:
            return config.format_money(minor, symbol=False)

        def rule(char: str = "-") -> str:
            return char * width

        def center(text: str) -> str:
            # Arabic renders right-to-left; centring by character count is close
            # enough for a monospace text copy.
            pad = max(0, (width - len(text)) // 2)
            return " " * pad + text

        out: list[str] = []
        out.append(center(data.cafe_name))
        if data.cafe_address:
            out.append(center(data.cafe_address))
        if data.cafe_phone:
            out.append(center(f"هاتف: {data.cafe_phone}"))
        if data.tax_number:
            out.append(center(f"الرقم الضريبي: {data.tax_number}"))
        out.append(rule("="))
        out.append(f"رقم الطلب: {data.order_number}")
        out.append(f"النوع: {data.order_type_label}    الكاشير: {data.cashier_name}")
        out.append(f"التاريخ: {data.created_at}")
        out.append(rule())

        for line in data.lines:
            qty = int(line.get("quantity") or 1)
            out.append(f"{line.get('name','')}")
            out.append(f"  {qty} × {money(int(line.get('unit_price_minor') or 0))}"
                       f"{' ' * 3}{money(int(line.get('line_total_minor') or 0))}")
            for mod in line.get("modifiers") or []:
                out.append(f"    + {mod}")
            if line.get("note"):
                out.append(f"    * {line['note']}")

        out.append(rule())
        out.append(f"المجموع{' ' * (width - 24)}{money(data.subtotal_minor)}")
        if data.discount_minor:
            out.append(f"الخصم{' ' * (width - 22)}-{money(data.discount_minor)}")
        if data.show_tax:
            out.append(f"منها ضريبة{' ' * (width - 26)}{money(data.tax_minor)}")
        out.append(rule("="))
        out.append(f"الإجمالي (شامل الضريبة){' ' * 6}{money(data.total_minor)}")
        out.append(rule("="))
        out.append(f"الدفع: {data.payment_label}")
        if data.payment_method == config.PAYMENT_CASH:
            out.append(f"المدفوع{' ' * (width - 24)}{money(data.paid_minor)}")
            out.append(f"الباقي{' ' * (width - 22)}{money(data.change_minor)}")
        out.append(rule())
        if data.footer:
            out.append(center(data.footer))
        out.append("")
        return "\n".join(out)

    def render_image(self, data: ReceiptData) -> Path:
        """
        Rasterise the receipt to a 1-bit PNG at the printer's dot width.

        Uses Qt's own text engine, so Arabic shaping/ligatures are correct — the
        same reason the UI renders properly.
        """
        from PyQt6.QtCore import QRect, Qt
        from PyQt6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPen

        width = int(data.width_dots)
        margin = 16
        usable = width - margin * 2

        font = QFont(config.FONT_FAMILY.split(",")[0].strip())
        font.setPixelSize(22)
        font.setBold(False)
        bold = QFont(font)
        bold.setBold(True)
        small = QFont(font)
        small.setPixelSize(19)

        def wrap(text: str, f: QFont) -> list[str]:
            metrics = QFontMetrics(f)
            if metrics.horizontalAdvance(text) <= usable:
                return [text]
            words, lines, current = text.split(), [], ""
            for word in words:
                candidate = f"{current} {word}".strip()
                if metrics.horizontalAdvance(candidate) <= usable:
                    current = candidate
                else:
                    if current:
                        lines.append(current)
                    current = word
            if current:
                lines.append(current)
            return lines or [""]

        # -- build a draw list first so we can size the canvas exactly -------- #
        ops: list[tuple[str, Any]] = []          # (kind, payload)
        ops.append(("center_bold", data.cafe_name))
        if data.cafe_address:
            ops.append(("center", data.cafe_address))
        if data.cafe_phone:
            ops.append(("center", f"هاتف: {data.cafe_phone}"))
        if data.tax_number:
            ops.append(("center", f"الرقم الضريبي: {data.tax_number}"))
        ops.append(("rule", "double"))
        ops.append(("kv", ("رقم الطلب", data.order_number)))
        ops.append(("kv", ("النوع", data.order_type_label)))
        ops.append(("kv", ("الكاشير", data.cashier_name)))
        ops.append(("kv", ("التاريخ", data.created_at)))
        ops.append(("rule", "single"))

        for line in data.lines:
            qty = int(line.get("quantity") or 1)
            ops.append(("item", (line.get("name", ""), qty,
                                 int(line.get("unit_price_minor") or 0),
                                 int(line.get("line_total_minor") or 0))))
            for mod in line.get("modifiers") or []:
                ops.append(("mod", str(mod)))
            if line.get("note"):
                ops.append(("mod", f"* {line['note']}"))

        ops.append(("rule", "single"))
        ops.append(("kv_money", ("المجموع", data.subtotal_minor)))
        if data.discount_minor:
            ops.append(("kv_money", ("الخصم", -data.discount_minor)))
        if data.show_tax:
            ops.append(("kv_money", ("منها ضريبة", data.tax_minor)))
        ops.append(("rule", "double"))
        ops.append(("total", data.total_minor))
        ops.append(("rule", "double"))
        ops.append(("kv", ("الدفع", data.payment_label)))
        if data.payment_method == config.PAYMENT_CASH:
            ops.append(("kv_money", ("المدفوع", data.paid_minor)))
            ops.append(("kv_money", ("الباقي", data.change_minor)))
        ops.append(("rule", "single"))
        if data.footer:
            ops.append(("center", data.footer))

        # -- measure ---------------------------------------------------------- #
        line_h = QFontMetrics(font).height() + 6
        height = margin * 2
        for kind, payload in ops:
            if kind == "rule":
                height += 10
            elif kind == "item":
                height += line_h
            elif kind == "total":
                height += QFontMetrics(bold).height() + 14
            else:
                text = payload[0] if isinstance(payload, tuple) else payload
                f = bold if kind in ("center_bold", "total") else (small if kind == "mod" else font)
                height += line_h * len(wrap(str(text), f))

        # One extra line of slack: the per-op heights above are estimates and the
        # final line was being clipped at the bottom edge of the canvas.
        height += line_h

        # Paint on an 8-bit grayscale canvas, then convert to 1-bit at the end.
        # Painting directly onto Format_Mono does not work: Qt's mono colour
        # table inverts both fill() and drawText(), producing a solid black
        # bitmap (verified empirically — every pixel came out dark).
        image = QImage(width, max(height, 120), QImage.Format.Format_Grayscale8)
        image.fill(QColor("white"))

        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, False)
        # Arabic is RTL: lay the whole receipt out right-to-left.
        painter.setLayoutDirection(Qt.LayoutDirection.RightToLeft)

        y = margin
        for kind, payload in ops:
            if kind == "rule":
                y += 5
                painter.setPen(QPen(QColor("black"), 2 if payload == "double" else 1))
                painter.drawLine(margin, y, width - margin, y)
                y += 5
                continue

            if kind == "item":
                name, qty, unit, line_total = payload
                painter.setFont(font)
                painter.setPen(QColor("black"))
                for chunk in wrap(str(name), font):
                    painter.drawText(QRect(margin, y, usable, line_h),
                                     int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                                     chunk)
                    y += line_h
                # quantity and money on one secondary line
                painter.setFont(small)
                left = f"{qty} × {config.format_money(unit, symbol=False)}"
                painter.drawText(QRect(margin, y, usable, line_h),
                                 int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                                 left)
                painter.setFont(bold)
                painter.drawText(QRect(margin, y, usable, line_h),
                                 int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                                 config.format_money(line_total, symbol=False))
                y += line_h
                continue

            if kind == "total":
                painter.setFont(bold)
                painter.setPen(QColor("black"))
                label = "الإجمالي (شامل الضريبة)"
                painter.drawText(QRect(margin, y, usable, line_h + 8),
                                 int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                                 label)
                painter.drawText(QRect(margin, y, usable, line_h + 8),
                                 int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                                 config.format_money(payload, symbol=False))
                y += QFontMetrics(bold).height() + 14
                continue

            if kind == "kv_money":
                label, amount = payload
                painter.setFont(font)
                painter.setPen(QColor("black"))
                painter.drawText(QRect(margin, y, usable, line_h),
                                 int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                                 str(label))
                painter.drawText(QRect(margin, y, usable, line_h),
                                 int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                                 config.format_money(abs(int(amount)), symbol=False))
                y += line_h
                continue

            if kind == "kv":
                label, value = payload
                painter.setFont(small)
                painter.setPen(QColor("black"))
                painter.drawText(QRect(margin, y, usable, line_h),
                                 int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                                 f"{label}: {value}")
                y += line_h
                continue

            # plain text: center / center_bold / mod
            text = str(payload)
            f = bold if kind == "center_bold" else (small if kind == "mod" else font)
            painter.setFont(f)
            painter.setPen(QColor("black"))
            align = (Qt.AlignmentFlag.AlignCenter if kind.startswith("center")
                     else Qt.AlignmentFlag.AlignRight)
            for chunk in wrap(text, f):
                painter.drawText(QRect(margin, y, usable, line_h),
                                 int(align | Qt.AlignmentFlag.AlignVCenter), chunk)
                y += line_h

        painter.end()

        # 1-bit MSB-first is what ESC/POS GS v 0 raster expects. Qt6 dropped
        # Format_Monochrome; Format_Mono is its replacement.
        image = image.convertToFormat(
            QImage.Format.Format_Mono, Qt.ImageConversionFlag.MonoOnly
        )

        path = self._artifact_path(data, "png")
        if not image.save(str(path), "PNG"):
            raise RuntimeError(f"تعذّر حفظ صورة الإيصال: {path}")
        config.secure_file(path)
        return path

    # -- transports -------------------------------------------------------- #
    def _send_escpos(self, data: ReceiptData, image_path: Path) -> None:
        """python-escpos: rasterise then GS v 0."""
        from escpos.printer import Usb  # type: ignore

        name = self.printer_name()
        vendor, product = self._parse_usb_id(name)
        printer = Usb(vendor, product, timeout=config.PRINTER_CONNECT_TIMEOUT_S)
        try:
            printer.image(str(image_path))
            printer.cut()
        finally:
            try:
                printer.close()
            except Exception:  # pragma: no cover
                pass

    @staticmethod
    def _parse_usb_id(name: str) -> tuple[int, int]:
        """'0x04b8:0x0e15' → (0x04b8, 0x0e15)."""
        raw = (name or "").strip().lower().replace("usb:", "")
        if ":" not in raw:
            raise RuntimeError("معرّف الطابعة غير صالح — استخدم الصيغة 0xVVVV:0xPPPP")
        vendor, product = raw.split(":", 1)
        return int(vendor, 16), int(product, 16)

    def _send_cups(self, image_path: Path) -> None:
        name = self.printer_name()
        if not name:
            raise RuntimeError("لم يتم تحديد اسم الطابعة")
        result = subprocess.run(
            ["lp", "-d", name, "-o", "fit-to-page", str(image_path)],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "فشل أمر lp")

    def _send_windows(self, data: ReceiptData, image_path: Path) -> None:
        """Windows: print the rendered image through the default handler."""
        os.startfile(str(image_path), "print")  # type: ignore[attr-defined]

    # -- fallback artefacts ------------------------------------------------ #
    @staticmethod
    def _artifact_path(data: ReceiptData, suffix: str) -> Path:
        config.ensure_directories()
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        number = (data.order_number or "receipt").replace("/", "-")
        return config.RECEIPTS_DIR / f"receipt_{number}_{stamp}.{suffix}"

    def _write_text(self, text: str, data: ReceiptData) -> Path:
        path = self._artifact_path(data, "txt")
        try:
            path.write_text(text, encoding="utf-8")
            config.secure_file(path)
        except OSError as exc:  # pragma: no cover
            logger.error("تعذّر كتابة نسخة الإيصال: %s", exc)
        return path


# --------------------------------------------------------------------------- #
# Shared instance
# --------------------------------------------------------------------------- #
_service: PrinterService | None = None


def get_printer_service(db: Any = None) -> PrinterService:
    global _service
    if _service is None or db is not None:
        _service = PrinterService(db)
    return _service
