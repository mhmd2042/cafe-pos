"""
views/admin/charts.py — small dependency-free charts.

Deliberately hand-drawn with QPainter instead of pulling in matplotlib or
QtCharts: matplotlib would add ~40 MB and a second font stack (Arabic labels in
matplotlib need explicit font registration), and QtCharts is a separate wheel.
These are simple bar charts, and QPainter already renders Arabic correctly.

Everything is a widget, so it themes with the app and needs no file output.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from PyQt6.QtCore import QRect, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import QSizePolicy, QWidget

import config

__all__ = ["BarChart", "BarDatum"]


@dataclass(slots=True)
class BarDatum:
    label: str
    value: float
    tooltip: str = ""
    highlight: bool = False


class BarChart(QWidget):
    """
    Horizontal-by-default bar chart with Arabic labels.

    Vertical orientation is used for the peak-hours chart (24 narrow bars);
    horizontal for ranking charts (top items), where long Arabic names need the
    room a horizontal bar gives them.
    """

    def __init__(self, parent: QWidget | None = None, *, orientation: str = "horizontal",
                 value_formatter=None, bar_color: str | None = None,
                 highlight_color: str | None = None) -> None:
        super().__init__(parent)
        self.orientation = orientation
        self.data: list[BarDatum] = []
        self._format = value_formatter or (lambda v: f"{v:,.0f}")
        self.bar_color = bar_color or "#C98F4B"
        self.highlight_color = highlight_color or "#E0A860"
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(180)

    # -- data -------------------------------------------------------------- #
    def set_data(self, data: Sequence[BarDatum]) -> None:
        self.data = list(data)
        self.update()

    def set_colors(self, bar: str, highlight: str) -> None:
        self.bar_color, self.highlight_color = bar, highlight
        self.update()

    # -- painting ---------------------------------------------------------- #
    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setLayoutDirection(Qt.LayoutDirection.RightToLeft)

        surface = self.palette().color(self.backgroundRole())
        # Use the theme tokens the app already set on the widget, so charts match
        # whichever theme is active without a second colour source.
        text_color = QColor(self.property("chartText") or "#F6F0E7")
        muted = QColor(self.property("chartMuted") or "#B9AC9C")
        grid = QColor(self.property("chartGrid") or "#3D352E")

        painter.setPen(Qt.PenStyle.NoPen)
        painter.fillRect(self.rect(), Qt.BrushStyle.NoBrush)

        if not self.data:
            painter.setPen(QPen(muted))
            painter.drawText(self.rect(), int(Qt.AlignmentFlag.AlignCenter), "لا توجد بيانات")
            painter.end()
            return

        if self.orientation == "vertical":
            self._paint_vertical(painter, text_color, muted, grid)
        else:
            self._paint_horizontal(painter, text_color, muted, grid)
        painter.end()

    # -- vertical (peak hours) --------------------------------------------- #
    def _paint_vertical(self, painter: QPainter, text: QColor, muted: QColor,
                        grid: QColor) -> None:
        label_font = QFont(self.font())
        label_font.setPixelSize(11)
        metrics = QFontMetrics(label_font)
        bottom = self.height() - (metrics.height() + 8)
        top_pad = 22
        usable_h = max(10, bottom - top_pad)

        maximum = max((d.value for d in self.data), default=0) or 1
        count = len(self.data)
        slot = self.width() / max(1, count)
        # Cap the bar width: with one or two data points an uncapped bar spans
        # most of the chart and reads as a rendering bug rather than a figure.
        bar_w = max(3.0, min(slot * 0.62, 64.0))

        # baseline
        painter.setPen(QPen(grid, 1))
        painter.drawLine(0, bottom, self.width(), bottom)

        painter.setFont(label_font)
        for index, datum in enumerate(self.data):
            x_center = self.width() - (index + 0.5) * slot   # RTL: 00:00 on the right
            height = (datum.value / maximum) * usable_h if maximum else 0
            rect = QRectF(x_center - bar_w / 2, bottom - height, bar_w, height)

            color = QColor(self.highlight_color if datum.highlight else self.bar_color)
            if datum.value <= 0:
                color.setAlpha(60)
                rect.setHeight(2)
                rect.moveTop(bottom - 2)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(rect, 3, 3)

            if datum.value > 0:
                painter.setPen(QPen(text))
                value_text = self._format(datum.value)
                painter.drawText(
                    QRectF(x_center - slot / 2, bottom - height - 18, slot, 16),
                    int(Qt.AlignmentFlag.AlignCenter), value_text,
                )

            painter.setPen(QPen(muted))
            painter.drawText(
                QRectF(x_center - slot / 2, bottom + 2, slot, metrics.height()),
                int(Qt.AlignmentFlag.AlignCenter), datum.label,
            )

    # -- horizontal (rankings) --------------------------------------------- #
    def _paint_horizontal(self, painter: QPainter, text: QColor, muted: QColor,
                          grid: QColor) -> None:
        label_font = QFont(self.font())
        label_font.setPixelSize(13)
        metrics = QFontMetrics(label_font)
        painter.setFont(label_font)

        rows = len(self.data)
        row_h = max(28.0, min(46.0, self.height() / max(1, rows)))
        label_w = min(190.0, max(90.0, self.width() * 0.28))
        value_w = 110.0
        bar_area = max(20.0, self.width() - label_w - value_w - 16)

        maximum = max((d.value for d in self.data), default=0) or 1

        for index, datum in enumerate(self.data):
            y = index * row_h
            if y + row_h > self.height():
                break

            # label (right-aligned, RTL start)
            painter.setPen(QPen(text))
            label = datum.label
            elided = metrics.elidedText(label, Qt.TextElideMode.ElideRight, int(label_w))
            painter.drawText(
                QRectF(self.width() - label_w, y, label_w - 6, row_h),
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), elided,
            )

            # bar grows from the right edge of the bar area leftwards
            bar_x = self.width() - label_w - 6
            width = (datum.value / maximum) * bar_area if maximum else 0
            rect = QRectF(bar_x - width, y + row_h * 0.22, width, row_h * 0.56)
            color = QColor(self.highlight_color if datum.highlight else self.bar_color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(rect, 4, 4)

            # value on the far left
            painter.setPen(QPen(text))
            painter.drawText(
                QRectF(0, y, value_w, row_h),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                self._format(datum.value),
            )

    def sizeHint(self):  # noqa: N802
        from PyQt6.QtCore import QSize

        rows = max(3, len(self.data))
        return QSize(420, int(rows * 38) if self.orientation == "horizontal" else 200)
