"""
views/widgets.py — shared UI pieces used by more than one screen.

Kept deliberately small: Toast (soft inline notice) and FlowLayout (the
responsive product grid). Both are used by the login screen and the POS.
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QScrollArea,
    QSizePolicy,
    QWidget,
)

__all__ = ["Toast", "FlowLayout", "Divider", "QuantityStepper", "ResponsivePanel",
           "make_scroll"]


def make_scroll(body: QWidget) -> QScrollArea:
    """
    Wrap `body` in a chrome-free vertical scroll area that does not dictate the
    width of whatever contains it.

    A plain QScrollArea reports a minimum width taken from its content, which
    then propagates up through every layout above it: a 213px-wide editor pane
    ended up demanding 284px once the scrollbar and the content's own minimum
    were counted, and the pane was painted outside its parent.

    QScrollArea does not offer a switch for this — `setMinimumWidth(0)` is
    ignored because the viewport still propagates the widget's hint — so the
    hint itself is overridden here. Height is left alone: the vertical hint is
    what makes the surrounding column scroll.
    """
    scroll = _WidthAgnosticScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setWidget(body)
    return scroll


class _WidthAgnosticScrollArea(QScrollArea):
    """A QScrollArea whose width never depends on its content."""

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        hint = super().minimumSizeHint()
        return QSize(0, hint.height())

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        hint = super().sizeHint()
        return QSize(0, hint.height())


class ResponsivePanel(QFrame):
    """
    A side panel that keeps its intended width but gives space back when the
    window is too narrow to hold it.

    A plain setFixedWidth makes the whole row refuse to shrink: on a small or
    high-DPI screen the neighbouring content is squeezed to nothing and the
    widgets overlap. This panel instead reports its intended width as the size
    hint and its floor as the minimum, so Qt gives it `preferred_width` when
    there is room and shrinks it towards `min_width` when there is not. It is
    never pinned to one width, which is what let children paint outside it.

    Used by the POS rail/cart, the admin drawer and the menu/inventory columns.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        preferred_width: int,
        min_width: int,
        width_share: float,
        floor_height: int = 0,
    ) -> None:
        super().__init__(parent)
        self._preferred = preferred_width
        self._min = min_width
        self._share = width_share
        # The minimum stays at zero on purpose. Qt never shrinks a widget below
        # its minimumWidth, so a floor here would make the panel wider than a
        # parent that cannot hold it — the exact overflow this class exists to
        # prevent. `min_width` is honoured through the size hint below instead:
        # Qt prefers the hint, so the panel only goes below it when the row
        # genuinely has no room, and the panel's scroll area carries the rest.
        self.setMinimumWidth(0)
        self.setMaximumWidth(preferred_width)
        if floor_height:
            self.setMinimumHeight(floor_height)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        """The width the panel wants: its share of the window, clamped."""
        parent = self.parentWidget()
        if parent is None or parent.width() <= 0:
            return QSize(self._preferred, super().sizeHint().height())
        target = int(parent.width() * self._share)
        target = max(self._min, min(self._preferred, target))
        return QSize(target, super().sizeHint().height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        """Never demand more width than the parent has."""
        parent = self.parentWidget()
        height = super().minimumSizeHint().height()
        if parent is None or parent.width() <= 0:
            return QSize(self._min, height)
        return QSize(min(self._min, parent.width()), height)


class Toast(QFrame):
    """
    Soft, non-blocking notice.

    Deliberately not a QMessageBox: during a rush, a modal that needs dismissing
    is worse than a line of text that fades. Use `timeout_ms=0` to keep it up
    until it is replaced.

    The toast is an OVERLAY, not a layout citizen. It stays parented to whatever
    widget it was created with but never claims a slot in that widget's layout,
    and it is resized to sit across the bottom of its parent. Adding it with
    addWidget() is harmless — it simply will not be laid out, because
    sizePolicy is set to Ignored and it is raised above its siblings.

    This matters because a hidden QFrame still reserves its full size when it is
    a layout item: measured at 640x480, one toast was enough to push a panel past
    the window and clip the keypad underneath it.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setVisible(False)
        # Ignored means "do not let me influence the layout at all", and the
        # explicit zero minimum makes that enforceable: without it Qt still
        # reports a minimumSizeHint of the label's height, which is enough to
        # reserve a slot in a parent layout.
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setMinimumSize(0, 0)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        self.label = QLabel("")
        self.label.setWordWrap(True)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.label, 1)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(lambda: self.setVisible(False))
        if parent is not None:
            parent.installEventFilter(self)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        """Report no minimum: the toast must never reserve layout space."""
        return QSize(0, 0)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        """Only used when the toast sizes itself for display."""
        hint = self.layout().sizeHint() if self.layout() else QSize(200, 44)
        return QSize(max(hint.width(), 200), max(hint.height(), 44))

    def eventFilter(self, obj, event):  # noqa: N802 - Qt naming
        """Keep the overlay pinned to the bottom of its parent."""
        if event.type() == QEvent.Type.Resize and obj is self.parentWidget():
            self._reposition()
        return super().eventFilter(obj, event)

    def _reposition(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        margin = 16
        height = max(self.sizeHint().height(), 44)
        self.setGeometry(
            margin,
            max(parent.height() - height - margin, 0),
            max(parent.width() - 2 * margin, 120),
            height,
        )
        self.raise_()

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
        self._reposition()
        self.setVisible(True)
        self.raise_()
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
