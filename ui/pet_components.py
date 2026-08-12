from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ui.qt_utils import (
    ALIGN_CENTER,
    KEEP_ASPECT_RATIO,
    SMOOTH_TRANSFORMATION,
    _qt_enum,
)


@dataclass(frozen=True)
class PetActionConfig:
    name: str
    frames_dir: Path
    frame_interval_ms: int = 200
    protect_ms: int = 0
    loop: bool = True
    next_action: str | None = None
    auto_wait_ms: int | None = None
    wait_idle_after_ms: int | None = None
    idle_gap_ms: tuple[int, int] | None = None
    manual_advance: bool = False
    uninterruptible: bool = False


class PetAnimationView(QWidget):
    """Small visual component that owns frame loading and rendering."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.frames_dir: Path | None = None
        self.frames: list[QPixmap] = []
        self.frame_index = 0

        self.label = QLabel()
        self.label.setObjectName("PetImage")
        self.label.setAlignment(ALIGN_CENTER)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.label)

    def set_frames_dir(self, frames_dir: Path) -> None:
        self.frames_dir = frames_dir
        self.frames = load_frames(frames_dir)
        self.frame_index = 0
        self.show_current_frame()

    def advance(self, loop: bool = True) -> bool:
        if not self.frames:
            self.show_current_frame()
            return False
        if self.frame_index >= len(self.frames) - 1:
            if not loop:
                return False
            self.frame_index = 0
        else:
            self.frame_index += 1
        self.show_current_frame()
        return True

    def advance_manual(self) -> None:
        if self.frames:
            self.frame_index = (self.frame_index + 1) % len(self.frames)
            self.show_current_frame()

    def show_current_frame(self) -> None:
        if not self.frames:
            self.label.setText("pet")
            self.label.setPixmap(QPixmap())
            return
        pixmap = self.frames[self.frame_index]
        if pixmap.isNull():
            self.label.setText(str(self.frame_index + 1))
            self.label.setPixmap(QPixmap())
            return
        side = max(96, min(self.width(), self.height()))
        self.label.setText("")
        self.label.setPixmap(
            pixmap.scaled(
                side,
                side,
                KEEP_ASPECT_RATIO,
                SMOOTH_TRANSFORMATION,
            )
        )

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt override.
        super().resizeEvent(event)
        self.show_current_frame()


def load_frames(frames_dir: Path) -> list[QPixmap]:
    frame_paths = sorted(
        frames_dir.glob("*.png"),
        key=lambda path: (
            0,
            int(path.stem),
        )
        if path.stem.isdigit()
        else (1, path.stem),
    )
    return [QPixmap(str(path)) for path in frame_paths]


def coerce_config_value(config_key: str, value: Any) -> Any:
    if config_key == "auto_wait_ms":
        if value is None or value == "":
            return None
        return max(0, int(value))
    if config_key in {"frame_interval_ms", "protect_ms"}:
        if value is None or value == "":
            return 0
        return max(0, int(value))
    if config_key == "loop":
        if isinstance(value, str):
            return value.strip().lower() not in {"0", "false", "no", "off"}
        return bool(value)
    if config_key in {"manual_advance", "uninterruptible"}:
        if isinstance(value, str):
            return value.strip().lower() not in {"0", "false", "no", "off"}
        return bool(value)
    if config_key == "next_action":
        return None if value is None else str(value).strip().lower()
    return value


LEFT_BUTTON = _qt_enum(Qt, "MouseButton", "LeftButton")
