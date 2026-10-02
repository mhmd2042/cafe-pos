"""
models/user.py — Users, roles, capabilities and PIN authentication.

Phase 1 shipped the PIN primitives (needed to seed the default users).
Phase 2 completes the model: the `User` entity, the capability map that
enforces the two-role spec (cashier may sell, admin may administer), and the
`UserRepository` that owns every read/write on the `users` table.

No Qt imports here on purpose: this layer must stay testable headlessly.

PINs are never stored in plain text — PBKDF2-HMAC-SHA256, per-user random salt,
200k iterations, constant-time comparison.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

import config

logger = logging.getLogger(__name__)

__all__ = [
    "User",
    "UserRepository",
    "AuthError",
    "InvalidPinError",
    "UserNotFoundError",
    "UserExistsError",
    "PermissionDeniedError",
    "CAPABILITIES",
    "ROLE_LABELS_AR",
    "ROLE_DESCRIPTIONS_AR",
    "hash_pin",
    "verify_pin",
    "is_valid_pin",
    "generate_pin",
    "normalize_pin",
    "ROLE_ADMIN",
    "ROLE_CASHIER",
]

ROLE_ADMIN = config.ROLE_ADMIN
ROLE_CASHIER = config.ROLE_CASHIER

ROLE_LABELS_AR = {ROLE_ADMIN: "المدير", ROLE_CASHIER: "الكاشير"}
ROLE_DESCRIPTIONS_AR = {
    ROLE_ADMIN: "كل الصلاحيات: القائمة، التقارير، المخزون، النسخ الاحتياطي",
    ROLE_CASHIER: "البيع فقط: إنشاء الطلبات، تحصيل الدفع، إدارة الوردية",
}

# --------------------------------------------------------------------------- #
# Capabilities — the single place the spec's two-role rules are expressed.
# Views ask user.can("edit_prices") instead of comparing role strings, so a
# future third role only needs a new entry here.
# --------------------------------------------------------------------------- #
CAPABILITIES: dict[str, frozenset[str]] = {
    # Selling
    "create_order":       frozenset({ROLE_CASHIER, ROLE_ADMIN}),
    "take_payment":       frozenset({ROLE_CASHIER, ROLE_ADMIN}),
    "apply_discount":     frozenset({ROLE_ADMIN}),
    "void_order":         frozenset({ROLE_ADMIN}),
    "open_shift":         frozenset({ROLE_CASHIER, ROLE_ADMIN}),
    "close_shift":        frozenset({ROLE_CASHIER, ROLE_ADMIN}),
    # Menu & pricing
    "edit_prices":        frozenset({ROLE_ADMIN}),
    "manage_menu":        frozenset({ROLE_ADMIN}),
    "manage_modifiers":   frozenset({ROLE_ADMIN}),
    # Insight
    "view_reports":       frozenset({ROLE_ADMIN}),
    "view_profit":        frozenset({ROLE_ADMIN}),
    "view_shift_audit":   frozenset({ROLE_ADMIN}),
    # Inventory
    "manage_inventory":   frozenset({ROLE_ADMIN}),
    # System
    "manage_backups":     frozenset({ROLE_ADMIN}),
    "manage_users":       frozenset({ROLE_ADMIN}),
    "manage_settings":    frozenset({ROLE_ADMIN}),
}


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #
class AuthError(Exception):
    """Base class for authentication/authorisation failures (message is Arabic)."""


class InvalidPinError(AuthError):
    pass


class UserNotFoundError(AuthError):
    pass


class UserExistsError(AuthError):
    pass


class PermissionDeniedError(AuthError):
    pass


# --------------------------------------------------------------------------- #
# PIN primitives
# --------------------------------------------------------------------------- #
def normalize_pin(pin: str) -> str:
    """Digits only, trimmed — tolerates Arabic-Indic digits typed on a keypad."""
    pin = str(pin or "").strip()
    translated = pin.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    return "".join(ch for ch in translated if ch.isdigit())


def _digest(pin: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        config.PBKDF2_ALGORITHM,
        pin.encode("utf-8"),
        salt.encode("utf-8"),
        config.PBKDF2_ITERATIONS,
    ).hex()


def hash_pin(pin: str, salt: str | None = None) -> tuple[str, str]:
    """Hash a PIN. Returns (pin_hash, pin_salt)."""
    pin = normalize_pin(pin)
    salt = salt or secrets.token_hex(16)
    return _digest(pin, salt), salt


def verify_pin(pin: str, pin_hash: str, pin_salt: str) -> bool:
    """Constant-time verification of a PIN against a stored hash+salt."""
    if not pin or not pin_hash or not pin_salt:
        return False
    try:
        candidate = _digest(normalize_pin(pin), pin_salt)
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(candidate, pin_hash)


def is_valid_pin(pin: str) -> bool:
    """True when the PIN matches the configured length policy."""
    digits = normalize_pin(pin)
    return config.PIN_MIN_LENGTH <= len(digits) <= config.PIN_MAX_LENGTH


def pin_problem(pin: str) -> str:
    """Human-readable Arabic reason a PIN is unacceptable, or '' when it is fine."""
    digits = normalize_pin(pin)
    if len(digits) < config.PIN_MIN_LENGTH:
        return f"الرمز قصير جداً — {config.PIN_MIN_LENGTH} أرقام على الأقل"
    if len(digits) > config.PIN_MAX_LENGTH:
        return f"الرمز طويل جداً — {config.PIN_MAX_LENGTH} أرقام كحد أقصى"
    if len(set(digits)) == 1:
        return "لا يمكن استخدام رقم مكرر (مثل 1111)"
    if digits in {"0123", "1234", "4321", "9876", "0123456789"[: len(digits)]}:
        return "هذا الرمز شائع جداً — اختر رمزاً آخر"
    return ""


def generate_pin(length: int = config.PIN_LENGTH) -> str:
    """Random numeric PIN (for seeding / password resets)."""
    return "".join(secrets.choice("0123456789") for _ in range(length))


# --------------------------------------------------------------------------- #
# User entity
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class User:
    id: int
    username: str
    full_name: str
    role: str
    is_active: bool = True
    must_change_pin: bool = False
    last_login_at: str | None = None
    created_at: str | None = None
    pin_hash: str = field(default="", repr=False)
    pin_salt: str = field(default="", repr=False)

    # -- identity ---------------------------------------------------------- #
    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    @property
    def is_cashier(self) -> bool:
        return self.role == ROLE_CASHIER

    @property
    def role_label_ar(self) -> str:
        return ROLE_LABELS_AR.get(self.role, self.role)

    @property
    def display_name(self) -> str:
        return self.full_name or self.username

    @property
    def initials(self) -> str:
        """
        One character for the avatar circle. Taken from the username (distinct
        per account) rather than the display name, because both default Arabic
        names start with the same letter.
        """
        source = (self.username or self.display_name).strip()
        return source[0].upper() if source else "؟"

    # -- permissions ------------------------------------------------------- #
    def can(self, capability: str) -> bool:
        allowed = CAPABILITIES.get(capability)
        return bool(allowed and self.role in allowed)

    def require(self, capability: str) -> None:
        if not self.can(capability):
            raise PermissionDeniedError(
                f"صلاحية «{capability}» غير متاحة لدور {self.role_label_ar}"
            )

    # -- persistence ------------------------------------------------------- #
    @classmethod
    def from_row(cls, row: Any) -> "User":
        keys = row.keys() if hasattr(row, "keys") else []
        get = lambda key, default=None: (row[key] if key in keys else default)  # noqa: E731
        return cls(
            id=int(row["id"]),
            username=str(row["username"]),
            full_name=str(row["full_name"] or ""),
            role=str(row["role"]),
            is_active=bool(get("is_active", 1)),
            must_change_pin=bool(get("must_change_pin", 0)),
            last_login_at=get("last_login_at"),
            created_at=get("created_at"),
            pin_hash=str(get("pin_hash", "") or ""),
            pin_salt=str(get("pin_salt", "") or ""),
        )

    def verify(self, pin: str) -> bool:
        return verify_pin(pin, self.pin_hash, self.pin_salt)

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.display_name} ({self.role_label_ar})"


# --------------------------------------------------------------------------- #
# Repository
# --------------------------------------------------------------------------- #
DEFAULT_USERS: tuple[tuple[str, str, str, str], ...] = (
    ("admin", "المدير", ROLE_ADMIN, "1234"),
    ("cashier", "الكاشير", ROLE_CASHIER, "1111"),
)

_SELECT = (
    "SELECT id, username, full_name, role, pin_hash, pin_salt, is_active, "
    "must_change_pin, last_login_at, created_at FROM users"
)


class UserRepository:
    """Every read/write on the `users` table lives here."""

    def __init__(self, db: Any = None) -> None:
        self._db = db

    @property
    def db(self):
        if self._db is None:
            from database.db_manager import get_db

            self._db = get_db()
        return self._db

    # -- reads ------------------------------------------------------------- #
    def get_by_id(self, user_id: int) -> User | None:
        row = self.db.query_one(f"{_SELECT} WHERE id = ?", (int(user_id),))
        return User.from_row(row) if row else None

    def get_by_username(self, username: str) -> User | None:
        row = self.db.query_one(
            f"{_SELECT} WHERE username = ? COLLATE NOCASE", (str(username).strip(),)
        )
        return User.from_row(row) if row else None

    def list_users(self, role: str | None = None, active_only: bool = False) -> list[User]:
        sql, params = _SELECT, []
        clauses = []
        if role:
            clauses.append("role = ?")
            params.append(role)
        if active_only:
            clauses.append("is_active = 1")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY role = 'admin' DESC, full_name COLLATE NOCASE"
        return [User.from_row(r) for r in self.db.query_all(sql, tuple(params))]

    def count(self, role: str | None = None) -> int:
        if role:
            return self.db.scalar("SELECT COUNT(*) FROM users WHERE role = ?", (role,))
        return self.db.scalar("SELECT COUNT(*) FROM users")

    def has_admin(self) -> bool:
        return self.count(ROLE_ADMIN) > 0

    # -- writes ------------------------------------------------------------ #
    def create(
        self,
        username: str,
        full_name: str,
        role: str,
        pin: str,
        *,
        is_active: bool = True,
        must_change_pin: bool = False,
        enforce_policy: bool = True,
    ) -> User:
        """
        Create a user. `enforce_policy=False` is reserved for the seeded default
        accounts (admin/1234, cashier/1111): their PINs are deliberately weak and
        must_change_pin guarantees they are replaced at first login. Never pass
        False for a PIN a human chose.
        """
        username = str(username or "").strip()
        if not username:
            raise AuthError("اسم المستخدم مطلوب")
        if role not in config.ROLES:
            raise AuthError(f"دور غير معروف: {role}")
        problem = pin_problem(pin) if enforce_policy else ""
        if problem:
            raise InvalidPinError(problem)
        if self.get_by_username(username):
            raise UserExistsError(f"اسم المستخدم «{username}» مستخدم بالفعل")

        pin_hash, pin_salt = hash_pin(pin)
        user_id = self.db.insert(
            """
            INSERT INTO users (username, full_name, role, pin_hash, pin_salt,
                               is_active, must_change_pin)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (username, full_name or username, role, pin_hash, pin_salt,
             int(bool(is_active)), int(bool(must_change_pin))),
        )
        logger.info("تم إنشاء مستخدم: %s (%s)", username, role)
        user = self.get_by_id(user_id)
        assert user is not None
        return user

    def set_pin(self, user_id: int, pin: str, *, must_change: bool = False,
                enforce_policy: bool = True) -> None:
        problem = pin_problem(pin) if enforce_policy else ""
        if problem:
            raise InvalidPinError(problem)
        pin_hash, pin_salt = hash_pin(pin)
        self.db.execute(
            """
            UPDATE users
               SET pin_hash = ?, pin_salt = ?, must_change_pin = ?,
                   updated_at = datetime('now', 'localtime')
             WHERE id = ?
            """,
            (pin_hash, pin_salt, int(bool(must_change)), int(user_id)),
        )

    def set_active(self, user_id: int, active: bool) -> None:
        self.db.execute(
            "UPDATE users SET is_active = ?, updated_at = datetime('now', 'localtime') WHERE id = ?",
            (int(bool(active)), int(user_id)),
        )

    def touch_login(self, user_id: int) -> None:
        self.db.execute(
            "UPDATE users SET last_login_at = datetime('now', 'localtime') WHERE id = ?",
            (int(user_id),),
        )

    def change_pin(self, user_id: int, current_pin: str, new_pin: str) -> None:
        user = self.get_by_id(user_id)
        if user is None:
            raise UserNotFoundError("المستخدم غير موجود")
        if not user.verify(current_pin):
            raise InvalidPinError("الرمز الحالي غير صحيح")
        if current_pin == new_pin:
            raise InvalidPinError("الرمز الجديد مطابق للقديم")
        self.set_pin(user_id, new_pin, must_change=False)

    # -- authentication ---------------------------------------------------- #
    def authenticate(self, username: str, pin: str) -> User:
        """
        Verify credentials.

        Failure messages are deliberately generic (no user enumeration) and a
        disabled account is rejected with its own clear message so the cashier
        knows to call the manager rather than retrying the PIN.
        """
        user = self.get_by_username(username)
        if user is None or not user.verify(pin):
            raise AuthError("اسم المستخدم أو الرمز غير صحيح")
        if not user.is_active:
            raise AuthError("هذا الحساب موقوف — راجع المدير")
        return user

    def authenticate_by_id(self, user_id: int, pin: str) -> User:
        user = self.get_by_id(user_id)
        if user is None or not user.verify(pin):
            raise AuthError("الرمز غير صحيح")
        if not user.is_active:
            raise AuthError("هذا الحساب موقوف — راجع المدير")
        return user

    # -- seeding ----------------------------------------------------------- #
    def ensure_default_users(self) -> int:
        """
        Create the default admin + cashier only when the table is empty.
        They are flagged must_change_pin so the first login forces a new PIN.
        Returns how many users were created.
        """
        if self.count() > 0:
            return 0
        created = 0
        with self.db.transaction():
            for username, full_name, role, pin in DEFAULT_USERS:
                self.create(username, full_name, role, pin, must_change_pin=True,
                            enforce_policy=False)
                created += 1
        logger.warning(
            "تم إنشاء المستخدمين الافتراضيين (admin/1234, cashier/1111) — يجب تغيير الرموز"
        )
        return created
