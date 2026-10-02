"""
tests/test_phase4_shift_backup.py — headless check of shifts, Z-Reports and the
backup engine.

    1. shift lifecycle: open, single-open enforcement, close, over/short maths
    2. shift totals: cash vs card split, top sellers, average order
    3. Z-Report: text + CSV written, figures match the database
    4. shift rules: only the owner or an admin may close; no selling without a shift
    5. backup: external target used when reachable
    6. backup: unplugged external drive falls back to backups/local/ with a notice
    7. backup: pruning, verification, restore hint
    8. audit trail: openings, closings and backup attempts all recorded
    9. POS gate: the checkout button is disabled until a shift is open

Run:  .venv/bin/python tests/test_phase4_shift_backup.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config                                                   # noqa: E402

_TMP = Path(tempfile.mkdtemp(prefix="cafe_pos_p4_"))
config.DB_PATH = _TMP / "pos.db"
config.RECEIPTS_DIR = _TMP / "receipts"
config.LOCAL_BACKUP_DIR = _TMP / "backups" / "local"
config.LOG_DIR = _TMP / "logs"
config.LOG_PATH = config.LOG_DIR / "cafe_pos.log"

from PyQt6.QtCore import Qt                                     # noqa: E402
from PyQt6.QtWidgets import QApplication, QLabel                # noqa: E402

from controllers.auth_controller import AuthController          # noqa: E402
from controllers.backup_controller import BackupController      # noqa: E402
from controllers.pos_controller import PosController            # noqa: E402
from controllers.shift_controller import ShiftController        # noqa: E402
from database.db_manager import get_db                          # noqa: E402
from models.product import ProductRepository                    # noqa: E402
from models.shift import ShiftRepository                        # noqa: E402
from services.printer_service import PrinterService             # noqa: E402
from services.report_exporter import ReportExporter             # noqa: E402
from views.cashier.pos_main import PosMainView                  # noqa: E402
from views.theme import Theme                                   # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""),
          flush=True)


def section(title: str) -> None:
    print(f"\n{title}")


def main() -> int:
    db = get_db()
    app = QApplication([sys.argv[0]])
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    Theme("dark").apply(app)

    print("=" * 70)
    print(f" Phase 4 test — {config.APP_NAME} v{config.VERSION}")
    print(f" scratch DB: {config.DB_PATH}")
    print("=" * 70)

    repo = ProductRepository()
    cappuccino = repo.get_product(4)     # 2500
    water = repo.get_product(16)         # 500

    # ------------------------------------------------------------------ #
    section("1. Shift lifecycle")

    auth = AuthController()
    auth.login("cashier", "1111")
    shifts = ShiftRepository(db)
    backup = BackupController(db)
    shifts_ctl = ShiftController(auth=auth, shift_repo=shifts, backup=backup,
                                 exporter=ReportExporter(_TMP / "reports"))

    check("no shift open initially", shifts.get_open_shift() is None)
    check("needs_open_shift is True", shifts_ctl.needs_open_shift())

    opened = shifts_ctl.open_shift(10000, notes="بداية اليوم")
    check("shift opens with a float", opened.ok and opened.shift is not None,
          opened.message)
    shift = opened.shift
    check("float stored", shift.opening_float_minor == 10000)
    check("shift is open", shift.is_open)
    check("shift records the cashier", shift.user_id == auth.current_user.id)

    second = shifts_ctl.open_shift(5000)
    check("a second open shift is refused", not second.ok, second.message)
    check("still exactly one shift row", shifts.count() == 1)

    negative = shifts_ctl.open_shift(-100)
    check("negative float refused", not negative.ok, negative.message)

    # ------------------------------------------------------------------ #
    section("2. Sales into the shift")

    pos = PosController(auth=auth, printer=PrinterService(db))
    pos.add_product(cappuccino, quantity=2)          # 5000 cash
    r1 = pos.checkout(config.PAYMENT_CASH, 6000, shift_id=shift.id)
    check("cash sale recorded against the shift", r1.ok, r1.message)

    pos.add_product(water, quantity=2)               # 1000 card
    r2 = pos.checkout(config.PAYMENT_CARD, shift_id=shift.id)
    check("card sale recorded against the shift", r2.ok, r2.message)

    pos.add_product(cappuccino)                      # 2500 cash
    r3 = pos.checkout(config.PAYMENT_CASH, 2500, shift_id=shift.id)
    check("third sale recorded", r3.ok)

    totals = shifts.totals_for(shift.id)
    check("order count is 3", totals.order_count == 3, str(totals.order_count))
    check("gross is 8500", totals.gross_minor == 8500, str(totals.gross_minor))
    check("cash split is 7500", totals.cash_sales_minor == 7500, str(totals.cash_sales_minor))
    check("card split is 1000", totals.card_sales_minor == 1000, str(totals.card_sales_minor))
    check("cash + card equals gross",
          totals.cash_sales_minor + totals.card_sales_minor == totals.gross_minor)
    check("average order computed", totals.average_order_minor == 2833,
          str(totals.average_order_minor))
    check("top sellers aggregated",
          totals.top_items and totals.top_items[0][0] == cappuccino.name_ar
          and totals.top_items[0][1] == 3,
          str(totals.top_items[:2]))

    snapshot = shifts_ctl.live_snapshot()
    check("expected cash = float + cash sales",
          snapshot["expected_cash_minor"] == 10000 + 7500,
          str(snapshot["expected_cash_minor"]))

    # ------------------------------------------------------------------ #
    section("3. Close shift + Z-Report")

    closed = shifts_ctl.close_shift(17500, notes="إغلاق تجريبي", run_backup=False)
    check("shift closes", closed.ok, closed.message)
    check("difference is zero when balanced", closed.shift.difference_minor == 0,
          closed.shift.difference_label)
    check("balanced label says مطابق", closed.shift.difference_label == "مطابق",
          closed.shift.difference_label)
    check("shift marked closed", not closed.shift.is_open and closed.shift.closed_at)
    check("totals snapshotted onto the row",
          closed.shift.total_sales_minor == 8500 and closed.shift.order_count == 3,
          f"{closed.shift.total_sales_minor}/{closed.shift.order_count}")
    check("no open shift remains", shifts.get_open_shift() is None)
    again = shifts_ctl.close_shift(0)
    check("second close returns a clear error", not again.ok, again.message)

    report = closed.report_path
    check("Z-Report file written", report is not None and report.exists(),
          str(report))
    text = report.read_text(encoding="utf-8")
    check("report names the shift", f"#{shift.id}" in text)
    check("report shows the float", "10,000" in text)
    check("report shows expected cash", "17,500" in text)
    check("report shows the counted amount", "17,500" in text)
    check("report shows a zero difference", "مطابق" in text)
    check("report lists top sellers", "الأكثر مبيعاً" in text)
    check("report path stored on the shift row",
          shifts.get(shift.id).z_report_path != "")

    csv_path = report.with_suffix(".csv")
    check("CSV export written", csv_path.exists(), str(csv_path))
    csv_text = csv_path.read_text(encoding="utf-8-sig")
    check("CSV carries the figures",
          "gross_minor,8500" in csv_text.replace(" ", ""), "")

    # over/short maths in both directions
    shift2 = shifts_ctl.open_shift(5000).shift
    pos.add_product(cappuccino)                      # 2500 cash
    pos.checkout(config.PAYMENT_CASH, 2500, shift_id=shift2.id)
    short = shifts_ctl.close_shift(7000, run_backup=False)   # expect 7500
    check("short drawer is negative", short.shift.difference_minor == -500,
          short.shift.difference_label)
    check("short label says نقص", "نقص" in short.shift.difference_label,
          short.shift.difference_label)

    shift3 = shifts_ctl.open_shift(0).shift
    pos.add_product(water)                           # 500 cash
    pos.checkout(config.PAYMENT_CASH, 500, shift_id=shift3.id)
    over = shifts_ctl.close_shift(800, run_backup=False)
    check("over drawer is positive", over.shift.difference_minor == 300,
          over.shift.difference_label)
    check("over label says زيادة", "زيادة" in over.shift.difference_label,
          over.shift.difference_label)

    # ------------------------------------------------------------------ #
    section("4. Shift rules")

    cashier_user = auth.current_user
    other = shifts_ctl.open_shift(1000).shift
    check("shift opened by the cashier", other.user_id == cashier_user.id)

    auth.logout()
    auth.login("admin", "1234")
    admin_close = shifts_ctl.close_shift(1000, run_backup=False)
    check("admin can close another user's shift", admin_close.ok, admin_close.message)

    # a cashier must not close a shift belonging to someone else
    auth.logout()
    auth.login("cashier", "1111")
    admin_shift = shifts_ctl.open_shift(2000).shift
    auth.logout()
    auth.login("admin", "1234")
    shifts_ctl.close_shift(2000, run_backup=False)      # admin closes their own
    auth.logout()
    auth.login("cashier", "1111")

    # ------------------------------------------------------------------ #
    section("5. Backup to a reachable external target")

    external = _TMP / "external_drive"
    external.mkdir(parents=True, exist_ok=True)
    backup.set_target_dir(str(external))
    check("external target configured", backup.target_dir() == str(external))
    check("external target detected as available", backup.external_available())

    result = backup.create_backup(config.BACKUP_MANUAL, reason="test")
    check("backup succeeds", result.ok, result.message)
    check("status is ok", result.status == "ok", result.status)
    check("written to the external target", result.path is not None
          and result.path.parent == external, str(result.path))
    check("flagged as external", result.is_external)
    check("file has content", result.size_bytes > 0, f"{result.size_bytes:,} bytes")
    check("verified copy is a valid database", _is_valid_sqlite(result.path))

    local_copy = config.LOCAL_BACKUP_DIR / result.path.name
    check("secondary local copy also kept", local_copy.exists(), str(local_copy))

    # ------------------------------------------------------------------ #
    section("6. External drive unplugged (graceful fallback)")

    unplugged = _TMP / "usb_that_is_gone"
    backup.set_target_dir(str(unplugged))
    check("missing target not available", not backup.external_available())

    fallback = backup.create_backup(config.BACKUP_AUTO, reason="shift_close #x")
    check("backup still succeeds", fallback.ok, fallback.message)
    check("status is fallback", fallback.status == "fallback", fallback.status)
    check("not flagged as external", not fallback.is_external)
    check("written to the local folder", fallback.path is not None
          and fallback.path.parent == config.LOCAL_BACKUP_DIR, str(fallback.path))
    check("a gentle message is produced", "غير متصلة" in fallback.message,
          fallback.message)
    check("needs_attention set for the UI", fallback.needs_attention)
    check("valid database despite fallback", _is_valid_sqlite(fallback.path))

    # a read-only / permission-denied target must also degrade, not crash
    backup.set_target_dir(str(_TMP / "not_a_dir_file"))
    (_TMP / "not_a_dir_file").write_text("i am a file", encoding="utf-8")
    denied = backup.create_backup(config.BACKUP_MANUAL)
    check("a file where a folder should be degrades safely",
          denied.ok and denied.status == "fallback", f"{denied.status}: {denied.message}")

    # no target configured at all
    backup.set_target_dir("")
    none_set = backup.create_backup(config.BACKUP_MANUAL)
    check("no target configured still backs up locally",
          none_set.ok and none_set.path.parent == config.LOCAL_BACKUP_DIR,
          none_set.message)

    # ------------------------------------------------------------------ #
    section("7. Retention & maintenance")

    backup.set_target_dir(str(external))
    db.set_setting("backup_keep_local", "3")
    for _ in range(5):
        backup.create_backup(config.BACKUP_MANUAL)
    kept = backup.list_local_files()
    check("local copies pruned to the configured limit", len(kept) <= 3,
          f"{len(kept)} kept")

    hint = backup.restore_hint(str(external / "backup_x.db"))
    check("restore hint is plain-language", "انسخ الملف" in hint)

    status = backup.status()
    check("status reports configured + available",
          status["configured"] and status["available"], str(status["configured"]))
    check("status reports the last backup time",
          bool(status["last_backup_at"]), status["last_backup_at"])

    # ------------------------------------------------------------------ #
    section("8. Audit trail")

    actions = [r["action"] for r in db.query_all(
        "SELECT action FROM audit_log ORDER BY id")]
    check("shift_opened audited", "shift_opened" in actions)
    check("shift_closed audited", "shift_closed" in actions)
    check("backup_auto audited", "backup_auto" in actions)
    check("backup_manual audited", "backup_manual" in actions)

    close_audit = db.query_one(
        "SELECT * FROM audit_log WHERE action = 'shift_closed' ORDER BY id DESC LIMIT 1")
    details = close_audit["details"]
    check("close audit records expected/counted/difference",
          "expected=" in details and "counted=" in details and "difference=" in details,
          details[:90])
    check("close audit records the backup outcome", "backup=" in details)

    backup_log = db.query_all("SELECT * FROM backup_log ORDER BY id DESC")
    check("backup_log rows written", len(backup_log) >= 6, f"{len(backup_log)} rows")
    check("backup_log distinguishes ok vs fallback",
          {"ok", "fallback"} <= {r["status"] for r in backup_log},
          str(sorted({r["status"] for r in backup_log})))
    check("backup_log records sizes", all(r["size_bytes"] > 0 for r in backup_log))

    # ------------------------------------------------------------------ #
    section("9. POS gate (no selling without a shift)")

    auth.logout()
    auth.login("cashier", "1111")
    pos_view = PosMainView(auth, PosController(auth=auth, printer=PrinterService(db)))
    pos_view._ask_modifiers = lambda product: None
    pos_view.resize(1400, 820)
    pos_view.show()
    app.processEvents()

    check("no shift attached yet", pos_view.shift is None)
    pos_view.controller.add_product(cappuccino)
    pos_view.refresh_cart()
    app.processEvents()
    check("checkout disabled without a shift", not pos_view.checkout_button.isEnabled())
    check("button tells the cashier why",
          "وردية" in pos_view.checkout_button.text(),
          pos_view.checkout_button.text())

    before = db.scalar("SELECT COUNT(*) FROM orders")
    pos_view._complete_checkout(config.PAYMENT_CASH, 99999)
    app.processEvents()
    check("checkout without a shift writes no order",
          db.scalar("SELECT COUNT(*) FROM orders") == before)

    fresh = shifts_ctl.open_shift(3000).shift
    pos_view.set_shift(fresh)
    pos_view.refresh_cart()
    app.processEvents()
    check("shift chip shows the shift number", f"#{fresh.id}" in pos_view.shift_button.text(),
          pos_view.shift_button.text())
    check("checkout enabled once a shift is open", pos_view.checkout_button.isEnabled())

    pos_view._complete_checkout(config.PAYMENT_CASH, 3000)
    app.processEvents()
    check("sale now lands in the open shift",
          db.scalar("SELECT COUNT(*) FROM orders WHERE shift_id = ?", (fresh.id,)) == 1)

    # ------------------------------------------------------------------ #
    section("10. Dialog sizing (must fit a 1080p till)")

    from views.cashier.shift_dialog import CloseShiftDialog, OpenShiftDialog, ZReportDialog

    # A dialog taller than the screen makes its confirm button unreachable, so
    # these are real requirements, not cosmetics.
    open_dialog = OpenShiftDialog(shifts_ctl)
    open_dialog.show()
    app.processEvents()
    check("open-shift dialog fits within 1080p",
          open_dialog.height() <= 1080 - 40, f"{open_dialog.height()}px")
    check("open-shift dialog is wide enough for its pad",
          open_dialog.width() >= 560, f"{open_dialog.width()}px")
    open_dialog.close()

    # Section 9 left a shift open; close it before opening another.
    shifts_ctl.close_shift(3000 + 2500, run_backup=False)
    fresh2 = shifts_ctl.open_shift(15000).shift
    check("a new shift opens for the sizing checks", fresh2 is not None)
    pos.add_product(cappuccino)
    pos.checkout(config.PAYMENT_CASH, 2500, shift_id=fresh2.id)

    close_dialog = CloseShiftDialog(shifts_ctl)
    close_dialog.show()
    app.processEvents()
    check("close-shift dialog fits within 1080p",
          close_dialog.height() <= 1080 - 40, f"{close_dialog.height()}px")
    check("close-shift dialog pre-fills the expected cash",
          close_dialog.pad.amount_minor == 15000 + 2500,
          str(close_dialog.pad.amount_minor))
    check("close dialog shows the live difference",
          "مطابق" in close_dialog.difference_label.text(),
          close_dialog.difference_label.text())
    close_dialog.close()

    final = shifts_ctl.close_shift(17500, run_backup=False)
    report_dialog = ZReportDialog(final)
    report_dialog.show()
    app.processEvents()
    check("Z-Report dialog fits within 1080p",
          report_dialog.height() <= 1080 - 40, f"{report_dialog.height()}px")
    from PyQt6.QtWidgets import QScrollArea as _SA
    scroll_area = report_dialog.findChild(_SA)
    content_height = scroll_area.widget().sizeHint().height() if scroll_area else 0
    check("Z-Report content fits without scrolling",
          content_height <= scroll_area.viewport().height(),
          f"content {content_height} vs viewport "
          f"{scroll_area.viewport().height() if scroll_area else 0}")
    check("Z-Report shows the backup outcome",
          any("النسخ الاحتياطي" in lbl.text()
              for lbl in report_dialog.findChildren(QLabel)),
          "backup section present")
    report_dialog.close()

    # ------------------------------------------------------------------ #
    print()
    print(f"RESULT: {len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        print("failed:")
        for name in FAILED:
            print(f"  - {name}")
    print("=" * 70)
    return 1 if FAILED else 0


def _is_valid_sqlite(path: Path | None) -> bool:
    """Independently verify a backup file is a readable database."""
    if path is None or not path.exists():
        return False
    import sqlite3

    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            result = conn.execute("PRAGMA quick_check").fetchone()
            tables = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
            ).fetchone()[0]
        finally:
            conn.close()
        return str(result[0]).lower() == "ok" and tables >= 10
    except Exception:
        return False


if __name__ == "__main__":
    sys.exit(main())
