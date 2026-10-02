"""
tests/test_phase2_ui.py — headless end-to-end check of the Phase-2 UI flow.

Runs with QT_QPA_PLATFORM=offscreen so it works over SSH / in CI with no display.
It drives the REAL widgets (real clicks, real stylesheet, real controllers) and
uses a throwaway database file so the live till data is never touched.

    1. login screen builds and lists the seeded users
    2. tapping the PIN pad with the correct PIN authenticates
    3. the forced-PIN-change dialog rejects a weak PIN and accepts a good one
    4. wrong PIN shows an error and clears the dots
    5. both themes render with no unresolved {{tokens}}
    6. logout returns to the login screen

Run:  .venv/bin/python tests/test_phase2_ui.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config                                                   # noqa: E402

# Point the app at a scratch database BEFORE anything imports db_manager.
_TMP_DB = Path(tempfile.mkdtemp(prefix="cafe_pos_test_")) / "test.db"
config.DB_PATH = _TMP_DB

from PyQt6.QtCore import Qt                                     # noqa: E402
from PyQt6.QtWidgets import QApplication, QPushButton           # noqa: E402

from controllers.auth_controller import AuthController          # noqa: E402
from database.db_manager import get_db                          # noqa: E402
from views.login_view import LoginView, PinChangeDialog         # noqa: E402
from views.main_window import MainWindow                        # noqa: E402
from views.theme import Theme                                   # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASSED if condition else FAILED).append(name)
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""), flush=True)


def tap_pin(view: LoginView, pin: str) -> None:
    """Press the keypad exactly as a finger would, then submit."""
    for digit in pin:
        view._on_digit(digit)
    view._submit()
    QApplication.processEvents()


def main() -> int:
    db = get_db()
    app = QApplication([sys.argv[0]])
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    theme = Theme("dark").apply(app)

    print("=" * 62)
    print(f" Phase 2 UI test — {config.APP_NAME} v{config.VERSION}")
    print(f" scratch DB: {_TMP_DB}")
    print("=" * 62)

    # 1 — login screen builds, users listed ------------------------------- #
    auth = AuthController()
    window = MainWindow(auth, theme=theme)
    # Phase 4: the till is gated behind a mandatory open-shift modal. Stub the
    # gate so this headless test never hits a blocking exec().
    window._ensure_shift = lambda: True
    window.resize(config.WINDOW_MIN_WIDTH, config.WINDOW_MIN_HEIGHT)
    window.show()
    app.processEvents()

    view = window.login_view
    check("login screen builds", view is not None)
    check("seeded users listed", len(view._cards) == 2,
          f"{[c.user.username for c in view._cards]}")
    check("first user auto-selected", view._selected is not None,
          view._selected.username if view._selected else "none")
    check("pin pad has 12 buttons",
          len([b for b in view.pad.findChildren(QPushButton)]) == 12)

    # 2 — wrong PIN -------------------------------------------------------- #
    view.select_user(next(c.user for c in view._cards if c.user.username == "admin"))
    tap_pin(view, "0000")
    check("wrong PIN rejected", auth.current_user is None and view.toast.isVisible(),
          view.toast.label.text())
    check("PIN cleared after failure", view._pin == "")

    # 3 — correct PIN: dots fill, then authentication ---------------------- #
    view.select_user(next(c.user for c in view._cards if c.user.username == "admin"))
    for digit in "1234":
        view._on_digit(digit)
    check("PIN dots filled by tapping", view.dots._dots[3].objectName() == "PinDotFilled")
    check("PIN length reached", len(view._pin) == config.PIN_LENGTH)

    # The view defers the real submit to a 120ms timer; call it explicitly.
    # Neutralise the modal forced-PIN dialog: this test drives it by hand below.
    view._handle_forced_pin_change = lambda user: True
    view._submit()
    app.processEvents()
    check("correct PIN authenticates", auth.current_user is not None,
          auth.current_user.username if auth.current_user else "none")
    check("seed account demands PIN change", auth.current_user.must_change_pin)
    check("login emits role routing", window.stack.currentWidget() is window.admin_page)

    # 4 — forced PIN change dialog ----------------------------------------- #
    user = auth.current_user
    dialog = PinChangeDialog(auth, user, forced=True)
    dialog.new_edit.setText("1111")           # rejected by policy
    dialog.confirm_edit.setText("1111")
    dialog._save()
    # NB: isVisible() is False for children of a never-shown dialog, so assert on
    # the toast's state/text instead of its visibility.
    check("weak PIN rejected by dialog",
          dialog.result() != 1 and dialog.toast.objectName() == "ToastError",
          dialog.toast.label.text())

    dialog.new_edit.setText("7291")           # acceptable
    dialog.confirm_edit.setText("7291")
    dialog._save()
    check("strong PIN accepted", dialog.result() == 1)
    refreshed = auth.repo.get_by_id(user.id)
    check("must_change_pin cleared", refreshed is not None and not refreshed.must_change_pin)

    # 5 — login with the new PIN, then logout ------------------------------ #
    window.logout()
    view.reload_users()
    view.select_user(next(c.user for c in view._cards if c.user.username == "admin"))
    view._handle_forced_pin_change = lambda user: True
    tap_pin(view, "7291")
    check("login with new PIN", auth.current_user is not None)
    check("no PIN change requested now",
          not (auth.current_user and auth.current_user.must_change_pin))
    window.logout()
    app.processEvents()
    check("logout returns to login screen", auth.current_user is None
          and window.stack.currentWidget() is view)
    check("header hidden after logout", not window.header.isVisible())

    # 6 — themes ----------------------------------------------------------- #
    for name in ("dark", "light"):
        rendered = Theme(name).qss
        check(f"{name} theme renders", len(rendered) > 2000, f"{len(rendered)} chars")
        check(f"{name} theme has no unresolved tokens", "{{" not in rendered)

    # 7 — role routing for the cashier ------------------------------------- #
    auth.logout()
    view.select_user(next(c.user for c in view._cards if c.user.username == "cashier"))
    view._handle_forced_pin_change = lambda user: True
    # Phase 4 gates the till behind a mandatory open-shift modal. Stub it here:
    # exec() would block forever with no user to click it.
    window._ensure_shift = lambda: True
    tap_pin(view, "1111")
    check("cashier routes to POS page", window.stack.currentWidget() is window.pos_page,
          f"user={auth.current_user.username if auth.current_user else None}")

    print()
    print(f"RESULT: {len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        print("failed:", ", ".join(FAILED))
    print("=" * 62)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
