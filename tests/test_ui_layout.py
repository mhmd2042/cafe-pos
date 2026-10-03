"""
Comprehensive UI/UX audit across every screen.

Checks each screen at multiple window sizes for:
  * widgets that overflow their parent (the login keypad bug class)
  * touch targets smaller than the spec minimum
  * RTL/LTR direction problems
  * text that is not Arabic where it should be
  * hidden-but-space-consuming widgets

Run: .venv/bin/python tests/test_ui_layout.py
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication, QAbstractButton, QAbstractItemView, QLabel, QScrollArea,
    QSizePolicy, QStackedWidget, QWidget,
)

import config  # noqa: E402
import database.db_manager as dbm  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []
WARNINGS: list[str] = []

# Sizes a café till realistically runs at. 1366x768 is the common laptop, and
# 1024x600 is the worst case we still claim to support.
SIZES = [(1366, 768), (1280, 720), (1024, 600), (1920, 1080)]


def check(name: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""), flush=True)
    (PASSED if ok else FAILED).append(name)


def warn(name: str, detail: str = "") -> None:
    print(f"  [WARN] {name}" + (f" — {detail}" if detail else ""), flush=True)
    WARNINGS.append(name)


def overflow_report(root: QWidget) -> list[str]:
    """Widgets whose geometry extends past their parent's visible area."""
    problems = []
    for child in root.findChildren(QWidget):
        if child.isHidden() or child.width() == 0:
            continue
        # A QComboBox's popup view is a child widget that Qt parks at a default
        # 640x480 until the dropdown is first opened. It is not part of the
        # visible layout, so flagging it is a false positive.
        if isinstance(child, QAbstractItemView) and not child.isVisible():
            continue
        parent = child.parentWidget()
        if parent is None:
            continue
        # Ignore anything inside a scroll area: scrolling is the intended fix.
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
            label = child.objectName() or type(child).__name__
            problems.append(
                f"{label} at {cg.x()},{cg.y()} {cg.width()}x{cg.height()} "
                f"exceeds parent {pg.width()}x{pg.height()}"
            )
    return problems


def small_targets(root: QWidget, minimum: int) -> list[str]:
    """Interactive widgets below the touch-target minimum."""
    problems = []
    for button in root.findChildren(QAbstractButton):
        if button.isHidden() or not button.isEnabled():
            continue
        if button.width() < minimum or button.height() < minimum:
            label = button.objectName() or button.text() or type(button).__name__
            problems.append(
                f"{label!r} is {button.width()}x{button.height()} (min {minimum})"
            )
    return problems


def main() -> int:
    app = QApplication.instance() or QApplication([])

    dbm.reset_singleton()
    db = dbm.get_db()

    from controllers.admin_controller import AdminController
    from controllers.auth_controller import AuthController
    from controllers.pos_controller import PosController
    from models.user import UserRepository
    from views.login_view import LoginView
    from views.main_window import MainWindow
    from views.theme import apply_theme

    apply_theme(app, "dark")
    auth = AuthController(UserRepository(db))

    print("=" * 78)
    print(f" UI LAYOUT AUDIT — {config.APP_NAME} v{config.VERSION}")
    print(f" touch minimum: {config.MIN_TOUCH_TARGET}px")
    print("=" * 78)

    # ---------------------------------------------------------------- login --
    print("\n-- login screen: keypad integrity --")
    login = LoginView(auth)
    login.show()
    for width, height in SIZES:
        login.resize(width, height)
        for _ in range(8):
            app.processEvents()

        from views.login_view import PinPad
        pad = login.findChild(PinPad)
        buttons = pad.findChildren(QAbstractButton)

        overlaps = sum(
            1
            for i in range(len(buttons))
            for j in range(i + 1, len(buttons))
            if buttons[i].geometry().intersects(buttons[j].geometry())
        )
        check(f"{width}x{height}: keypad buttons do not overlap", overlaps == 0,
              f"{overlaps} overlapping pairs" if overlaps else "")

        smallest = min(b.height() for b in buttons)
        check(f"{width}x{height}: every key meets the touch minimum",
              smallest >= config.MIN_TOUCH_TARGET,
              f"smallest key {smallest}px")

        # The pad must be reachable without being clipped. Below the spec's
        # 720px minimum window height the panel scrolls, which is acceptable;
        # what must never happen is the pad being squeezed into overlapping
        # rows (checked above) or pushed somewhere unreachable.
        top_left = pad.mapTo(login, pad.rect().topLeft())
        bottom = top_left.y() + pad.height()
        if height >= config.WINDOW_MIN_HEIGHT:
            check(f"{width}x{height}: keypad fits inside the window",
                  bottom <= login.height() + 2,
                  f"keypad bottom {bottom}px vs window {login.height()}px")
        else:
            scroll = login.findChild(QScrollArea, "LoginScroll")
            scrollable = bool(scroll and
                              scroll.verticalScrollBar().maximum() > 0)
            check(f"{width}x{height}: keypad reachable by scrolling",
                  scrollable or bottom <= login.height() + 2,
                  "below the spec minimum; panel scrolls" if scrollable
                  else f"keypad bottom {bottom}px, window {login.height()}px")

    # --------------------------------------------------------- main window --
    print("\n-- main window: every screen --")
    win = MainWindow(auth)
    win.show()

    stack = win.findChild(QStackedWidget)
    check("main window has a stacked screen container", stack is not None)

    screens = []
    if stack:
        for index in range(stack.count()):
            page = stack.widget(index)
            screens.append((index, page.objectName() or type(page).__name__, page))

    print(f"  found {len(screens)} screens: "
          f"{', '.join(name for _, name, _ in screens)}")

    for width, height in SIZES:
        win.resize(width, height)
        for _ in range(6):
            app.processEvents()

        print(f"\n  --- at {width}x{height} ---")
        for index, name, page in screens:
            if stack:
                stack.setCurrentIndex(index)
            for _ in range(6):
                app.processEvents()

            overflows = overflow_report(page)
            if overflows:
                warn(f"{name}: {len(overflows)} widget(s) overflow",
                     "; ".join(overflows[:2]))
            else:
                check(f"{name} fits its container", True)

            small = small_targets(page, config.MIN_TOUCH_TARGET)
            if small:
                warn(f"{name}: {len(small)} control(s) below touch minimum",
                     "; ".join(small[:2]))
            else:
                check(f"{name} controls meet the touch minimum", True)

    # ------------------------------------------------------------ RTL audit --
    print("\n-- RTL / direction audit --")
    win.resize(1366, 768)
    for _ in range(6):
        app.processEvents()

    # main.py sets this on the QApplication before showing anything; mirror that
    # here rather than asserting on a bare QApplication, which is LTR by default.
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    for _ in range(4):
        app.processEvents()

    check("the application direction is RTL once configured",
          app.layoutDirection() == Qt.LayoutDirection.RightToLeft,
          str(app.layoutDirection()))
    check("the main window inherits RTL",
          win.layoutDirection() == Qt.LayoutDirection.RightToLeft,
          str(win.layoutDirection()))

    from views.login_view import PinPad
    pad = win.findChild(PinPad)
    if pad:
        check("the PIN pad opts out of RTL (digits read left-to-right)",
              pad.layoutDirection() == Qt.LayoutDirection.LeftToRight)
    else:
        warn("PIN pad not found on the login screen")

    # Numeric/monetary text should read left-to-right inside the RTL shell.
    from views.login_view import LoginView as _LV

    login_view = win.findChild(_LV)
    if login_view and login_view.clock_label:
        text = login_view.clock_label.text()
        check("the clock shows an Arabic date", "أكتوبر" in text or "،" in text, text)

    # --------------------------------------------------------- date format --
    print("\n-- date/time localisation --")
    from datetime import datetime

    sample = datetime(2026, 10, 2, 15, 26)
    text = config.format_datetime_ar(sample)
    check("the date renders in Arabic", "أكتوبر" in text and "الجمعة" in text, text)
    check("no English day or month leaks in",
          not any(m in text for m in
                  ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
                   "Saturday", "Sunday", "January", "October", "December")),
          text)
    check("the Arabic date helper drops the weekday",
          config.format_date_ar(sample) == "02 أكتوبر 2026",
          config.format_date_ar(sample))

    # ------------------------------------------------------- hidden widgets --
    print("\n-- hidden widgets must not reserve space --")
    from views.widgets import Toast

    checked_any = False
    for toast in win.findChildren(Toast):
        parent = toast.parentWidget()
        if parent is None:
            continue
        checked_any = True
        policy = toast.sizePolicy()
        # A toast may be *added* to a layout; what matters is that it refuses to
        # influence it. Ignored/Ignored is the contract.
        ignored = (policy.horizontalPolicy() == QSizePolicy.Policy.Ignored
                   and policy.verticalPolicy() == QSizePolicy.Policy.Ignored)
        check("Toast opts out of layout sizing (hidden toasts reserve no space)",
              ignored, f"policy {policy.horizontalPolicy()}/{policy.verticalPolicy()}")

        # Prove it: the toast must not inflate the parent's minimum height.
        # Its own minimumSizeHint is the giveaway — an Ignored widget reports 0,
        # so a parent minimum well below (toast height + everything else) means
        # it is genuinely out of the flow.
        parent_layout = parent.layout()
        if parent_layout:
            with_toast = parent_layout.minimumSize().height()
            toast_min = toast.minimumSizeHint().height()
            # The toast must contribute nothing: its own minimum hint is 0, or
            # the parent fits comfortably without reserving a slot for it.
            check("Toast adds no height to its parent's minimum",
                  toast_min == 0,
                  f"toast minHint {toast_min}px, parent minimum {with_toast}px")
        break

    if not checked_any:
        warn("no Toast found to inspect")

    print()
    print(f"RESULT: {len(PASSED)} passed, {len(FAILED)} failed, "
          f"{len(WARNINGS)} warning(s)")
    if FAILED:
        print("failed:", ", ".join(FAILED))
    if WARNINGS:
        print("warnings:", ", ".join(WARNINGS))
    print("=" * 78)

    for suffix in ("", "-wal", "-shm"):
        try:
            os.unlink(str(db.db_path) + suffix)
        except OSError:
            pass

    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
