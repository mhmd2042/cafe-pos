"""
tests/render_screens.py — render the real UI to PNGs (offscreen) for review.

Useful for verifying layout, Arabic shaping, RTL direction and theme tokens
without a display. Writes into /tmp by default.

Run:  .venv/bin/python tests/render_screens.py [outdir]
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config                                                   # noqa: E402

_TMP_DB = Path(tempfile.mkdtemp(prefix="cafe_pos_shots_")) / "shots.db"
config.DB_PATH = _TMP_DB

from PyQt6.QtCore import Qt                                     # noqa: E402
from PyQt6.QtWidgets import QApplication                        # noqa: E402

from controllers.auth_controller import AuthController          # noqa: E402
from database.db_manager import get_db                          # noqa: E402
from views.main_window import MainWindow                        # noqa: E402
from views.theme import Theme                                   # noqa: E402


def shoot(window, app, path: Path) -> None:
    app.processEvents()
    pixmap = window.grab()
    pixmap.save(str(path))
    print(f"  {path}  ({path.stat().st_size:,} bytes, {pixmap.width()}x{pixmap.height()})")


def main() -> int:
    outdir = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp").expanduser()
    outdir.mkdir(parents=True, exist_ok=True)

    get_db()
    app = QApplication([sys.argv[0]])
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)

    for theme_name in ("dark", "light"):
        theme = Theme(theme_name).apply(app)
        auth = AuthController()
        window = MainWindow(auth, theme=theme)
        window.resize(config.WINDOW_MIN_WIDTH, config.WINDOW_MIN_HEIGHT)
        window.show()
        app.processEvents()

        print(f"{theme_name}:")
        shoot(window, app, outdir / f"login_{theme_name}.png")

        # logged-in views (skip the modal forced-PIN dialog for the render)
        view = window.login_view
        view._handle_forced_pin_change = lambda user: True
        view.select_user(next(c.user for c in view._cards if c.user.username == "admin"))
        for digit in "1234":
            view._on_digit(digit)
        view._submit()
        app.processEvents()
        shoot(window, app, outdir / f"admin_{theme_name}.png")

        window.logout()
        view.select_user(next(c.user for c in view._cards if c.user.username == "cashier"))
        view._handle_forced_pin_change = lambda user: True
        for digit in "1111":
            view._on_digit(digit)
        view._submit()
        app.processEvents()
        shoot(window, app, outdir / f"pos_{theme_name}.png")

        window.close()
        window.deleteLater()
        app.processEvents()

    # cold-start measurement: DB open + theme render + window build
    start = time.perf_counter()
    Theme("dark").apply(app)
    window = MainWindow(AuthController(), theme=Theme("dark"))
    window.resize(config.WINDOW_MIN_WIDTH, config.WINDOW_MIN_HEIGHT)
    window.show()
    app.processEvents()
    elapsed = time.perf_counter() - start
    budget = config.STARTUP_BUDGET_MS / 1000
    print(f"cold start: {elapsed * 1000:.0f} ms "
          f"({'within' if elapsed < budget else 'OVER'} {config.STARTUP_BUDGET_MS} ms budget)")
    return 0 if elapsed < budget else 1


if __name__ == "__main__":
    sys.exit(main())
