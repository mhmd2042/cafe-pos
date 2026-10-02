"""
tests/render_shift.py — render the Phase-4 shift screens and a real Z-Report.

Drives a full day: open a shift, take some sales, close it short, and capture
the open dialog, the close dialog, the Z-Report dialog and the printed report.

Run:  .venv/bin/python tests/render_shift.py [outdir]
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config                                                   # noqa: E402

_TMP = Path(tempfile.mkdtemp(prefix="cafe_pos_shift_render_"))
config.DB_PATH = _TMP / "shift.db"
config.RECEIPTS_DIR = _TMP / "receipts"
config.LOCAL_BACKUP_DIR = _TMP / "backups"
config.LOG_DIR = _TMP / "logs"
config.LOG_PATH = config.LOG_DIR / "cafe_pos.log"

from PyQt6.QtCore import Qt                                     # noqa: E402
from PyQt6.QtWidgets import QApplication                        # noqa: E402

from controllers.auth_controller import AuthController          # noqa: E402
from controllers.backup_controller import BackupController      # noqa: E402
from controllers.pos_controller import ModifierSelection, PosController  # noqa: E402
from controllers.shift_controller import ShiftController        # noqa: E402
from database.db_manager import get_db                          # noqa: E402
from models.order import SelectedModifier                       # noqa: E402
from models.product import ProductRepository                    # noqa: E402
from models.shift import ShiftRepository                        # noqa: E402
from services.printer_service import PrinterService             # noqa: E402
from services.report_exporter import ReportExporter             # noqa: E402
from views.cashier.shift_dialog import (                        # noqa: E402
    CloseShiftDialog,
    OpenShiftDialog,
    ZReportDialog,
)
from views.cashier.pos_main import PosMainView                  # noqa: E402
from views.main_window import MainWindow                        # noqa: E402
from views.theme import Theme                                   # noqa: E402


def shoot(widget, path: Path, app) -> None:
    app.processEvents()
    pixmap = widget.grab()
    pixmap.save(str(path))
    print(f"  {path}  ({path.stat().st_size:,} bytes, {pixmap.width()}x{pixmap.height()})")


def main() -> int:
    outdir = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/cafepos4").expanduser()
    outdir.mkdir(parents=True, exist_ok=True)

    db = get_db()
    app = QApplication([sys.argv[0]])
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    Theme("dark").apply(app)

    repo = ProductRepository()
    cappuccino = repo.get_product(4)
    latte = repo.get_product(5)
    cheesecake = repo.get_product(17)
    water = repo.get_product(16)

    auth = AuthController()
    auth.login("cashier", "1111")
    shifts = ShiftRepository(db)
    backup = BackupController(db)
    exporter = ReportExporter(_TMP / "reports")
    ctl = ShiftController(auth=auth, shift_repo=shifts, backup=backup, exporter=exporter)

    # -- open dialog --------------------------------------------------- #
    print("open shift dialog:")
    open_dialog = OpenShiftDialog(ctl)
    open_dialog.show()
    open_dialog.pad.set_amount(15000)
    app.processEvents()
    shoot(open_dialog, outdir / "open_shift.png", app)
    open_dialog.close()

    # -- trade all day -------------------------------------------------- #
    shift = ctl.open_shift(15000, notes="بداية اليوم").shift
    pos = PosController(auth=auth, printer=PrinterService(db))
    pos.add_product(cappuccino, ModifierSelection(
        modifiers=[SelectedModifier(1, "الحجم", 3, "كبير", 500),
                   SelectedModifier(2, "نوع الحليب", 12, "حليب شوفان", 500)]), quantity=2)
    pos.checkout(config.PAYMENT_CASH, 8000, shift_id=shift.id)
    pos.add_product(latte, ModifierSelection(
        modifiers=[SelectedModifier(1, "الحجم", 1, "صغير", -300)]))
    pos.checkout(config.PAYMENT_CARD, shift_id=shift.id)
    pos.add_product(cheesecake, quantity=2)
    pos.checkout(config.PAYMENT_CASH, 7000, shift_id=shift.id)
    pos.add_product(water, quantity=3)
    pos.checkout(config.PAYMENT_CASH, 1500, shift_id=shift.id)

    snapshot = ctl.live_snapshot()
    expected = snapshot["expected_cash_minor"]
    print(f"expected cash in drawer: {config.format_money(expected)}")

    # -- close dialog (counted short by 700) ---------------------------- #
    print("close shift dialog:")
    close_dialog = CloseShiftDialog(ctl)
    close_dialog.show()
    close_dialog.pad.set_amount(expected - 700)
    app.processEvents()
    shoot(close_dialog, outdir / "close_shift.png", app)
    close_dialog.close()

    # -- close for real -------------------------------------------------- #
    result = ctl.close_shift(expected - 700, notes="نقص بسيط في العد")
    print(f"closed: {result.message}")

    print("z-report dialog:")
    report_dialog = ZReportDialog(result)
    report_dialog.show()
    app.processEvents()
    shoot(report_dialog, outdir / "z_report.png", app)
    report_dialog.close()

    # -- POS with a shift open (full window) ----------------------------- #
    for theme_name in ("dark", "light"):
        Theme(theme_name).apply(app)
        window = MainWindow(AuthController(), theme=Theme(theme_name))
        window.resize(1400, 820)
        window.show()
        app.processEvents()
        window.auth.login("cashier", "1111")
        window._on_logged_in(window.auth.current_user)
        # bypass the modal: attach a shift directly
        fresh = window.shift_controller.open_shift(12000).shift
        window.stack.setCurrentWidget(window.pos_page)
        window.pos_page.reload_menu()
        window.pos_page.set_shift(fresh)
        window.pos_page.controller.add_product(cappuccino, ModifierSelection(
            modifiers=[SelectedModifier(1, "الحجم", 3, "كبير", 500)]))
        window.pos_page.refresh_cart()
        app.processEvents()
        print(f"{theme_name} — POS with an open shift:")
        shoot(window, outdir / f"pos_shift_{theme_name}.png", app)
        window.close()
        window.shift_controller.close_shift(12000, run_backup=False)

    # -- the report artefacts -------------------------------------------- #
    text_path = result.report_path
    (outdir / "z_report.txt").write_text(text_path.read_text(encoding="utf-8"),
                                         encoding="utf-8")
    csv_src = text_path.with_suffix(".csv")
    (outdir / "z_report.csv").write_bytes(csv_src.read_bytes())
    print(f"\nz-report text: {outdir / 'z_report.txt'}")
    print("\n--- Z-Report ---")
    print(text_path.read_text(encoding="utf-8"))

    # -- backup outcome for the same shift ------------------------------- #
    ext = _TMP / "usb"
    ext.mkdir(exist_ok=True)
    backup.set_target_dir(str(ext))
    ok = backup.create_backup(config.BACKUP_AUTO, reason="shift_close")
    print(f"backup with drive present : {ok.status} -> {ok.path}")
    backup.set_target_dir(str(_TMP / "missing_usb"))
    fb = backup.create_backup(config.BACKUP_AUTO, reason="shift_close")
    print(f"backup with drive removed : {fb.status} -> {fb.path}")
    print(f"message: {fb.message}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
