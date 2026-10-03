# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Application icon, and the spell icons drawn on the raid frames."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon, QPixmap

_APP_USER_MODEL_ID = "AriesXiao.WCLReplay"
_ICON_FILE = "wcl-replay.png"


def icon_path() -> Path:
    """PNG next to this package, or beside the frozen executable."""
    bundled = Path(__file__).resolve().parent.parent / "assets" / _ICON_FILE
    if bundled.is_file():
        return bundled
    beside_executable = Path(sys.executable).resolve().parent / "wcl_replay" / "assets" / _ICON_FILE
    if beside_executable.is_file():
        return beside_executable
    return bundled


def windows_icon_path() -> Path:
    """Multi-resolution .ico embedded into wcl-replay.exe by the Windows build."""
    return icon_path().with_name("wcl-replay.ico")


def load_app_icon() -> QIcon:
    return QIcon(str(icon_path()))


def aura_icon_path(stem: str) -> Path:
    """Spell icon PNG shipped for the raid frames, or the same file beside a frozen build."""
    name = f"auras/{stem}.png"
    bundled = Path(__file__).resolve().parent.parent / "assets" / name
    if bundled.is_file():
        return bundled
    frozen = Path(sys.executable).resolve().parent / "wcl_replay" / "assets" / name
    if frozen.is_file():
        return frozen
    return bundled


_AURA_PIXMAPS: dict[str, QPixmap] = {}


def aura_pixmap(stem: str) -> QPixmap | None:
    """Cached spell icon. ``None`` when the file is missing or unreadable."""
    cached = _AURA_PIXMAPS.get(stem)
    if cached is not None:
        return None if cached.isNull() else cached
    pixmap = QPixmap(str(aura_icon_path(stem)))
    _AURA_PIXMAPS[stem] = pixmap
    return None if pixmap.isNull() else pixmap


def configure_windows_taskbar() -> None:
    """Show this app's icon on the taskbar instead of grouping under python.exe."""
    if sys.platform != "win32":
        return
    import ctypes

    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(_APP_USER_MODEL_ID)
