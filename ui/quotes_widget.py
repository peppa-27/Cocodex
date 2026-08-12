from __future__ import annotations

import random
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QEvent, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from ui.qt_utils import NO_PEN, _qt_enum, apply_default_letter_spacing
from paths import log_path


DEFAULT_QUOTES_PATH = log_path("quotes.txt")
DEFAULT_VISIBLE_MS = 10_000
DEFAULT_INTERVAL_MS = (1 * 60 * 1000, 2 * 60 * 1000)
FOREGROUND_RETRY_MS = 30_000
BUBBLE_MAX_WIDTH = 230
BUBBLE_MIN_WIDTH = 118
TAIL_WIDTH = 13
TAIL_HEIGHT = 12
PLACEMENT_SIDE = "side"
PLACEMENT_ABOVE = "above"


class QuoteBubbleWidget(QFrame):
    """Floating quote bubble used by the bar character portrait."""

    def __init__(
        self,
        quotes_path: Path = DEFAULT_QUOTES_PATH,
        parent: QWidget | None = None,
        can_show: Callable[[], bool] | None = None,
        visible_ms: int = DEFAULT_VISIBLE_MS,
        interval_ms: tuple[int, int] = DEFAULT_INTERVAL_MS,
        placement: str = PLACEMENT_SIDE,
    ):
        super().__init__(parent)
        self.quotes_path = quotes_path
        self.can_show = can_show
        self.visible_ms = visible_ms
        self.interval_ms = interval_ms
        self.placement = placement
        self.anchor_rect = QRect()
        self._active = False
        self._filtered_window: QWidget | None = None

        self.setObjectName("QuoteBubble")
        self.setAttribute(_qt_enum(Qt, "WidgetAttribute", "WA_TranslucentBackground"), True)
        self.setAttribute(_qt_enum(Qt, "WidgetAttribute", "WA_TransparentForMouseEvents"), True)

        self.quote_label = QLabel()
        self.quote_label.setObjectName("QuoteText")
        self.quote_label.setAlignment(_qt_enum(Qt, "AlignmentFlag", "AlignCenter"))
        self.quote_label.setWordWrap(True)
        self.quote_label.setMinimumWidth(BUBBLE_MIN_WIDTH)
        self.quote_label.setMaximumWidth(BUBBLE_MAX_WIDTH)

        self._layout = QVBoxLayout(self)
        if self.placement == PLACEMENT_ABOVE:
            self._layout.setContentsMargins(14, 10, 14, 18)
        else:
            self._layout.setContentsMargins(14, 10, 18, 14)
        self._layout.addWidget(self.quote_label)

        self.show_timer = QTimer(self)
        self.show_timer.setSingleShot(True)
        self.show_timer.timeout.connect(self.show_random_quote)
        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self._finish_current_quote)

        self._apply_style()
        self.hide()

    def start(self) -> None:
        self._active = True
        self._ensure_window_filter()
        if not self.show_timer.isActive() and not self.isVisible():
            self._schedule_next_quote()

    def stop(self) -> None:
        self._active = False
        self.show_timer.stop()
        self.hide_timer.stop()
        self.hide()

    def set_anchor_rect(self, anchor_rect: QRect) -> None:
        self.anchor_rect = QRect(anchor_rect)
        if self.isVisible():
            self._position_near_anchor()

    def show_random_quote(self) -> None:
        if not self._active:
            return
        if not self._can_show_now():
            self.show_timer.start(FOREGROUND_RETRY_MS)
            return

        quote = self._random_quote()
        if not quote:
            self._schedule_next_quote()
            return

        self.quote_label.setText(quote)
        self.quote_label.setFixedWidth(self._preferred_text_width(quote))
        self.adjustSize()
        self._position_near_anchor()
        self.raise_()
        self.show()
        self.hide_timer.start(self.visible_ms)

    def show_message(self, message: str) -> bool:
        if not self._active or not self._can_show_now() or not message.strip():
            return False

        self.show_timer.stop()
        self.hide_timer.stop()
        self.quote_label.setText(message.strip())
        self.quote_label.setFixedWidth(self._preferred_text_width(message))
        self.adjustSize()
        self._position_near_anchor()
        self.raise_()
        self.show()
        self.hide_timer.start(self.visible_ms)
        return True

    def eventFilter(self, watched: Any, event: Any) -> bool:  # noqa: N802 - Qt override.
        if (
            watched is self._filtered_window
            and self.isVisible()
            and event.type()
            in (
                _qt_enum(QEvent, "Type", "ActivationChange"),
                _qt_enum(QEvent, "Type", "Hide"),
                _qt_enum(QEvent, "Type", "WindowStateChange"),
            )
            and not self._can_show_now()
        ):
            self.hide_timer.stop()
            self.hide()
            self._schedule_next_quote()
        return super().eventFilter(watched, event)

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt override.
        painter = QPainter(self)
        try:
            painter.setRenderHint(_qt_enum(QPainter, "RenderHint", "Antialiasing"))
            painter.setPen(QPen(QColor(189, 181, 239, 225), 1))
            painter.setBrush(QColor(255, 253, 255, 242))

            if self.placement == PLACEMENT_ABOVE:
                body = self.rect().adjusted(0, 0, 0, -TAIL_HEIGHT)
            else:
                body = self.rect().adjusted(0, 0, -TAIL_WIDTH, -TAIL_HEIGHT)
            path = QPainterPath()
            path.addRoundedRect(QRectF(body), 13, 13)

            if self.placement == PLACEMENT_ABOVE:
                tail_x = min(max(24, self.anchor_rect.center().x() - self.x()), body.width() - 24)
                path.moveTo(tail_x - 8, body.bottom() - 1)
                path.lineTo(tail_x, body.bottom() + TAIL_HEIGHT)
                path.lineTo(tail_x + 8, body.bottom() - 1)
            else:
                tail_y = min(max(20, body.height() // 2), max(20, body.height() - 12))
                path.moveTo(body.right() - 2, tail_y - 7)
                path.lineTo(body.right() + TAIL_WIDTH, tail_y)
                path.lineTo(body.right() - 2, tail_y + 7)
            path.closeSubpath()
            painter.drawPath(path)

            painter.setPen(NO_PEN)
            painter.setBrush(QColor(255, 255, 255, 72))
            painter.drawRoundedRect(body.adjusted(5, 4, -5, -body.height() + 13), 6, 6)
        finally:
            painter.end()

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            #QuoteBubble {
                background: transparent;
                border: none;
            }
            #QuoteText {
                background: transparent;
                border: none;
                color: #586191;
                font-family: "SimHei", "Microsoft YaHei", "Segoe UI", sans-serif;
                font-size: 16px;
                font-weight: 800;
                line-height: 135%;
            }
            """
        )
        font = QFont()
        apply_default_letter_spacing(font)
        self.setFont(font)

    def _finish_current_quote(self) -> None:
        self.hide()
        self._schedule_next_quote()

    def _schedule_next_quote(self) -> None:
        if not self._active:
            return
        low, high = self.interval_ms
        self.show_timer.start(random.randint(low, high))

    def _random_quote(self) -> str:
        try:
            lines = self.quotes_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return ""

        quotes = [line.strip().replace("\\n", "\n") for line in lines if line.strip()]
        return random.choice(quotes) if quotes else ""

    def _preferred_text_width(self, quote: str) -> int:
        metrics = self.quote_label.fontMetrics()
        longest_line = max(quote.splitlines() or [quote], key=len)
        width = metrics.horizontalAdvance(longest_line) + 6
        return max(BUBBLE_MIN_WIDTH, min(BUBBLE_MAX_WIDTH, width))

    def _position_near_anchor(self) -> None:
        if self.parentWidget() is None or self.anchor_rect.isNull():
            return

        parent_rect = self.parentWidget().rect()
        if self.placement == PLACEMENT_ABOVE:
            x = self.anchor_rect.center().x() - self.width() // 2
            y = self.anchor_rect.top() - self.height() + 8
        else:
            x = self.anchor_rect.left() - self.width() + 26
            y = self.anchor_rect.top() + 12

        if x < 10:
            x = 10
        if x + self.width() > parent_rect.right() - 10:
            x = parent_rect.right() - self.width() - 10
        if y + self.height() > parent_rect.bottom() - 10:
            y = parent_rect.bottom() - self.height() - 10
        if y < 10:
            y = 10
        self.move(x, y)

    def _can_show_now(self) -> bool:
        if self.can_show is not None and not self.can_show():
            return False
        window = self.window()
        return (
            self.parentWidget() is not None
            and self.parentWidget().isVisible()
            and window is not None
            and window.isVisible()
            and not window.isMinimized()
        )

    def _ensure_window_filter(self) -> None:
        window = self.window()
        if window is None or window is self._filtered_window:
            return
        if self._filtered_window is not None:
            self._filtered_window.removeEventFilter(self)
        self._filtered_window = window
        self._filtered_window.installEventFilter(self)
