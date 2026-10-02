"""
views/main_window.py — application shell (Phase 2).

Owns the window, the stacked pages and the header, and routes between login,
cashier and admin surfaces according to the authenticated role.

Phase 3 replaces the POS placeholder with views/cashier/pos_main.py;
Phase 5 replaces the admin placeholder with views/admin/dashboard.py.
"""

from __future__ import annotations

import logging
from datetime import datetime

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import config
from controllers.auth_controller import AuthController
from controllers.shift_controller import ShiftController
from models.user import ROLE_ADMIN, User
from views.admin.dashboard import AdminDashboard
from views.cashier.pos_main import PosMainView
from views.cashier.shift_dialog import CloseShiftDialog, OpenShiftDialog, ZReportDialog
from views.login_view import LoginView, PinChangeDialog

logger = logging.getLogger(__name__)

__all__ = ["MainWindow"]


class PlaceholderPage(QWidget):
    """Temporary Phase-2 stand-in for the POS / dashboard screens."""

    def __init__(self, title: str, lines: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(14)

        heading = QLabel(title)
        heading.setObjectName("BrandTitle")
        heading.setWordWrap(True)
        layout.addWidget(heading)

        for text in lines:
            line = QLabel(text)
            line.setObjectName("StatusText")
            line.setWordWrap(True)
            layout.addWidget(line)

        layout.addStretch(1)


class MainWindow(QMainWindow):
    """Top-level window: header + stacked pages, driven by AuthController."""

    logged_out = pyqtSignal()

    def __init__(self, auth: AuthController, theme=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.auth = auth
        self.theme = theme
        self.shift_controller = ShiftController(auth=auth)
        self._clock_timer: QTimer | None = None

        self.setWindowTitle(f"{config.APP_NAME} — {config.APP_NAME_AR}")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setMinimumSize(config.WINDOW_MIN_WIDTH, config.WINDOW_MIN_HEIGHT)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.header = self._build_header()
        self.header.setVisible(False)
        root.addWidget(self.header)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)

        self.login_view = LoginView(self.auth)
        self.login_view.logged_in.connect(self._on_logged_in)
        self.login_view.open_admin.connect(self._open_admin)
        self.login_view.open_cashier.connect(self._open_cashier)
        self.stack.addWidget(self.login_view)

        self.pos_page = PosMainView(self.auth)
        self.pos_page.checkout_completed.connect(self._on_checkout)
        self.pos_page.close_shift_requested.connect(self._close_shift)
        self.stack.addWidget(self.pos_page)

        self.admin_page = AdminDashboard(self.auth)
        self.admin_page.logout_requested.connect(self.logout)
        self.stack.addWidget(self.admin_page)

        self.status = self.statusBar()
        self.status.showMessage("جاهز — يعمل بدون إنترنت")

        self.stack.setCurrentWidget(self.login_view)
        self._start_clock()

    # -- header ------------------------------------------------------------ #
    def _build_header(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("Card")
        bar.setFixedHeight(86)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(20, 10, 20, 10)
        layout.setSpacing(14)

        self.avatar = QLabel("؟")
        self.avatar.setObjectName("Avatar")
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)

        identity = QVBoxLayout()
        identity.setSpacing(0)
        self.user_label = QLabel("")
        self.user_label.setObjectName("UserChip")
        self.role_badge = QLabel("")
        self.role_badge.setObjectName("RoleBadge")
        badge_row = QHBoxLayout()
        badge_row.setSpacing(8)
        badge_row.addWidget(self.role_badge)
        badge_row.addStretch(1)
        identity.addWidget(self.user_label)
        identity.addLayout(badge_row)

        self.header_clock = QLabel("")
        self.header_clock.setObjectName("ClockLabel")

        self.theme_button = QPushButton("تبديل المظهر")
        self.theme_button.setObjectName("GhostAction")
        self.theme_button.setMinimumHeight(52)
        self.theme_button.clicked.connect(self._toggle_theme)

        self.pin_button = QPushButton("تغيير الرمز")
        self.pin_button.setObjectName("GhostAction")
        self.pin_button.setMinimumHeight(52)
        self.pin_button.clicked.connect(self._change_pin)

        self.logout_button = QPushButton("خروج")
        self.logout_button.setObjectName("DangerAction")
        self.logout_button.setMinimumHeight(52)
        self.logout_button.clicked.connect(self.logout)

        layout.addWidget(self.avatar)
        layout.addLayout(identity, 1)
        layout.addWidget(self.header_clock)
        layout.addWidget(self.theme_button)
        layout.addWidget(self.pin_button)
        layout.addWidget(self.logout_button)
        return bar

    def _start_clock(self) -> None:
        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._tick)
        self._clock_timer.start(1000)
        self._tick()

    def _tick(self) -> None:
        self.header_clock.setText(datetime.now().strftime("%H:%M"))

    # -- routing ----------------------------------------------------------- #
    def _on_logged_in(self, user: User) -> None:
        self.avatar.setText(user.initials)
        self.user_label.setText(user.display_name)
        self.role_badge.setText(user.role_label_ar)
        self.header.setVisible(True)

    def _open_admin(self, user: User) -> None:
        self.stack.setCurrentWidget(self.admin_page)
        self.admin_page.refresh_current()
        self.status.showMessage(f"لوحة المدير — {user.display_name}")

    def _open_cashier(self, user: User) -> None:
        """Enter the till, forcing the open-shift step when required."""
        self.stack.setCurrentWidget(self.pos_page)
        self.pos_page.reload_menu()
        self.pos_page.refresh_cart()
        self._ensure_shift()
        self.status.showMessage(f"شاشة الكاشير — {user.display_name}")

    # -- shift gate -------------------------------------------------------- #
    def _ensure_shift(self) -> bool:
        """
        Make sure a shift is open before the cashier can sell.

        The spec makes this mandatory: no open shift, no selling. Closing the
        dialog without opening one sends the user back to the login screen
        rather than leaving a till that cannot take money.

        Kept as its own method (rather than inlined into _open_cashier) so a
        headless test can stub it — exec() blocks forever with no user present.
        """
        if not self.shift_controller.needs_open_shift():
            return True

        dialog = OpenShiftDialog(self.shift_controller, parent=self)
        if dialog.exec() == dialog.DialogCode.Accepted:
            shift = self.shift_controller.current_shift()
            if shift is not None:
                self.pos_page.set_shift(shift)
                self.status.showMessage(
                    f"وردية مفتوحة #{shift.id} — رصيد البداية "
                    f"{config.format_money(shift.opening_float_minor)}"
                )
            return True

        self.pos_page.toast.show_message(
            "لا يمكن البيع بدون وردية مفتوحة", "warning", 6000
        )
        self.logout()
        return False

    def _close_shift(self) -> None:
        dialog = CloseShiftDialog(self.shift_controller, parent=self)
        if dialog.exec() != dialog.DialogCode.Accepted or dialog.result is None:
            return

        result = dialog.result
        ZReportDialog(result, parent=self).exec()

        if result.should_logout:
            self.logout()
            self.status.showMessage(result.message, 10000)

    def _on_checkout(self, order) -> None:
        """Called after a completed sale — keeps the header/status in step."""
        self.status.showMessage(
            f"تم الطلب {order.order_number} — {order.total_label}", 8000
        )

    def logout(self) -> None:
        self.auth.logout()
        self.header.setVisible(False)
        self.login_view.reload_users()
        self.stack.setCurrentWidget(self.login_view)
        self.status.showMessage("تم تسجيل الخروج")
        self.logged_out.emit()

    def _change_pin(self) -> None:
        user = self.auth.current_user
        if user is None:
            return
        PinChangeDialog(self.auth, user, forced=False, parent=self).exec()

    def _toggle_theme(self) -> None:
        from views.theme import toggle_theme

        self.theme = toggle_theme(self.app, self.theme)

    # -- convenience ------------------------------------------------------- #
    @property
    def app(self):
        from PyQt6.QtWidgets import QApplication

        return QApplication.instance()
