# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""The window icon and the Windows executable icon both exist and load."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6.QtWidgets import QApplication

from wcl_replay.ui.icon import configure_windows_taskbar, icon_path, load_app_icon, windows_icon_path


def test_window_icon_loads():
    QApplication.instance() or QApplication([])
    path = icon_path()
    assert path.is_file()
    assert path.suffix == ".png"
    assert path.read_bytes().startswith(b"\x89PNG")
    icon = load_app_icon()
    assert not icon.isNull()
    assert not icon.pixmap(32, 32).isNull()


def test_windows_executable_icon_has_several_sizes():
    data = windows_icon_path().read_bytes()
    assert data[:4] == b"\x00\x00\x01\x00"
    count = int.from_bytes(data[4:6], "little")
    assert count >= 7


def test_windows_taskbar_id_is_accepted():
    configure_windows_taskbar()
