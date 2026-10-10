"""
tests/test_small_screens.py — the layout must survive a real laptop.

Regression guard for a reported fault: on a 1366x768 laptop the windows were
bigger than the screen and widgets painted on top of each other. Qt6 is
per-monitor DPI aware, so Windows' default 125% scaling gives the app only
about 1092x582 logical pixels — well under what the screens used to demand
(the menu editor alone wanted 963px of height).

This asserts, for every screen at every size a café laptop plausibly offers:
  * no two sibling widgets overlap,
  * no visible widget paints outside a non-scrolling parent.

Run:  QT_QPA_PLATFORM=offscreen .venv/bin/python tests/test_small_screens.py
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QAbstractItemView, QApplication, QScrollArea, QWidget

import config
import database.db_manager as dbm

# (label, width, height) in logical px. A 1366x768 panel minus a ~40px taskbar,
# divided by the Windows scale factor.
SIZES = (
    ("window minimum", config.WINDOW_MIN_WIDTH, config.WINDOW_MIN_HEIGHT),
    ("1366x768 @150%", 910, 485),
    ("1366x768 @125%", 1092, 582),
    ("1366x768 @100%", 1366, 728),
    ("1920x1080 @100%", 1920, 1040),
)

# Overlays and hairlines are allowed to sit on top of other things.
SKIP_NAMES = {"Divider"}
SKIP_CLASSES = {"Toast"}

PROBLEMS: list[str] = []
CHECKS = 0


def _audit(widget: QWidget, label: str) -> None:
    """Record overlaps between siblings and children outside their parent."""
    global CHECKS

    siblings = [
        c for c in widget.findChildren(QWidget)
        if c.isVisible() and c.parentWidget() is widget
        and c.width() > 0 and c.height() > 0
        and c.objectName() not in SKIP_NAMES
        and c.__class__.__name__ not in SKIP_CLASSES
    ]
    for i, a in enumerate(siblings):
        for b in siblings[i + 1:]:
            CHECKS += 1
            hit = a.geometry().intersected(b.geometry())
            if hit.width() > 2 and hit.height() > 2:
                PROBLEMS.append(
                    f"{label}: {a.__class__.__name__}({a.objectName()}) overlaps "
                    f"{b.__class__.__name__}({b.objectName()})"
                )

    for child in widget.findChildren(QWidget):
        if not child.isVisible() or child.width() == 0 or child.height() == 0:
            continue
        if child.objectName() in SKIP_NAMES:
            continue
        if isinstance(child, QAbstractItemView) and not child.isVisible():
            continue
        parent = child.parentWidget()
        if parent is None or parent is widget:
            continue
        # Content inside a scroll area is legitimately taller than the viewport.
        ancestor, in_scroll = parent, False
        while ancestor is not None:
            if isinstance(ancestor, QScrollArea):
                in_scroll = True
                break
            ancestor = ancestor.parentWidget()
        if in_scroll:
            continue
        CHECKS += 1
        if (child.geometry().right() > parent.width() + 1
                or child.geometry().bottom() > parent.height() + 1):
            PROBLEMS.append(
                f"{label}: {child.__class__.__name__}({child.objectName()}) is "
                f"{child.width()}x{child.height()} inside a "
                f"{parent.width()}x{parent.height()} parent"
            )


def main() -> int:
    dbm.reset_singleton()
    db = dbm.get_db()

    from controllers.admin_controller import AdminController
    from controllers.auth_controller import AuthController
    from models.user import UserRepository
    from views.theme import apply_theme

    app = QApplication([])
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    apply_theme(app, "dark")
    auth = AuthController(UserRepository(db))
    controller = AdminController(auth=auth, db=db)

    from views.admin.backup_view import BackupView
    from views.admin.dashboard import AdminDashboard
    from views.admin.inventory_view import InventoryView
    from views.admin.menu_editor import MenuEditorView
    from views.admin.reports_view import ReportsView
    from views.admin.settings_view import SettingsView
    from views.cashier.pos_main import PosMainView
    from views.login_view import LoginView

    builders = (
        ("LoginView", lambda: LoginView(auth)),
        ("PosMainView", lambda: PosMainView(auth)),
        ("AdminDashboard", lambda: AdminDashboard(auth, controller)),
        ("MenuEditorView", lambda: MenuEditorView(auth, controller)),
        ("InventoryView", lambda: InventoryView(auth, controller)),
        ("BackupView", lambda: BackupView(auth, controller)),
        ("ReportsView", lambda: ReportsView(auth, controller)),
        ("SettingsView", lambda: SettingsView(auth, controller)),
    )

    print("=" * 74)
    print(" SMALL-SCREEN / HIGH-DPI LAYOUT")
    print(f" sizes: {', '.join(f'{w}x{h}' for _, w, h in SIZES)}")
    print("=" * 74)

    for label, width, height in SIZES:
        print(f"\n {label}  ({width}x{height})")
        for name, build in builders:
            try:
                view = build()
                view.resize(width, height)
                view.show()
                for _ in range(22):
                    app.processEvents()
                before = len(PROBLEMS)
                _audit(view, f"{name}@{width}x{height}")
                found = len(PROBLEMS) - before
                print(f"   {'FAIL' if found else ' ok '}  {name}"
                      + (f"  ({found} problem(s))" if found else ""))
                view.close()
            except Exception as exc:  # noqa: BLE001 - report and continue
                PROBLEMS.append(f"{name}@{width}x{height}: {exc}")
                print(f"   FAIL  {name}  ({exc})")

    print()
    print("=" * 74)
    if PROBLEMS:
        print(f" FINDINGS ({len(PROBLEMS)})")
        print("=" * 74)
        for problem in PROBLEMS[:40]:
            print(f"   • {problem}")
    print(f" RESULT: {CHECKS} checks, {len(PROBLEMS)} problem(s)")
    print("=" * 74)

    for suffix in ("", "-wal", "-shm"):
        try:
            os.unlink(str(db.db_path) + suffix)
        except OSError:
            pass

    return 1 if PROBLEMS else 0


if __name__ == "__main__":
    raise SystemExit(main())
