from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.get_weather import get_weather_info
from core.start_codex_server import parse_frontend_display_result, read_codexbar_info
from ui.bar_components import (
    CharacterPortrait,
    ImageFrame,
    InfoTile,
    MetricPill,
    QuotaRing,
)
from ui.qt_utils import (
    ALIGN_CENTER,
    _compact_number,
    _format_duration,
    _format_timestamp,
    _number,
    _qt_enum,
    apply_default_letter_spacing,
)
from ui.quotes_widget import QuoteBubbleWidget
from paths import asset_path, log_path

#-----配置区域-----
CHARACTER_IMAGE = asset_path("bar", "lean.png")
RING_BACKGROUND_IMAGE = asset_path("bar", "ring.png")
QUOTA_CACHE_PATH = log_path("last_quota_cache.json")
QUOTES_PATH = log_path("quotes.txt")
QUOTA_REFRESH_INTERVAL_MS = 5 * 60 * 1000
RING_BOX_SIZE = 288
RIGHT_INFO_TILE_WIDTH = 160
INFO_TILE_HEIGHT = 65
WEATHER_BOX_HEIGHT = INFO_TILE_HEIGHT
BODY_COLUMN_SPACING = 8
BODY_ROW_SPACING = 5
LEFT_CLOCK_HEIGHT = 31
LEFT_BODY_HEIGHT = (
    LEFT_CLOCK_HEIGHT
    + RING_BOX_SIZE
    + WEATHER_BOX_HEIGHT
    + BODY_ROW_SPACING * 2
)
RIGHT_CHARACTER_SPACE_HEIGHT = (
    LEFT_BODY_HEIGHT
    - INFO_TILE_HEIGHT * 4
    - BODY_ROW_SPACING * 4
)
CONTENT_WIDTH = RING_BOX_SIZE + BODY_COLUMN_SPACING + RIGHT_INFO_TILE_WIDTH
#-----配置区域-----


def _load_quota_cache() -> dict[str, Any] | None:
    try:
        if not QUOTA_CACHE_PATH.exists() or QUOTA_CACHE_PATH.stat().st_size == 0:
            return None
        data = json.loads(QUOTA_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _save_quota_cache(data: dict[str, Any]) -> None:
    try:
        QUOTA_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        temp_path = QUOTA_CACHE_PATH.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(QUOTA_CACHE_PATH)
    except OSError:
        pass


class DataWorkerSignals(QObject):
    finished = Signal(dict)
    failed = Signal(str)


class DataWorker(QRunnable):
    def __init__(self, sources: list[str] | tuple[str, ...] | str | None):
        super().__init__()
        self.sources = sources
        self.signals = DataWorkerSignals()

    def run(self) -> None:
        try:
            raw = read_codexbar_info(self.sources)
            data = parse_frontend_display_result(raw)#这一步获取了后台数据
            try:
                data["weatherInfo"] = get_weather_info()
            except Exception as error:  # noqa: BLE001 - weather is non-critical.
                data["weatherInfo"] = {"ok": False, "error": str(error)}
            self.signals.finished.emit(data)
        except Exception as error:  # noqa: BLE001 - UI should stay alive.
            self.signals.failed.emit(str(error))


class BarWidget(QFrame):
    pet_requested = Signal()
    minimize_requested = Signal()
    tray_requested = Signal()
    topmost_requested = Signal()
    close_requested = Signal()

    def __init__(
        self,
        source: list[str] | tuple[str, ...] | str | None = None,
        loader: Callable[[list[str] | tuple[str, ...] | str | None], dict[str, Any]] | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.source = source
        self.loader = loader
        self.thread_pool = QThreadPool.globalInstance()
        self._refresh_in_progress = False
        self._weather_greeting_shown = False
        self._pending_weather_greeting = ""
        self._weather_greeting_retry_armed = False
        self._pending_codex_status_message = ""
        self._codex_status_retry_armed = False

        self.setObjectName("BarWidget")
        self._build_ui()
        self._apply_style()
        self._update_clock_label()
        self._apply_cached_data()
        self.clock_timer = QTimer(self)
        self.clock_timer.setInterval(1000)
        self.clock_timer.timeout.connect(self._update_clock_label)
        self.clock_timer.start()
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(QUOTA_REFRESH_INTERVAL_MS)
        self.refresh_timer.timeout.connect(self.refresh)
        self.refresh_timer.start()
        self.refresh()

    def refresh(self) -> None:
        if self._refresh_in_progress:
            return
        self._refresh_in_progress = True
        self.refresh_button.setEnabled(False)
        if self.loader is not None:
            try:
                self._apply_data(self.loader(self.source))
            except Exception as error:  # noqa: BLE001
                self._show_error(str(error))
            return

        worker = DataWorker(self.source)
        worker.signals.finished.connect(self._handle_worker_data)#接收后端数据的槽
        worker.signals.failed.connect(self._show_error)
        self.thread_pool.start(worker)

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt override.
        super().resizeEvent(event)
        self._position_character()

    def showEvent(self, event: Any) -> None:  # noqa: N802 - Qt override.
        super().showEvent(event)
        if hasattr(self, "quote_bubble"):
            self.quote_bubble.start()
            self._show_pending_weather_greeting()
            self._show_pending_codex_status_message()

    def hideEvent(self, event: Any) -> None:  # noqa: N802 - Qt override.
        if hasattr(self, "quote_bubble"):
            self.quote_bubble.stop()
        self._weather_greeting_retry_armed = False
        self._codex_status_retry_armed = False
        super().hideEvent(event)

    def set_topmost_state(self, is_topmost: bool) -> None:
        self.topmost_button.setText("⌖" if is_topmost else "📌")
        self.topmost_button.setToolTip("取消窗口置顶" if is_topmost else "窗口置顶")

    def apply_codex_status_event(self, event: dict[str, Any]) -> None:
        message = _codex_status_bubble_text(event)
        if message and hasattr(self, "quote_bubble"):
            self._pending_codex_status_message = message
            self._show_pending_codex_status_message()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 10)
        root.setSpacing(7)

        self._build_header(root)
        self._build_body(root)
        self._build_footer(root)
        self._build_character_overlay()

    def _build_header(self, root: QVBoxLayout) -> None:
        header = QHBoxLayout()
        self.title_label = QLabel("❉ Codex 使用额度")
        self.title_label.setObjectName("Title")
        self.refresh_button = QPushButton("刷新")
        self.topmost_button = QPushButton("📌")
        self.topmost_button.setToolTip("窗口置顶")
        self.minimize_button = _control_button("−")
        self.tray_button = _control_button("▾")
        self.tray_button.setToolTip("隐藏到系统托盘")
        self.close_button = _control_button("×", "CloseButton")

        self.refresh_button.clicked.connect(self.refresh)
        self.topmost_button.clicked.connect(self.topmost_requested.emit)
        self.minimize_button.clicked.connect(self.minimize_requested.emit)
        self.tray_button.clicked.connect(self.tray_requested.emit)
        self.close_button.clicked.connect(self.close_requested.emit)

        header.addWidget(self.title_label, 1)
        header.addWidget(self.refresh_button)
        header.addWidget(self.topmost_button)
        header.addSpacing(2)
        header.addWidget(self.minimize_button)
        header.addWidget(self.tray_button)
        header.addWidget(self.close_button)
        root.addLayout(header)

    def _build_body(self, root: QVBoxLayout) -> None:
        self.body_widget = QWidget()
        self.body_widget.setFixedWidth(CONTENT_WIDTH)
        body = QGridLayout(self.body_widget)
        body.setContentsMargins(0, 0, 0, 0)
        body.setHorizontalSpacing(BODY_COLUMN_SPACING)
        body.setVerticalSpacing(BODY_ROW_SPACING)

        body.addWidget(self._create_left_panel(), 0, 0, 5, 1)
        body.addWidget(self._create_right_panel(), 0, 1, 5, 1, ALIGN_CENTER)
        body.setColumnMinimumWidth(0, RING_BOX_SIZE)
        body.setColumnMinimumWidth(1, RIGHT_INFO_TILE_WIDTH)
        body.setColumnStretch(0, 0)
        body.setColumnStretch(1, 0)
        root.addWidget(self.body_widget, 0, ALIGN_CENTER)

    def _create_left_panel(self) -> QWidget:
        panel = QWidget()
        panel.setFixedWidth(RING_BOX_SIZE)
        panel.setFixedHeight(LEFT_BODY_HEIGHT)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(BODY_ROW_SPACING)

        self.clock_label = QLabel()
        self.clock_label.setObjectName("ClockLabel")
        self.clock_label.setAlignment(ALIGN_CENTER)
        self.clock_label.setFixedHeight(LEFT_CLOCK_HEIGHT)
        panel_layout.addWidget(self.clock_label)
        panel_layout.addWidget(self._create_ring_box(), 1)
        panel_layout.addWidget(self._create_weather_box(), 0)
        return panel

    def _create_ring_box(self) -> QFrame:
        self.ring = QuotaRing()
        ring_box = ImageFrame(RING_BACKGROUND_IMAGE)
        ring_box.setObjectName("RingBox")
        ring_box.setFixedSize(RING_BOX_SIZE, RING_BOX_SIZE)

        # Ring area details live here: margins, text box padding, and layout
        # spacing are the easiest knobs for manual visual tuning.
        ring_layout = QVBoxLayout(ring_box)
        ring_layout.setContentsMargins(7, 7, 7, 7)
        ring_layout.setSpacing(2)
        ring_layout.addWidget(self.ring, 1)

        return ring_box

    def _create_weather_box(self) -> QFrame:
        weather_box = QFrame()
        weather_box.setObjectName("WeatherBox")
        weather_box.setFixedWidth(RING_BOX_SIZE)
        weather_box.setFixedHeight(WEATHER_BOX_HEIGHT)
        weather_layout = QVBoxLayout(weather_box)
        weather_layout.setContentsMargins(8, 5, 8, 5)
        weather_layout.setSpacing(1)

        self.weather_city_label = QLabel("定位 --")
        self.weather_city_label.setObjectName("WeatherCity")
        self.weather_city_label.setAlignment(ALIGN_CENTER)
        self.weather_main_label = QLabel("天气 --")
        self.weather_main_label.setObjectName("WeatherMain")
        self.weather_main_label.setAlignment(ALIGN_CENTER)
        self.weather_detail_label = QLabel("温度 -- · 湿度 --")
        self.weather_detail_label.setObjectName("SoftText")
        self.weather_detail_label.setAlignment(ALIGN_CENTER)
        weather_layout.addWidget(self.weather_city_label)
        weather_layout.addWidget(self.weather_main_label)
        weather_layout.addWidget(self.weather_detail_label)
        return weather_box

    def _create_right_panel(self) -> QWidget:
        panel = QWidget()
        panel.setFixedWidth(RIGHT_INFO_TILE_WIDTH)
        panel.setFixedHeight(LEFT_BODY_HEIGHT)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(BODY_ROW_SPACING)

        self.model_tile = InfoTile("▧", "当前模型")
        self.plan_tile = InfoTile("♕", "套餐类型")
        self.reset_tile = InfoTile("▣", "重置时间")
        self.connection_tile = InfoTile("☆", "同步状态")
        for tile in (
            self.model_tile,
            self.plan_tile,
            self.reset_tile,
            self.connection_tile,
        ):
            tile.setFixedWidth(RIGHT_INFO_TILE_WIDTH)
            tile.setFixedHeight(INFO_TILE_HEIGHT)
        self.character_spacer = QWidget()
        self.character_spacer.setFixedHeight(RIGHT_CHARACTER_SPACE_HEIGHT)

        layout.addWidget(self.character_spacer)
        layout.addWidget(self.model_tile)
        layout.addWidget(self.plan_tile)
        layout.addWidget(self.reset_tile)
        layout.addWidget(self.connection_tile)
        return panel

    def _build_footer(self, root: QVBoxLayout) -> None:
        self.footer_widget = QWidget()
        self.footer_widget.setFixedWidth(CONTENT_WIDTH)
        self.footer_widget.setMinimumHeight(58)
        footer = QGridLayout(self.footer_widget)
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(6)

        self.tokens_pill = MetricPill("↗", "总使用", "--", "#78b8ff")
        self.streak_pill = MetricPill("✿", "连续天数", "--", "#cf9cff")
        self.peak_pill = MetricPill("◷", "峰值用量", "--", "#f1b66b")
        self.server_pill = MetricPill("✦", "App Server", "--", "#8fdbb0")

        footer.addWidget(self.tokens_pill, 0, 0)
        footer.addWidget(self.streak_pill, 0, 1)
        footer.addWidget(self.peak_pill, 0, 2)
        footer.addWidget(self.server_pill, 0, 3)
        root.addWidget(self.footer_widget, 0, ALIGN_CENTER)

    def _build_character_overlay(self) -> None:
        self.portrait = CharacterPortrait(CHARACTER_IMAGE, self)
        self.portrait.clicked.connect(self.pet_requested.emit)
        self.quote_bubble = QuoteBubbleWidget(
            quotes_path=QUOTES_PATH,
            parent=self,
            can_show=self._can_show_quote_bubble,
        )
        self.portrait.raise_()
        self._position_character()

    def _position_character(self) -> None:
        if not hasattr(self, "portrait"):
            return
        side = min(183, max(183, int(self.width() * 0.25)))
        x = self.width() - side +  0
        y = 33
        self.portrait.setGeometry(x, y, side, side)
        if hasattr(self, "quote_bubble"):
            self.quote_bubble.set_anchor_rect(self.portrait.geometry())

    def _can_show_quote_bubble(self) -> bool:
        window = self.window()
        return (
            self.isVisible()
            and window is not None
            and window.isVisible()
            and not window.isMinimized()
        )

    def _apply_style(self) -> None:
        # Main stylesheet: adjust colors, borders, radii, and typography here.
        # Custom painting for the ring and background image lives in
        # ui/bar_components.py.
        self.setStyleSheet(
            """
            #BarWidget {
                background: rgba(255, 248, 255, 0.96);
                border: 2px solid rgba(143, 166, 255, 0.95);
                border-radius: 22px;
                color: #536086;
                font-family: "SimHei", "Microsoft YaHei", "Segoe UI", sans-serif;
                font-weight: 700;
            }
            #Title {
                color: #6562c8;
                font-size: 19px;
                font-weight: 800;
            }
            QLabel {
                color: #687397;
                font-size: 11px;
                font-weight: 700;
            }
            QPushButton {
                background: #f6edff;
                border: 1px solid #c9bcff;
                border-radius: 8px;
                color: #6860b9;
                padding: 4px 8px;
                font-weight: 800;
            }
            QPushButton:hover {
                background: #edf7ff;
                border-color: #94cfff;
            }
            QPushButton:disabled {
                color: #aaa3bd;
                background: #f3f0f6;
            }
            #WindowControlButton, #CloseButton {
                background: transparent;
                border: 1px solid transparent;
                border-radius: 8px;
                color: #7a76b9;
                font-size: 14px;
                padding: 0;
            }
            #WindowControlButton:hover {
                background: #edf7ff;
                border-color: #b7ddff;
            }
            #CloseButton:hover {
                background: #ffe7ef;
                border-color: #ffacc8;
                color: #d95583;
            }
            #RingBox {
                background: transparent;
                border: 1px solid rgba(202, 210, 255, 0.96);
                border-radius: 14px;
            }
            #WeatherBox {
                background: rgba(255, 255, 255, 0.86);
                border: 1px solid rgba(202, 210, 255, 0.92);
                border-radius: 9px;
            }
            #InfoTile, #MetricPill {
                background: rgba(255, 255, 255, 0.92);
                border: 1px solid rgba(202, 210, 255, 0.96);
                border-radius: 14px;
            }
            #CharacterOverlay, #CharacterImage {
                background: transparent;
                border: none;
            }
            #SoftText, #TileHint {
                color: #9da6c7;
                font-size: 11px;
            }
            #ClockLabel {
                color: #6671ad;
                font-size: 17px;
                font-weight: 800;
                padding: 4px 0 4px 0;
                background: transparent;
                border: none;
            }
            #TileValue {
                color: #5964a5;
                font-size: 15px;
                font-weight: 800;
            }
            #WeatherCity {
                color: #6671ad;
                font-size: 11px;
                font-weight: 800;
            }
            #WeatherMain {
                color: #5964a5;
                font-size: 14px;
                font-weight: 800;
            }
            #MetricValue {
                color: #5964a5;
                font-size: 13px;
                font-weight: 800;
            }
            """
        )
        font = QFont()
        apply_default_letter_spacing(font)
        self.setFont(font)

    def _apply_cached_data(self) -> None:
        cached_data = _load_quota_cache()
        if cached_data is None:
            return
        self._apply_data(cached_data, from_cache=True)

    def _handle_worker_data(self, data: dict[str, Any]) -> None:
        _save_quota_cache(data)
        self._apply_data(data)

    def _update_clock_label(self) -> None:
        now = datetime.now()
        self.clock_label.setText(
            f"◷ {now.year}/{now.month:02d}/{now.day:02d} "
            f"{now.hour:02d}:{now.minute:02d}:{now.second:02d}"
        )

    #后端数据的进一步解析
    def _apply_data(self, data: dict[str, Any], from_cache: bool = False) -> None:
        quota = data.get("quota") or {}
        account = data.get("account") or {}
        usage = data.get("usage") or {}
        model = data.get("model") or {}
        app_server = data.get("appServer") or {}

        remaining = quota.get("remainingPercent")
        used = quota.get("usedPercent")
        if not isinstance(used, (int, float)) and isinstance(remaining, (int, float)):
            used = 100 - remaining
        self.ring.set_used_percent(used)

        reset_time = _format_timestamp(quota.get("resetsAt"))
        reset_hint = _format_duration(quota.get("resetsInSeconds"))
        self.ring.set_quota_summary(
            f"已使用 {_number(used)}%",
            f"剩余 {_number(remaining)}%",
            f"距离重置 {reset_hint}",
        )
        self._apply_weather_data(data.get("weatherInfo") or {})
        if not from_cache:
            self._queue_initial_weather_greeting(data.get("weatherInfo") or {})
        
        self.model_tile.set_value(
            model.get("displayName") or model.get("current"),
            model.get("reasoningEffort") or model.get("serviceTier") or "",
        )
        self.plan_tile.set_value(account.get("planType"), account.get("email") or "")
        self.reset_tile.set_value(reset_time, reset_hint)
        self.connection_tile.set_value("已连接" if data.get("ok") else "异常", _format_sources(data.get("source")))
        
        self.tokens_pill.set_value(_compact_number(usage.get("lifetimeTokens")))
        self.streak_pill.set_value(f"{_number(usage.get('currentStreakDays'))} 天")
        self.peak_pill.set_value(_compact_number(usage.get("peakDailyTokens")))
        self.server_pill.set_value(f"PID {app_server.get('pid') or '--'}")

        self.refresh_button.setEnabled(True)
        self._refresh_in_progress = False

    def _show_error(self, message: str) -> None:
        self.ring.set_used_percent(0)
        self.ring.set_quota_summary()
        self.weather_city_label.setText("定位 --")
        self.weather_main_label.setText("天气 --")
        self.weather_detail_label.setText("温度 -- · 湿度 --")
        self.connection_tile.set_value("未连接", message[:48])
        self.refresh_button.setEnabled(True)
        self._refresh_in_progress = False

    def _apply_weather_data(self, data: dict[str, Any]) -> None:
        location = data.get("location") or {}
        weather = data.get("weather") or {}
        if not data.get("ok"):
            self.weather_city_label.setText("定位失败")
            self.weather_main_label.setText("天气 --")
            self.weather_detail_label.setText(str(data.get("error") or "--")[:28])
            return

        city = location.get("city") or location.get("displayName") or "未知位置"
        description = weather.get("description") or "--"
        weather_icon = _weather_icon(weather.get("weatherCode"))
        temperature = _format_temperature(weather.get("temperature"))
        apparent = _format_temperature(weather.get("apparentTemperature"))
        humidity = _number(weather.get("humidity"))
        wind_speed = _format_decimal(weather.get("windSpeed"), " km/h")

        self.weather_city_label.setText(str(city))
        self.weather_main_label.setText(f"{weather_icon} {description} · 🌡️ {temperature}")
        self.weather_detail_label.setText(
            f"体感 {apparent} · 湿度 {humidity}% · 风 {wind_speed}"
        )

    def _queue_initial_weather_greeting(self, data: dict[str, Any]) -> None:
        if self._weather_greeting_shown or not data.get("ok"):
            return

        greeting = _weather_greeting_text(data)
        if not greeting:
            return
        self._pending_weather_greeting = greeting
        self._show_pending_weather_greeting()

    def _show_pending_weather_greeting(self) -> None:
        if (
            self._weather_greeting_shown
            or not self._pending_weather_greeting
            or not hasattr(self, "quote_bubble")
        ):
            return
        if not self.isVisible():
            return
        if self.quote_bubble.show_message(self._pending_weather_greeting):
            self._pending_weather_greeting = ""
            self._weather_greeting_shown = True
            self._weather_greeting_retry_armed = False
        else:
            self._schedule_pending_weather_greeting_retry()

    def _schedule_pending_weather_greeting_retry(self) -> None:
        if self._weather_greeting_retry_armed:
            return
        self._weather_greeting_retry_armed = True
        QTimer.singleShot(1000, self._retry_pending_weather_greeting)

    def _retry_pending_weather_greeting(self) -> None:
        self._weather_greeting_retry_armed = False
        self._show_pending_weather_greeting()

    def _show_pending_codex_status_message(self) -> None:
        if not self._pending_codex_status_message or not hasattr(self, "quote_bubble"):
            return
        if not self.isVisible():
            return
        if self.quote_bubble.show_message(self._pending_codex_status_message):
            self._pending_codex_status_message = ""
            self._codex_status_retry_armed = False
        else:
            self._schedule_pending_codex_status_retry()

    def _schedule_pending_codex_status_retry(self) -> None:
        if self._codex_status_retry_armed:
            return
        self._codex_status_retry_armed = True
        QTimer.singleShot(1000, self._retry_pending_codex_status_message)

    def _retry_pending_codex_status_message(self) -> None:
        self._codex_status_retry_armed = False
        self._show_pending_codex_status_message()


def _control_button(text: str, object_name: str = "WindowControlButton") -> QPushButton:
    button = QPushButton(text)
    button.setObjectName(object_name)
    button.setFixedSize(28, 26)
    button.setFocusPolicy(_qt_enum(Qt, "FocusPolicy", "NoFocus"))
    return button


def _format_sources(sources: Any) -> str:
    if isinstance(sources, str):
        return sources
    if isinstance(sources, (list, tuple)):
        names = [str(source) for source in sources if source]
        if names:
            return ", ".join(names)
    return "--"


def _format_temperature(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"{value:.1f}°C"
    return "--"


def _format_decimal(value: Any, suffix: str = "") -> str:
    if isinstance(value, (int, float)):
        return f"{value:.1f}{suffix}"
    return "--"


def _weather_greeting_text(data: dict[str, Any]) -> str:
    real_ip = data.get("realIp") or {}
    virtual_ip = data.get("virtualIp") or {}

    real_location = real_ip.get("location") if real_ip.get("ok") else None
    virtual_location = virtual_ip.get("location") if virtual_ip.get("ok") else None

    if isinstance(virtual_location, dict):
        place = _location_name(virtual_location)
        return f"好巧呀！\n你也来{place}玩啦！" if place else ""
    if isinstance(real_location, dict):
        place = _location_name(real_location)
        return f"你好哇！\n我在{place}很想你！" if place else ""
    return ""


def _location_name(location: dict[str, Any]) -> str:
    return str(
        location.get("city")
        or location.get("region")
        or location.get("displayName")
        or location.get("country")
        or ""
    ).strip()


def _codex_status_bubble_text(event: dict[str, Any]) -> str:
    status = str(event.get("status") or "").strip().lower()
    if status == "request":
        return "需要你确认一下～\n我先在这里等你！"
    if status == "complete":
        return "完成啦！\n快看看这次的结果吧！"
    return ""


def _weather_icon(code: Any) -> str:
    if not isinstance(code, int):
        return "☁️"
    if code == 0:
        return "☀️"
    if code in (1, 2):
        return "🌤️"
    if code == 3:
        return "☁️"
    if code in (45, 48):
        return "🌫️"
    if code in (51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82):
        return "🌧️"
    if code in (71, 73, 75, 77, 85, 86):
        return "🌨️"
    if code in (95, 96, 99):
        return "⛈️"
    return "☁️"
