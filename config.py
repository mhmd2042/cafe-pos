"""
config.py — Application configuration & constants for Bunney POS.

This module is deliberately dependency-free (stdlib only) so that every other
layer (database, models, controllers, views, services) can import it without
creating import cycles.

Key decisions recorded here:
  * MONEY is stored everywhere as INTEGER MINOR UNITS — the smallest amount the
    currency can express. For YER (no subunit in practice) that is a whole
    rial: 2,500 YER is stored as 2500. For a subdivided currency such as SAR
    set MINOR_UNITS_PER_MAJOR = 100 and 15.50 becomes 1550. This removes
    floating point rounding errors from an accounting application; use
    to_minor()/format_money() at the UI boundary only.
  * PRICES ARE TAX-INCLUSIVE. The price on a product row is what the customer
    pays. The embedded tax portion is derived for reporting only.
  * Everything is local: no network, no telemetry, no external API.
"""

from __future__ import annotations

import os
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

# --------------------------------------------------------------------------- #
# Application identity
# --------------------------------------------------------------------------- #
APP_NAME = "Bunney POS"
APP_NAME_AR = "نظام بُنّي للنقاط البيع"
APP_ID = "bunney_pos"
VERSION = "1.0.0"
PHASE = 5  # final phase — see README/plan

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
# Two different locations matter in a frozen (PyInstaller) build, and confusing
# them is the classic way to ship a bundle that crashes on launch:
#
#   BUNDLE_DIR  — where the *bundled read-only* files were extracted. For a
#                 onefile build this is a temporary directory that PyInstaller
#                 deletes when the app exits, so nothing writable may live here.
#   BASE_DIR    — the folder holding the executable. This is what the user sees
#                 and is stable across runs, so the database, backups and logs
#                 go here and survive upgrades.
if getattr(sys, "frozen", False):
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BUNDLE_DIR = Path(__file__).resolve().parent
    BASE_DIR = BUNDLE_DIR

# Read-only resources that ship inside the bundle.
ASSETS_DIR = BUNDLE_DIR / "assets"
STYLES_DIR = ASSETS_DIR / "styles"
ICONS_DIR = ASSETS_DIR / "icons"
SCHEMA_PATH = BUNDLE_DIR / "database" / "schema.sql"

# The logo is needed as a real file for QIcon, so prefer an on-disk copy next to
# the executable (the user can drop in their own) and fall back to the bundled one.
def _resolve_logo() -> Path:
    override = os.environ.get("BUNNEY_LOGO")
    if override:
        return Path(override)
    beside_exe = BASE_DIR / "assets" / "icons" / "logo.svg"
    if beside_exe.exists():
        return beside_exe
    return ICONS_DIR / "logo.svg"


LOGO_PATH = _resolve_logo()

# Writable state. The default is "beside the executable", which is what you want
# for a portable copy on a USB stick. But an *installed* copy lives somewhere the
# user cannot write (/opt for a .deb, C:\Program Files for an .exe, a read-only
# squashfs mount for an AppImage), so fall back to a per-user directory in that
# case. Detecting it here means every packaging format works without needing a
# wrapper script to set environment variables.
def _is_writable(directory: Path) -> bool:
    probe = directory / ".bunney_write_test"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe.write_text("", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _default_data_dir() -> Path:
    override = os.environ.get("BUNNEY_DATA_DIR")
    if override:
        return Path(override).expanduser()

    # Portable / development: keep everything next to the app.
    if _is_writable(BASE_DIR):
        return BASE_DIR

    # Installed: use the platform's per-user data location.
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
    else:
        xdg = os.environ.get("XDG_DATA_HOME")
        root = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return root / "bunney-pos"


APP_DATA_DIR = _default_data_dir()
DB_DIR = APP_DATA_DIR / "assets" / "database"
DB_PATH = Path(os.environ.get("BUNNEY_DB", DB_DIR / "bunney_pos.db"))
LOCAL_BACKUP_DIR = APP_DATA_DIR / "backups" / "local"
LOG_DIR = APP_DATA_DIR / "logs"
LOG_PATH = LOG_DIR / "bunney_pos.log"
RECEIPTS_DIR = LOG_DIR / "receipts"

# Default external backup target. Admin overrides this in Settings and the
# value is persisted in the `settings` table (key: backup_dir).
DEFAULT_EXTERNAL_BACKUP_DIR = ""

# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #
SCHEMA_VERSION = 4
SQLITE_BUSY_TIMEOUT_MS = 8000          # wait instead of failing on concurrent write
SQLITE_JOURNAL_MODE = "WAL"            # crash-safe + concurrent readers
SQLITE_SYNCHRONOUS = "NORMAL"          # safe with WAL, much faster than FULL
SQLITE_CACHE_SIZE_KB = -8000           # 8 MB page cache
BACKUP_KEEP_LOCAL = 30                 # how many local fallback copies to retain
BACKUP_FILENAME_TEMPLATE = "backup_{stamp}.db"

# --------------------------------------------------------------------------- #
# Business rules
# --------------------------------------------------------------------------- #
ROLE_ADMIN = "admin"
ROLE_CASHIER = "cashier"
ROLES = (ROLE_CASHIER, ROLE_ADMIN)

PIN_LENGTH = 4
PIN_MIN_LENGTH = 4
PIN_MAX_LENGTH = 8
PBKDF2_ITERATIONS = 200_000
PBKDF2_ALGORITHM = "sha256"

# Tax: configurable. Prices are tax-INCLUSIVE, so with a 0% rate the tax
# portion derived for reports is simply zero and the UI never shows a tax line.
DEFAULT_TAX_RATE = Decimal("0.00")
TAX_INCLUSIVE = True
CURRENCY_CODE = "YER"
CURRENCY_SYMBOL = "ر.ي"
# YER has no subunit in circulation: 1 minor unit == 1 rial, shown with no
# decimals. Set MINOR_UNITS_PER_MAJOR = 100 + MONEY_DECIMALS = 2 for SAR/AED/USD.
MONEY_DECIMALS = 0
MINOR_UNITS_PER_MAJOR = 1

ORDER_TYPE_DINE_IN = "dine_in"
ORDER_TYPE_TAKEAWAY = "takeaway"
ORDER_TYPES = (ORDER_TYPE_DINE_IN, ORDER_TYPE_TAKEAWAY)

ORDER_STATUS_OPEN = "open"
ORDER_STATUS_COMPLETED = "completed"
ORDER_STATUS_VOIDED = "voided"

PAYMENT_CASH = "cash"
PAYMENT_CARD = "card"
PAYMENT_METHODS = (PAYMENT_CASH, PAYMENT_CARD)

SHIFT_OPEN = "open"
SHIFT_CLOSED = "closed"

UNIT_GRAM = "g"
UNIT_MILLILITER = "ml"
UNIT_PIECE = "piece"
UNITS = (UNIT_GRAM, UNIT_MILLILITER, UNIT_PIECE)

STOCK_SALE = "sale"
STOCK_PURCHASE = "purchase"
STOCK_WASTE = "waste"
STOCK_ADJUSTMENT = "adjustment"
STOCK_RETURN = "return"
STOCK_REASONS = (STOCK_SALE, STOCK_PURCHASE, STOCK_WASTE, STOCK_ADJUSTMENT, STOCK_RETURN)

BACKUP_AUTO = "auto"
BACKUP_MANUAL = "manual"

# --------------------------------------------------------------------------- #
# UI / UX constants
# --------------------------------------------------------------------------- #
DEFAULT_LANGUAGE = "ar"
LAYOUT_DIRECTION = "rtl"          # native Arabic right-to-left layout
MIN_TOUCH_TARGET = 60             # px — spec: minimum 60x60 touch area
PRODUCT_TILE_MIN_WIDTH = 150
PRODUCT_TILE_MIN_HEIGHT = 96
WINDOW_MIN_WIDTH = 1180
WINDOW_MIN_HEIGHT = 720
STARTUP_BUDGET_MS = 3000          # spec: cold start under 3 seconds

# Arabic-first font stack with sane cross-platform fallbacks.
FONT_FAMILY = (
    "Noto Naskh Arabic, Cairo, Tajawal, Almarai, Segoe UI, "
    "DejaVu Sans, sans-serif"
)
FONT_FAMILY_MONO = "JetBrains Mono, Cascadia Mono, DejaVu Sans Mono, monospace"

# Warm, human-centred coffee palette (charcoal + warm white + caramel accent).
# Kept here so both QSS generation and chart colours share one source of truth.
THEME_DARK = {
    "bg": "#191512",
    "surface": "#221D19",
    "surface_alt": "#2B2521",
    "surface_raised": "#332C27",
    "border": "#3D352E",
    "border_strong": "#4E443B",
    "text": "#F6F0E7",
    "text_muted": "#B9AC9C",
    "text_disabled": "#7C7168",
    "accent": "#C98F4B",
    "accent_hover": "#DA9F5C",
    "accent_press": "#A87334",
    "accent_soft": "#3A2E20",
    "success": "#6E9C6B",
    "danger": "#C4593F",
    "warning": "#D9A441",
    "info": "#6C93A8",
    "shadow": "rgba(0, 0, 0, 0.45)",
    "overlay": "rgba(12, 10, 9, 0.72)",
}

THEME_LIGHT = {
    "bg": "#F7F2EA",
    "surface": "#FFFFFF",
    "surface_alt": "#F1E9DD",
    "surface_raised": "#FFFFFF",
    # Borders and muted text are darker than a "designer" light theme would use:
    # on a cream background the softer values dropped secondary text to ~3:1
    # contrast and left the product tiles looking floaty.
    "border": "#DCCEBB",
    "border_strong": "#C2AE94",
    "text": "#2A231D",
    "text_muted": "#5F5347",
    "text_disabled": "#A79B8C",
    "accent": "#B77A33",
    "accent_hover": "#A66C2A",
    "accent_press": "#8E5C22",
    "accent_soft": "#F5E7D3",
    "success": "#4F7F4C",
    "danger": "#B1472F",
    "warning": "#B8862C",
    "info": "#4C7387",
    "shadow": "rgba(60, 45, 30, 0.18)",
    "overlay": "rgba(40, 32, 24, 0.45)",
}

ACTIVE_THEME = "dark"

# --------------------------------------------------------------------------- #
# Runtime behaviour
# --------------------------------------------------------------------------- #
DEBUG = os.environ.get("CAFE_POS_DEBUG", "0") == "1"
LOG_LEVEL = "DEBUG" if DEBUG else "INFO"
ENABLE_ANIMATIONS = True
AUTO_LOCK_SECONDS = 0             # 0 = disabled; admin can set an idle lock later

# Receipt / printer (Phase 3)
RECEIPT_WIDTH_MM = 80
RECEIPT_WIDTH_CHARS_58MM = 32
RECEIPT_WIDTH_CHARS_80MM = 48
PRINTER_ENABLED_DEFAULT = False   # graceful fallback to file logging when absent
# Thermal head width in dots. Arabic cannot be printed with the printer's built-in
# font (no CP437 glyphs), so receipts are rendered as a 1-bit raster image.
RECEIPT_DOTS_58MM = 384
RECEIPT_DOTS_80MM = 576
RECEIPT_RENDER_DPI = 203
PRINTER_CONNECT_TIMEOUT_S = 4

# --------------------------------------------------------------------------- #
# Cashier POS layout (Phase 3)
# --------------------------------------------------------------------------- #
# The spec describes categories on the left and the cart on the right (written
# for an LTR UI). In an Arabic RTL interface the correct mirror puts the reading
# start — the category rail — on the RIGHT and the cart on the LEFT. Set this to
# False to force the literal LTR placement.
POS_LAYOUT_MIRROR_RTL = True
CATEGORY_RAIL_WIDTH = 236
CART_PANEL_WIDTH = 452
PRODUCT_GRID_SPACING = 12
# Quick-tender buttons in the cash dialog (whole rials).
QUICK_CASH_AMOUNTS = (500, 1000, 2000, 5000, 10000)
CART_MERGE_IDENTICAL_LINES = True   # tapping the same drink twice bumps quantity
ORDER_NUMBER_FORMAT = "{date}-{seq:04d}"


# --------------------------------------------------------------------------- #
# Helpers — money & small utilities
# --------------------------------------------------------------------------- #
def to_minor(amount) -> int:
    """
    Convert a human amount to integer minor units.

    With MINOR_UNITS_PER_MAJOR = 1 (YER) an int is already minor units and is
    returned unchanged, while 250.7 rounds to 251. With a subdivided currency
    (SAR, MINOR_UNITS_PER_MAJOR = 100) 15.50 becomes 1550.
    """
    if isinstance(amount, bool):
        raise TypeError("amount must be numeric, not bool")
    if isinstance(amount, int):
        return amount if MINOR_UNITS_PER_MAJOR == 1 else amount * MINOR_UNITS_PER_MAJOR
    return int(
        (Decimal(str(amount)) * MINOR_UNITS_PER_MAJOR).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )


def from_minor(minor: int) -> Decimal:
    """Convert integer minor units to a Decimal major amount."""
    value = Decimal(int(minor or 0)) / MINOR_UNITS_PER_MAJOR
    return value.quantize(Decimal(1).scaleb(-MONEY_DECIMALS))


def format_money(minor: int, *, symbol: bool = True, decimals: int | None = None) -> str:
    """Format minor units for display, e.g. 2500 -> '2,500 ر.ي'."""
    if decimals is None:
        decimals = MONEY_DECIMALS
    value = from_minor(minor)
    text = f"{value:,.{decimals}f}"
    return f"{text} {CURRENCY_SYMBOL}" if symbol else text


def format_quantity(qty: float, unit: str = "") -> str:
    """Compact human quantity: 18 g, 1.5 ml, 3 قطعة."""
    qty = float(qty or 0)
    text = f"{qty:g}"
    return f"{text} {unit}".strip()


def extract_tax_from_inclusive(total_minor: int, tax_rate: Decimal | None = None) -> int:
    """
    Return the tax portion already embedded in a tax-inclusive total.

    total = net * (1 + rate)  ->  tax = total - total / (1 + rate)
    """
    rate = Decimal(str(tax_rate if tax_rate is not None else DEFAULT_TAX_RATE))
    if rate <= 0:
        return 0
    total = Decimal(int(total_minor or 0))
    net = (total / (Decimal("1") + rate)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(total - net)


def net_from_inclusive(total_minor: int, tax_rate: Decimal | None = None) -> int:
    """Return the net (pre-tax) portion of a tax-inclusive total, in minor units."""
    return int(total_minor or 0) - extract_tax_from_inclusive(total_minor, tax_rate)


def timestamp_stamp(dt=None) -> str:
    """Filename-safe timestamp: 2026-10-02_2359."""
    from datetime import datetime as _dt

    return (dt or _dt.now()).strftime("%Y-%m-%d_%H%M")


def ensure_directories() -> None:
    """
    Create every runtime directory the app needs (idempotent).

    Directories that hold sales data (the database, backups, receipts, logs) are
    created owner-only. The default umask on many desktops is 0002, which would
    make a café's sales database and its backups world-readable on a shared
    machine — see secure_dir() for the reasoning.
    """
    for path in (ASSETS_DIR, STYLES_DIR, ICONS_DIR, LOG_DIR):
        Path(path).mkdir(parents=True, exist_ok=True)

    for private in (DB_DIR, LOCAL_BACKUP_DIR, RECEIPTS_DIR):
        secure_dir(private)


# --------------------------------------------------------------------------- #
# File permissions
# --------------------------------------------------------------------------- #
# Modes for anything that can contain sales data or credentials. 0600/0700 means
# only the account running the till can read them, which matters on a shared or
# kiosk machine where another local user could otherwise copy the SQLite file —
# including the PBKDF2 PIN hashes and salts — straight off disk.
PRIVATE_FILE_MODE = 0o600
PRIVATE_DIR_MODE = 0o700


def secure_file(path, mode: int = PRIVATE_FILE_MODE) -> None:
    """Restrict a file to its owner. Best-effort: never fatal, never raises."""
    try:
        target = Path(path)
        if target.exists():
            current = target.stat().st_mode & 0o777
            if current != mode:
                target.chmod(mode)
    except OSError:
        pass


def secure_dir(path, mode: int = PRIVATE_DIR_MODE) -> None:
    """Create (if needed) and restrict a directory to its owner."""
    try:
        target = Path(path)
        target.mkdir(parents=True, exist_ok=True)
        target.chmod(mode)
    except OSError:
        pass


def secure_sqlite_sidecars(db_path) -> None:
    """Restrict the -wal and -shm files that accompany a WAL-mode database."""
    for suffix in ("", "-wal", "-shm", "-journal"):
        secure_file(Path(str(db_path) + suffix))
