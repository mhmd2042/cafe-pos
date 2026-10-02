"""
views/login_view.py — touch-friendly PIN pad login (Phase 2).

Layout (RTL):
    ┌──────────────────────┬──────────────────────────────────┐
    │  brand + clock +     │  selected user card              │
    │  shift status        │  ● ● ● ●   (PIN dots)            │
    │  user cards          │  1 2 3 / 4 5 6 / 7 8 9 / ⌫ 0 ✓  │
    └──────────────────────┴──────────────────────────────────┘

Design notes
    * Keys are 96x84 px — comfortably above the 60x60 minimum touch target.
    * The PIN is never displayed; only filled/empty dots.
    * Every key press gives haptic-style visual feedback via :pressed styling.
    * Login is keyboard-friendly too (digits, Backspace, Enter, Esc).
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import config
from controllers.auth_controller import AuthController
from models.user import ROLE_ADMIN, ROLE_CASHIER, ROLE_DESCRIPTIONS_AR, ROLE_LABELS_AR, User
from views.widgets import Toast

logger = logging.getLogger(__name__)

__all__ = ["LoginView", "PinChangeDialog"]


# --------------------------------------------------------------------------- #
# Small reusable widgets
# --------------------------------------------------------------------------- #
class PinDots(QWidget):
    """Row of filled/empty dots representing the entered PIN length."""

    def __init__(self, length: int = config.PIN_LENGTH, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._length = length
        self._dots: list[QLabel] = []

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addStretch(1)
        for _ in range(length):
            dot = QLabel()
            dot.setObjectName("PinDot")
            self._dots.append(dot)
            layout.addWidget(dot)
        layout.addStretch(1)

    def set_count(self, count: int, error: bool = False) -> None:
        for index, dot in enumerate(self._dots):
            if index < count:
                dot.setObjectName("PinDotError" if error else "PinDotFilled")
            else:
                dot.setObjectName("PinDot")
            dot.style().unpolish(dot)
            dot.style().polish(dot)

    @property
    def capacity(self) -> int:
        return self._length


class UserCard(QPushButton):
    """Selectable card for one till user (avatar + name + role badge)."""

    def __init__(self, user: User, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.user = user
        self.setCheckable(True)
        self.setMinimumHeight(84)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(12)

        avatar = QLabel(user.initials)
        avatar.setObjectName("Avatar")
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)

        text_box = QVBoxLayout()
        text_box.setSpacing(2)
        name = QLabel(user.display_name)
        name.setObjectName("UserChip")
        role = QLabel(user.role_label_ar)
        role.setObjectName("HintText")
        text_box.addWidget(name)
        text_box.addWidget(role)

        layout.addWidget(avatar)
        layout.addLayout(text_box, 1)
        self.setToolTip(f"{user.username} — {ROLE_DESCRIPTIONS_AR.get(user.role, '')}")


class Toast(QFrame):
    """
    Soft inline notice — used instead of modal message boxes during login.

    Superseded by views.widgets.Toast (re-exported here for backwards
    compatibility with anything importing it from this module).
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setVisible(False)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        self.label = QLabel("")
        self.label.setWordWrap(True)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.label, 1)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(lambda: self.setVisible(False))

    def show_message(self, text: str, kind: str = "info", timeout_ms: int = 4000) -> None:
        names = {
            "error": ("ToastError", "ErrorText"),
            "success": ("ToastSuccess", "SuccessText"),
            "warning": ("ToastWarning", "WarningText"),
            "info": ("Toast", "StatusText"),
        }
        frame_name, label_name = names.get(kind, names["info"])
        self.setObjectName(frame_name)
        self.label.setObjectName(label_name)
        self.style().unpolish(self)
        self.style().polish(self)
        self.label.setText(text)
        self.setVisible(True)
        if timeout_ms > 0:
            self._timer.start(timeout_ms)


# --------------------------------------------------------------------------- #
# PIN pad
# --------------------------------------------------------------------------- #
class PinPad(QWidget):
    """
    3x4 numeric keypad emitting digit / backspace / submit signals.

    Height is deliberately locked to what four rows of touch-sized keys actually
    need. Without that, Qt treats this as the one stretchable child of the login
    panel and shrinks it whenever the window is shorter than the panel's
    minimum — which silently collapses the rows onto each other and paints the
    middle digits out of existence. A keypad that overlaps itself is unusable,
    so it refuses to compress and lets the surrounding layout scroll instead.
    """

    digit_pressed = pyqtSignal(str)
    backspace_pressed = pyqtSignal()
    submitted = pyqtSignal()

    ROWS = 4
    COLUMNS = 3

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Digits are read left-to-right even in an Arabic UI (as on every phone
        # dialer), so this pad opts out of the app-wide RTL mirroring. Without
        # this the grid renders 3-2-1 and puts the confirm key on the far left.
        self.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        grid = QGridLayout(self)
        grid.setSpacing(14)
        grid.setContentsMargins(0, 0, 0, 0)

        keys = [("1", 0, 0), ("2", 0, 1), ("3", 0, 2),
                ("4", 1, 0), ("5", 1, 1), ("6", 1, 2),
                ("7", 2, 0), ("8", 2, 1), ("9", 2, 2)]

        for label, row, col in keys:
            button = QPushButton(label)
            button.setObjectName("PinKey")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, d=label: self.digit_pressed.emit(d))
            grid.addWidget(button, row, col)

        back = QPushButton("حذف")
        back.setObjectName("PinKeyBack")
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.clicked.connect(self.backspace_pressed.emit)
        back.setToolTip("حذف آخر رقم")
        grid.addWidget(back, 3, 0)

        zero = QPushButton("0")
        zero.setObjectName("PinKey")
        zero.setCursor(Qt.CursorShape.PointingHandCursor)
        zero.clicked.connect(lambda: self.digit_pressed.emit("0"))
        grid.addWidget(zero, 3, 1)

        # Words rather than symbols: ⌫ (U+232B) and ✓ (U+2713) are missing from
        # the Arabic font fallbacks and render as blank keys.
        submit = QPushButton("دخول")
        submit.setObjectName("PinKeyOk")
        submit.setCursor(Qt.CursorShape.PointingHandCursor)
        submit.clicked.connect(self.submitted.emit)
        submit.setToolTip("تأكيد الرمز")
        grid.addWidget(submit, 3, 2)

        for col in range(self.COLUMNS):
            grid.setColumnStretch(col, 1)
        for row in range(self.ROWS):
            grid.setRowStretch(row, 1)

        # Every key gets an explicit floor so the grid can never be squeezed to
        # something untappable.
        for button in self.findChildren(QPushButton):
            button.setMinimumSize(config.MIN_TOUCH_TARGET, config.MIN_TOUCH_TARGET)

        self._lock_height()

    def _lock_height(self) -> None:
        """
        Pin the pad to exactly the height its keys need.

        The number comes from the buttons' own minimumSizeHint rather than from
        MIN_TOUCH_TARGET: the stylesheet's min-height (84px) plus border and font
        metrics makes a key 110px tall in practice, and sizing the container off
        the 60px constant would leave the rows overlapping by 50px.
        """
        height = self.required_height()
        self.setMinimumHeight(height)
        self.setMaximumHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def required_height(self) -> int:
        """Height four rows of keys need, including the gaps between them."""
        buttons = self.findChildren(QPushButton)
        spacing = self.layout().spacing() if self.layout() else 14
        if buttons:
            key_height = max(b.minimumSizeHint().height() for b in buttons)
        else:  # pragma: no cover - only before the grid is built
            key_height = config.MIN_TOUCH_TARGET
        return self.ROWS * key_height + (self.ROWS - 1) * spacing

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        return QSize(430, self.required_height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        return QSize(300, self.required_height())


# --------------------------------------------------------------------------- #
# Login view
# --------------------------------------------------------------------------- #
class LoginView(QWidget):
    """Full-screen login surface. Emits `logged_in` once authentication succeeds."""

    logged_in = pyqtSignal(object)          # User
    open_admin = pyqtSignal(object)         # User (admin) -> dashboard
    open_cashier = pyqtSignal(object)       # User (cashier) -> POS
    exit_requested = pyqtSignal()

    def __init__(self, auth: AuthController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.auth = auth
        self._pin = ""
        self._selected: User | None = None
        self._cards: list[UserCard] = []

        self.setObjectName("LoginView")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self._build()
        self._wire()
        self.reload_users()
        self._start_clock()

    @staticmethod
    def _ltr(text: str) -> str:
        """
        Wrap Latin/digit runs in Unicode isolates (LRI…PDI).

        Without this, an RTL paragraph reorders them: "admin / 1234" renders as
        "1234 / admin" and "0.2.0" loses its leading zero on screen.
        """
        return f"\u2066{text}\u2069"

    # -- construction ------------------------------------------------------ #
    def _build(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(28, 28, 28, 28)
        root.setSpacing(24)

        root.addWidget(self._build_brand_panel(), 5)
        root.addWidget(self._build_keypad_panel(), 6)

    def _build_brand_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("Card")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(34, 34, 34, 34)
        layout.setSpacing(18)

        title = QLabel(config.APP_NAME_AR)
        title.setObjectName("BrandTitle")
        title.setWordWrap(True)

        subtitle = QLabel("بيع سريع • يعمل بدون إنترنت • نسخ احتياطي محلي")
        subtitle.setObjectName("BrandSubtitle")
        subtitle.setWordWrap(True)

        self.clock_label = QLabel("")
        self.clock_label.setObjectName("ClockLabel")

        self.shift_label = QLabel("")
        self.shift_label.setObjectName("WarningText")
        self.shift_label.setWordWrap(True)

        self.cafe_name_label = QLabel("")
        self.cafe_name_label.setObjectName("SectionTitle")

        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(6)
        layout.addWidget(self._divider())
        layout.addWidget(self.cafe_name_label)
        layout.addWidget(self.clock_label)
        layout.addWidget(self.shift_label)

        hint = QLabel(
            "اختر المستخدم ثم أدخل الرمز السري.\n"
            f"المستخدمون الافتراضيون: {self._ltr('admin / 1234')} — "
            f"{self._ltr('cashier / 1111')}\n"
            "سيُطلب تغيير الرمز عند أول تسجيل دخول."
        )
        hint.setObjectName("HintText")
        hint.setWordWrap(True)

        version = QLabel(
            f"الإصدار {self._ltr(config.VERSION)} — إصدار نهائي"
        )
        version.setObjectName("HintText")

        # This panel is the flexible half of the login screen: when the window is
        # short (a 768px laptop), it absorbs the shortfall by scrolling its own
        # reference text instead of squeezing the keypad. Keeping the keypad
        # fixed is the whole point — a half-visible number pad cannot be used to
        # log in.
        self.brand_scroll = QScrollArea()
        self.brand_scroll.setObjectName("BrandScroll")
        self.brand_scroll.setWidgetResizable(True)
        self.brand_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.brand_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.brand_scroll.setMinimumHeight(200)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(18)
        body_layout.addWidget(title)
        body_layout.addWidget(subtitle)
        body_layout.addSpacing(6)
        body_layout.addWidget(self._divider())
        body_layout.addWidget(self.cafe_name_label)
        body_layout.addWidget(self.clock_label)
        body_layout.addWidget(self.shift_label)
        body_layout.addStretch(1)
        body_layout.addWidget(hint)
        body_layout.addWidget(version)
        self.brand_scroll.setWidget(body)

        layout.addWidget(self.brand_scroll, 1)
        return panel

    def _build_keypad_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("Card")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)

        self.user_section_title = QLabel("من يعمل الآن؟")
        self.user_section_title.setObjectName("SectionTitle")

        self.user_scroll = QScrollArea()
        self.user_scroll.setWidgetResizable(True)
        self.user_scroll.setMinimumHeight(120)
        self.user_scroll.setMaximumHeight(230)
        self.user_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.user_container = QWidget()
        self.user_layout = QVBoxLayout(self.user_container)
        self.user_layout.setContentsMargins(0, 0, 0, 0)
        self.user_layout.setSpacing(10)
        self.user_layout.addStretch(1)
        self.user_scroll.setWidget(self.user_container)

        self.selected_label = QLabel("لم يتم اختيار مستخدم")
        self.selected_label.setObjectName("StatusText")
        self.selected_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.dots = PinDots(config.PIN_LENGTH)

        # The toast floats above the panel as an overlay rather than sitting in
        # the layout. In the flow it claimed its full wrapped height even while
        # hidden (480px in practice), which alone was enough to push the panel
        # past the window and clip the keypad.
        self.toast = Toast(panel)
        self.toast.hide()

        self.pad = PinPad()

        layout.addWidget(self.user_section_title)
        layout.addWidget(self.user_scroll, 1)   # the user list takes the slack
        layout.addWidget(self.selected_label)
        layout.addWidget(self.dots)
        # No stretch factor: the pad owns its height and must not be squeezed.
        layout.addWidget(self.pad)

        self.manager_button = QPushButton("دخول المدير")
        self.manager_button.setObjectName("GhostAction")
        layout.addWidget(self.manager_button)

        # Wrap the whole panel so that on a short screen it scrolls rather than
        # compressing anything to an unusable size. The spec asks for a 720px
        # minimum window, and a 768px laptop panel cannot fit everything at full
        # size — scrolling is the honest answer.
        scroller = QScrollArea()
        scroller.setObjectName("LoginScroll")
        scroller.setWidgetResizable(True)
        scroller.setFrameShape(QFrame.Shape.NoFrame)
        scroller.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroller.setWidget(panel)
        return scroller

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """Keep the floating toast pinned across the bottom of the panel."""
        super().resizeEvent(event)
        if hasattr(self, "toast") and self.toast.parent() is not None:
            parent = self.toast.parentWidget()
            margin = 16
            height = max(self.toast.sizeHint().height(), 44)
            self.toast.setGeometry(
                margin, parent.height() - height - margin,
                max(parent.width() - 2 * margin, 100), height,
            )
            self.toast.raise_()

    @staticmethod
    def _divider() -> QFrame:
        line = QFrame()
        line.setObjectName("Divider")
        line.setFixedHeight(1)
        return line

    # -- wiring ------------------------------------------------------------ #
    def _wire(self) -> None:
        self.pad.digit_pressed.connect(self._on_digit)
        self.pad.backspace_pressed.connect(self._on_backspace)
        self.pad.submitted.connect(self._submit)
        self.manager_button.clicked.connect(self._select_first_admin)

    def _start_clock(self) -> None:
        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._tick)
        self._clock_timer.start(1000)
        self._tick()

    def _tick(self) -> None:
        self.clock_label.setText(config.format_datetime_ar())
        try:
            self.cafe_name_label.setText(
                self.auth.repo.db.get_setting("cafe_name", config.APP_NAME_AR)
            )
        except Exception:
            self.cafe_name_label.setText(config.APP_NAME_AR)
        # Phase 4 replaces this with the real open-shift lookup.
        self.shift_label.setText("لا توجد وردية مفتوحة — سيُطلب رصيد البداية عند الدخول")

    # -- users ------------------------------------------------------------- #
    def reload_users(self, select: User | None = None) -> None:
        while self.user_layout.count() > 1:
            item = self.user_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._cards.clear()

        try:
            users = self.auth.list_users(active_only=True)
        except Exception as exc:  # pragma: no cover
            logger.exception("تعذّر تحميل المستخدمين")
            self.toast.show_message(f"تعذّر تحميل المستخدمين: {exc}", "error", 0)
            return

        if not users:
            self.toast.show_message("لا يوجد مستخدمون — أعد تهيئة قاعدة البيانات", "error", 0)
            return

        for user in users:
            card = UserCard(user)
            card.clicked.connect(lambda _=False, u=user: self.select_user(u))
            self.user_layout.insertWidget(self.user_layout.count() - 1, card)
            self._cards.append(card)

        self.select_user(select or users[0])

    def select_user(self, user: User) -> None:
        self._selected = user
        for card in self._cards:
            card.setChecked(card.user.id == user.id)
        self.selected_label.setText(f"أدخل رمز {user.display_name}")
        self._clear_pin()
        self.toast.setVisible(False)

    def _select_first_admin(self) -> None:
        admin = next((c.user for c in self._cards if c.user.role == ROLE_ADMIN), None)
        if admin is None:
            self.toast.show_message("لا يوجد حساب مدير في النظام", "warning")
            return
        self.select_user(admin)
        self.toast.show_message("حساب المدير جاهز — أدخل الرمز", "info", 2500)

    # -- PIN entry --------------------------------------------------------- #
    def _on_digit(self, digit: str) -> None:
        if self._selected is None:
            self.toast.show_message("اختر المستخدم أولاً", "warning", 2500)
            return
        if len(self._pin) >= config.PIN_MAX_LENGTH:
            return
        self._pin += digit
        self.dots.set_count(len(self._pin))
        if len(self._pin) >= config.PIN_LENGTH:
            QTimer.singleShot(120, self._submit)

    def _on_backspace(self) -> None:
        if self._pin:
            self._pin = self._pin[:-1]
            self.dots.set_count(len(self._pin))

    def _clear_pin(self) -> None:
        self._pin = ""
        self.dots.set_count(0)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 (Qt naming)
        text = event.text()
        if text.isdigit():
            self._on_digit(text)
        elif event.key() == Qt.Key.Key_Backspace:
            self._on_backspace()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._submit()
        elif event.key() == Qt.Key.Key_Escape:
            self._clear_pin()
        else:
            super().keyPressEvent(event)

    # -- submit ------------------------------------------------------------ #
    def _submit(self) -> None:
        if self._selected is None:
            self.toast.show_message("اختر المستخدم أولاً", "warning", 2500)
            return
        if len(self._pin) < config.PIN_MIN_LENGTH:
            self.toast.show_message("أدخل الرمز كاملاً", "warning", 2500)
            return

        result = self.auth.login(self._selected.username, self._pin)

        if result.failed:
            self._clear_pin()
            self.dots.set_count(config.PIN_LENGTH, error=True)
            QTimer.singleShot(700, lambda: self.dots.set_count(0))
            self.toast.show_message(result.message, "error", 5000)
            return

        user = result.user
        assert user is not None
        self._clear_pin()
        self.toast.show_message(result.message, "success", 2500)

        if result.must_change_pin and not self._handle_forced_pin_change(user):
            self.auth.logout()
            self.toast.show_message("تم إلغاء تغيير الرمز — لم يتم تسجيل الدخول", "warning")
            return

        self.logged_in.emit(user)
        if user.role == ROLE_ADMIN:
            self.open_admin.emit(user)
        else:
            self.open_cashier.emit(user)

    def _handle_forced_pin_change(self, user: User) -> bool:
        """
        Run the mandatory PIN change. Returns True when the PIN was replaced.

        Kept as a separate method so it can be overridden/injected — the modal
        exec() below must not be reachable from a headless test or a batch run.
        """
        dialog = PinChangeDialog(self.auth, user, forced=True, parent=self)
        return dialog.exec() == QDialog.DialogCode.Accepted


# --------------------------------------------------------------------------- #
# Forced / voluntary PIN change
# --------------------------------------------------------------------------- #
class PinChangeDialog(QDialog):
    """
    Two- or three-field PIN change.

    forced=True  → current PIN is already proven by the login, only the new PIN
                   is requested twice (the spec's "must change on first login").
    forced=False → asks for the current PIN as well.
    """

    def __init__(self, auth: AuthController, user: User, forced: bool = False,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.auth = auth
        self.user = user
        self.forced = forced

        self.setWindowTitle("تغيير الرمز السري")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setMinimumWidth(460)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 24)
        layout.setSpacing(14)

        title = QLabel("تغيير الرمز السري")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        if forced:
            note = QLabel(
                "هذا أول تسجيل دخول لهذا الحساب، ويجب تعيين رمز سري خاص قبل المتابعة."
            )
        else:
            note = QLabel(f"تغيير رمز: {user.display_name}")
        note.setObjectName("HintText")
        note.setWordWrap(True)
        layout.addWidget(note)

        self.current_edit = self._field(layout, "الرمز الحالي") if not forced else None
        self.new_edit = self._field(layout, "الرمز الجديد")
        self.confirm_edit = self._field(layout, "تأكيد الرمز الجديد")

        policy = QLabel(
            f"الرمز من {config.PIN_MIN_LENGTH} إلى {config.PIN_MAX_LENGTH} أرقام، "
            "ويجب ألا يكون رقماً مكرراً أو شائعاً."
        )
        policy.setObjectName("HintText")
        policy.setWordWrap(True)
        layout.addWidget(policy)

        self.toast = Toast()
        layout.addWidget(self.toast)

        buttons = QHBoxLayout()
        buttons.setSpacing(12)
        cancel = QPushButton("إلغاء")
        cancel.setObjectName("GhostAction")
        cancel.clicked.connect(self.reject)
        save = QPushButton("حفظ الرمز")
        save.setObjectName("PrimaryAction")
        save.setDefault(True)
        save.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(save, 1)
        layout.addLayout(buttons)

        (self.new_edit or self.current_edit).setFocus()

    @staticmethod
    def _field(layout: QVBoxLayout, label_text: str) -> QLineEdit:
        label = QLabel(label_text)
        label.setObjectName("FieldLabel")
        edit = QLineEdit()
        edit.setEchoMode(QLineEdit.EchoMode.Password)
        edit.setMaxLength(config.PIN_MAX_LENGTH)
        edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        edit.setPlaceholderText("••••")
        layout.addWidget(label)
        layout.addWidget(edit)
        return edit

    def _save(self) -> None:
        from models.user import InvalidPinError, pin_problem

        new_pin = self.new_edit.text()
        confirm = self.confirm_edit.text()

        problem = pin_problem(new_pin)
        if problem:
            self.toast.show_message(problem, "error", 5000)
            self.new_edit.selectAll()
            self.new_edit.setFocus()
            return
        if new_pin != confirm:
            self.toast.show_message("الرمزان غير متطابقين", "error", 5000)
            self.confirm_edit.clear()
            self.confirm_edit.setFocus()
            return

        try:
            if self.forced:
                self.auth.repo.set_pin(self.user.id, new_pin, must_change=False)
                self.auth.audit(self.user, "pin_changed_forced", "users", self.user.id)
                refreshed = self.auth.repo.get_by_id(self.user.id)
                if refreshed is not None:
                    self.auth._set_user(refreshed)
            else:
                self.auth.change_pin(self.current_edit.text(), new_pin)
        except InvalidPinError as exc:
            self.toast.show_message(str(exc), "error", 5000)
            if self.current_edit is not None:
                self.current_edit.selectAll()
                self.current_edit.setFocus()
            return
        except Exception as exc:  # pragma: no cover
            logger.exception("فشل تغيير الرمز")
            self.toast.show_message(f"تعذّر حفظ الرمز: {exc}", "error", 5000)
            return

        self.accept()
