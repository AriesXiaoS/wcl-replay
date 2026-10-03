# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""General settings remember how many finished local pulls stay in memory."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QLabel

from wcl_replay import __version__
from wcl_replay.ui.main_window import MainWindow
from wcl_replay.ui.settings_dialog import PROJECT_URL, SettingsDialog, cached_limit


def test_the_keep_limit_defaults_to_ten_and_can_be_saved(tmp_path):
    QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    assert cached_limit(settings) == 10
    dialog = SettingsDialog(settings)
    assert dialog.categories.item(0).text() == "通用设置"
    assert dialog.keep.value() == 10
    dialog.keep.setValue(4)
    dialog._save()
    assert cached_limit(settings) == 4


def test_about_page_names_the_author_license_and_update_url(tmp_path):
    QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    dialog = SettingsDialog(settings)
    assert dialog.categories.item(1).text() == "关于"
    dialog.categories.setCurrentRow(1)
    text = "\n".join(label.text() for label in dialog.pages.currentWidget().findChildren(QLabel))
    assert "伐竹取道 (AriesXiao)" in text
    assert "PolyForm Noncommercial License 1.0.0" in text
    assert "禁止出售、嵌入收费产品，或用于收费服务" in text
    assert PROJECT_URL in text


def test_settings_button_sits_beside_the_source_switch():
    QApplication.instance() or QApplication([])
    window = MainWindow()
    assert window.settings_btn.text() == "设置"
    assert window.settings_btn.parentWidget() is window._local_mode.parentWidget()
    assert window.windowTitle() == f"WCL Replay {__version__} · 战斗复盘"
    window.close()
