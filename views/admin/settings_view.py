"""
views/admin/settings_view.py — cafe identity, tax, printer and system settings.

Kept deliberately small: anything that changes money behaviour (the tax rate)
says plainly what it does, because the admin is the only person who can change
it and a wrong rate silently misreports every future Z-Report.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import config
from controllers.admin_controller import AdminController, MenuError
from views.widgets import Divider, Toast

logger = logging.getLogger(__name__)

__all__ = ["SettingsView"]


class SettingsView(QWidget):
    """Cafe details, tax, receipt/printer and system toggles."""

    def __init__(self, auth, controller: AdminController,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.auth = auth
        self.controller = controller

        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self._build()
        self.reload()

    # -- construction ------------------------------------------------------ #
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 6, 0)
        layout.setSpacing(12)

        layout.addWidget(self._build_cafe_card())
        layout.addWidget(self._build_tax_card())
        layout.addWidget(self._build_printer_card())
        layout.addWidget(self._build_system_card())
        layout.addWidget(self._build_about_card())
        layout.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        self.toast = Toast()
        root.addWidget(self.toast)

    def _build_cafe_card(self) -> QWidget:
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        layout.addWidget(self._title("بيانات المقهى"))
        layout.addWidget(Divider())

        form = QFormLayout()
        form.setSpacing(10)
        self.cafe_name = QLineEdit()
        self.cafe_phone = QLineEdit()
        self.cafe_address = QLineEdit()
        self.tax_number = QLineEdit()
        for label, widget in (("اسم المقهى", self.cafe_name), ("الهاتف", self.cafe_phone),
                              ("العنوان", self.cafe_address),
                              ("الرقم الضريبي", self.tax_number)):
            widget.setMinimumHeight(config.MIN_TOUCH_TARGET - 8)
            form.addRow(label, widget)
        layout.addLayout(form)

        self.receipt_footer = QLineEdit()
        self.receipt_footer.setMinimumHeight(config.MIN_TOUCH_TARGET - 8)
        form.addRow("نص أسفل الإيصال", self.receipt_footer)
        return card

    def _build_tax_card(self) -> QWidget:
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        layout.addWidget(self._title("الضريبة"))
        layout.addWidget(Divider())

        note = QLabel(
            "الأسعار شاملة الضريبة دائماً — لا يُضاف أي مبلغ عند الدفع. "
            "النسبة هنا تُستخدم فقط لاستخراج قيمة الضريبة في التقارير. "
            "النسبة الحالية 0٪ فتصبح قيمة الضريبة صفراً."
        )
        note.setObjectName("HintText")
        note.setWordWrap(True)
        layout.addWidget(note)

        row = QHBoxLayout()
        row.addWidget(QLabel("نسبة الضريبة ٪"))
        self.tax_spin = QDoubleSpinBox()
        self.tax_spin.setDecimals(2)
        self.tax_spin.setRange(0, 100)
        self.tax_spin.setSingleStep(1)
        self.tax_spin.setSuffix(" ٪")
        self.tax_spin.setMinimumHeight(config.MIN_TOUCH_TARGET - 8)
        row.addWidget(self.tax_spin)
        row.addStretch(1)

        self.tax_preview = QLabel("")
        self.tax_preview.setObjectName("HintText")
        row.addWidget(self.tax_preview)
        layout.addLayout(row)
        self.tax_spin.valueChanged.connect(self._update_tax_preview)
        return card

    def _build_printer_card(self) -> QWidget:
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        layout.addWidget(self._title("الطابعة الحرارية"))
        layout.addWidget(Divider())

        self.printer_status = QLabel("")
        self.printer_status.setObjectName("HintText")
        self.printer_status.setWordWrap(True)
        layout.addWidget(self.printer_status)

        form = QFormLayout()
        self.printer_enabled = QCheckBox("تفعيل الطباعة على الطابعة الحرارية")
        form.addRow("", self.printer_enabled)

        self.printer_backend = QComboBox()
        for label, value in (("تلقائي", "auto"), ("ESC/POS عبر USB", "escpos"),
                             ("CUPS (Linux)", "cups"), ("Windows", "windows"),
                             ("حفظ كملف فقط", "file")):
            self.printer_backend.addItem(label, value)
        self.printer_backend.setMinimumHeight(config.MIN_TOUCH_TARGET - 8)
        form.addRow("طريقة الاتصال", self.printer_backend)

        self.printer_name = QLineEdit()
        self.printer_name.setPlaceholderText("اسم الطابعة أو 0x04b8:0x0e15")
        self.printer_name.setMinimumHeight(config.MIN_TOUCH_TARGET - 8)
        form.addRow("الطابعة", self.printer_name)

        self.receipt_width = QComboBox()
        self.receipt_width.addItem("80 مم", "80")
        self.receipt_width.addItem("58 مم", "58")
        self.receipt_width.setMinimumHeight(config.MIN_TOUCH_TARGET - 8)
        form.addRow("عرض الإيصال", self.receipt_width)
        layout.addLayout(form)

        note = QLabel(
            "الإيصالات تُطبع كصورة نقطية لأن الطابعات الحرارية لا تدعم الخط العربي. "
            "عند عدم توفر طابعة يُحفظ الإيصال كملف نصي وصورة داخل مجلد logs/receipts."
        )
        note.setObjectName("HintText")
        note.setWordWrap(True)
        layout.addWidget(note)
        return card

    def _build_system_card(self) -> QWidget:
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        layout.addWidget(self._title("النظام"))
        layout.addWidget(Divider())

        self.require_shift = QCheckBox("إلزام فتح الوردية قبل البيع")
        layout.addWidget(self.require_shift)
        self.low_stock_alerts = QCheckBox("تنبيهات المخزون المنخفض")
        layout.addWidget(self.low_stock_alerts)

        self.db_info = QLabel("")
        self.db_info.setObjectName("HintText")
        self.db_info.setWordWrap(True)
        layout.addWidget(self.db_info)

        verify = QPushButton("فحص سلامة قاعدة البيانات")
        verify.setObjectName("GhostAction")
        verify.setMinimumHeight(config.MIN_TOUCH_TARGET)
        verify.clicked.connect(self._verify_db)
        layout.addWidget(verify)
        return card

    def _build_about_card(self) -> QWidget:
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        layout.addWidget(self._title("عن التطبيق"))

        about = QLabel(
            f"{config.APP_NAME} — {config.APP_NAME_AR}\n"
            f"الإصدار {config.VERSION} — إصدار نهائي (المرحلة {config.PHASE} من 5)\n"
            "يعمل بالكامل بدون إنترنت: لا سحابة، لا خوادم، لا تتبّع.\n"
            f"قاعدة البيانات: {config.DB_PATH}"
        )
        about.setObjectName("HintText")
        about.setWordWrap(True)
        layout.addWidget(about)

        save = QPushButton("حفظ الإعدادات")
        save.setObjectName("PrimaryAction")
        save.setMinimumHeight(config.MIN_TOUCH_TARGET + 8)
        save.clicked.connect(self._save)
        layout.addWidget(save)
        return card

    @staticmethod
    def _title(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("SectionTitle")
        return label

    # -- data -------------------------------------------------------------- #
    def reload(self) -> None:
        settings = self.controller.settings()
        self.cafe_name.setText(settings.get("cafe_name", ""))
        self.cafe_phone.setText(settings.get("cafe_phone", ""))
        self.cafe_address.setText(settings.get("cafe_address", ""))
        self.tax_number.setText(settings.get("tax_number", ""))
        self.receipt_footer.setText(settings.get("receipt_footer", ""))

        try:
            rate = float(settings.get("tax_rate", "0") or 0)
        except ValueError:
            rate = 0.0
        self.tax_spin.blockSignals(True)
        self.tax_spin.setValue(rate * 100.0)
        self.tax_spin.blockSignals(False)
        self._update_tax_preview()

        self.printer_enabled.setChecked(
            str(settings.get("printer_enabled", "0")) in ("1", "true", "yes")
        )
        index = self.printer_backend.findData(settings.get("printer_backend", "auto"))
        self.printer_backend.setCurrentIndex(index if index >= 0 else 0)
        self.printer_name.setText(settings.get("printer_name", ""))
        width_index = self.receipt_width.findData(settings.get("receipt_width_mm", "80"))
        self.receipt_width.setCurrentIndex(width_index if width_index >= 0 else 0)

        self.require_shift.setChecked(
            str(settings.get("require_open_shift", "1")) in ("1", "true", "yes")
        )
        self.low_stock_alerts.setChecked(
            str(settings.get("low_stock_alerts", "1")) in ("1", "true", "yes")
        )

        status = self.controller.printer_status()
        self.printer_status.setText(
            f"الحالة: {'مُفعّلة' if status['enabled'] else 'غير مُفعّلة'}  |  "
            f"الطريقة المكتشفة: {status['backend']}  |  "
            f"python-escpos: {'مثبّت' if status['escpos_installed'] else 'غير مثبّت'}"
        )

        db = self.controller.db
        size = db.db_size_bytes()
        self.db_info.setText(
            f"حجم قاعدة البيانات: {size:,} بايت  |  "
            f"إصدار المخطط: {db.query_value('PRAGMA user_version')}  |  "
            f"وضع السجل: {db.query_value('PRAGMA journal_mode')}"
        )

    def _update_tax_preview(self) -> None:
        rate = self.tax_spin.value() / 100.0
        if rate <= 0:
            self.tax_preview.setText("الضريبة صفر — لن يظهر سطر ضريبة في الإيصال")
            return
        sample = config.extract_tax_from_inclusive(2500, __import__("decimal").Decimal(str(rate)))
        self.tax_preview.setText(
            f"مثال: فاتورة {config.format_money(2500)} تحتوي ضريبة "
            f"{config.format_money(sample)}"
        )

    # -- actions ------------------------------------------------------------ #
    def _save(self) -> None:
        try:
            self.controller.set_tax_rate(str(self.tax_spin.value() / 100.0))
            self.controller.update_settings({
                "cafe_name": self.cafe_name.text().strip(),
                "cafe_phone": self.cafe_phone.text().strip(),
                "cafe_address": self.cafe_address.text().strip(),
                "tax_number": self.tax_number.text().strip(),
                "receipt_footer": self.receipt_footer.text().strip(),
                "printer_enabled": "1" if self.printer_enabled.isChecked() else "0",
                "printer_backend": self.printer_backend.currentData(),
                "printer_name": self.printer_name.text().strip(),
                "receipt_width_mm": self.receipt_width.currentData(),
                "require_open_shift": "1" if self.require_shift.isChecked() else "0",
                "low_stock_alerts": "1" if self.low_stock_alerts.isChecked() else "0",
            })
        except MenuError as exc:
            self.toast.show_message(str(exc), "error", 5000)
            return
        except Exception as exc:
            logger.exception("تعذّر حفظ الإعدادات")
            self.toast.show_message(f"تعذّر الحفظ: {exc}", "error", 5000)
            return
        self.toast.show_message("تم حفظ الإعدادات", "success", 4000)

    def _verify_db(self) -> None:
        try:
            ok = self.controller.db.integrity_check()
        except Exception as exc:
            self.toast.show_message(f"فشل الفحص: {exc}", "error", 6000)
            return
        self.toast.show_message(
            "قاعدة البيانات سليمة" if ok else "تم اكتشاف مشكلة في قاعدة البيانات",
            "success" if ok else "error", 6000,
        )
