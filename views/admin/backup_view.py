"""
views/admin/backup_view.py — configure the backup destination and run backups.

Shows the detected drives so the admin picks a real mount point instead of
typing a path, reports whether the target is currently reachable, runs a manual
backup on demand, and lists the backup history from `backup_log`.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import config
from controllers.admin_controller import AdminController
from controllers.backup_controller import BackupController
from views.widgets import Divider, Toast

logger = logging.getLogger(__name__)

__all__ = ["BackupView"]


class BackupView(QWidget):
    """External-drive selection + manual backup + history."""

    def __init__(self, auth, controller: AdminController,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.auth = auth
        self.controller = controller
        self.backup = BackupController(controller.db)

        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self._build()
        self.reload()

    # -- construction ------------------------------------------------------ #
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        root.addWidget(self._build_target_card())
        root.addWidget(self._build_status_card())
        root.addWidget(self._build_history_card(), 1)

        self.toast = Toast()
        root.addWidget(self.toast)

    def _build_target_card(self) -> QWidget:
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        title = QLabel("وحدة التخزين الخارجية")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        hint = QLabel(
            "اختر مجلد النسخ الاحتياطي على قرص خارجي (USB أو HDD). "
            "عند فصل القرص يحفظ التطبيق نسخة داخل مجلد التطبيق وينبّهك دون توقف العمل."
        )
        hint.setObjectName("HintText")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addWidget(Divider())

        row = QHBoxLayout()
        row.setSpacing(8)

        self.path_edit = QLineEdit()
        # Show a plausible example for the platform the app is actually running
        # on — an admin on Windows should not be told to look for /media/usb.
        self.path_edit.setPlaceholderText(
            r"E:\Cafe_Backups" if config.IS_WINDOWS else "/media/usb/Cafe_Backups"
        )
        self.path_edit.setMinimumHeight(config.MIN_TOUCH_TARGET - 8)
        self.path_edit.textChanged.connect(lambda: self._update_target_state())
        row.addWidget(self.path_edit, 1)

        browse = QPushButton("استعراض…")
        browse.setObjectName("GhostAction")
        browse.setMinimumHeight(config.MIN_TOUCH_TARGET)
        browse.clicked.connect(self._browse)
        row.addWidget(browse)

        save = QPushButton("حفظ المسار")
        save.setObjectName("PrimaryAction")
        save.setMinimumHeight(config.MIN_TOUCH_TARGET)
        save.clicked.connect(self._save_target)
        row.addWidget(save)
        layout.addLayout(row)

        # Detected drives: a real mount point beats a typed path.
        detect_row = QHBoxLayout()
        detect_row.setSpacing(8)
        self.drive_combo = QComboBox()
        self.drive_combo.setMinimumHeight(config.MIN_TOUCH_TARGET - 8)
        self.drive_combo.setMinimumWidth(340)
        detect_row.addWidget(QLabel("الأقراص المكتشفة:"))
        detect_row.addWidget(self.drive_combo, 1)

        use_drive = QPushButton("استخدام القرص المحدد")
        use_drive.setObjectName("GhostAction")
        use_drive.setMinimumHeight(config.MIN_TOUCH_TARGET)
        use_drive.clicked.connect(self._use_detected_drive)
        detect_row.addWidget(use_drive)

        rescan = QPushButton("إعادة الفحص")
        rescan.setObjectName("GhostAction")
        rescan.setMinimumHeight(config.MIN_TOUCH_TARGET)
        rescan.clicked.connect(self._scan_drives)
        detect_row.addWidget(rescan)
        layout.addLayout(detect_row)

        options = QHBoxLayout()
        options.setSpacing(12)
        self.auto_check = QCheckBox("نسخة تلقائية عند إغلاق الوردية")
        self.auto_check.stateChanged.connect(self._save_options)
        options.addWidget(self.auto_check)

        options.addWidget(QLabel("عدد النسخ المحلية المحفوظة:"))
        self.keep_spin = QSpinBox()
        self.keep_spin.setRange(1, 365)
        self.keep_spin.setMinimumHeight(config.MIN_TOUCH_TARGET - 12)
        self.keep_spin.valueChanged.connect(self._save_options)
        options.addWidget(self.keep_spin)
        options.addStretch(1)
        layout.addLayout(options)
        return card

    def _build_status_card(self) -> QWidget:
        card = QFrame()
        card.setObjectName("CardRaised")
        layout = QGridLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        self.status_label = QLabel("—")
        self.status_label.setObjectName("SectionTitle")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label, 0, 0, 1, 3)

        self.detail_label = QLabel("")
        self.detail_label.setObjectName("HintText")
        self.detail_label.setWordWrap(True)
        layout.addWidget(self.detail_label, 1, 0, 1, 3)

        backup_now = QPushButton("إنشاء نسخة احتياطية الآن")
        backup_now.setObjectName("PrimaryAction")
        backup_now.setMinimumHeight(config.MIN_TOUCH_TARGET)
        backup_now.clicked.connect(self._backup_now)
        layout.addWidget(backup_now, 2, 0)

        test_printer = QPushButton("اختبار الطابعة")
        test_printer.setObjectName("GhostAction")
        test_printer.setMinimumHeight(config.MIN_TOUCH_TARGET)
        test_printer.clicked.connect(self._test_printer)
        layout.addWidget(test_printer, 2, 1)

        open_folder = QPushButton("فتح مجلد النسخ المحلي")
        open_folder.setObjectName("GhostAction")
        open_folder.setMinimumHeight(config.MIN_TOUCH_TARGET)
        open_folder.clicked.connect(self._open_local_folder)
        layout.addWidget(open_folder, 2, 2)
        return card

    def _build_history_card(self) -> QWidget:
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        title = QLabel("سجل النسخ الاحتياطي")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        self.history_table = QTableWidget(0, 5)
        self.history_table.setHorizontalHeaderLabels(
            ["التاريخ", "النوع", "الحالة", "الحجم", "المسار"]
        )
        self.history_table.verticalHeader().setVisible(False)
        self.history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.history_table.setAlternatingRowColors(True)
        header = self.history_table.horizontalHeader()
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        for column in range(4):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.history_table, 1)

        self.restore_label = QLabel("")
        self.restore_label.setObjectName("HintText")
        self.restore_label.setWordWrap(True)
        layout.addWidget(self.restore_label)
        return card

    # -- data -------------------------------------------------------------- #
    def reload(self) -> None:
        status = self.backup.status()
        self.path_edit.setText(status["target_dir"])
        self.auto_check.blockSignals(True)
        self.auto_check.setChecked(status["auto_on_shift_close"])
        self.auto_check.blockSignals(False)
        self.keep_spin.blockSignals(True)
        self.keep_spin.setValue(int(status["keep_local"]))
        self.keep_spin.blockSignals(False)

        self._scan_drives()
        self._update_target_state()
        self._load_history()

    def _scan_drives(self) -> None:
        self.drive_combo.clear()
        for drive in self.backup.detect_drives():
            suffix = f" — {drive.free_label} متاح" if drive.free_bytes else ""
            prefix = "★ " if drive.is_removable else ""
            self.drive_combo.addItem(f"{prefix}{drive.label}{suffix}", drive.path)

    def _update_target_state(self) -> None:
        raw = self.path_edit.text().strip()
        if not raw:
            self.status_label.setText("لم تُحدَّد وحدة خارجية")
            self.status_label.setObjectName("WarningText")
            self.detail_label.setText(
                "النسخ الاحتياطي يعمل الآن داخل مجلد التطبيق فقط. "
                "يُنصح بشدة بتحديد قرص خارجي."
            )
        else:
            path = Path(raw).expanduser()
            available = self.backup._is_writable_directory(path)
            if available:
                separate = self.backup.is_on_separate_device(path)
                self.status_label.setText(
                    "الوحدة الخارجية متصلة وجاهزة ✓".replace("✓", "")
                )
                self.status_label.setObjectName("SuccessText")
                self.detail_label.setText(
                    f"المسار: {path}\n"
                    + ("قرص منفصل عن قرص النظام — ممتاز."
                       if separate else
                       "تنبيه: هذا المسار على نفس قرص النظام، لذا لن يحميك من تلف القرص.")
                )
            else:
                self.status_label.setText("الوحدة الخارجية غير متصلة")
                self.status_label.setObjectName("WarningText")
                self.detail_label.setText(
                    f"تعذّر الوصول إلى: {raw}\n"
                    "سيتم حفظ النسخ داخل مجلد التطبيق حتى تعود الوحدة."
                )
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

        last = self.backup.last_successful()
        if last:
            self.restore_label.setText(
                f"آخر نسخة ناجحة: {last['created_at']} ({last['status']})\n"
                + self.backup.restore_hint(last["target_path"])
            )
        else:
            self.restore_label.setText("لا توجد نسخ احتياطية بعد.")

    def _load_history(self) -> None:
        history = self.backup.history(limit=30)
        self.history_table.setRowCount(len(history))
        status_labels = {"ok": "ناجحة", "fallback": "نسخة محلية (احتياطية)",
                         "failed": "فاشلة"}
        for row, entry in enumerate(history):
            size = int(entry["size_bytes"] or 0)
            size_text = (f"{size / 1024:.0f} KB" if size < 1024 * 1024
                         else f"{size / 1024 / 1024:.1f} MB")
            values = [
                entry["created_at"],
                "تلقائية" if entry["kind"] == config.BACKUP_AUTO else "يدوية",
                status_labels.get(entry["status"], entry["status"]),
                size_text if size else "—",
                entry["target_path"],
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                if column:
                    cell.setTextAlignment(int(Qt.AlignmentFlag.AlignLeft
                                              | Qt.AlignmentFlag.AlignVCenter))
                self.history_table.setItem(row, column, cell)

    # -- actions ------------------------------------------------------------ #
    def _browse(self) -> None:
        start = self.path_edit.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "اختر مجلد النسخ الاحتياطي", start)
        if chosen:
            self.path_edit.setText(chosen)

    def _use_detected_drive(self) -> None:
        path = self.drive_combo.currentData()
        if not path:
            return
        candidate = Path(path) / "Cafe_Backups"
        self.path_edit.setText(str(candidate))

    def _save_target(self) -> None:
        path = self.path_edit.text().strip()
        self.backup.set_target_dir(path)
        self._update_target_state()
        self.toast.show_message(
            f"تم حفظ مسار النسخ: {path}" if path else "تم إلغاء الوحدة الخارجية",
            "success", 4000,
        )

    def _save_options(self) -> None:
        try:
            self.controller.update_settings({
                "backup_auto_on_shift_close": "1" if self.auto_check.isChecked() else "0",
                "backup_keep_local": str(self.keep_spin.value()),
            })
        except Exception as exc:
            logger.exception("تعذّر حفظ إعدادات النسخ")
            self.toast.show_message(f"تعذّر الحفظ: {exc}", "error", 5000)

    def _backup_now(self) -> None:
        # Persist the typed path first so "backup now" uses what is on screen.
        self.backup.set_target_dir(self.path_edit.text().strip())
        result = self.backup.create_backup(config.BACKUP_MANUAL, reason="manual (admin)")
        kind = {"ok": "success", "fallback": "warning", "failed": "error"}.get(
            result.status, "info"
        )
        self.toast.show_message(result.message, kind, 8000)
        self.reload()

    def _test_printer(self) -> None:
        from services.printer_service import PrinterService

        result = PrinterService(self.controller.db).test_print()
        kind = "warning" if result.needs_attention else "success"
        self.toast.show_message(result.message, kind, 6000)

    def _open_local_folder(self) -> None:
        # config.open_in_file_manager picks the right launcher per platform
        # (startfile / open / xdg-open) and never raises.
        if not config.open_in_file_manager(config.LOCAL_BACKUP_DIR):
            self.toast.show_message(f"مجلد النسخ: {config.LOCAL_BACKUP_DIR}", "info", 6000)
