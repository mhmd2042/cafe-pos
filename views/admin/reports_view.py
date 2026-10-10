"""
views/admin/reports_view.py — sales analytics: daily/monthly, top items, peak
hours, category split, and CSV/HTML export.

The charts are hand-drawn (views/admin/charts.py) so there is no charting
dependency and Arabic labels render with the app's own font.
"""

from __future__ import annotations

import logging
from datetime import date

from PyQt6.QtCore import Qt, QDate
from PyQt6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import config
from controllers.admin_controller import AdminController, SalesReport
from views.admin.charts import BarChart, BarDatum
from views.widgets import Divider, Toast

logger = logging.getLogger(__name__)

__all__ = ["ReportsView"]


class StatCard(QFrame):
    """One headline figure."""

    def __init__(self, caption: str, value: str = "—", accent: bool = False,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(2)

        self.caption = QLabel(caption)
        self.caption.setObjectName("FieldLabel")
        self.value = QLabel(value)
        self.value.setObjectName("TotalValue" if accent else "MoneyValue")
        layout.addWidget(self.caption)
        layout.addWidget(self.value)

    def set_value(self, text: str) -> None:
        self.value.setText(text)


class ReportsView(QWidget):
    """Reporting page. `reload()` re-runs the current period."""

    def __init__(self, auth, controller: AdminController,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.auth = auth
        self.controller = controller
        self.report: SalesReport | None = None

        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self._build()
        self.reload()

    # -- construction ------------------------------------------------------ #
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        root.addWidget(self._build_toolbar())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        self.body = QVBoxLayout(body)
        self.body.setContentsMargins(0, 0, 6, 0)
        self.body.setSpacing(12)

        self.body.addWidget(self._build_cards())
        self.body.addWidget(self._build_daily_chart())
        self.body.addWidget(self._build_middle_row())
        self.body.addWidget(self._build_peak_chart())
        self.body.addWidget(self._build_category_table())

        self.body.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        self.toast = Toast()
        root.addWidget(self.toast)

    def _build_toolbar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("Card")
        # Seven controls in one QHBoxLayout forced a 950px minimum on the whole
        # reports page. A QGridLayout lets them wrap onto a second row when the
        # window is narrow, so the page follows the window instead of the other
        # way round.
        layout = QGridLayout(bar)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        title = QLabel("التقارير والتحليلات")
        title.setObjectName("SectionTitle")
        layout.addWidget(title, 0, 0, 1, 3)
        layout.setColumnStretch(2, 1)

        self.period_combo = QComboBox()
        self.period_combo.addItem("اليوم", "today")
        self.period_combo.addItem("أمس", "yesterday")
        self.period_combo.addItem("آخر 7 أيام", "last7")
        self.period_combo.addItem("هذا الشهر", "month")
        self.period_combo.addItem("فترة مخصصة", "custom")
        self.period_combo.setMinimumHeight(config.MIN_TOUCH_TARGET - 8)
        self.period_combo.currentIndexChanged.connect(self._on_period_changed)
        layout.addWidget(self.period_combo, 1, 0)

        self.from_edit = QDateEdit(QDate.currentDate().addDays(-7))
        self.to_edit = QDateEdit(QDate.currentDate())
        for column, editor in enumerate((self.from_edit, self.to_edit), start=1):
            editor.setCalendarPopup(True)
            editor.setDisplayFormat("yyyy-MM-dd")
            editor.setMinimumHeight(config.MIN_TOUCH_TARGET - 8)
            editor.setEnabled(False)
            editor.dateChanged.connect(self.reload)
            layout.addWidget(editor, 1, column)

        export_csv = QPushButton("تصدير CSV")
        export_csv.setObjectName("GhostAction")
        export_csv.setMinimumHeight(config.MIN_TOUCH_TARGET - 8)
        export_csv.clicked.connect(self._export_csv)
        layout.addWidget(export_csv, 2, 0)

        export_html = QPushButton("تصدير تقرير (HTML/PDF)")
        export_html.setObjectName("PrimaryAction")
        export_html.setMinimumHeight(config.MIN_TOUCH_TARGET - 8)
        export_html.setToolTip("يفتح في المتصفح ويمكن طباعته أو حفظه PDF")
        export_html.clicked.connect(self._export_html)
        layout.addWidget(export_html, 2, 1, 1, 2)
        return bar

    def _build_cards(self) -> QWidget:
        box = QWidget()
        layout = QGridLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.cards = {
            "orders": StatCard("عدد الطلبات"),
            "gross": StatCard("إجمالي المبيعات", accent=True),
            "avg": StatCard("متوسط الطلب"),
            "cash": StatCard("نقداً"),
            "card": StatCard("بطاقة"),
            "margin": StatCard("الربح الإجمالي", accent=True),
        }
        self._cards_box = box
        self._cards_layout = layout
        # The column count follows the available width, so the six cards reflow
        # from 3x2 on a normal screen to 2x3 (or 1x6) when the window is narrow.
        # A fixed 3-column grid made the reports page demand 950px, which a
        # 1366x768 laptop at 125% scaling cannot give it.
        self._cards_columns = 0
        self._reflow_cards(3)
        return box

    def _reflow_cards(self, columns: int) -> None:
        """Lay the stat cards out in `columns` columns."""
        if columns == self._cards_columns:
            return
        self._cards_columns = columns
        layout = self._cards_layout
        # takeAt() only detaches the layout item; the card keeps living so it can
        # be re-added in the new shape. deleteLater() here would destroy it.
        while layout.count():
            layout.takeAt(0)
        for index, card in enumerate(self.cards.values()):
            layout.addWidget(card, index // columns, index % columns)
        for column in range(3):
            layout.setColumnStretch(column, 1 if column < columns else 0)

    def resizeEvent(self, a0) -> None:  # noqa: N802 - Qt naming
        """Reflow the stat cards as the window width changes."""
        super().resizeEvent(a0)
        if not hasattr(self, "_cards_layout"):
            return
        width = self.width()
        # ~200px per card plus spacing is the readable minimum.
        if width >= 780:
            columns = 3
        elif width >= 520:
            columns = 2
        else:
            columns = 1
        self._reflow_cards(columns)

        # The two middle tables sit side by side only when there is room for
        # both; below that they stack so neither is squeezed to a few pixels.
        if hasattr(self, "_middle_box"):
            self._set_middle_orientation(horizontal=width >= 620)

    def _build_daily_chart(self) -> QWidget:
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        self.daily_caption = QLabel("المبيعات اليومية")
        self.daily_caption.setObjectName("SectionTitle")
        layout.addWidget(self.daily_caption)

        self.daily_chart = BarChart(orientation="vertical",
                                    value_formatter=lambda v: f"{v:,.0f}")
        self.daily_chart.setMinimumHeight(190)
        layout.addWidget(self.daily_chart)
        return card

    def _build_middle_row(self) -> QWidget:
        # The two tables stack vertically when the window is narrow: side by
        # side they need ~700px between them, which the reports page does not
        # have at the window minimum. A QSplitter would drag, not reflow, so the
        # direction is chosen in resizeEvent instead.
        box = QWidget()
        self._middle_box = box
        self._middle_horizontal = None
        self._middle_layout = None

        self.top_card = QFrame()
        self.top_card.setObjectName("Card")
        top_layout = QVBoxLayout(self.top_card)
        top_layout.setContentsMargins(14, 12, 14, 12)
        top_layout.setSpacing(8)
        top_title = QLabel("الأصناف الأكثر مبيعاً")
        top_title.setObjectName("SectionTitle")
        top_layout.addWidget(top_title)

        self.top_table = self._make_table(["الصنف", "الكمية", "الإيراد"])
        top_layout.addWidget(self.top_table)

        self.pay_card = QFrame()
        self.pay_card.setObjectName("Card")
        pay_layout = QVBoxLayout(self.pay_card)
        pay_layout.setContentsMargins(14, 12, 14, 12)
        pay_layout.setSpacing(8)
        pay_title = QLabel("طرق الدفع")
        pay_title.setObjectName("SectionTitle")
        pay_layout.addWidget(pay_title)

        self.payment_chart = BarChart(orientation="horizontal",
                                      value_formatter=lambda v: config.format_money(int(v)))
        self.payment_chart.setMinimumHeight(150)
        pay_layout.addWidget(self.payment_chart)

        self._set_middle_orientation(horizontal=True)
        return box

    def _set_middle_orientation(self, *, horizontal: bool) -> None:
        """Rebuild the middle row as a row or a column."""
        if self._middle_horizontal is horizontal:
            return
        self._middle_horizontal = horizontal

        if self._middle_layout is not None:
            while self._middle_layout.count():
                self._middle_layout.takeAt(0)
            # Detach the old layout from the box before installing the new one.
            old = self._middle_box.layout()
            if old is not None:
                QWidget().setLayout(old)

        layout = QHBoxLayout() if horizontal else QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        if horizontal:
            layout.addWidget(self.top_card, 3)
            layout.addWidget(self.pay_card, 2)
        else:
            layout.addWidget(self.top_card, 3)
            layout.addWidget(self.pay_card, 2)
        self._middle_box.setLayout(layout)
        self._middle_layout = layout

    def _build_peak_chart(self) -> QWidget:
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        self.peak_caption = QLabel("ساعات الذروة")
        self.peak_caption.setObjectName("SectionTitle")
        layout.addWidget(self.peak_caption)

        self.peak_chart = BarChart(orientation="vertical",
                                   value_formatter=lambda v: f"{v:,.0f}")
        self.peak_chart.setMinimumHeight(180)
        layout.addWidget(self.peak_chart)
        return card

    def _build_category_table(self) -> QWidget:
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        title = QLabel("المبيعات حسب التصنيف")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        self.category_table = self._make_table(["التصنيف", "الكمية", "الإيراد"])
        layout.addWidget(self.category_table)
        return card

    @staticmethod
    def _make_table(headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        table.setAlternatingRowColors(True)
        table.setMinimumHeight(220)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, len(headers)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        return table

    # -- data -------------------------------------------------------------- #
    def _period_range(self) -> tuple[str, str, str]:
        key = self.period_combo.currentData()
        today = date.today()
        if key == "today":
            return today.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d"), "تقرير اليوم"
        if key == "yesterday":
            day = today.replace(day=today.day - 1) if today.day > 1 else today
            from datetime import timedelta

            day = today - timedelta(days=1)
            return day.strftime("%Y-%m-%d"), day.strftime("%Y-%m-%d"), "تقرير أمس"
        if key == "last7":
            from datetime import timedelta

            start = today - timedelta(days=6)
            return start.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d"), "آخر 7 أيام"
        if key == "month":
            return self.controller.month_start(today), today.strftime("%Y-%m-%d"), "هذا الشهر"
        return (self.from_edit.date().toString("yyyy-MM-dd"),
                self.to_edit.date().toString("yyyy-MM-dd"), "فترة مخصصة")

    def _on_period_changed(self) -> None:
        custom = self.period_combo.currentData() == "custom"
        self.from_edit.setEnabled(custom)
        self.to_edit.setEnabled(custom)
        self.reload()

    def reload(self) -> None:
        start, end, label = self._period_range()
        try:
            self.report = self.controller.sales_report(start, end, label=label)
        except Exception as exc:
            logger.exception("تعذّر إنشاء التقرير")
            self.toast.show_message(f"تعذّر تحميل التقرير: {exc}", "error", 6000)
            return
        self._render(self.report)

    def _render(self, report: SalesReport) -> None:
        money = config.format_money
        self.cards["orders"].set_value(f"{report.order_count:,}")
        self.cards["gross"].set_value(money(report.gross_minor))
        self.cards["avg"].set_value(money(report.avg_order_minor))
        self.cards["cash"].set_value(money(report.cash_minor))
        self.cards["card"].set_value(money(report.card_minor))
        self.cards["margin"].set_value(
            f"{money(report.gross_margin_minor)} ({report.margin_percent:.0f}%)"
        )

        # daily series (cap the labels so a long month stays readable)
        daily = report.daily
        step = max(1, len(daily) // 12)
        self.daily_chart.set_data([
            BarDatum(
                label=row["day"][5:],                       # MM-DD
                value=row["gross_minor"],
                highlight=row["gross_minor"] == max(
                    (d["gross_minor"] for d in daily), default=0
                ) and row["gross_minor"] > 0,
                tooltip=f"{row['day']}: {money(row['gross_minor'])} ({row['orders']} طلب)",
            )
            for index, row in enumerate(daily) if index % step == 0
        ])
        self.daily_caption.setText(
            f"المبيعات اليومية — {report.start} إلى {report.end}"
            + (f"   |   طلبات ملغاة: {report.voided_count}" if report.voided_count else "")
        )

        self._fill_table(self.top_table, [
            [row["name"], f"{row['qty']:,}", money(row["revenue_minor"])]
            for row in report.top_items
        ])
        self._fill_table(self.category_table, [
            [row["name"], f"{row['qty']:,}", money(row["revenue_minor"])]
            for row in report.by_category
        ])

        self.payment_chart.set_data([
            BarDatum(label=row["label"], value=row["total_minor"],
                     tooltip=f"{row['count']} طلب")
            for row in report.by_payment
        ])

        busiest = report.busiest_hour
        peak_data = []
        for row in report.peak_hours:
            peak_data.append(BarDatum(
                label=row["label"][:2],
                value=row["orders"],
                highlight=bool(busiest and row["hour"] == busiest["hour"]),
                tooltip=f"{row['label']}: {row['orders']} طلب",
            ))
        self.peak_chart.set_data(peak_data)
        self.peak_caption.setText(
            "ساعات الذروة — عدد الطلبات لكل ساعة"
            + (f"   |   الأكثر ازدحاماً: {busiest['label']} ({busiest['orders']} طلب)"
               if busiest and busiest["orders"] else "")
        )

    @staticmethod
    def _fill_table(table: QTableWidget, rows: list[list[str]]) -> None:
        table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column, value in enumerate(row):
                item = QTableWidgetItem(str(value))
                if column > 0:
                    item.setTextAlignment(int(Qt.AlignmentFlag.AlignLeft
                                              | Qt.AlignmentFlag.AlignVCenter))
                table.setItem(row_index, column, item)

    # -- exports ----------------------------------------------------------- #
    def _export_csv(self) -> None:
        if self.report is None:
            return
        try:
            path = self.controller.export_report_csv(self.report)
        except Exception as exc:
            logger.exception("تعذّر تصدير CSV")
            self.toast.show_message(f"تعذّر التصدير: {exc}", "error", 6000)
            return
        self.toast.show_message(f"تم التصدير: {path}", "success", 8000)

    def _export_html(self) -> None:
        if self.report is None:
            return
        try:
            path = self.controller.export_report_html(self.report)
        except Exception as exc:
            logger.exception("تعذّر تصدير التقرير")
            self.toast.show_message(f"تعذّر التصدير: {exc}", "error", 6000)
            return
        self.toast.show_message(f"تم إنشاء التقرير: {path}", "success", 8000)

    def apply_chart_theme(self, tokens: dict[str, str]) -> None:
        """Let the active theme drive the chart colours."""
        for chart in (self.daily_chart, self.peak_chart, self.payment_chart):
            chart.setProperty("chartText", tokens.get("text", "#F6F0E7"))
            chart.setProperty("chartMuted", tokens.get("text_muted", "#B9AC9C"))
            chart.setProperty("chartGrid", tokens.get("border", "#3D352E"))
            chart.set_colors(tokens.get("accent", "#C98F4B"),
                             tokens.get("accent_hover", "#E0A860"))
