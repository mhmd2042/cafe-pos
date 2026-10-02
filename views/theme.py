"""
views/theme.py — theme loading & application.

Single source of truth for colours is config.THEME_DARK / config.THEME_LIGHT.
The QSS files in assets/styles/ are templates containing {{token}} placeholders;
this module substitutes them so a palette change never has to be made twice.

Usage (from main.py):
    from views.theme import apply_theme
    theme = apply_theme(app, "dark")
    ...
    theme = toggle_theme(app, theme)
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import config

logger = logging.getLogger(__name__)

__all__ = ["Theme", "apply_theme", "toggle_theme", "available_themes", "PaletteProxy",
           "app_icon", "apply_app_icon"]

_QT_IMPORT_ERROR: Exception | None = None
try:  # Qt is only needed by the UI layer
    from PyQt6.QtGui import QFont, QFontDatabase  # noqa: F401
    from PyQt6.QtWidgets import QApplication
except Exception as exc:  # pragma: no cover - exercised only without PyQt6
    _QT_IMPORT_ERROR = exc


class Theme:
    """A resolved theme: name, palette dict and rendered stylesheet."""

    def __init__(self, name: str = "dark") -> None:
        self.name = name if name in ("dark", "light") else "dark"
        self.tokens: dict[str, str] = dict(
            config.THEME_DARK if self.name == "dark" else config.THEME_LIGHT
        )
        self.tokens["font"] = config.FONT_FAMILY
        self.tokens["font_mono"] = config.FONT_FAMILY_MONO
        self.qss = self._render()

    # -- internals --------------------------------------------------------- #

    def _render(self) -> str:
        path = config.STYLES_DIR / f"cafe_{self.name}.qss"
        if not path.exists():
            logger.warning("ملف التنسيق غير موجود: %s", path)
            return ""
        text = path.read_text(encoding="utf-8")
        for key, value in self.tokens.items():
            text = text.replace("{{" + key + "}}", str(value))

        # A leftover token means a typo in the .qss or a colour missing from the
        # palette; Qt would silently ignore the whole declaration, so surface it.
        leftover = sorted(set(re.findall(r"\{\{(\w+)\}\}", text)))
        if leftover:
            logger.warning("رموز غير معروفة في %s: %s", path.name, ", ".join(leftover))
            for token in leftover:
                text = text.replace("{{" + token + "}}", "")
        return text

    def color(self, token: str, default: str = "#000000") -> str:
        return self.tokens.get(token, default)

    # -- application ------------------------------------------------------- #

    def apply(self, app) -> "Theme":
        """Apply this theme to a QApplication (and remember the choice)."""
        if app is None:
            raise RuntimeError("apply() needs a QApplication instance")
        app.setStyleSheet(self.qss)
        _apply_font(app)
        config.ACTIVE_THEME = self.name
        logger.info("تم تطبيق المظهر: %s", self.name)
        return self

    @property
    def is_dark(self) -> bool:
        return self.name == "dark"

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Theme {self.name}>"


def _apply_font(app) -> None:
    """Pick the first installed font from the Arabic-first stack."""
    try:
        families = set(QFontDatabase.families())
    except Exception:  # pragma: no cover
        families = set()

    wanted = [f.strip() for f in config.FONT_FAMILY.split(",") if f.strip()]
    chosen = next((name for name in wanted if name in families), None)

    font = QFont(chosen) if chosen else QFont()
    font.setPointSize(11)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    app.setFont(font)
    if chosen:
        logger.debug("الخط المستخدم: %s", chosen)


def available_themes() -> list[str]:
    return ["dark", "light"]


def apply_theme(app, name: str | None = None) -> Theme:
    """Load and apply a theme; falls back to the configured default."""
    if _QT_IMPORT_ERROR is not None:
        raise RuntimeError(f"PyQt6 غير متاح: {_QT_IMPORT_ERROR}")
    return Theme(name or config.ACTIVE_THEME).apply(app)


def toggle_theme(app, current: Theme | str | None) -> Theme:
    """Flip between dark and light and persist the choice in the settings table."""
    name = getattr(current, "name", current) or config.ACTIVE_THEME
    new_name = "light" if name == "dark" else "dark"
    theme = Theme(new_name).apply(app)
    try:
        from database.db_manager import get_db

        get_db().set_setting("theme", new_name)
    except Exception as exc:  # pragma: no cover - settings are best-effort here
        logger.debug("تعذّر حفظ المظهر: %s", exc)
    return theme


class PaletteProxy:
    """Late-bound access to the active palette (for views built before theming)."""

    def __init__(self, theme: Theme) -> None:
        self._theme = theme

    def __getitem__(self, token: str) -> str:
        return self._theme.color(token)


# --------------------------------------------------------------------------- #
# Application icon
# --------------------------------------------------------------------------- #
def app_icon() -> "QIcon":
    """
    The window/taskbar icon, loaded from assets/icons/logo.svg.

    SVG is used so one file scales from a 16 px taskbar entry to a 256 px
    launcher icon. Qt renders it through its imageformats plugin; if that plugin
    is missing (a stripped PyQt6 build) the icon is simply absent rather than
    fatal — a café till must still open.
    """
    if _QT_IMPORT_ERROR is not None:
        return None

    from PyQt6.QtGui import QIcon

    path = Path(config.LOGO_PATH)
    if not path.exists():
        logger.debug("ملف الشعار غير موجود: %s", path)
        return QIcon()

    icon = QIcon(str(path))
    if icon.isNull():
        logger.warning(
            "تعذّر تحميل الشعار (%s) — قد لا تكون إضافة SVG مثبّتة في Qt", path.name
        )
    return icon


def apply_app_icon(app) -> "QIcon":
    """
    Set the icon for the whole application, so every window and dialog inherits
    it without each one calling setWindowIcon.
    """
    if app is None or _QT_IMPORT_ERROR is not None:
        return None
    icon = app_icon()
    if icon is not None and not icon.isNull():
        app.setWindowIcon(icon)
    return icon
