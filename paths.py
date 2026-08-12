from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
IS_FROZEN = bool(getattr(sys, "frozen", False))


def app_path(*parts: str | Path) -> Path:
    """Path next to the executable when packaged, or project root in dev."""
    base_dir = Path(sys.executable).resolve().parent if IS_FROZEN else PROJECT_ROOT
    return base_dir.joinpath(*parts)


def resource_path(*parts: str | Path) -> Path:
    """Path inside PyInstaller's bundle, or project root in dev."""
    base_dir = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))
    return base_dir.joinpath(*parts)


def asset_path(*parts: str | Path) -> Path:
    """Path for files from ./resources, bundled as ./assets by CocoDex.spec."""
    root_name = "assets" if IS_FROZEN else "resources"
    return resource_path(root_name, *parts)


def log_path(*parts: str | Path) -> Path:
    """Writable runtime log/cache path."""
    return app_path("logs", *parts)
