"""
tests/render_pos.py — render the Phase-3 cashier screens and a sample receipt.

Writes PNGs plus the plain-text receipt so both can be eyeballed. Uses a scratch
database, so the live till is untouched.

Run:  .venv/bin/python tests/render_pos.py [outdir]
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config                                                   # noqa: E402

_TMP = Path(tempfile.mkdtemp(prefix="cafe_pos_render_"))
config.DB_PATH = _TMP / "render.db"
config.RECEIPTS_DIR = _TMP / "receipts"

from PyQt6.QtCore import Qt                                     # noqa: E402
from PyQt6.QtWidgets import QApplication                        # noqa: E402

from controllers.auth_controller import AuthController          # noqa: E402
from controllers.pos_controller import ModifierSelection, PosController  # noqa: E402
from database.db_manager import get_db                          # noqa: E402
from models.order import SelectedModifier                       # noqa: E402
from models.product import ProductRepository                    # noqa: E402
from services.printer_service import PrinterService             # noqa: E402
from views.cashier.cash_dialog import CashPaymentDialog         # noqa: E402
from views.cashier.modifier_dialog import ModifierDialog        # noqa: E402
from views.cashier.pos_main import PosMainView                  # noqa: E402
from views.main_window import MainWindow                        # noqa: E402
from views.theme import Theme                                   # noqa: E402


def shoot(widget, path: Path, app) -> None:
    app.processEvents()
    pixmap = widget.grab()
    pixmap.save(str(path))
    print(f"  {path}  ({path.stat().st_size:,} bytes, {pixmap.width()}x{pixmap.height()})")


def main() -> int:
    outdir = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/cafepos3").expanduser()
    outdir.mkdir(parents=True, exist_ok=True)

    db = get_db()
    app = QApplication([sys.argv[0]])
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)

    repo = ProductRepository()
    cappuccino = repo.get_product(4)
    water = repo.get_product(16)
    cheesecake = repo.get_product(17)

    for theme_name in ("dark", "light"):
        Theme(theme_name).apply(app)

        auth = AuthController()
        auth.login("cashier", "1111")
        controller = PosController(auth=auth, printer=PrinterService(db))
        view = PosMainView(auth, controller)
        view._ask_modifiers = lambda product: None
        view.resize(1400, 820)
        view.show()
        app.processEvents()

        print(f"{theme_name} — empty cart:")
        shoot(view, outdir / f"pos_{theme_name}_empty.png", app)

        # build a realistic order
        controller.add_product(cappuccino, ModifierSelection(
            modifiers=[SelectedModifier(1, "الحجم", 3, "كبير", 500),
                       SelectedModifier(2, "نوع الحليب", 12, "حليب شوفان", 500),
                       SelectedModifier(3, "مستوى السكر", 20, "بدون سكر", 0)],
            note="بدون رغوة",
        ))
        controller.add_product(water, quantity=2)
        controller.add_product(cheesecake, ModifierSelection(
            modifiers=[SelectedModifier(5, "إضافات", 40, "كريمة مخفوقة", 500)]))
        view.refresh_cart()
        app.processEvents()
        print(f"{theme_name} — cart with 3 lines:")
        shoot(view, outdir / f"pos_{theme_name}_cart.png", app)

        # modifier dialog
        dialog = ModifierDialog(controller, cappuccino)
        dialog.show()
        app.processEvents()
        print(f"{theme_name} — modifier dialog:")
        shoot(dialog, outdir / f"modifiers_{theme_name}.png", app)
        dialog.close()

        # cash payment dialog
        cash = CashPaymentDialog(controller.cart.total_minor,
                                 controller.suggested_cash_amounts())
        cash.show()
        app.processEvents()
        cash._set_amount(controller.cart.total_minor + 2000)
        app.processEvents()
        print(f"{theme_name} — cash dialog:")
        shoot(cash, outdir / f"cash_{theme_name}.png", app)
        cash.close()

        # full window with header, cashier logged in
        window = MainWindow(AuthController(), theme=Theme(theme_name))
        window.resize(1400, 820)
        window.show()
        app.processEvents()
        window.auth.login("cashier", "1111")
        window._on_logged_in(window.auth.current_user)
        window._open_cashier(window.auth.current_user)
        window.pos_page._ask_modifiers = lambda product: None
        window.pos_page.controller.add_product(cappuccino, ModifierSelection(
            modifiers=[SelectedModifier(1, "الحجم", 3, "كبير", 500)]))
        window.pos_page.refresh_cart()
        app.processEvents()
        print(f"{theme_name} — full window (cashier):")
        shoot(window, outdir / f"window_{theme_name}.png", app)
        window.close()

    # ---- receipt ------------------------------------------------------- #
    Theme("dark").apply(app)
    auth = AuthController()
    auth.login("cashier", "1111")
    controller = PosController(auth=auth, printer=PrinterService(db))
    controller.add_product(cappuccino, ModifierSelection(
        modifiers=[SelectedModifier(1, "الحجم", 3, "كبير", 500),
                   SelectedModifier(2, "نوع الحليب", 12, "حليب شوفان", 500)],
        note="بدون رغوة"))
    controller.add_product(water, quantity=2)
    result = controller.checkout(config.PAYMENT_CASH,
                                 controller.cart.total_minor + 2000)

    print("\nreceipt:")
    data = controller.receipt_data(result.order.id)
    text_path = result.print_result.text_path
    print(f"  text: {text_path}")
    (outdir / "receipt.txt").write_text(text_path.read_text(encoding="utf-8"),
                                        encoding="utf-8")
    image_path = controller.printer.render_image(data)
    target = outdir / "receipt.png"
    target.write_bytes(image_path.read_bytes())
    print(f"  raster: {target} ({target.stat().st_size:,} bytes)")

    print("\n--- receipt text ---")
    print(text_path.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
