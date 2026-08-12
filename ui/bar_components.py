from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QBrush, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui.qt_utils import (
    ALIGN_CENTER,
    KEEP_ASPECT_RATIO,
    NO_BRUSH,
    NO_PEN,
    SMOOTH_TRANSFORMATION,
    _dash,
    _qt_enum,
)


# Quota ring visual knobs. These are intentionally near the widget so you can
# tune the ring thickness and contrast without reading the whole layout file.
RING_STROKE_WIDTH = 31
RING_TRACK_COLOR = QColor("#d7d4ef")
RING_USED_COLOR = QColor("#6750e8")
RING_TEXT_COLOR = QColor("#5e72d9")
RING_VALUE_COLOR = QColor("#d85bbf")

#左边那个大圆环的box
class QuotaRing(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._used = 0
        self._used_text = "已使用 --%"
        self._remaining_text = "剩余 --%"
        self._reset_text = "距离重置 --"
        self.setMinimumSize(144, 144)
        self.setSizePolicy(
            _qt_enum(QSizePolicy, "Policy", "Expanding"),
            _qt_enum(QSizePolicy, "Policy", "Expanding"),
        )

    def set_used_percent(self, percent: int | float | None) -> None:
        if isinstance(percent, (int, float)) and math.isfinite(percent):
            self._used = max(0, min(100, int(round(percent))))
        else:
            self._used = 0
        self.update()

    def set_quota_summary(
        self,
        used_text: str = "已使用 --%",
        remaining_text: str = "剩余 --%",
        reset_text: str = "距离重置 --",
    ) -> None:
        self._used_text = used_text
        self._remaining_text = remaining_text
        self._reset_text = reset_text
        self.update()

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt override.
        super().paintEvent(event)
        painter = QPainter(self)
        try:
            painter.setRenderHint(_qt_enum(QPainter, "RenderHint", "Antialiasing"))
            side = max(1, min(self.width(), self.height()) - RING_STROKE_WIDTH - 16)
            ring_rect = QRectF(
                (self.width() - side) / 2,
                (self.height() - side) / 2,
                side,
                side,
            )

            painter.setBrush(NO_BRUSH)
            painter.setPen(_ring_pen(RING_TRACK_COLOR))
            painter.drawArc(ring_rect, 90 * 16, -360 * 16)
            painter.setPen(_ring_pen(RING_USED_COLOR))
            painter.drawArc(
                ring_rect,
                90 * 16,
                int(-360 * 16 * self._used / 100),
            )

            painter.setPen(NO_PEN)
            painter.setBrush(QColor(255, 250, 255, 192))
            painter.drawEllipse(ring_rect.adjusted(33, 33, -33, -33))
            

            painter.setPen(RING_TEXT_COLOR)
            painter.setFont(QFont("SimHei", 27, QFont.Weight.Black))
            painter.drawText(
                ring_rect.adjusted(20, 18, -20, -61),
                ALIGN_CENTER,
                f"{self._used}%",
            )

            painter.setFont(QFont("SimHei", 9, QFont.Weight.Black))
            _draw_centered_segments(
                painter,
                ring_rect.adjusted(18, 60, -18, -42),
                _quota_line_segments(self._used_text, self._remaining_text),
            )
            painter.setFont(QFont("SimHei", 8, QFont.Weight.Black))
            _draw_centered_segments(
                painter,
                ring_rect.adjusted(14, 83, -14, -25),
                _quota_reset_segments(self._reset_text),
            )
        finally:
            painter.end()

#绘制圆环的pen
def _ring_pen(brush_or_color: QBrush | QColor) -> QPen:
    pen = QPen(brush_or_color)
    pen.setWidth(RING_STROKE_WIDTH)
    pen.setCapStyle(_qt_enum(Qt, "PenCapStyle", "RoundCap"))
    return pen


def _quota_line_segments(used_text: str, remaining_text: str) -> list[tuple[str, QColor]]:
    return [
        ("已使用 ", RING_TEXT_COLOR),
        (_strip_prefix(used_text, "已使用 "), RING_VALUE_COLOR),
        (" · 剩余 ", RING_TEXT_COLOR),
        (_strip_prefix(remaining_text, "剩余 "), RING_VALUE_COLOR),
    ]


def _quota_reset_segments(reset_text: str) -> list[tuple[str, QColor]]:
    return [
        ("距离重置 ", RING_TEXT_COLOR),
        (_strip_prefix(reset_text, "距离重置 "), RING_VALUE_COLOR),
    ]


def _strip_prefix(text: str, prefix: str) -> str:
    return text[len(prefix) :] if text.startswith(prefix) else text


def _draw_centered_segments(
    painter: QPainter,
    rect: QRectF,
    segments: list[tuple[str, QColor]],
) -> None:
    metrics = painter.fontMetrics()
    total_width = sum(metrics.horizontalAdvance(text) for text, _color in segments)
    x = rect.center().x() - total_width / 2
    y = rect.center().y() + (metrics.ascent() - metrics.descent()) / 2
    for text, color in segments:
        painter.setPen(color)
        painter.drawText(int(round(x)), int(round(y)), text)
        x += metrics.horizontalAdvance(text)

#底下那些小 widgets
class MetricPill(QFrame):
    def __init__(self, icon: str, title: str, value: str = "--", accent: str = "#8ea7ff"):
        super().__init__()
        self.setObjectName("MetricPill")
        self.icon_label = QLabel(icon)
        self.title_label = QLabel(title)
        self.value_label = QLabel(value)
        self.value_label.setObjectName("MetricValue")
        self.icon_label.setStyleSheet(f"color: {accent};")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 7, 8, 7)
        layout.setSpacing(6)
        layout.addWidget(self.icon_label)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(1)
        text_layout.addWidget(self.title_label)
        text_layout.addWidget(self.value_label)
        layout.addLayout(text_layout, 1)

    def set_value(self, value: Any) -> None:
        self.value_label.setText(_dash(value))

#右边那些小widgets
class InfoTile(QFrame):
    def __init__(self, icon: str, title: str, value: str = "--", hint: str = ""):
        super().__init__()
        self.setObjectName("InfoTile")
        icon_width = 22
        self.icon_label = QLabel(icon)
        self.title_label = QLabel(title)
        self.value_label = QLabel(value)
        self.hint_label = QLabel(hint)
        self.value_label.setObjectName("TileValue")
        self.hint_label.setObjectName("TileHint")
        self.icon_label.setFixedWidth(icon_width)
        self.title_label.setAlignment(ALIGN_CENTER)
        self.value_label.setAlignment(ALIGN_CENTER)
        self.hint_label.setAlignment(ALIGN_CENTER)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)
        layout.addWidget(self.icon_label, 0)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(1)
        text_layout.addWidget(self.title_label)
        text_layout.addWidget(self.value_label)
        text_layout.addWidget(self.hint_label)
        layout.addLayout(text_layout, 1)
        layout.addSpacing(icon_width)

    def set_value(self, value: Any, hint: Any = "") -> None:
        self.value_label.setText(_dash(value))
        self.hint_label.setText("" if hint is None else str(hint))

#圆环底下的图
class ImageFrame(QFrame):
    def __init__(self, image_path: Path, parent: QWidget | None = None):
        super().__init__(parent)
        self.background_pixmap = QPixmap(str(image_path))

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt override.
        if self.background_pixmap.isNull():
            super().paintEvent(event)
            return

        painter = QPainter(self)
        try:
            painter.setRenderHint(_qt_enum(QPainter, "RenderHint", "Antialiasing"))
            clip = QPainterPath()
            clip.addRoundedRect(QRectF(self.rect()), 14, 14)
            painter.setClipPath(clip)

            scaled = self.background_pixmap.scaled(
                self.size(),
                _qt_enum(Qt, "AspectRatioMode", "KeepAspectRatioByExpanding"),
                SMOOTH_TRANSFORMATION,
            )
            offset = QPoint(
                (self.width() - scaled.width()) // 2,
                (self.height() - scaled.height()) // 2,
            )
            painter.setOpacity(0.85)
            painter.drawPixmap(offset, scaled)
        finally:
            painter.end()

        super().paintEvent(event)

#角色
class CharacterPortrait(QFrame):
    clicked = Signal()

    def __init__(self, image_path: Path, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("CharacterOverlay")
        self.original_pixmap = QPixmap(str(image_path))
        self.setCursor(_qt_enum(Qt, "CursorShape", "PointingHandCursor"))

        self.image_label = QLabel()
        self.image_label.setObjectName("CharacterImage")
        self.image_label.setAlignment(ALIGN_CENTER)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.image_label)
        self._update_pixmap()

    def mousePressEvent(self, event: Any) -> None:  # noqa: N802 - Qt override.
        if event.button() == _qt_enum(Qt, "MouseButton", "LeftButton"):
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt override.
        super().resizeEvent(event)
        self._update_pixmap()

    def _update_pixmap(self) -> None:
        side = max(96, min(self.width(), self.height()))
        if self.original_pixmap.isNull():
            self.image_label.setText("aki.png")
            self.image_label.setFixedSize(side, side)
            return

        scaled = self.original_pixmap.scaled(
            side,
            side,
            KEEP_ASPECT_RATIO,
            SMOOTH_TRANSFORMATION,
        )
        self.image_label.setFixedSize(side, side)
        self.image_label.setPixmap(scaled)
