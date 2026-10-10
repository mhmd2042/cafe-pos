"""
views/admin/dashboard.py — the Admin hub.

A persistent navigation drawer on the reading-start side (right, in this RTL
app) plus a stacked page area. Pages are built lazily on first visit: the reports
page runs several aggregate queries, and the admin should not pay for that while
editing the menu.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import config
from controllers.admin_controller import AdminController
from views.admin.backup_view import BackupView
from views.admin.inventory_view import InventoryView
from views.admin.menu_editor import MenuEditorView
from views.admin.reports_view import ReportsView
from views.widgets import Divider, Toast

logger = logging.getLogger(__name__)

__all__ = ["AdminDashboard"]

# (key, label, builder) — the builder is called on first visit only.
PAGES = (
    ("reports", "التقارير والتحليلات"),
    ("menu", "القائمة والأسعار"),
    ("inventory", "المخزون والوصفات"),
    ("backup", "النسخ الاحتياطي"),
    ("settings", "الإعدادات"),
)


class AdminDashboard(QWidget):
    """Navigation drawer + stacked admin pages."""

    logout_requested = pyqtSignal()

    def __init__(self, auth, controller: AdminController | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.auth = auth
        self.controller = controller or AdminController(auth=auth)
        self._pages: dict[str, QWidget] = {}
        self._buttons: dict[str, QPushButton] = {}
        self._current: str | None = None

        self.setObjectName("AdminDashboard")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        # The dashboard needs enough height for the drawer's buttons plus the
        # page header. Without this, a short window compresses the layout and
        # the drawer card overflows its parent.
        self.setMinimumHeight(600)
        self._build()
        self.show_page("reports")

    # -- construction ------------------------------------------------------ #
    def _build(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(14)

        root.addWidget(self._build_drawer())
        root.addWidget(self._build_stack(), 1)

    def _build_drawer(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("Card")
        panel.setFixedWidth(248)
        # The drawer must never be squeezed below the height its buttons need.
        # Without this, a short window compresses the layout and the nav buttons
        # are painted on top of each other.
        panel.setMinimumHeight(5 * config.MIN_TOUCH_TARGET + 200)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 14, 12, 14)
        layout.setSpacing(8)
        # Prevent the layout from compressing its children below their minimum
        # size. Without this, a short window squeezes the nav buttons together
        # and they overlap.
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)

        title = QLabel("لوحة المدير")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)
        layout.addWidget(Divider())

        for key, label in PAGES:
            button = QPushButton(label)
            button.setObjectName("NavButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setMinimumHeight(config.MIN_TOUCH_TARGET)
            button.clicked.connect(lambda _=False, k=key: self.show_page(k))
            layout.addWidget(button)
            self._buttons[key] = button

        layout.addStretch(1)
        layout.addWidget(Divider())

        # Toast is an overlay, not a layout item — a hidden QFrame still reserves
        # its full size when it is in the layout, which was enough to push the
        # panel past the window and clip the keypad underneath it.
        self.toast = Toast(panel)
        self.toast.hide()

        refresh = QPushButton("تحديث البيانات")
        refresh.setObjectName("GhostAction")
        refresh.setMinimumHeight(config.MIN_TOUCH_TARGET)
        refresh.clicked.connect(self.refresh_current)
        layout.addWidget(refresh)

        exit_button = QPushButton("خروج")
        exit_button.setObjectName("DangerAction")
        exit_button.setMinimumHeight(config.MIN_TOUCH_TARGET)
        exit_button.clicked.connect(self.logout_requested.emit)
        layout.addWidget(exit_button)
        return panel

    def resizeEvent(self, a0) -> None:  # noqa: N802 - Qt naming
        """Keep the floating toast pinned across the bottom of the drawer."""
        super().resizeEvent(a0)
        if hasattr(self, "toast") and self.toast.parent() is not None:
            parent = self.toast.parentWidget()
            if parent is None:
                return
            margin = 16
            height = max(self.toast.sizeHint().height(), 44)
            self.toast.setGeometry(
                margin, parent.height() - height - margin,
                max(parent.width() - 2 * margin, 100), height,
            )
            self.toast.raise_()

    def _build_stack(self) -> QWidget:
        self.stack = QStackedWidget()
        return self.stack

    # -- pages ------------------------------------------------------------- #
    def _build_page(self, key: str) -> QWidget:
        if key == "reports":
            return ReportsView(self.auth, self.controller)
        if key == "menu":
            return MenuEditorView(self.auth, self.controller)
        if key == "inventory":
            return InventoryView(self.auth, self.controller)
        if key == "backup":
            return BackupView(self.auth, self.controller)
        if key == "settings":
            from views.admin.settings_view import SettingsView

            return SettingsView(self.auth, self.controller)
        raise KeyError(key)

    def show_page(self, key: str) -> QWidget | None:
        """Switch pages, building the target on first visit."""
        if key not in self._pages:
            try:
                page = self._build_page(key)
            except Exception as exc:
                logger.exception("تعذّر بناء صفحة %s", key)
                self.toast.show_message(f"تعذّر فتح الصفحة: {exc}", "error", 6000)
                return None
            self._pages[key] = page
            self.stack.addWidget(page)

        page = self._pages[key]
        self.stack.setCurrentWidget(page)
        self._current = key

        for page_key, button in self._buttons.items():
            button.setChecked(page_key == key)

        # Refresh on entry — an admin who just closed a shift expects the
        # reports page to reflect it.
        reload_method = getattr(page, "reload", None)
        if callable(reload_method):
            try:
                reload_method()
            except Exception:
                logger.exception("تعذّر تحديث صفحة %s", key)
        return page

    def refresh_current(self) -> None:
        if self._current is None:
            return
        page = self._pages.get(self._current)
        reload_method = getattr(page, "reload", None)
        if callable(reload_method):
            reload_method()
            self.toast.show_message("تم تحديث البيانات", "success", 2000)

    @property
    def current_page_key(self) -> str | None:
        return self._current
