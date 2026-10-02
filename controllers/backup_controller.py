"""
controllers/backup_controller.py — local & external-drive backup engine.

The whole point of this module is that a café's sales history survives a dead
laptop. It is offline-only: no cloud, no network.

WHAT IT DOES
    * Copies the live SQLite database with sqlite3's online backup API, so a
      backup taken mid-sale is still a valid, consistent database that includes
      WAL content (a plain file copy is not).
    * Writes to the admin-configured external drive (USB / HDD) when it is
      reachable, otherwise falls back to backups/local/ inside the app folder.
    * Never raises at the call site: every outcome is a BackupResult the UI can
      report gently. A till must keep selling even if the drive is yanked out.
    * Records every attempt in `backup_log` and `audit_log`.

FALLBACK POLICY (spec: "If the external drive is unplugged, notify the user
gently while maintaining a secondary backup inside the local application
folder")
    * external configured and reachable  -> status 'ok',       is_external = 1
    * external configured but unreachable -> status 'fallback', local copy kept
    * no external configured at all      -> status 'ok',       local copy only
"""

from __future__ import annotations

import logging
import os
import shutil
import string
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import config
from database.db_manager import DatabaseError

logger = logging.getLogger(__name__)

__all__ = ["BackupController", "BackupResult", "DriveInfo", "get_backup_controller"]


# --------------------------------------------------------------------------- #
# Result / info objects
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class DriveInfo:
    """A candidate backup destination discovered on this machine."""

    path: str
    label: str
    is_removable: bool = False
    free_bytes: int = 0

    @property
    def free_label(self) -> str:
        gb = self.free_bytes / (1024 ** 3)
        return f"{gb:.1f} GB" if gb >= 1 else f"{self.free_bytes / (1024 ** 2):.0f} MB"


@dataclass(slots=True)
class BackupResult:
    ok: bool
    status: str = "ok"                  # ok | fallback | failed
    path: Path | None = None            # the file that was actually written
    target_dir: str = ""
    is_external: bool = False
    size_bytes: int = 0
    message: str = ""
    error: str = ""
    duration_ms: int = 0

    @property
    def used_fallback(self) -> bool:
        return self.status == "fallback"

    @property
    def needs_attention(self) -> bool:
        """True when the user should be told something."""
        return self.status in ("fallback", "failed")


# --------------------------------------------------------------------------- #
# Controller
# --------------------------------------------------------------------------- #
class BackupController:
    """Creates, verifies, prunes and logs database backups."""

    def __init__(self, db: Any = None) -> None:
        self._db = db

    @property
    def db(self):
        if self._db is None:
            from database.db_manager import get_db

            self._db = get_db()
        return self._db

    # -- configuration ----------------------------------------------------- #
    def target_dir(self) -> str:
        """The admin-configured external backup folder ('' when unset)."""
        try:
            return (self.db.get_setting("backup_dir", "") or "").strip()
        except Exception:  # pragma: no cover
            return ""

    def set_target_dir(self, path: str) -> None:
        self.db.set_setting("backup_dir", (path or "").strip())

    def auto_on_shift_close(self) -> bool:
        try:
            return self.db.get_setting_bool("backup_auto_on_shift_close", True)
        except Exception:  # pragma: no cover
            return True

    def keep_local(self) -> int:
        try:
            return int(self.db.get_setting("backup_keep_local", str(config.BACKUP_KEEP_LOCAL)))
        except (TypeError, ValueError):
            return config.BACKUP_KEEP_LOCAL

    # -- availability ------------------------------------------------------ #
    @staticmethod
    def _is_writable_directory(path: Path) -> bool:
        try:
            if not path.is_dir():
                return False
            probe = path / ".cafe_pos_write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return True
        except OSError:
            return False

    def external_available(self) -> bool:
        """True when a target is configured AND currently reachable and writable."""
        raw = self.target_dir()
        if not raw:
            return False
        return self._is_writable_directory(Path(raw).expanduser())

    def is_on_separate_device(self, path: Path) -> bool:
        """
        Heuristic for "this is a different drive".

        Delegates to config.is_on_separate_device, which compares st_dev on POSIX
        (a USB stick under /media has a different device id) and falls back to
        the drive letter on Windows, where st_dev is not reliable.
        """
        return config.is_on_separate_device(path)

    def status(self) -> dict[str, Any]:
        """Health summary for the admin screen."""
        raw = self.target_dir()
        target = Path(raw).expanduser() if raw else None
        available = self.external_available()
        return {
            "configured": bool(raw),
            "target_dir": raw,
            "available": available,
            "is_separate_device": bool(target and available
                                       and self.is_on_separate_device(target)),
            "auto_on_shift_close": self.auto_on_shift_close(),
            "local_dir": str(config.LOCAL_BACKUP_DIR),
            "keep_local": self.keep_local(),
            "db_size_bytes": self.db.db_size_bytes(),
            "last_backup_at": self.db.get_setting("last_backup_at", ""),
            "local_count": len(self.list_local_files()),
        }

    # -- destination discovery (admin UI) ---------------------------------- #
    def detect_drives(self) -> list[DriveInfo]:
        """
        Candidate destinations on this machine.

        The mount points come from config.drive_roots(), so Windows scans drive
        letters, Linux the usual /media and /mnt locations, and macOS /Volumes.
        """
        drives: list[DriveInfo] = [DriveInfo(str(config.LOCAL_BACKUP_DIR), "مجلد التطبيق (محلي)")]

        candidates: list[Path] = []
        if config.IS_WINDOWS:  # pragma: no cover - platform specific
            # Drive letters are already the volumes themselves.
            candidates.extend(config.drive_roots())
        else:
            for base in config.drive_roots():
                # On Linux/macOS the roots are directories that *contain*
                # mounts, so list their children.
                if base == Path("/mnt") or base.name == Path.home().name or base == Path("/Volumes"):
                    try:
                        for child in base.iterdir():
                            if child.is_dir() and not child.name.startswith("."):
                                candidates.append(child)
                    except OSError:
                        continue
                else:
                    candidates.append(base)

        seen: set[str] = set()
        for path in candidates:
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            try:
                usage = shutil.disk_usage(path)
                free = usage.free
            except OSError:
                free = 0
            drives.append(
                DriveInfo(
                    path=key,
                    label=f"{path.name} ({key})",
                    is_removable=self.is_on_separate_device(path),
                    free_bytes=free,
                )
            )
        return drives

    # -- backups ----------------------------------------------------------- #
    def _backup_filename(self, when: datetime | None = None) -> str:
        stamp = config.timestamp_stamp(when)
        return config.BACKUP_FILENAME_TEMPLATE.format(stamp=stamp)

    def create_backup(self, kind: str = config.BACKUP_MANUAL, *, reason: str = "") -> BackupResult:
        """
        Take a backup. Never raises — always returns a BackupResult.

        kind: 'manual' | 'auto' (auto is the shift-close trigger).
        """
        started = datetime.now()
        config.ensure_directories()

        external_raw = self.target_dir()
        external_ok = self.external_available()
        target_dir = Path(external_raw).expanduser() if (external_raw and external_ok) \
            else Path(config.LOCAL_BACKUP_DIR)

        result = BackupResult(
            ok=False,
            target_dir=str(target_dir),
            is_external=bool(external_ok),
        )

        # Tell the cashier plainly that the drive is missing, before we even try.
        if external_raw and not external_ok:
            result.status = "fallback"
            result.message = (
                "وحدة التخزين الخارجية غير متصلة — تم حفظ نسخة داخل مجلد التطبيق"
            )
        elif not external_raw:
            result.message = "تم حفظ نسخة احتياطية داخل مجلد التطبيق (لم تُحدَّد وحدة خارجية)"
        else:
            result.message = "تم إنشاء نسخة احتياطية على وحدة التخزين الخارجية"

        try:
            # Fold the WAL back in first so the copied file is self-contained.
            try:
                self.db.checkpoint()
            except Exception as exc:  # pragma: no cover - non-fatal
                logger.debug("تعذّر تنفيذ checkpoint: %s", exc)

            destination = target_dir / self._backup_filename(started)
            self.db.backup_to(destination, verify=True)

            result.path = destination
            result.size_bytes = destination.stat().st_size
            result.ok = True
            if result.status != "fallback":
                result.status = "ok"

            # A fallback must still leave a local copy — the external attempt
            # failing must never mean "no backup at all".
            if result.is_external:
                local_copy = Path(config.LOCAL_BACKUP_DIR) / destination.name
                if local_copy.parent != destination.parent:
                    try:
                        shutil.copy2(destination, local_copy)
                    except OSError as exc:  # pragma: no cover
                        logger.warning("تعذّر إنشاء النسخة المحلية الثانوية: %s", exc)
            self._prune_local()

        except DatabaseError as exc:
            result.ok = False
            result.status = "failed"
            result.error = str(exc)
            result.message = f"فشل النسخ الاحتياطي: {exc}"
            logger.error("فشل النسخ الاحتياطي: %s", exc)
        except OSError as exc:
            result.ok = False
            result.status = "failed"
            result.error = str(exc)
            result.message = f"تعذّر الكتابة إلى {target_dir}: {exc}"
            logger.error("تعذّر الكتابة إلى %s: %s", target_dir, exc)
        except Exception as exc:  # pragma: no cover - last-resort guard
            result.ok = False
            result.status = "failed"
            result.error = repr(exc)
            result.message = "حدث خطأ غير متوقع أثناء النسخ الاحتياطي"
            logger.exception("خطأ غير متوقع أثناء النسخ الاحتياطي")

        result.duration_ms = int((datetime.now() - started).total_seconds() * 1000)
        self._log(result, kind, reason)
        return result

    def create_backup_auto(self, reason: str = "") -> BackupResult:
        """Automatic backup (shift close)."""
        return self.create_backup(config.BACKUP_AUTO, reason=reason)

    # -- history ----------------------------------------------------------- #
    def _log(self, result: BackupResult, kind: str, reason: str) -> None:
        """Write backup_log + audit_log. Best-effort: never breaks the backup."""
        details = reason or result.message
        try:
            self.db.execute(
                """
                INSERT INTO backup_log (kind, target_path, is_external, status,
                                        size_bytes, message)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    kind, str(result.path or result.target_dir),
                    int(result.is_external), result.status,
                    int(result.size_bytes),
                    (details + (f" | {result.error}" if result.error else ""))[:500],
                ),
            )
        except Exception as exc:  # pragma: no cover
            logger.debug("تعذّر تسجيل النسخة الاحتياطية: %s", exc)

        try:
            self.db.execute(
                """
                INSERT INTO audit_log (user_id, action, entity, entity_id, details)
                VALUES (NULL, ?, 'backup', NULL, ?)
                """,
                (
                    f"backup_{kind}",
                    f"status={result.status} path={result.path or '-'} "
                    f"size={result.size_bytes} {reason}".strip()[:500],
                ),
            )
        except Exception as exc:  # pragma: no cover
            logger.debug("تعذّر كتابة سجل التدقيق للنسخة: %s", exc)

        if result.ok:
            try:
                self.db.set_setting("last_backup_at",
                                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            except Exception:  # pragma: no cover
                pass

    def history(self, limit: int = 20) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self.db.query_all(
                "SELECT * FROM backup_log ORDER BY id DESC LIMIT ?", (int(limit),)
            )
        ]

    def last_successful(self) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM backup_log WHERE status IN ('ok','fallback') ORDER BY id DESC LIMIT 1"
        )
        return dict(row) if row else None

    # -- local housekeeping ------------------------------------------------ #
    def list_local_files(self) -> list[Path]:
        directory = Path(config.LOCAL_BACKUP_DIR)
        if not directory.is_dir():
            return []
        return sorted(directory.glob("backup_*.db"), key=lambda p: p.stat().st_mtime,
                      reverse=True)

    def _prune_local(self) -> int:
        """Keep only the newest N local copies. Returns how many were removed."""
        keep = max(1, self.keep_local())
        files = self.list_local_files()
        removed = 0
        for stale in files[keep:]:
            try:
                stale.unlink()
                removed += 1
            except OSError as exc:  # pragma: no cover
                logger.debug("تعذّر حذف نسخة قديمة %s: %s", stale, exc)
        if removed:
            logger.info("تم حذف %s نسخة احتياطية قديمة", removed)
        return removed

    def restore_hint(self, backup_path: str) -> str:
        """
        Plain-language restore instructions for the admin.

        Deliberately not an automatic restore: overwriting a live till database
        from inside the app is how you lose a day's sales by mis-tapping.
        """
        return (
            "لاستعادة نسخة: أغلق التطبيق، ثم انسخ الملف\n"
            f"{backup_path}\n"
            f"إلى {config.DB_PATH} مع الاحتفاظ بنسخة من الملف الحالي."
        )


# --------------------------------------------------------------------------- #
# Shared instance
# --------------------------------------------------------------------------- #
_controller: BackupController | None = None


def get_backup_controller(db: Any = None) -> BackupController:
    global _controller
    if _controller is None or db is not None:
        _controller = BackupController(db)
    return _controller
