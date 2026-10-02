"""
views/cashier/shift_dialog.py — open-shift, close-shift and Z-Report dialogs.

OpenShiftDialog  : mandatory float entry before the till can be used.
CloseShiftDialog : shows what the system expects, takes the counted drawer,
                   then presents the Z-Report and the backup outcome.

Both use a big numeric pad with quick-amount buttons — the same interaction the
cashier already learned for taking payment.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import config
from controllers.shift_controller import CloseShiftResult, ShiftController
from views.widgets import Divider, Toast

logger = logging.getLogger(__name__)

__all__ = ["OpenShiftDialog", "CloseShiftDialog", "ZReportDialog"]

isolate = lambda text: f"\u2066{text}\u2069"  # noqa: E731

FLOAT_PRESETS = (0, 5000, 10000, 20000, 50000)


class AmountPad(QWidget):
    """Numeric entry shared by both shift dialogs."""

    def __init__(self, on_change, parent: QWidget | None = None,
                 *, compact: bool = False) -> None:
        super().__init__(parent)
        self._typed = ""
        self._on_change = on_change
        self.compact = compact

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10 if not compact else 8)

        # Presets
        presets = QHBoxLayout()
        presets.setSpacing(8)
        for amount in FLOAT_PRESETS:
            button = QPushButton(isolate(config.format_money(amount, symbol=False)))
            button.setObjectName("QuickCash")
            button.setMinimumHeight(52 if compact else 56)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, a=amount: self.set_amount(a))
            presets.addWidget(button)
        layout.addLayout(presets)

        # Keypad — LTR so the digits read 7-8-9, as on every till.
        grid = QGridLayout()
        grid.setSpacing(8)
        self.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        keys = [("7", 0, 0), ("8", 0, 1), ("9", 0, 2),
                ("4", 1, 0), ("5", 1, 1), ("6", 1, 2),
                ("1", 2, 0), ("2", 2, 1), ("3", 2, 2),
                ("00", 3, 0), ("0", 3, 1)]
        key_height = 52 if compact else 60
        for label, row, column in keys:
            button = QPushButton(label)
            button.setObjectName("NumKey")
            button.setMinimumHeight(key_height)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, d=label: self.append(d))
            grid.addWidget(button, row, column)

        back = QPushButton("حذف")
        back.setObjectName("NumKeyBack")
        back.setMinimumHeight(key_height)
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.clicked.connect(self.backspace)
        grid.addWidget(back, 3, 2)
        for column in range(3):
            grid.setColumnStretch(column, 1)
        layout.addLayout(grid)

    # -- value ------------------------------------------------------------- #
    @property
    def amount_minor(self) -> int:
        try:
            return int(self._typed or 0)
        except ValueError:
            return 0

    def append(self, digits: str) -> None:
        self._typed = (self._typed + digits).lstrip("0")[:9]
        self._on_change()

    def backspace(self) -> None:
        self._typed = self._typed[:-1]
        self._on_change()

    def set_amount(self, amount: int) -> None:
        self._typed = str(max(0, int(amount)))
        self._on_change()

    def clear(self) -> None:
        self._typed = ""
        self._on_change()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        text = event.text()
        if text.isdigit():
            self.append(text)
        elif event.key() == Qt.Key.Key_Backspace:
            self.backspace()
        else:
            super().keyPressEvent(event)


class OpenShiftDialog(QDialog):
    """
    Mandatory float entry. Cannot be dismissed with Escape — the spec requires a
    shift to be open before selling, so the only ways out are opening one or
    logging out.
    """

    def __init__(self, controller: ShiftController, parent: QWidget | None = None,
                 *, allow_cancel: bool = True) -> None:
        super().__init__(parent)
        self.controller = controller
        self.allow_cancel = allow_cancel

        self.setWindowTitle("فتح الوردية")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setModal(True)
        self.setMinimumWidth(620)

        self._build()
        self._refresh()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 22)
        root.setSpacing(14)

        header = QFrame()
        header.setObjectName("CardRaised")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(18, 16, 18, 16)
        header_layout.setSpacing(6)
        title = QLabel("فتح وردية جديدة")
        title.setObjectName("BrandTitle")
        hint = QLabel(
            "أدخل رصيد البداية (النقد الموجود في الصندوق قبل أول عملية بيع). "
            "لا يمكن البيع قبل فتح الوردية."
        )
        hint.setObjectName("HintText")
        hint.setWordWrap(True)
        header_layout.addWidget(title)
        header_layout.addWidget(hint)
        root.addWidget(header)

        self.amount_label = QLabel(config.format_money(0))
        self.amount_label.setObjectName("TotalValue")
        self.amount_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.amount_label)

        self.pad = AmountPad(self._refresh)
        root.addWidget(self.pad)

        self.toast = Toast()
        root.addWidget(self.toast)

        actions = QHBoxLayout()
        actions.setSpacing(12)
        if self.allow_cancel:
            cancel = QPushButton("إلغاء")
            cancel.setObjectName("GhostAction")
            cancel.setMinimumHeight(config.MIN_TOUCH_TARGET)
            cancel.clicked.connect(self.reject)
            actions.addWidget(cancel, 1)

        self.confirm = QPushButton("فتح الوردية")
        self.confirm.setObjectName("PrimaryAction")
        self.confirm.setMinimumHeight(config.MIN_TOUCH_TARGET)
        self.confirm.setDefault(True)
        self.confirm.clicked.connect(self._confirm)
        actions.addWidget(self.confirm, 2)
        root.addLayout(actions)

    def _refresh(self) -> None:
        self.amount_label.setText(config.format_money(self.pad.amount_minor))

    def keyPressEvent(self, event) -> None:  # noqa: N802
        # Escape must not skip the mandatory step; the pad handles digits.
        if event.key() == Qt.Key.Key_Escape:
            if self.allow_cancel:
                self.reject()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._confirm()
            return
        self.pad.keyPressEvent(event)

    def _confirm(self) -> None:
        result = self.controller.open_shift(self.pad.amount_minor)
        if not result.ok:
            self.toast.show_message(result.message, "error", 5000)
            return
        self.accept()

    @property
    def shift(self):
        return self.controller.current_shift()


class ZReportDialog(QDialog):
    """Shows the closed shift's summary, the backup outcome and the warnings."""

    def __init__(self, result: CloseShiftResult, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.result = result

        self.setWindowTitle("تقرير إغلاق الوردية")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setModal(True)
        # Tall enough for the cash + sales cards side by side, plus the backup
        # outcome underneath — the backup notice must not fall below the fold.
        self.setMinimumSize(880, 600)
        height = 700
        screen = self.screen()
        if screen is not None:
            height = min(height, screen.availableGeometry().height() - 60)
        self.resize(900, height)

        shift = result.shift
        totals = result.totals

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)

        title = QLabel(f"تم إغلاق الوردية #{shift.id}")
        title.setObjectName("BrandTitle")
        root.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 6, 0)
        body_layout.setSpacing(10)

        # Cash reconciliation and sales summary sit side by side.
        top_row = QHBoxLayout()
        top_row.setSpacing(12)

        # Cash reconciliation — the number that matters, so it leads.
        cash_card = QFrame()
        cash_card.setObjectName("Card")
        cash_layout = QGridLayout(cash_card)
        cash_layout.setContentsMargins(16, 14, 16, 14)
        cash_layout.setSpacing(8)
        rows = [
            ("رصيد البداية", config.format_money(shift.opening_float_minor)),
            ("المبيعات النقدية", config.format_money(shift.cash_sales_minor)),
            ("المتوقع في الصندوق", config.format_money(shift.expected_cash_minor)),
            ("المعدود فعلياً", config.format_money(shift.counted_cash_minor)),
        ]
        for index, (label, value) in enumerate(rows):
            cash_layout.addWidget(self._label(label, "FieldLabel"), index, 0)
            cash_layout.addWidget(self._label(value, "MoneyValue"), index, 1,
                                  alignment=Qt.AlignmentFlag.AlignLeft)
        cash_layout.addWidget(Divider(), len(rows), 0, 1, 2)

        cash_layout.addWidget(
            self._label(f"الفرق ({shift.difference_state})", "SectionTitle"), len(rows) + 1, 0
        )
        diff = self._label(shift.difference_label,
                           "SuccessText" if shift.is_balanced else "ErrorText")
        cash_layout.addWidget(diff, len(rows) + 1, 1, alignment=Qt.AlignmentFlag.AlignLeft)
        top_row.addWidget(cash_card, 1)

        # Sales summary
        sales_card = QFrame()
        sales_card.setObjectName("Card")
        sales_layout = QGridLayout(sales_card)
        sales_layout.setContentsMargins(16, 14, 16, 14)
        sales_layout.setSpacing(8)
        sales_rows = [
            ("عدد الطلبات", str(totals.order_count)),
            ("إجمالي المبيعات", config.format_money(totals.gross_minor)),
            ("نقداً", config.format_money(totals.cash_sales_minor)),
            ("بطاقة", config.format_money(totals.card_sales_minor)),
        ]
        if totals.discount_minor:
            sales_rows.append(("الخصومات", config.format_money(totals.discount_minor)))
        if totals.order_count:
            sales_rows.append(("متوسط الطلب", config.format_money(totals.average_order_minor)))
        for index, (label, value) in enumerate(sales_rows):
            sales_layout.addWidget(self._label(label, "FieldLabel"), index, 0)
            sales_layout.addWidget(self._label(value, "MoneyValue"), index, 1,
                                   alignment=Qt.AlignmentFlag.AlignLeft)
        top_row.addWidget(sales_card, 1)
        body_layout.addLayout(top_row)

        # Top sellers and the backup outcome side by side as well.
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(12)

        if totals.top_items:
            top_card = QFrame()
            top_card.setObjectName("Card")
            top_layout = QVBoxLayout(top_card)
            top_layout.setContentsMargins(16, 14, 16, 14)
            top_layout.setSpacing(6)
            top_layout.addWidget(self._label("الأكثر مبيعاً", "SectionTitle"))
            for name, qty, revenue in totals.top_items:
                row = QHBoxLayout()
                row.addWidget(self._label(name, "StatusText"), 1)
                row.addWidget(self._label(f"{qty} × {config.format_money(revenue)}",
                                          "MoneyValue"))
                top_layout.addLayout(row)
            bottom_row.addWidget(top_card, 1)

        # Backup outcome — always visible, never below the fold.
        backup_card = QFrame()
        backup_card.setObjectName("Card")
        backup_layout = QVBoxLayout(backup_card)
        backup_layout.setContentsMargins(16, 14, 16, 14)
        backup_layout.setSpacing(6)
        backup_layout.addWidget(self._label("النسخ الاحتياطي", "SectionTitle"))
        if result.backup is None:
            backup_layout.addWidget(self._label("لم يتم تنفيذ نسخة احتياطية", "WarningText"))
        else:
            kind = {"ok": "SuccessText", "fallback": "WarningText", "failed": "ErrorText"}
            backup_layout.addWidget(
                self._label(result.backup.message, kind.get(result.backup.status, "StatusText"))
            )
            if result.backup.path:
                backup_layout.addWidget(self._label(str(result.backup.path), "HintText"))
        if result.report_path:
            backup_layout.addWidget(self._label("تقرير الوردية", "FieldLabel"))
            backup_layout.addWidget(self._label(str(result.report_path), "HintText"))
        bottom_row.addWidget(backup_card, 1)
        body_layout.addLayout(bottom_row)

        for warning in result.warnings or []:
            body_layout.addWidget(self._label(warning, "WarningText"))

        body_layout.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        close = QPushButton("تم")
        close.setObjectName("PrimaryAction")
        close.setMinimumHeight(config.MIN_TOUCH_TARGET)
        close.setDefault(True)
        close.clicked.connect(self.accept)
        root.addWidget(close)

    @staticmethod
    def _label(text: str, object_name: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName(object_name)
        label.setWordWrap(True)
        return label


class CloseShiftDialog(QDialog):
    """
    Count the drawer, close the shift.

    Shows the expected cash up front so the cashier knows what they are counting
    against, then hands off to ZReportDialog on success.
    """

    def __init__(self, controller: ShiftController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.result: CloseShiftResult | None = None

        self.setWindowTitle("إغلاق الوردية")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setModal(True)
        self.setMinimumWidth(940)
        self._fit_to_screen()

        self._build()
        self._refresh()

    def _fit_to_screen(self) -> None:
        """
        Fit within the display. A cash drawer dialog that runs off the bottom of
        a 1080p till cannot be completed — the confirm button is unreachable.
        """
        height = 640
        screen = self.screen()
        if screen is not None:
            available = screen.availableGeometry()
            height = min(height, available.height() - 60)
        self.resize(980, height)

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)

        title = QLabel("إغلاق الوردية")
        title.setObjectName("BrandTitle")
        root.addWidget(title)

        # Two columns: the summary (what the system expects) beside the counting
        # pad. Stacking them made the dialog 1125px tall.
        columns = QHBoxLayout()
        columns.setSpacing(16)

        # -- left: summary ------------------------------------------------ #
        self.summary_card = QFrame()
        self.summary_card.setObjectName("CardRaised")
        self.summary_layout = QGridLayout(self.summary_card)
        self.summary_layout.setContentsMargins(18, 16, 18, 16)
        self.summary_layout.setSpacing(8)

        self.rows: dict[str, QLabel] = {}
        for index, key in enumerate(("cashier", "opened_at", "orders", "gross",
                                     "cash_sales", "expected")):
            label = QLabel("")
            label.setObjectName("FieldLabel")
            value = QLabel("")
            value.setObjectName("MoneyValue")
            value.setWordWrap(True)
            self.summary_layout.addWidget(label, index, 0)
            self.summary_layout.addWidget(value, index, 1,
                                          alignment=Qt.AlignmentFlag.AlignLeft)
            self.rows[key] = label
            self.rows[key + "_value"] = value
        self.summary_layout.setRowStretch(len(self.rows) // 2, 1)
        columns.addWidget(self.summary_card, 3)

        # -- right: counting ---------------------------------------------- #
        counting = QVBoxLayout()
        counting.setSpacing(8)

        count_label = QLabel("المبلغ المعدود فعلياً في الصندوق")
        count_label.setObjectName("SectionTitle")
        count_label.setWordWrap(True)
        counting.addWidget(count_label)

        self.counted_label = QLabel(config.format_money(0))
        self.counted_label.setObjectName("TotalValue")
        self.counted_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        counting.addWidget(self.counted_label)

        self.difference_label = QLabel("")
        self.difference_label.setObjectName("StatusText")
        self.difference_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        counting.addWidget(self.difference_label)

        self.pad = AmountPad(self._refresh, compact=True)
        counting.addWidget(self.pad)
        counting.addStretch(1)
        columns.addLayout(counting, 4)

        root.addLayout(columns, 1)

        self.toast = Toast()
        root.addWidget(self.toast)

        actions = QHBoxLayout()
        actions.setSpacing(12)
        cancel = QPushButton("رجوع")
        cancel.setObjectName("GhostAction")
        cancel.setMinimumHeight(config.MIN_TOUCH_TARGET)
        cancel.clicked.connect(self.reject)

        self.confirm = QPushButton("إغلاق الوردية وتوليد التقرير")
        self.confirm.setObjectName("DangerAction")
        self.confirm.setMinimumHeight(config.MIN_TOUCH_TARGET)
        self.confirm.clicked.connect(self._confirm)
        actions.addWidget(cancel, 1)
        actions.addWidget(self.confirm, 2)
        root.addLayout(actions)

        self._load_snapshot()

    def _load_snapshot(self) -> None:
        snapshot = self.controller.live_snapshot()
        if snapshot is None:
            self.rows["cashier_value"].setText("لا توجد وردية مفتوحة")
            self.confirm.setEnabled(False)
            return
        shift = snapshot["shift"]
        totals = snapshot["totals"]
        self.expected_minor = snapshot["expected_cash_minor"]
        self._set("cashier", "الكاشير", shift.cashier_name or "-")
        self._set("opened_at", "فُتحت في", shift.opened_at or "-")
        self._set("orders", "عدد الطلبات", str(totals.order_count))
        self._set("gross", "إجمالي المبيعات", config.format_money(totals.gross_minor))
        self._set("cash_sales", "المبيعات النقدية", config.format_money(totals.cash_sales_minor))
        self._set("expected", "المتوقع في الصندوق", config.format_money(self.expected_minor))
        self.pad.set_amount(self.expected_minor)

    def _set(self, key: str, label: str, value: str) -> None:
        self.rows[key].setText(label)
        self.rows[key + "_value"].setText(value)

    def _refresh(self) -> None:
        counted = self.pad.amount_minor
        self.counted_label.setText(config.format_money(counted))
        expected = getattr(self, "expected_minor", 0)
        difference = counted - expected
        from models.product import price_delta_label

        if difference == 0:
            self.difference_label.setText("مطابق")
            self.difference_label.setObjectName("SuccessText")
        elif difference > 0:
            self.difference_label.setText(f"زيادة {price_delta_label(difference)}")
            self.difference_label.setObjectName("WarningText")
        else:
            self.difference_label.setText(f"نقص {price_delta_label(difference)}")
            self.difference_label.setObjectName("ErrorText")
        self.difference_label.style().unpolish(self.difference_label)
        self.difference_label.style().polish(self.difference_label)

    def _confirm(self) -> None:
        result = self.controller.close_shift(self.pad.amount_minor)
        self.result = result
        if not result.ok:
            self.toast.show_message(result.message, "error", 6000)
            return
        self.accept()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._confirm()
            return
        self.pad.keyPressEvent(event)
