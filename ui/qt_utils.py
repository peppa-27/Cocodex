from __future__ import annotations

from datetime import datetime
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont


def _qt_enum(owner: Any, enum_name: str, value: str) -> Any:
    """Compatibility helper for PySide6 enum names across versions."""
    return getattr(getattr(owner, enum_name, owner), value)


# Shared Qt constants. If a label or layout alignment looks odd, these are the
# small cross-version aliases used by the UI files.
ALIGN_CENTER = _qt_enum(Qt, "AlignmentFlag", "AlignCenter")
ALIGN_RIGHT = _qt_enum(Qt, "AlignmentFlag", "AlignRight")
ALIGN_VCENTER = _qt_enum(Qt, "AlignmentFlag", "AlignVCenter")
NO_PEN = _qt_enum(Qt, "PenStyle", "NoPen")
NO_BRUSH = _qt_enum(Qt, "BrushStyle", "NoBrush")
KEEP_ASPECT_RATIO = _qt_enum(Qt, "AspectRatioMode", "KeepAspectRatio")
SMOOTH_TRANSFORMATION = _qt_enum(Qt, "TransformationMode", "SmoothTransformation")


def apply_default_letter_spacing(font: QFont) -> None:
    font.setFamily("SimHei")
    font.setWeight(QFont.Weight.Bold)
    font.setLetterSpacing(_qt_enum(QFont, "SpacingType", "PercentageSpacing"), 100)


def _dash(value: Any) -> str:
    if value is None or value == "":
        return "--"
    return str(value)


def _number(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.0f}"
    if isinstance(value, int):
        return str(value)
    return "--"


def _compact_number(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "--"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(int(value))


def _format_timestamp(value: Any) -> str:
    if not isinstance(value, int):
        return "--"
    return datetime.fromtimestamp(value).strftime("%m-%d %H:%M")


def _format_duration(value: Any) -> str:
    if not isinstance(value, int):
        return "--"
    hours, remainder = divmod(value, 3600)
    minutes = remainder // 60
    if hours >= 24:
        days, hours = divmod(hours, 24)
        return f"{days}天{hours}小时"
    return f"{hours}小时{minutes:02d}分"
