"""
views/widgets.py — shared UI pieces used by more than one screen.

Kept deliberately small: Toast (soft inline notice) and FlowLayout (the
responsive product grid). Both are used by the login screen and the POS.
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QSizePolicy,
    QWidget,
)

__all__ = ["Toast", "FlowLayout", "Divider", "QuantityStepper"]


class Toast(QFrame):
    """
    Soft, non-blocking notice.

    Deliberately not a QMessageBox: during a rush, a modal that needs dismissing
    is worse than a line of text that fades. Use `timeout_ms=0` to keep it up
    until it is replaced.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setVisible(False)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        self.label = QLabel("")
        self.label.setWordWrap(True)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.label, 1)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(lambda: self.setVisible(False))

    def show_message(self, text: str, kind: str = "info", timeout_ms: int = 4000) -> None:
        frame_name, label_name = {
            "error": ("ToastError", "ErrorText"),
            "success": ("ToastSuccess", "SuccessText"),
            "warning": ("ToastWarning", "WarningText"),
            "info": ("Toast", "StatusText"),
        }.get(kind, ("Toast", "StatusText"))

        self.setObjectName(frame_name)
        self.label.setObjectName(label_name)
        self.style().unpolish(self)
        self.style().polish(self)
        self.label.setText(text)
        self.setVisible(True)
        self._timer.stop()
        if timeout_ms > 0:
            self._timer.start(timeout_ms)

    def hide_message(self) -> None:
        self._timer.stop()
        self.setVisible(False)


class FlowLayout(QLayout):
    """
    Left-to-right wrapping layout.

    This is what makes the product grid responsive: tiles keep their minimum
    touch size and reflow to as many columns as the window allows, instead of
    being squeezed by a fixed grid.
    """

    def __init__(self, parent: QWidget | None = None, margin: int = 0, spacing: int = 12) -> None:
        super().__init__(parent)
        self._items: list = []
        self._spacing = spacing
        self.setContentsMargins(margin, margin, margin, margin)

    # -- QLayout plumbing -------------------------------------------------- #
    def addItem(self, item) -> None:  # noqa: N802 (Qt naming)
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):  # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):  # noqa: N802
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    # -- layout ------------------------------------------------------------ #
    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        margins = self.contentsMargins()
        effective = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y, line_height = effective.x(), effective.y(), 0

        for item in self._items:
            widget = item.widget()
            if widget is not None and widget.isHidden():
                continue
            space = self._spacing
            next_x = x + item.sizeHint().width() + space
            if next_x - space > effective.right() and line_height > 0:
                x = effective.x()
                y = y + line_height + space
                next_x = x + item.sizeHint().width() + space
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))
            x = next_x
            line_height = max(line_height, item.sizeHint().height())

        return y + line_height - rect.y() + margins.bottom()


class Divider(QFrame):
    """1px warm rule."""

    def __init__(self, parent: QWidget | None = None, *, vertical: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("Divider")
        if vertical:
            self.setFixedWidth(1)
        else:
            self.setFixedHeight(1)


class QuantityStepper(QWidget):
    """−  n  +  control with big touch targets, emitting change requests."""

    increment_requested = pyqtSignal(int)     # line_id
    decrement_requested = pyqtSignal(int)     # line_id

    def __init__(self, line_id: int, quantity: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from PyQt6.QtWidgets import QPushButton

        self.line_id = line_id
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.minus = QPushButton("−")
        self.minus.setObjectName("QtyButton")
        self.minus.setCursor(Qt.CursorShape.PointingHandCursor)
        self.minus.setToolTip("تقليل الكمية")
        self.minus.clicked.connect(lambda: self.decrement_requested.emit(self.line_id))

        self.value = QLabel(str(quantity))
        self.value.setObjectName("QtyValue")
        self.value.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.plus = QPushButton("+")
        self.plus.setObjectName("QtyButton")
        self.plus.setCursor(Qt.CursorShape.PointingHandCursor)
        self.plus.setToolTip("زيادة الكمية")
        self.plus.clicked.connect(lambda: self.increment_requested.emit(self.line_id))

        layout.addWidget(self.plus)
        layout.addWidget(self.value)
        layout.addWidget(self.minus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
