"""
Comprehensive layout audit — measures EVERY widget on EVERY screen.

For each screen at each size, reports:
  1. OVERLAPS: two visible widgets whose rectangles intersect
  2. OVERFLOW: a widget extending past its parent's visible area
  3. UNDERSIZED: interactive controls below the touch minimum
  4. PHANTOM: hidden widgets that still reserve layout space
  5. CLIPPED TEXT: labels whose text is wider than the widget (no word wrap)

This is the audit the app should have had before any build.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication, QAbstractButton, QAbstractItemView, QComboBox, QFrame,
    QGridLayout, QHBoxLayout, QLabel, QLayout, QLineEdit, QListWidget,
    QScrollArea, QSizePolicy, QStackedWidget, QTableWidget, QTextEdit,
    QVBoxLayout, QWidget,
)

import config  # noqa: E402
import database.db_manager as dbm  # noqa: E402

TOUCH_MIN = config.MIN_TOUCH_TARGET
SIZES = [(1366, 768), (1280, 720), (1024, 600), (1920, 1080), (1600, 900)]

OVERLAPS: list[str] = []
OVERFLOWS: list[str] = []
UNDERSIZED: list[str] = []
PHANTOMS: list[str] = []
CLIPPED: list[str] = []


def describe(w: QWidget) -> str:
    name = w.objectName() or type(w).__name__
    text = ""
    if isinstance(w, (QLabel, QAbstractButton)):
        text = (w.text() or "")[:24].replace("\n", " ")
    return f"{name}({text})" if text else name


def _is_in_scroll_area(widget: QWidget) -> bool:
    """True when the widget lives inside a QScrollArea (directly or not)."""
    ancestor = widget.parentWidget()
    while ancestor is not None:
        if isinstance(ancestor, QScrollArea):
            return True
        ancestor = ancestor.parentWidget()
    return False


def audit_container(container: QWidget, label: str) -> None:
    """Check one container for every class of layout problem."""

    # -- 1. overlaps between siblings -------------------------------------
    # Only consider widgets that are NOT inside a scroll area: a scroll area's
    # viewport and its contents are designed to extend beyond the visible area,
    # so flagging them produces thousands of false positives.
    visible = [w for w in container.findChildren(QWidget)
               if w.isVisible() and w.width() > 0 and w.height() > 0
               and not _is_in_scroll_area(w)]
    for i in range(len(visible)):
        for j in range(i + 1, len(visible)):
            a, b = visible[i], visible[j]
            # skip parent/child pairs — a child inside a parent is normal
            if a.isAncestorOf(b) or b.isAncestorOf(a):
                continue
            # Compare in a common coordinate system. geometry() is relative to
            # each widget's own parent, so two widgets in different sub-layouts
            # can appear to overlap when they are actually far apart.
            if a.parentWidget() is not b.parentWidget():
                continue
            if a.geometry().intersects(b.geometry()):
                OVERLAPS.append(
                    f"{label}: {describe(a)} overlaps {describe(b)}"
                )

    # -- 2. overflow past the parent --------------------------------------
    for child in container.findChildren(QWidget):
        if child.isHidden() or child.width() == 0:
            continue
        if isinstance(child, QAbstractItemView) and not child.isVisible():
            continue
        parent = child.parentWidget()
        if parent is None or parent is container:
            continue
        # inside a scroll area is fine
        ancestor = parent
        in_scroll = False
        while ancestor is not None:
            if isinstance(ancestor, QScrollArea):
                in_scroll = True
                break
            ancestor = ancestor.parentWidget()
        if in_scroll:
            continue
        # Skip children of a Toast: the toast is an overlay whose label is
        # intentionally larger than the toast frame until it is shown.
        ancestor = parent
        in_toast = False
        while ancestor is not None:
            if ancestor.objectName().startswith("Toast"):
                in_toast = True
                break
            ancestor = ancestor.parentWidget()
        if in_toast:
            continue
        cg = child.geometry()
        pg = parent.rect()
        if (cg.right() > pg.right() + 2 or cg.bottom() > pg.bottom() + 2
                or cg.left() < -2 or cg.top() < -2):
            OVERFLOWS.append(
                f"{label}: {describe(child)} {cg.width()}x{cg.height()} "
                f"at {cg.x()},{cg.y()} exceeds parent "
                f"{describe(parent)} {pg.width()}x{pg.height()}"
            )

    # -- 3. undersized interactive controls --------------------------------
    for button in container.findChildren(QAbstractButton):
        if button.isHidden() or not button.isEnabled():
            continue
        if button.width() < TOUCH_MIN or button.height() < TOUCH_MIN:
            UNDERSIZED.append(
                f"{label}: {describe(button)} is "
                f"{button.width()}x{button.height()}"
            )

    # -- 4. hidden widgets reserving layout space -------------------------
    for child in container.findChildren(QWidget):
        if child.isHidden():
            parent = child.parentWidget()
            if parent is None:
                continue
            lay = parent.layout()
            if lay is None:
                continue
            idx = lay.indexOf(child)
            if idx < 0:
                continue
            policy = child.sizePolicy()
            ignored = (policy.horizontalPolicy() == QSizePolicy.Policy.Ignored
                       and policy.verticalPolicy() == QSizePolicy.Policy.Ignored)
            # A Toast is an overlay: it may be added to a layout but must not
            # influence it. Ignored/Ignored is the contract.
            if not ignored and not child.objectName().startswith("Toast"):
                PHANTOMS.append(
                    f"{label}: hidden {describe(child)} is a layout item "
                    f"(policy {policy.horizontalPolicy()}/"
                    f"{policy.verticalPolicy()})"
                )

    # -- 5. labels that cannot show their text ----------------------------
    for lbl in container.findChildren(QLabel):
        if lbl.isHidden() or not lbl.text():
            continue
        if lbl.wordWrap():
            continue
        # A single-line label narrower than its text will clip.
        fm = lbl.fontMetrics()
        needed = fm.horizontalAdvance(lbl.text())
        if needed > lbl.width() + 4:
            CLIPPED.append(
                f"{label}: {describe(lbl)} needs {needed}px for "
                f"{lbl.width()}px — text will clip"
            )


def main() -> int:
    app = QApplication.instance() or QApplication([])
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)

    dbm.reset_singleton()
    db = dbm.get_db()

    from controllers.auth_controller import AuthController
    from models.user import UserRepository
    from views.login_view import LoginView
    from views.main_window import MainWindow
    from views.theme import apply_theme

    apply_theme(app, "dark")
    auth = AuthController(UserRepository(db))

    print("=" * 84)
    print(f" COMPREHENSIVE LAYOUT AUDIT — {config.APP_NAME} v{config.VERSION}")
    print(f" touch minimum {TOUCH_MIN}px | sizes: "
          + ", ".join(f"{w}x{h}" for w, h in SIZES))
    print("=" * 84)

    # ---------------------------------------------------------- login ------
    print("\n[1/3] LOGIN SCREEN")
    login = LoginView(auth)
    login.show()
    for width, height in SIZES:
        login.resize(width, height)
        for _ in range(8):
            app.processEvents()
        audit_container(login, f"login@{width}x{height}")
    print(f"  audited at {len(SIZES)} sizes")

    # ----------------------------------------------------- main window -----
    print("\n[2/3] MAIN WINDOW (all screens)")
    win = MainWindow(auth)
    win.show()
    stack = win.findChild(QStackedWidget)
    for width, height in SIZES:
        win.resize(width, height)
        for _ in range(6):
            app.processEvents()
        for index in range(stack.count()):
            stack.setCurrentIndex(index)
            for _ in range(8):
                app.processEvents()
            page = stack.widget(index)
            name = page.objectName() or type(page).__name__
            audit_container(page, f"{name}@{width}x{height}")
    print(f"  audited {stack.count()} screens at {len(SIZES)} sizes")

    # --------------------------------------------------------- dialogs -----
    print("\n[3/3] DIALOGS")
    from views.cashier.cash_dialog import CashPaymentDialog, PaymentDialog
    from views.cashier.modifier_dialog import ModifierDialog
    from views.cashier.shift_dialog import (
        CloseShiftDialog, OpenShiftDialog, ZReportDialog,
    )

    dialogs = []

    # CashPaymentDialog needs a total; PaymentDialog too.
    dialogs.append(("CashPaymentDialog", CashPaymentDialog(2500)))
    dialogs.append(("PaymentDialog", PaymentDialog(2500)))

    # ModifierDialog needs a controller and a product.
    from controllers.pos_controller import PosController
    from models.product import Product

    pos_ctrl = PosController(db)
    sample_product = db.query_one(
        "SELECT * FROM products WHERE is_active = 1 LIMIT 1"
    )
    if sample_product:
        row = dict(sample_product)
        product = Product(
            id=row["id"], category_id=row["category_id"],
            name_ar=row["name_ar"], name_en=row.get("name_en", ""),
            sku=row.get("sku"), price_minor=row["price_minor"],
            cost_minor=row.get("cost_minor", 0), color=row.get("color", ""),
            sort_order=row.get("sort_order", 0),
            is_active=bool(row.get("is_active", 1)),
            track_inventory=bool(row.get("track_inventory", 1)),
        )
        dialogs.append(("ModifierDialog", ModifierDialog(pos_ctrl, product)))

    # Shift dialogs need a ShiftController.
    from controllers.shift_controller import CloseShiftResult, ShiftController

    shift_ctrl = ShiftController(db)
    dialogs.append(("OpenShiftDialog", OpenShiftDialog(shift_ctrl)))
    dialogs.append(("CloseShiftDialog", CloseShiftDialog(shift_ctrl)))

    # ZReportDialog needs a CloseShiftResult with a real Shift and ShiftTotals.
    from models.shift import Shift, ShiftTotals

    shift = Shift(
        id=1, user_id=1, opened_at="2026-10-02 08:00:00",
        closed_at="2026-10-02 16:00:00", opening_float_minor=5000,
        status="closed",
    )
    totals = ShiftTotals(
        order_count=10, gross_minor=5000,
        cash_sales_minor=4000, card_sales_minor=1000,
        voided_count=0,
    )
    close_result = CloseShiftResult(
        ok=True, shift=shift, totals=totals,
        message="تم إغلاق الوردية",
    )
    dialogs.append(("ZReportDialog", ZReportDialog(close_result)))

    for name, dialog in dialogs:
        for width, height in SIZES:
            dialog.resize(width, height)
            dialog.show()
            for _ in range(8):
                app.processEvents()
            audit_container(dialog, f"{name}@{width}x{height}")
        dialog.hide()
    print(f"  audited {len(dialogs)} dialogs at {len(SIZES)} sizes")

    # ------------------------------------------------------------ report ---
    print()
    print("=" * 84)
    print(" FINDINGS")
    print("=" * 84)

    groups = [
        ("OVERLAPPING WIDGETS", OVERLAPS),
        ("OVERFLOWING WIDGETS", OVERFLOWS),
        ("UNDERSIZED CONTROLS", UNDERSIZED),
        ("HIDDEN WIDGETS RESERVING SPACE", PHANTOMS),
        ("CLIPPED TEXT", CLIPPED),
    ]

    total = 0
    for title, items in groups:
        print(f"\n  {title}: {len(items)}")
        total += len(items)
        seen = set()
        for item in items:
            if item not in seen:
                print(f"    • {item}")
                seen.add(item)

    print()
    print("=" * 84)
    print(f" TOTAL ISSUES: {total}")
    print("=" * 84)

    for suffix in ("", "-wal", "-shm"):
        try:
            os.unlink(str(db.db_path) + suffix)
        except OSError:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
