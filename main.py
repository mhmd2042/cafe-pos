"""
main.py — Café POS application entry point.

Phase 2: boots the database, applies the warm theme and opens the PIN-pad login.
The Phase-1 database self-test is still available via --selftest.

Run:
    python main.py               # launch the app
    python main.py --selftest    # headless database/auth self-test
    python main.py --reset       # delete the local DB and rebuild it
    python main.py --screenshot  # render the login screen to a PNG and exit
                                 # (used for offline UI verification)
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import config
from database.db_manager import DatabaseError, get_db, now_stamp

# --------------------------------------------------------------------------- #
# Logging — a file inside the app folder, so a crash is diagnosable offline.
# --------------------------------------------------------------------------- #


def setup_logging() -> None:
    config.ensure_directories()
    handlers: list[logging.Handler] = [
        logging.FileHandler(config.LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
    # The log records the database path and internal errors; keep it owner-only.
    config.secure_file(config.LOG_PATH)
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL, logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        handlers=handlers,
    )


# --------------------------------------------------------------------------- #
# Self-test (headless — no Qt needed)
# --------------------------------------------------------------------------- #


def run_selftest() -> int:
    """Exercise the persistence + auth layer and print a readable report."""
    from controllers.auth_controller import AuthController
    from models.user import ROLE_ADMIN, ROLE_CASHIER, verify_pin

    print("=" * 62)
    print(f" {config.APP_NAME}  v{config.VERSION}  —  Phase {config.PHASE} self-test")
    print("=" * 62)

    db = get_db()
    print(f"DB file          : {db.db_path}")
    print(f"Schema version   : {db.query_value('PRAGMA user_version')}")
    print(f"Journal mode     : {db.query_value('PRAGMA journal_mode')}")
    print(f"Foreign keys     : {db.query_value('PRAGMA foreign_keys')}")
    print(f"Integrity check  : {'ok' if db.integrity_check(quick=True) else 'FAILED'}")
    print(f"DB size          : {db.db_size_bytes():,} bytes")
    print()

    tables = db.table_names()
    print(f"Tables ({len(tables)}):")
    for name in tables:
        count = db.scalar(f"SELECT COUNT(*) FROM {name}")  # names come from sqlite_master
        print(f"  • {name:<22} {count:>5} rows")
    print()

    print("Seeded menu:")
    for row in db.query_all(
        """
        SELECT c.name_ar AS category, COUNT(p.id) AS n
        FROM categories c LEFT JOIN products p ON p.category_id = c.id
        GROUP BY c.id ORDER BY c.sort_order
        """
    ):
        print(f"  • {row['category']:<18} {row['n']:>3} products")
    print()

    # --- transaction + rollback behaviour --------------------------------- #
    with db.transaction():
        db.execute("INSERT INTO categories (name_ar, name_en, sort_order) VALUES (?, ?, ?)",
                   ("__selftest__", "Self Test", 999))
    rolled_back = False
    try:
        with db.transaction():
            db.execute("INSERT INTO categories (name_ar, name_en, sort_order) VALUES (?, ?, ?)",
                       ("__rollback__", "Rollback", 999))
            raise RuntimeError("intentional rollback")
    except RuntimeError:
        rolled_back = True
    leftover = db.scalar("SELECT COUNT(*) FROM categories WHERE name_ar IN ('__selftest__','__rollback__')")
    db.execute("DELETE FROM categories WHERE name_ar IN ('__selftest__','__rollback__')")
    print("Transactions     : commit ok, rollback ok" if rolled_back and leftover == 1
          else f"Transactions     : UNEXPECTED (leftover={leftover})")
    print()

    # --- foreign key enforcement ------------------------------------------ #
    fk_enforced = False
    try:
        db.execute("INSERT INTO products (category_id, name_ar, price_minor) VALUES (99999, 'x', 100)")
    except Exception:
        fk_enforced = True
    print(f"FK enforcement   : {'enforced' if fk_enforced else 'NOT enforced'}")
    print()

    # --- authentication --------------------------------------------------- #
    admin_row = db.query_one("SELECT * FROM users WHERE username = 'admin'")
    cashier_row = db.query_one("SELECT * FROM users WHERE username = 'cashier'")
    print("Authentication (raw hashes):")
    print(f"  • admin   PIN 1234 -> {verify_pin('1234', admin_row['pin_hash'], admin_row['pin_salt'])}")
    print(f"  • admin   PIN 9999 -> {verify_pin('9999', admin_row['pin_hash'], admin_row['pin_salt'])}")
    print(f"  • cashier PIN 1111 -> {verify_pin('1111', cashier_row['pin_hash'], cashier_row['pin_salt'])}")
    print()

    # --- controller: login, lockout, permissions -------------------------- #
    auth = AuthController()
    ok_login = auth.login("admin", "1234")
    print("AuthController:")
    print(f"  • login admin/1234      -> success={ok_login.success} "
          f"must_change_pin={ok_login.must_change_pin} (fresh seed forces a new PIN)")

    bad = auth.login("admin", "0000")
    print(f"  • login admin/0000      -> success={bad.success} msg='{bad.message}'")

    lock = None
    for _ in range(6):
        lock = auth.login("cashier", "0000")
    print(f"  • lockout after retries -> locked_for={lock.locked_for}s")
    # The throttle is per-username and would block the legitimate login below,
    # so clear it the way an admin unlock would.
    auth.tracker.reset("cashier")

    cashier_login = auth.login("cashier", "1111")
    cashier_user = cashier_login.user
    print(f"  • login cashier/1111    -> success={cashier_login.success}")
    print(f"  • cashier can sell      -> {cashier_user.can('create_order')}")
    print(f"  • cashier can edit price-> {cashier_user.can('edit_prices')}")
    print(f"  • admin   can edit price-> {auth.repo.get_by_username('admin').can('edit_prices')}")
    denied = False
    try:
        auth.require("manage_backups")
    except Exception:
        denied = True
    print(f"  • cashier blocked from backups -> {denied}")
    print()

    # --- money / tax helpers --------------------------------------------- #
    print("Money helpers (YER, tax-inclusive @ 0%):")
    for minor in (1200, 2500, 3200):
        tax = config.extract_tax_from_inclusive(minor)
        net = config.net_from_inclusive(minor)
        print(f"  • {config.format_money(minor):>12}  = net {config.format_money(net)} + tax {config.format_money(tax)}")
    # Prove the tax maths still works for any rate the admin may set later.
    rate = config.DEFAULT_TAX_RATE
    try:
        config.DEFAULT_TAX_RATE = __import__("decimal").Decimal("0.15")
        sample = config.extract_tax_from_inclusive(2500)
        print(f"  • {config.format_money(2500)} @ 15% would embed tax {config.format_money(sample)}")
    finally:
        config.DEFAULT_TAX_RATE = rate
    print()

    # --- backup API -------------------------------------------------------- #
    stamp = config.timestamp_stamp()
    dest = config.LOCAL_BACKUP_DIR / config.BACKUP_FILENAME_TEMPLATE.format(stamp=stamp)
    db.backup_to(dest)
    print(f"Backup test      : {dest.name} ({dest.stat().st_size:,} bytes, verified)")
    print()

    ok = (
        db.integrity_check(quick=True)
        and rolled_back
        and leftover == 1
        and fk_enforced
        and verify_pin("1234", admin_row["pin_hash"], admin_row["pin_salt"])
        and not verify_pin("9999", admin_row["pin_hash"], admin_row["pin_salt"])
        and dest.exists()
        and ok_login.success
        and ok_login.must_change_pin
        and not bad.success
        and cashier_login.success
        and cashier_user.can("create_order")
        and not cashier_user.can("edit_prices")
        and denied
    )
    print(f"RESULT: {'ALL CHECKS PASSED' if ok else 'CHECKS FAILED'}  ({now_stamp()})")
    print("=" * 62)
    return 0 if ok else 1


# --------------------------------------------------------------------------- #
# GUI
# --------------------------------------------------------------------------- #


def run_app(argv: list[str] | None = None) -> int:
    """Launch the Qt application."""
    try:
        from PyQt6.QtWidgets import QApplication
    except ImportError as exc:
        print("PyQt6 غير مثبّت. شغّل:  pip install -r requirements.txt")
        print(f"({exc})")
        return 3

    from views.main_window import MainWindow
    from views.theme import Theme, apply_theme

    db = get_db()
    theme_name = db.get_setting("theme", config.ACTIVE_THEME)

    app = QApplication(argv or sys.argv)
    app.setApplicationName(config.APP_NAME)
    app.setApplicationDisplayName(config.APP_NAME_AR)
    app.setOrganizationName(config.APP_ID)
    app.setLayoutDirection(
        __import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.LayoutDirection.RightToLeft
    )

    theme = apply_theme(app, theme_name)

    from controllers.auth_controller import AuthController

    window = MainWindow(AuthController(), theme=theme)
    window.show()
    return app.exec()


def run_screenshot(path: str) -> int:
    """
    Render the login screen offscreen to a PNG.

    This is how the UI is verified without a human at the screen: it exercises
    real widget construction, the real stylesheet and real font/layout metrics.
    """
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication

    from controllers.auth_controller import AuthController
    from views.main_window import MainWindow
    from views.theme import apply_theme

    db = get_db()
    app = QApplication([sys.argv[0]])
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    theme = apply_theme(app, db.get_setting("theme", "dark"))

    window = MainWindow(AuthController(), theme=theme)
    window.resize(config.WINDOW_MIN_WIDTH, config.WINDOW_MIN_HEIGHT)
    window.show()
    app.processEvents()

    out = Path(path).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    pixmap = window.grab()
    if not pixmap.save(str(out)):
        print(f"FAILED to save screenshot to {out}")
        return 4
    print(f"screenshot: {out} ({out.stat().st_size:,} bytes, {pixmap.width()}x{pixmap.height()})")
    return 0


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=config.APP_ID, description=f"{config.APP_NAME} — offline café POS")
    parser.add_argument("--selftest", action="store_true", help="run the headless database/auth self-test")
    parser.add_argument("--reset", action="store_true", help="delete the local database and rebuild it")
    parser.add_argument("--db", metavar="PATH", help="use an alternative database file")
    parser.add_argument("--screenshot", metavar="PATH", help="render the login screen to a PNG and exit")
    args = parser.parse_args(argv)

    setup_logging()

    if args.db:
        config.DB_PATH = Path(args.db).expanduser().resolve()

    if args.reset:
        for suffix in ("", "-wal", "-shm"):
            Path(str(config.DB_PATH) + suffix).unlink(missing_ok=True)
        print(f"تم حذف قاعدة البيانات: {config.DB_PATH}")

    if args.selftest:
        try:
            return run_selftest()
        except DatabaseError as exc:
            logging.getLogger("main").error("خطأ في قاعدة البيانات: %s", exc)
            print(f"\nDATABASE ERROR: {exc}")
            return 2

    if args.screenshot:
        return run_screenshot(args.screenshot)

    return run_app(argv)


if __name__ == "__main__":
    sys.exit(main())
