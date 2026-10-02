"""
controllers/shift_controller.py — the shift workflow.

Qt-free. Owns the rules the spec lays out:
    * a shift MUST be open before the till can be used
    * only the cashier who opened it (or an admin) may close it
    * closing computes the over/short, writes the Z-Report, runs the backup and
      ends the session

The controller deliberately does NOT call auth.logout() itself — it returns an
outcome describing what should happen, and the view performs the logout. That
keeps this layer testable without a session and stops a logging bug from
silently ending a cashier's session mid-sale.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import config
from controllers.backup_controller import BackupController, BackupResult
from models.shift import Shift, ShiftError, ShiftRepository, ShiftTotals
from services.report_exporter import ReportExporter

logger = logging.getLogger(__name__)

__all__ = ["ShiftController", "OpenShiftResult", "CloseShiftResult", "ShiftStatus"]


@dataclass(slots=True)
class ShiftStatus:
    """What the UI needs to decide whether to show the open-shift modal."""

    has_open_shift: bool
    shift: Shift | None = None
    required: bool = True

    @property
    def opened_at(self) -> str:
        return self.shift.opened_at if self.shift else ""

    @property
    def cashier_name(self) -> str:
        return self.shift.cashier_name if self.shift else ""


@dataclass(slots=True)
class OpenShiftResult:
    ok: bool
    shift: Shift | None = None
    message: str = ""


@dataclass(slots=True)
class CloseShiftResult:
    ok: bool
    shift: Shift | None = None
    totals: ShiftTotals | None = None
    backup: BackupResult | None = None
    report_path: Path | None = None
    should_logout: bool = False
    message: str = ""
    warnings: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.warnings is None:
            self.warnings = []

    @property
    def difference_minor(self) -> int:
        return self.shift.difference_minor if self.shift else 0


class ShiftController:
    """Open/close shifts, build Z-Reports, trigger the backup."""

    def __init__(
        self,
        auth: Any = None,
        shift_repo: ShiftRepository | None = None,
        backup: BackupController | None = None,
        exporter: ReportExporter | None = None,
    ) -> None:
        self.auth = auth
        self.shifts = shift_repo or ShiftRepository()
        self.backup = backup or BackupController()
        self.exporter = exporter or ReportExporter()

    # -- current state ----------------------------------------------------- #
    def current_shift(self) -> Shift | None:
        return self.shifts.get_open_shift()

    def status(self) -> ShiftStatus:
        shift = self.current_shift()
        required = True
        try:
            required = self.shifts.db.get_setting_bool("require_open_shift", True)
        except Exception:  # pragma: no cover
            pass
        return ShiftStatus(has_open_shift=shift is not None, shift=shift, required=required)

    def needs_open_shift(self) -> bool:
        """True when the login flow must show the open-shift modal."""
        status = self.status()
        return status.required and not status.has_open_shift

    def live_snapshot(self) -> dict[str, Any] | None:
        shift = self.current_shift()
        if shift is None:
            return None
        return self.shifts.live_snapshot(shift.id)

    # -- opening ----------------------------------------------------------- #
    def open_shift(self, opening_float_minor: int, notes: str = "") -> OpenShiftResult:
        user = getattr(self.auth, "current_user", None)
        if user is None:
            return OpenShiftResult(ok=False, message="يجب تسجيل الدخول أولاً")
        if self.auth is not None and not self.auth.can("open_shift"):
            return OpenShiftResult(ok=False, message="لا تملك صلاحية فتح الوردية")

        try:
            shift = self.shifts.open_shift(user.id, int(opening_float_minor), notes)
        except ShiftError as exc:
            return OpenShiftResult(ok=False, message=str(exc))
        except Exception as exc:
            logger.exception("فشل فتح الوردية")
            return OpenShiftResult(ok=False, message=f"تعذّر فتح الوردية: {exc}")

        self._audit(user, "shift_opened", shift.id,
                    f"opening_float={shift.opening_float_minor}")
        return OpenShiftResult(
            ok=True, shift=shift,
            message=f"تم فتح الوردية #{shift.id} برصيد {config.format_money(shift.opening_float_minor)}",
        )

    # -- closing ----------------------------------------------------------- #
    def close_shift(
        self,
        counted_cash_minor: int,
        notes: str = "",
        *,
        run_backup: bool = True,
        logout: bool = True,
    ) -> CloseShiftResult:
        """
        Close the open shift.

        Order matters and is deliberate:
          1. snapshot the totals and close the shift row
          2. write the Z-Report file
          3. run the backup (must not be able to fail the close)
          4. only then tell the caller to log out
        """
        user = getattr(self.auth, "current_user", None)
        if user is None:
            return CloseShiftResult(ok=False, message="يجب تسجيل الدخول أولاً")

        shift = self.current_shift()
        if shift is None:
            return CloseShiftResult(ok=False, message="لا توجد وردية مفتوحة لإغلاقها")

        # A cashier may close their own shift; an admin may close any.
        if self.auth is not None:
            if not self.auth.can("close_shift"):
                return CloseShiftResult(ok=False, message="لا تملك صلاحية إغلاق الوردية")
            if shift.user_id != user.id and not user.is_admin:
                return CloseShiftResult(
                    ok=False,
                    message="هذه الوردية مفتوحة بواسطة مستخدم آخر — يغلقها المدير فقط",
                )

        warnings: list[str] = []
        try:
            closed = self.shifts.close_shift(shift.id, int(counted_cash_minor), notes)
        except ShiftError as exc:
            return CloseShiftResult(ok=False, message=str(exc))
        except Exception as exc:
            logger.exception("فشل إغلاق الوردية")
            return CloseShiftResult(ok=False, message=f"تعذّر إغلاق الوردية: {exc}")

        totals = self.shifts.totals_for(closed.id)

        # -- Z-Report ------------------------------------------------------ #
        report_path: Path | None = None
        try:
            report_path = self.exporter.export_z_report(closed, totals)
            self.shifts.set_z_report_path(closed.id, str(report_path))
        except Exception as exc:
            logger.exception("فشل إنشاء تقرير الوردية")
            warnings.append(f"تعذّر إنشاء ملف تقرير الوردية: {exc}")

        # -- backup -------------------------------------------------------- #
        backup_result: BackupResult | None = None
        if run_backup and self.backup.auto_on_shift_close():
            try:
                backup_result = self.backup.create_backup_auto(
                    reason=f"shift_close #{closed.id}"
                )
                if backup_result.needs_attention:
                    warnings.append(backup_result.message)
            except Exception as exc:  # pragma: no cover - belt and braces
                logger.exception("فشل النسخ الاحتياطي بعد إغلاق الوردية")
                warnings.append(f"تعذّر إنشاء النسخة الاحتياطية: {exc}")
        elif run_backup:
            warnings.append("النسخ التلقائي عند إغلاق الوردية معطّل من الإعدادات")

        self._audit(
            user, "shift_closed", closed.id,
            f"expected={closed.expected_cash_minor} counted={closed.counted_cash_minor} "
            f"difference={closed.difference_minor} orders={closed.order_count} "
            f"gross={closed.total_sales_minor} "
            f"report={report_path or '-'} "
            f"backup={backup_result.status if backup_result else 'skipped'}",
        )

        message = (
            f"تم إغلاق الوردية #{closed.id} — "
            f"فرق الصندوق {closed.difference_label}"
        )
        return CloseShiftResult(
            ok=True,
            shift=closed,
            totals=totals,
            backup=backup_result,
            report_path=report_path,
            should_logout=bool(logout),
            message=message,
            warnings=warnings,
        )

    # -- reporting --------------------------------------------------------- #
    def preview_close(self) -> dict[str, Any] | None:
        """Figures for the close dialog: expected cash, sales, order count."""
        return self.live_snapshot()

    def export_z_report(self, shift_id: int) -> Path | None:
        shift = self.shifts.get(shift_id)
        if shift is None:
            return None
        return self.exporter.export_z_report(shift, self.shifts.totals_for(shift_id))

    def list_shifts(self, limit: int = 30) -> list[Shift]:
        return self.shifts.list_shifts(limit)

    def today_summary(self) -> dict[str, int]:
        return self.shifts.today_summary()

    # -- audit ------------------------------------------------------------- #
    def _audit(self, user: Any, action: str, shift_id: int | None, details: str = "") -> None:
        try:
            self.shifts.db.execute(
                """
                INSERT INTO audit_log (user_id, action, entity, entity_id, details)
                VALUES (?, ?, 'shifts', ?, ?)
                """,
                (getattr(user, "id", None), action, shift_id, details[:500]),
            )
        except Exception as exc:  # pragma: no cover
            logger.debug("تعذّر كتابة سجل التدقيق: %s", exc)
