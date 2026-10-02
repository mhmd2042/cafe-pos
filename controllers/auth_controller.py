"""
controllers/auth_controller.py — login, session and permission glue.

Deliberately Qt-free: the view owns the widgets, this controller owns the rules,
so the whole login flow can be tested without a display.

Responsibilities
    * Authenticate a username + PIN (with a small in-memory throttle).
    * Hold the *current* session (who is logged in right now).
    * Enforce role capabilities and write an audit trail entry per login,
      failed attempt, PIN change and user edit.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import config
from models.user import (
    AuthError,
    InvalidPinError,
    PermissionDeniedError,
    User,
    UserExistsError,
    UserNotFoundError,
    UserRepository,
)

logger = logging.getLogger(__name__)

__all__ = ["AuthController", "LoginResult", "AttemptTracker"]

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 30


@dataclass(slots=True)
class LoginResult:
    """Outcome of a login attempt — everything the view needs to react."""

    success: bool
    user: User | None = None
    message: str = ""
    must_change_pin: bool = False
    locked_for: int = 0          # seconds remaining on a lockout
    attempts_left: int = MAX_FAILED_ATTEMPTS

    @property
    def failed(self) -> bool:
        return not self.success


class AttemptTracker:
    """In-memory failed-attempt counter with a short lockout (per username)."""

    def __init__(self, max_attempts: int = MAX_FAILED_ATTEMPTS, lockout: int = LOCKOUT_SECONDS):
        self.max_attempts = max_attempts
        self.lockout = lockout
        self._state: dict[str, list[Any]] = {}   # username -> [failures, locked_until]

    def locked_for(self, username: str) -> int:
        failures, locked_until = self._state.get(username, [0, 0.0])
        remaining = int(round(locked_until - time.monotonic()))
        return max(0, remaining)

    def register_failure(self, username: str) -> int:
        """Record a failure; returns attempts remaining before lockout."""
        failures, locked_until = self._state.get(username, [0, 0.0])
        failures += 1
        if failures >= self.max_attempts:
            locked_until = time.monotonic() + self.lockout
            failures = 0
        self._state[username] = [failures, locked_until]
        return max(0, self.max_attempts - failures)

    def reset(self, username: str) -> None:
        self._state.pop(username, None)

    def clear(self) -> None:
        self._state.clear()


class AuthController:
    """Session + permission controller for the whole application."""

    def __init__(self, repo: UserRepository | None = None) -> None:
        self.repo = repo or UserRepository()
        self._current_user: User | None = None
        self._login_at: float | None = None
        self.tracker = AttemptTracker()
        self.listeners: list[Any] = []      # callables(user|None) — optional observers

    # -- session ----------------------------------------------------------- #
    @property
    def current_user(self) -> User | None:
        return self._current_user

    @property
    def is_authenticated(self) -> bool:
        return self._current_user is not None

    @property
    def current_shift(self):
        """
        Phase-4 hook: the open shift of the current user, or None.
        Kept here so views never reach into the shift layer directly.
        """
        return None

    def _set_user(self, user: User | None) -> None:
        self._current_user = user
        self._login_at = time.monotonic() if user else None
        for listener in list(self.listeners):
            try:
                listener(user)
            except Exception:  # pragma: no cover - observers must not break login
                logger.exception("فشل مستمع تغيير الجلسة")

    def session_seconds(self) -> int:
        return int(time.monotonic() - self._login_at) if self._login_at else 0

    # -- login / logout ---------------------------------------------------- #
    def login(self, username: str, pin: str) -> LoginResult:
        username = str(username or "").strip()

        locked = self.tracker.locked_for(username)
        if locked:
            return LoginResult(
                success=False,
                message=f"تم إيقاف المحاولات — أعد المحاولة بعد {locked} ثانية",
                locked_for=locked,
            )

        try:
            user = self.repo.authenticate(username, pin)
        except AuthError as exc:
            attempts_left = self.tracker.register_failure(username)
            self.audit(None, "login_failed", "users", None, f"username={username}")
            message = str(exc)
            if attempts_left == 0:
                locked = self.tracker.locked_for(username)
                message = f"محاولات كثيرة خاطئة — انتظر {locked} ثانية"
            elif attempts_left <= 2:
                message = f"{message} — تبقّى {attempts_left} محاولة"
            return LoginResult(
                success=False,
                message=message,
                locked_for=self.tracker.locked_for(username),
                attempts_left=attempts_left,
            )

        self.tracker.reset(username)
        self.repo.touch_login(user.id)
        self._set_user(user)
        self.audit(user, "login", "users", user.id, f"role={user.role}")

        if user.must_change_pin:
            return LoginResult(
                success=True, user=user,
                message="يجب تغيير الرمز الافتراضي قبل المتابعة",
                must_change_pin=True,
            )
        return LoginResult(success=True, user=user, message=f"أهلاً {user.display_name}")

    def logout(self) -> None:
        if self._current_user is not None:
            self.audit(self._current_user, "logout", "users", self._current_user.id)
        self._set_user(None)

    # -- permissions ------------------------------------------------------- #
    def can(self, capability: str) -> bool:
        return bool(self._current_user and self._current_user.can(capability))

    def require(self, capability: str) -> User:
        """Raise PermissionDeniedError unless the current user may do this."""
        user = self._current_user
        if user is None:
            raise PermissionDeniedError("يجب تسجيل الدخول أولاً")
        user.require(capability)
        return user

    def require_role(self, *roles: str) -> User:
        user = self._current_user
        if user is None:
            raise PermissionDeniedError("يجب تسجيل الدخول أولاً")
        if roles and user.role not in roles:
            raise PermissionDeniedError(
                "هذه الشاشة متاحة لـ" + " أو ".join(roles) + " فقط"
            )
        return user

    # -- PIN management ---------------------------------------------------- #
    def change_pin(self, current_pin: str, new_pin: str) -> None:
        user = self._current_user
        if user is None:
            raise PermissionDeniedError("يجب تسجيل الدخول أولاً")
        self.repo.change_pin(user.id, current_pin, new_pin)
        self.audit(user, "pin_changed", "users", user.id)
        refreshed = self.repo.get_by_id(user.id)
        if refreshed is not None:
            self._set_user(refreshed)

    def reset_user_pin(self, user_id: int, new_pin: str) -> None:
        self.require("manage_users")
        self.repo.set_pin(user_id, new_pin, must_change=True)
        self.audit(self._current_user, "pin_reset", "users", user_id)

    # -- user administration (admin only) ---------------------------------- #
    def list_users(self, role: str | None = None, active_only: bool = False) -> list[User]:
        return self.repo.list_users(role=role, active_only=active_only)

    def create_user(self, username: str, full_name: str, role: str, pin: str) -> User:
        self.require("manage_users")
        user = self.repo.create(username, full_name, role, pin, must_change_pin=True)
        self.audit(self._current_user, "user_created", "users", user.id, f"role={role}")
        return user

    def set_user_active(self, user_id: int, active: bool) -> None:
        self.require("manage_users")
        if not active:
            target = self.repo.get_by_id(user_id)
            if target and target.is_admin and self.repo.count(config.ROLE_ADMIN) <= 1:
                raise AuthError("لا يمكن إيقاف آخر حساب مدير في النظام")
        self.repo.set_active(user_id, active)
        self.audit(self._current_user, "user_%s" % ("enabled" if active else "disabled"),
                   "users", user_id)

    # -- audit ------------------------------------------------------------- #
    def audit(self, user: User | None, action: str, entity: str = "",
              entity_id: int | None = None, details: str = "") -> None:
        """Best-effort audit write — never lets logging break a transaction."""
        try:
            self.repo.db.execute(
                """
                INSERT INTO audit_log (user_id, action, entity, entity_id, details)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user.id if user else None, action, entity, entity_id, details),
            )
        except Exception as exc:  # pragma: no cover
            logger.debug("تعذّر كتابة سجل التدقيق: %s", exc)
