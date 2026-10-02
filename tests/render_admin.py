"""
tests/render_admin.py — render the Phase-5 admin dashboard pages.

Seeds a day of realistic trade so the charts and tables have data, then captures
each admin page in both themes plus the printable HTML report.

Run:  .venv/bin/python tests/render_admin.py [outdir]
"""

from __future__ import annotations

import os
import random
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config                                                   # noqa: E402

_TMP = Path(tempfile.mkdtemp(prefix="cafe_pos_admin_render_"))
config.DB_PATH = _TMP / "admin.db"
config.RECEIPTS_DIR = _TMP / "receipts"
config.LOCAL_BACKUP_DIR = _TMP / "backups"
config.LOG_DIR = _TMP / "logs"
config.LOG_PATH = config.LOG_DIR / "cafe_pos.log"

from datetime import datetime, timedelta                        # noqa: E402

from PyQt6.QtCore import Qt                                     # noqa: E402
from PyQt6.QtWidgets import QApplication                        # noqa: E402

from controllers.admin_controller import AdminController        # noqa: E402
from controllers.auth_controller import AuthController          # noqa: E402
from controllers.pos_controller import ModifierSelection, PosController  # noqa: E402
from controllers.shift_controller import ShiftController        # noqa: E402
from database.db_manager import get_db                          # noqa: E402
from models.order import SelectedModifier                       # noqa: E402
from models.product import ProductRepository                    # noqa: E402
from models.shift import ShiftRepository                        # noqa: E402
from services.printer_service import PrinterService             # noqa: E402
from views.admin.dashboard import AdminDashboard                # noqa: E402
from views.theme import Theme                                   # noqa: E402


def shoot(widget, path: Path, app) -> None:
    app.processEvents()
    pixmap = widget.grab()
    pixmap.save(str(path))
    print(f"  {path.name}  ({pixmap.width()}x{pixmap.height()})")


def seed_trade(db, auth, days: int = 7) -> None:
    """
    Create a plausible week of sales: a morning and an afternoon rush, a mix of
    products and payment methods. Needed so the reports page has something real
    to draw rather than an empty state.
    """
    products = ProductRepository(db)
    all_products = [p for p in products.list_products() if p.category_id != 5]
    shifts = ShiftController(auth=auth, shift_repo=ShiftRepository(db))
    pos = PosController(auth=auth, printer=PrinterService(db))

    random.seed(7)
    for day_offset in range(days - 1, -1, -1):
        day = datetime.now() - timedelta(days=day_offset)
        shift = shifts.open_shift(15000).shift
        if shift is None:
            continue

        # two rushes: 08:00-10:00 and 16:00-19:00
        orders = random.randint(14, 26)
        for _ in range(orders):
            hour = random.choice([8, 8, 9, 9, 9, 10, 16, 17, 17, 18, 18, 19])
            product = random.choice(all_products)
            quantity = random.choice([1, 1, 1, 2])
            modifiers = []
            if product.category_id in (1, 2) and random.random() < 0.6:
                modifiers = [SelectedModifier(1, "الحجم", 3, "كبير", 500)]
                if random.random() < 0.3:
                    modifiers.append(SelectedModifier(2, "نوع الحليب", 12, "حليب شوفان", 500))
            pos.add_product(product, ModifierSelection(modifiers=modifiers), quantity)
            method = config.PAYMENT_CARD if random.random() < 0.35 else config.PAYMENT_CASH
            total = pos.cart.total_minor
            pos.checkout(method, total + 5000 if method == config.PAYMENT_CASH else None,
                         shift_id=shift.id, print_receipt=False)

            # backdate so the report spreads across the day/week
            stamp = day.replace(hour=hour, minute=random.randint(0, 59),
                                second=random.randint(0, 59))
            text = stamp.strftime("%Y-%m-%d %H:%M:%S")
            db.execute("UPDATE orders SET created_at = ?, completed_at = ? "
                       "WHERE id = (SELECT MAX(id) FROM orders)", (text, text))

        shifts.close_shift(15000 + 8000, run_backup=False)

    # one open shift so the dashboard reflects a live till
    shifts.open_shift(20000)


def main() -> int:
    outdir = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/cafepos5").expanduser()
    outdir.mkdir(parents=True, exist_ok=True)

    db = get_db()
    app = QApplication([sys.argv[0]])
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)

    auth = AuthController()
    auth.login("admin", "1234")
    seed_trade(db, auth, days=7)
    admin = AdminController(auth=auth)

    report = admin.monthly_report()
    print(f"seeded: {report.order_count} orders, "
          f"gross {config.format_money(report.gross_minor)}, "
          f"margin {report.margin_percent:.1f}%")

    for theme_name in ("dark", "light"):
        Theme(theme_name).apply(app)
        dashboard = AdminDashboard(auth)
        dashboard.resize(1480, 900)
        dashboard.show()
        app.processEvents()

        # theme the charts from the active palette
        tokens = Theme(theme_name).tokens
        for key in ("reports", "menu", "inventory"):
            page = dashboard.show_page(key)
            app.processEvents()
            if key == "reports":
                page.apply_chart_theme(tokens)
                app.processEvents()

        print(f"{theme_name}:")
        for key, label in (("reports", "reports"), ("menu", "menu"),
                           ("inventory", "inventory"), ("backup", "backup"),
                           ("settings", "settings")):
            page = dashboard.show_page(key)
            app.processEvents()
            shoot(page, outdir / f"admin_{label}_{theme_name}.png", app)
        dashboard.close()

    # printable report
    html_path = admin.export_report_html(report)
    (outdir / "report.html").write_bytes(html_path.read_bytes())
    csv_path = admin.export_report_csv(report)
    (outdir / "report.csv").write_bytes(csv_path.read_bytes())
    print(f"\nreport: {outdir / 'report.html'}")
    print(f"csv:    {outdir / 'report.csv'}")
    print(f"report lines: {len(html_path.read_text(encoding='utf-8').splitlines())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
