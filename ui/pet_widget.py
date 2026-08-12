from __future__ import annotations

import random
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPointF, QTimer, Signal
from PySide6.QtWidgets import QApplication, QHBoxLayout, QVBoxLayout, QWidget

from core.respone_from_watch import CodexPetStatus, QtPetStatusSource
from ui.pet_components import (
    LEFT_BUTTON,
    PetActionConfig,
    PetAnimationView,
    coerce_config_value,
)
from ui.quotes_widget import DEFAULT_QUOTES_PATH, PLACEMENT_ABOVE, QuoteBubbleWidget
from paths import asset_path


WAIT_FRAMES_DIR = asset_path("wait")
WAITINGS_DIR = asset_path("waitings")
RESPONSES_DIR = asset_path("respones")
PET_BODY_SIZE = 168

INTERACT_HIT = "interact_hit"


def _has_png_frames(frames_dir: Path) -> bool:
    return frames_dir.is_dir() and any(frames_dir.glob("*.png"))


IDLE_ACTIONS = tuple(
    path.name for path in sorted(WAITINGS_DIR.iterdir()) if _has_png_frames(path)
) if WAITINGS_DIR.exists() else ()


def _response_dir(action: str) -> Path:
    return RESPONSES_DIR / action


def _waiting_dir(action: str) -> Path:
    return WAITINGS_DIR / action


ACTION_CONFIGS: dict[str, PetActionConfig] = {
    CodexPetStatus.WAIT.value: PetActionConfig(
        name=CodexPetStatus.WAIT.value,
        frames_dir=WAIT_FRAMES_DIR,
        frame_interval_ms=200,
        protect_ms=300,
        wait_idle_after_ms=8_000,
        idle_gap_ms=(7_000, 16_000),
    ),
    CodexPetStatus.RECEIPT.value: PetActionConfig(
        name=CodexPetStatus.RECEIPT.value,
        frames_dir=_response_dir(CodexPetStatus.RECEIPT.value),
        frame_interval_ms=150,
        protect_ms=800,
    ),
    CodexPetStatus.THINKING.value: PetActionConfig(
        name=CodexPetStatus.THINKING.value,
        frames_dir=_response_dir(CodexPetStatus.THINKING.value),
        frame_interval_ms=160,
        protect_ms=500,
    ),
    CodexPetStatus.WORKING.value: PetActionConfig(
        name=CodexPetStatus.WORKING.value,
        frames_dir=_response_dir(CodexPetStatus.WORKING.value),
        frame_interval_ms=130,
        protect_ms=700,
    ),
    CodexPetStatus.REQUEST.value: PetActionConfig(
        name=CodexPetStatus.REQUEST.value,
        frames_dir=_response_dir(CodexPetStatus.REQUEST.value),
        frame_interval_ms=180,
        protect_ms=1_200,
    ),
    CodexPetStatus.COMPLETE.value: PetActionConfig(
        name=CodexPetStatus.COMPLETE.value,
        frames_dir=_response_dir(CodexPetStatus.COMPLETE.value),
        frame_interval_ms=160,
        protect_ms=1_200,
        auto_wait_ms=4_000,
    ),
    CodexPetStatus.ERROR.value: PetActionConfig(
        name=CodexPetStatus.ERROR.value,
        frames_dir=_response_dir(CodexPetStatus.ERROR.value),
        frame_interval_ms=180,
        protect_ms=1_800,
    ),
    CodexPetStatus.NEWCHAT.value: PetActionConfig(
        name=CodexPetStatus.NEWCHAT.value,
        frames_dir=_response_dir(CodexPetStatus.NEWCHAT.value),
        frame_interval_ms=150,
        protect_ms=1_000,
        auto_wait_ms=2_500,
    ),
    INTERACT_HIT: PetActionConfig(
        name=INTERACT_HIT,
        frames_dir=_response_dir(INTERACT_HIT),
        frame_interval_ms=200,
        protect_ms=3_000,
        loop=True,
        auto_wait_ms=3_000,
        manual_advance=True,
        uninterruptible=True,
    ),
}

for action_name in IDLE_ACTIONS:
    ACTION_CONFIGS[action_name] = PetActionConfig(
        name=action_name,
        frames_dir=_waiting_dir(action_name),
        frame_interval_ms=180,
        protect_ms=600,
        loop=False,
        next_action=CodexPetStatus.WAIT.value,
    )


class PetWidget(QWidget):
    bar_requested = Signal()
    status_event = Signal(dict)

    def __init__(self, frames_dir: Path = WAIT_FRAMES_DIR, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("PetWidget")
        self.current_action = self._status_from_frames_dir(frames_dir)
        self.current_config = self._config_for_action(self.current_action)
        self._pending_event: dict[str, Any] | None = None
        self._protected_until = 0.0
        self._drag_origin: QPointF | None = None
        self._press_position: QPointF | None = None
        self._drag_moved = False

        self.animation = PetAnimationView(self)
        self.animation.setFixedSize(PET_BODY_SIZE, PET_BODY_SIZE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addStretch(1)
        body_row = QHBoxLayout()
        body_row.setContentsMargins(0, 0, 0, 0)
        body_row.addStretch(1)
        body_row.addWidget(self.animation)
        body_row.addStretch(1)
        layout.addLayout(body_row)

        self.quote_bubble = QuoteBubbleWidget(
            quotes_path=DEFAULT_QUOTES_PATH,
            parent=self,
            can_show=self._can_show_quote_bubble,
            placement=PLACEMENT_ABOVE,
        )

        self.timer = QTimer(self)
        self.timer.setInterval(self.current_config.frame_interval_ms)
        self.timer.timeout.connect(self._advance_frame)
        self.protection_timer = QTimer(self)
        self.protection_timer.setSingleShot(True)
        self.protection_timer.timeout.connect(self._apply_pending_event)
        self.auto_wait_timer = QTimer(self)
        self.auto_wait_timer.setSingleShot(True)
        self.auto_wait_timer.timeout.connect(self._auto_wait_if_current)
        self.idle_timer = QTimer(self)
        self.idle_timer.setSingleShot(True)
        self.idle_timer.timeout.connect(self._play_random_idle_action)
        self.single_click_timer = QTimer(self)
        self.single_click_timer.setSingleShot(True)
        self.single_click_timer.setInterval(QApplication.doubleClickInterval())
        self.single_click_timer.timeout.connect(self._emit_single_click)

        self._apply_style()
        self._enter_action(self.current_action, clear_pending=True)

        self.status_source = QtPetStatusSource(parent=self) if QtPetStatusSource is not None else None
        if self.status_source is not None:
            self.status_source.status_changed.connect(self.apply_status_event)
            self.status_source.failed.connect(self._handle_status_source_failure)
            self.status_source.start()

    def start(self) -> None:
        if not self.timer.isActive() and not self.current_config.manual_advance:
            self.timer.start()
        if self.current_action == CodexPetStatus.WAIT.value:
            self._schedule_wait_idle()
        self._sync_quote_bubble_state()

    def stop(self) -> None:
        self.timer.stop()
        self.protection_timer.stop()
        self.auto_wait_timer.stop()
        self.idle_timer.stop()
        self.single_click_timer.stop()
        self.quote_bubble.stop()

    def apply_status_event(self, event: dict[str, Any]) -> None:
        self.status_event.emit(event)
        status = str(event.get("status") or "")
        if status:
            self.request_action(status, event)

    def set_status(self, status: str) -> None:
        self.request_action(status)

    def request_action(self, action: str, event: dict[str, Any] | None = None) -> None:
        action = self._normalize_action(action)
        if action == self.current_action and not event:
            return

        if self.current_config.uninterruptible and action != self.current_action:
            self._pending_event = {"status": action, **(event or {})}
            return

        if self._is_protected():
            self._pending_event = {"status": action, **(event or {})}
            self._arm_protection_timer()
            return

        self._enter_action(action, event, clear_pending=True)

    def mousePressEvent(self, event: Any) -> None:  # noqa: N802 - Qt override.
        if event.button() == LEFT_BUTTON:
            global_position = self._event_global_position(event)
            self._drag_origin = global_position - QPointF(self.window().frameGeometry().topLeft())
            self._press_position = global_position
            self._drag_moved = False
            if self.current_action == INTERACT_HIT:
                self._advance_interact_hit_frame()
            else:
                self.single_click_timer.start()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: Any) -> None:  # noqa: N802 - Qt override.
        if self._drag_origin is not None and event.buttons() & LEFT_BUTTON:
            global_position = self._event_global_position(event)
            drag_distance = self._drag_distance(global_position)
            if (
                self._press_position is not None
                and drag_distance >= QApplication.startDragDistance()
            ):
                self._drag_moved = True
                self.single_click_timer.stop()
            if self._drag_moved:
                self.window().move((global_position - self._drag_origin).toPoint())
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: Any) -> None:  # noqa: N802 - Qt override.
        if event.button() == LEFT_BUTTON:
            was_dragging = self._drag_moved
            self._drag_origin = None
            self._press_position = None
            self._drag_moved = False
            if was_dragging:
                self.single_click_timer.stop()
                event.accept()
                return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: Any) -> None:  # noqa: N802 - Qt override.
        if event.button() == LEFT_BUTTON:
            self.single_click_timer.stop()
            if self.current_action == INTERACT_HIT:
                self._advance_interact_hit_frame()
            else:
                self._enter_interact_hit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt override.
        if self.status_source is not None:
            self.status_source.stop()
        super().closeEvent(event)

    def showEvent(self, event: Any) -> None:  # noqa: N802 - Qt override.
        super().showEvent(event)
        self._position_quote_bubble()
        self._sync_quote_bubble_state()

    def hideEvent(self, event: Any) -> None:  # noqa: N802 - Qt override.
        self.quote_bubble.stop()
        super().hideEvent(event)

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt override.
        super().resizeEvent(event)
        self._position_quote_bubble()

    def _enter_action(
        self,
        action: str,
        event: dict[str, Any] | None = None,
        clear_pending: bool = False,
    ) -> None:
        config = self._config_for_action(action, event)
        frames_dir = self._frames_dir_for_config(config)

        self.current_action = config.name
        self.current_config = config
        if clear_pending:
            self._pending_event = None
        self._protected_until = time.monotonic() + config.protect_ms / 1000
        self.animation.set_frames_dir(frames_dir)
        self.timer.setInterval(config.frame_interval_ms)
        self.auto_wait_timer.stop()
        self.idle_timer.stop()
        self._arm_protection_timer()
        self._sync_quote_bubble_state()

        if config.manual_advance:
            self.timer.stop()
        elif self.isVisible() and not self.timer.isActive():
            self.timer.start()

        if config.auto_wait_ms is not None:
            self.auto_wait_timer.start(config.auto_wait_ms)
        if config.name == CodexPetStatus.WAIT.value:
            self._schedule_wait_idle()
            self._sync_quote_bubble_state()

    def _advance_frame(self) -> None:
        if self.current_config.manual_advance:
            return
        if self.animation.advance(loop=self.current_config.loop):
            return

        if self._pending_event is not None and not self._is_protected():
            self._apply_pending_event()
            return
        next_action = self.current_config.next_action or CodexPetStatus.WAIT.value
        self._enter_action(next_action, clear_pending=False)

    def _emit_single_click(self) -> None:
        if self._drag_moved:
            return
        self.bar_requested.emit()

    def _enter_interact_hit(self) -> None:
        self._pending_event = None
        self._enter_action(INTERACT_HIT, clear_pending=True)
        self._reset_interact_hit_timeout()

    def _advance_interact_hit_frame(self) -> None:
        self.animation.advance_manual()
        self._reset_interact_hit_timeout()

    def _reset_interact_hit_timeout(self) -> None:
        self._protected_until = time.monotonic() + 3
        self.auto_wait_timer.start(3_000)

    def _event_global_position(self, event: Any) -> QPointF:
        if hasattr(event, "globalPosition"):
            return event.globalPosition()
        return QPointF(event.globalPos())

    def _drag_distance(self, global_position: QPointF) -> float:
        if self._press_position is None:
            return 0
        delta = global_position - self._press_position
        return abs(delta.x()) + abs(delta.y())

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            #PetWidget, #PetImage {
                background: transparent;
                border: none;
            }
            """
        )

    def _handle_status_source_failure(self, message: str) -> None:
        print(f"Pet status watcher failed: {message}", flush=True)

    def _can_show_quote_bubble(self) -> bool:
        window = self.window()
        return (
            self.current_action == CodexPetStatus.WAIT.value
            and self.isVisible()
            and window is not None
            and window.isVisible()
            and not window.isMinimized()
        )

    def _position_quote_bubble(self) -> None:
        self.quote_bubble.set_anchor_rect(self.animation.geometry())
        self.quote_bubble.raise_()

    def _sync_quote_bubble_state(self) -> None:
        if self.current_action == CodexPetStatus.WAIT.value and self.isVisible():
            self._position_quote_bubble()
            self.quote_bubble.start()
        else:
            self.quote_bubble.stop()

    def _apply_pending_event(self) -> None:
        if self._is_protected():
            self._arm_protection_timer()
            return
        if self._pending_event is None:
            return
        event = self._pending_event
        self._pending_event = None
        self._enter_action(
            str(event.get("status") or CodexPetStatus.WAIT.value),
            event,
            clear_pending=True,
        )

    def _auto_wait_if_current(self) -> None:
        if self.current_action == INTERACT_HIT:
            self._pending_event = None
            self._enter_action(CodexPetStatus.WAIT.value, clear_pending=True)
            return
        if self._pending_event is not None:
            self._apply_pending_event()
            return
        if self.current_config.auto_wait_ms is not None:
            self.request_action(CodexPetStatus.WAIT.value)

    def _schedule_wait_idle(self) -> None:
        config = self.current_config
        if (
            not IDLE_ACTIONS
            or config.name != CodexPetStatus.WAIT.value
            or config.wait_idle_after_ms is None
            or config.idle_gap_ms is None
        ):
            return
        low, high = config.idle_gap_ms
        delay = config.wait_idle_after_ms + random.randint(low, high)
        self.idle_timer.start(delay)

    def _play_random_idle_action(self) -> None:
        if self.current_action != CodexPetStatus.WAIT.value or self._pending_event is not None:
            return
        self.request_action(random.choice(IDLE_ACTIONS))

    def _is_protected(self) -> bool:
        return time.monotonic() < self._protected_until

    def _arm_protection_timer(self) -> None:
        remaining_ms = int(max(0, (self._protected_until - time.monotonic()) * 1000))
        if remaining_ms > 0 and self._pending_event is not None:
            self.protection_timer.start(remaining_ms)

    def _normalize_action(self, action: str) -> str:
        action = action.strip().lower()
        if action in ACTION_CONFIGS:
            return action
        return CodexPetStatus.WAIT.value

    def _config_for_action(
        self,
        action: str,
        event: dict[str, Any] | None = None,
    ) -> PetActionConfig:
        config = ACTION_CONFIGS.get(action, ACTION_CONFIGS[CodexPetStatus.WAIT.value])
        if event is None:
            return config

        updates: dict[str, Any] = {}
        for event_key, config_key in {
            "frameIntervalMs": "frame_interval_ms",
            "protectMs": "protect_ms",
            "autoWaitMs": "auto_wait_ms",
            "loop": "loop",
            "nextAction": "next_action",
            "manualAdvance": "manual_advance",
            "uninterruptible": "uninterruptible",
        }.items():
            if event_key in event:
                updates[config_key] = coerce_config_value(config_key, event[event_key])
        return replace(config, **updates) if updates else config

    def _frames_dir_for_config(self, config: PetActionConfig) -> Path:
        if _has_png_frames(config.frames_dir):
            return config.frames_dir
        return WAIT_FRAMES_DIR

    def _status_from_frames_dir(self, frames_dir: Path) -> str:
        if frames_dir == WAIT_FRAMES_DIR:
            return CodexPetStatus.WAIT.value
        if frames_dir.parent == RESPONSES_DIR or frames_dir.parent == WAITINGS_DIR:
            return frames_dir.name
        return CodexPetStatus.WAIT.value
