from __future__ import annotations

import sys
from enum import Enum
from typing import Any

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QStackedLayout, QSystemTrayIcon, QWidget

from ui.bar_widget import BarWidget, CHARACTER_IMAGE
from ui.pet_widget import PetWidget


def _qt_enum(owner: Any, enum_name: str, value: str) -> Any:
    return getattr(getattr(owner, enum_name, owner), value)


LEFT_BUTTON = _qt_enum(Qt, "MouseButton", "LeftButton")


def _window_type(value: str) -> Any:
    return _qt_enum(Qt, "WindowType", value)


def _widget_attribute(value: str) -> Any:
    return _qt_enum(Qt, "WidgetAttribute", value)


class DisplayMode(Enum):
    BAR = "bar"
    PET = "pet"


class CocodexWidget(QWidget):
    """Frameless host window that switches between quota bar and pet modes."""

    BAR_SIZE = (490, 430)
    PET_SIZE = (280, 300)

    def __init__(
        self,
        source: list[str] | tuple[str, ...] | str | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.source = source
        self.mode = DisplayMode.BAR
        self.is_topmost = False
        self._drag_origin: QPointF | None = None

        self.setWindowTitle("Cocodex")
        self._apply_window_flags()
        self.setAttribute(_widget_attribute("WA_TranslucentBackground"), True)
        self.tray_icon = self._create_tray_icon()

        self.stack = QStackedLayout(self)
        self.stack.setContentsMargins(0, 0, 0, 0)
        self.bar_widget = BarWidget(source=source)
        self.pet_widget = PetWidget()
        self.stack.addWidget(self.bar_widget)
        self.stack.addWidget(self.pet_widget)

        self.bar_widget.pet_requested.connect(self.show_pet)
        self.bar_widget.minimize_requested.connect(self.showMinimized)
        self.bar_widget.tray_requested.connect(self.hide_to_tray)
        self.bar_widget.topmost_requested.connect(self.toggle_topmost)
        self.bar_widget.close_requested.connect(self.quit_app)
        self.pet_widget.bar_requested.connect(self.show_bar)
        self.pet_widget.status_event.connect(self.bar_widget.apply_codex_status_event)
        self.show_bar()

    def show_bar(self) -> None:
        previous_center = self.frameGeometry().center()
        self.mode = DisplayMode.BAR
        self.pet_widget.stop()
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        self.resize(*self.BAR_SIZE)
        self.move(previous_center - self.rect().center())
        self.stack.setCurrentWidget(self.bar_widget)
        self.bar_widget.resize(*self.BAR_SIZE)
        self.bar_widget.updateGeometry()
        self.bar_widget.update()
        self.bar_widget.set_topmost_state(self.is_topmost)

    def show_pet(self) -> None:
        previous_center = self.frameGeometry().center()
        self.mode = DisplayMode.PET
        self.setFixedSize(*self.PET_SIZE)
        self.move(previous_center - self.rect().center())
        self.stack.setCurrentWidget(self.pet_widget)
        self.pet_widget.start()

    def toggle_topmost(self) -> None:
        was_visible = self.isVisible()
        position = self.pos()
        self.is_topmost = not self.is_topmost
        self._apply_window_flags()
        self.move(position)
        self.bar_widget.set_topmost_state(self.is_topmost)
        if was_visible:
            self.show()

    def hide_to_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.showMinimized()
            return
        self.hide()

    def restore_from_tray(self) -> None:
        if self.mode is DisplayMode.PET:
            self.show_pet()
        elif self.stack.currentWidget() is not self.bar_widget:
            self.show_bar()
        self.show()
        self.raise_()
        self.activateWindow()

    def quit_app(self) -> None:
        self.tray_icon.hide()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _create_tray_icon(self) -> QSystemTrayIcon:
        app = QApplication.instance()
        if app is not None:
            app.setQuitOnLastWindowClosed(False)

        icon = QIcon(str(CHARACTER_IMAGE))
        tray_icon = QSystemTrayIcon(icon, self)
        tray_icon.setToolTip("Cocodex")

        menu = QMenu(self)
        show_action = QAction("显示", self)
        quit_action = QAction("退出", self)
        show_action.triggered.connect(self.restore_from_tray)
        quit_action.triggered.connect(self.quit_app)
        menu.addAction(show_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        tray_icon.setContextMenu(menu)
        tray_icon.activated.connect(self._handle_tray_activated)
        tray_icon.show()
        return tray_icon

    def _handle_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            _qt_enum(QSystemTrayIcon, "ActivationReason", "Trigger"),
            _qt_enum(QSystemTrayIcon, "ActivationReason", "DoubleClick"),
        ):
            self.restore_from_tray()

    def _apply_window_flags(self) -> None:
        flags = _window_type("FramelessWindowHint") | _window_type("Window")
        if self.is_topmost:
            flags |= _window_type("WindowStaysOnTopHint")
        self.setWindowFlags(flags)

    def mousePressEvent(self, event: Any) -> None:  # noqa: N802 - Qt override.
        if event.button() == LEFT_BUTTON:
            self._drag_origin = (
                self._event_global_position(event)
                - QPointF(self.frameGeometry().topLeft())
            )
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: Any) -> None:  # noqa: N802 - Qt override.
        if self._drag_origin is not None and event.buttons() & LEFT_BUTTON:
            self.move((self._event_global_position(event) - self._drag_origin).toPoint())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: Any) -> None:  # noqa: N802 - Qt override.
        self._drag_origin = None
        super().mouseReleaseEvent(event)

    def _event_global_position(self, event: Any) -> QPointF:
        if hasattr(event, "globalPosition"):
            return event.globalPosition()
        return QPointF(event.globalPos())


def run_demo(source: list[str] | tuple[str, ...] | str | None = None) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    widget = CocodexWidget(source=source)
    widget.show()
    if hasattr(app, "exec"):
        return app.exec()
    return app.exec_()
