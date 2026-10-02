"""
views/cashier/cash_dialog.py — payment collection (cash with change calculator,
or card).

The change calculator is the part that matters at speed: quick-tender buttons
for round notes, a numeric pad, and a live "الباقي" figure that turns red if the
tendered amount is short.
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
    QVBoxLayout,
    QWidget,
)

import config
from views.widgets import Toast

logger = logging.getLogger(__name__)

__all__ = ["CashPaymentDialog", "PaymentDialog"]

isolate = lambda text: f"\u2066{text}\u2069"  # noqa: E731


class CashPaymentDialog(QDialog):
    """Collect cash, show the change, confirm."""

    def __init__(self, total_minor: int, quick_amounts: list[int] | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.total_minor = int(total_minor)
        self.typed = ""
        self.quick_amounts = quick_amounts or []

        self.setWindowTitle("الدفع نقداً")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setModal(True)
        self.setMinimumWidth(560)

        self._build()
        self._refresh()

    # -- construction ------------------------------------------------------ #
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 22)
        root.setSpacing(14)

        root.addWidget(self._build_totals())
        root.addWidget(self._build_quick_row())
        root.addWidget(self._build_pad())
        self.toast = Toast()
        root.addWidget(self.toast)
        root.addLayout(self._build_actions())

    def _build_totals(self) -> QWidget:
        card = QFrame()
        card.setObjectName("CardRaised")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        total_row = QHBoxLayout()
        total_label = QLabel("المطلوب")
        total_label.setObjectName("SectionTitle")
        total_value = QLabel(config.format_money(self.total_minor))
        total_value.setObjectName("TotalValue")
        total_row.addWidget(total_label)
        total_row.addStretch(1)
        total_row.addWidget(total_value)

        paid_row = QHBoxLayout()
        paid_label = QLabel("المدفوع")
        paid_label.setObjectName("FieldLabel")
        self.paid_value = QLabel(config.format_money(0))
        self.paid_value.setObjectName("MoneyValue")
        paid_row.addWidget(paid_label)
        paid_row.addStretch(1)
        paid_row.addWidget(self.paid_value)

        change_row = QHBoxLayout()
        change_label = QLabel("الباقي")
        change_label.setObjectName("FieldLabel")
        self.change_value = QLabel(config.format_money(0))
        self.change_value.setObjectName("TotalValue")
        change_row.addWidget(change_label)
        change_row.addStretch(1)
        change_row.addWidget(self.change_value)

        layout.addLayout(total_row)
        layout.addLayout(paid_row)
        layout.addLayout(change_row)
        return card

    def _build_quick_row(self) -> QWidget:
        box = QWidget()
        layout = QHBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        amounts = self.quick_amounts or list(config.QUICK_CASH_AMOUNTS)
        for amount in amounts:
            button = QPushButton(isolate(config.format_money(amount, symbol=False)))
            button.setObjectName("QuickCash")
            button.setMinimumHeight(config.MIN_TOUCH_TARGET)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, a=amount: self._set_amount(a))
            layout.addWidget(button)
        return box

    def _build_pad(self) -> QWidget:
        box = QWidget()
        grid = QGridLayout(box)
        grid.setSpacing(10)
        grid.setContentsMargins(0, 0, 0, 0)

        # LTR so the digits read 1-2-3 rather than mirroring under RTL.
        box.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        keys = [("7", 0, 0), ("8", 0, 1), ("9", 0, 2),
                ("4", 1, 0), ("5", 1, 1), ("6", 1, 2),
                ("1", 2, 0), ("2", 2, 1), ("3", 2, 2),
                ("00", 3, 0), ("0", 3, 1)]
        for label, row, column in keys:
            button = QPushButton(label)
            button.setObjectName("NumKey")
            button.setMinimumHeight(config.MIN_TOUCH_TARGET)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, d=label: self._append(d))
            grid.addWidget(button, row, column)

        back = QPushButton("حذف")
        back.setObjectName("NumKeyBack")
        back.setMinimumHeight(config.MIN_TOUCH_TARGET)
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.clicked.connect(self._backspace)
        grid.addWidget(back, 3, 2)

        for column in range(3):
            grid.setColumnStretch(column, 1)
        return box

    def _build_actions(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(12)

        cancel = QPushButton("إلغاء")
        cancel.setObjectName("GhostAction")
        cancel.setMinimumHeight(config.MIN_TOUCH_TARGET)
        cancel.clicked.connect(self.reject)

        exact = QPushButton("المبلغ بالضبط")
        exact.setObjectName("GhostAction")
        exact.setMinimumHeight(config.MIN_TOUCH_TARGET)
        exact.clicked.connect(lambda: self._set_amount(self.total_minor))

        self.confirm = QPushButton("تأكيد الدفع")
        self.confirm.setObjectName("PrimaryAction")
        self.confirm.setMinimumHeight(config.MIN_TOUCH_TARGET)
        self.confirm.setDefault(True)
        self.confirm.clicked.connect(self._confirm)

        row.addWidget(cancel, 1)
        row.addWidget(exact, 1)
        row.addWidget(self.confirm, 2)
        return row

    # -- input ------------------------------------------------------------- #
    @property
    def paid_minor(self) -> int:
        try:
            return int(self.typed or 0)
        except ValueError:
            return 0

    @property
    def change_minor(self) -> int:
        return self.paid_minor - self.total_minor

    def _append(self, digits: str) -> None:
        self.typed = (self.typed + digits).lstrip("0")
        if len(self.typed) > 9:
            self.typed = self.typed[:9]
        self._refresh()

    def _backspace(self) -> None:
        self.typed = self.typed[:-1]
        self._refresh()

    def _set_amount(self, amount: int) -> None:
        self.typed = str(int(amount))
        self._refresh()

    def _refresh(self) -> None:
        paid = self.paid_minor
        change = paid - self.total_minor
        self.paid_value.setText(config.format_money(paid))
        self.change_value.setText(config.format_money(max(0, change)))
        short = paid < self.total_minor
        self.change_value.setObjectName("ErrorText" if short else "TotalValue")
        self.change_value.style().unpolish(self.change_value)
        self.change_value.style().polish(self.change_value)
        self.confirm.setEnabled(not short)

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        text = event.text()
        if text.isdigit():
            self._append(text)
        elif event.key() == Qt.Key.Key_Backspace:
            self._backspace()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._confirm()
        elif event.key() == Qt.Key.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(event)

    def _confirm(self) -> None:
        if self.paid_minor < self.total_minor:
            self.toast.show_message("المبلغ المدفوع أقل من المطلوب", "error", 3500)
            return
        self.accept()


class PaymentDialog(QDialog):
    """Choose the payment method: cash (with change) or card."""

    def __init__(self, total_minor: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.total_minor = int(total_minor)
        self.method = ""
        self.paid_minor = 0

        self.setWindowTitle("طريقة الدفع")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setModal(True)
        self.setMinimumWidth(520)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 22)
        root.setSpacing(16)

        card = QFrame()
        card.setObjectName("CardRaised")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 16, 18, 16)
        title = QLabel("الإجمالي المطلوب")
        title.setObjectName("SectionTitle")
        value = QLabel(config.format_money(self.total_minor))
        value.setObjectName("TotalValue")
        value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(title)
        card_layout.addWidget(value)
        root.addWidget(card)

        buttons = QHBoxLayout()
        buttons.setSpacing(14)

        cash = QPushButton("نقداً")
        cash.setObjectName("PayCash")
        cash.setMinimumHeight(96)
        cash.clicked.connect(lambda: self._choose(config.PAYMENT_CASH))

        card_btn = QPushButton("بطاقة")
        card_btn.setObjectName("PayCard")
        card_btn.setMinimumHeight(96)
        card_btn.clicked.connect(lambda: self._choose(config.PAYMENT_CARD))

        buttons.addWidget(cash, 1)
        buttons.addWidget(card_btn, 1)
        root.addLayout(buttons)

        cancel = QPushButton("إلغاء")
        cancel.setObjectName("GhostAction")
        cancel.setMinimumHeight(config.MIN_TOUCH_TARGET)
        cancel.clicked.connect(self.reject)
        root.addWidget(cancel)

    def _choose(self, method: str) -> None:
        if method == config.PAYMENT_CARD:
            self.method = config.PAYMENT_CARD
            self.paid_minor = self.total_minor
            self.accept()
            return

        dialog = CashPaymentDialog(self.total_minor, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.method = config.PAYMENT_CASH
            self.paid_minor = dialog.paid_minor
            self.accept()
