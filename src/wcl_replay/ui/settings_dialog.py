# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Application settings. Categories sit on the left; each page edits one group."""

from __future__ import annotations

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from .theme import ACCENT

MAX_CACHED_FIGHTS = "max_cached_fights"
MAX_CACHED_WCL_FIGHTS = "max_cached_wcl_fights"
PROJECT_URL = "https://github.com/AriesXiaoS/wcl-replay"


def cached_limit(settings: QSettings) -> int:
    """How many finished local-log pulls stay in memory."""
    try:
        value = int(settings.value(MAX_CACHED_FIGHTS, 3))
    except (TypeError, ValueError):
        return 3
    return max(1, min(50, value))


def cached_wcl_limit(settings: QSettings) -> int:
    """Optional WCL result limit. Zero keeps the existing unlimited behavior."""
    try:
        value = int(settings.value(MAX_CACHED_WCL_FIGHTS, 0))
    except (TypeError, ValueError):
        return 0
    return max(0, min(50, value))


class SettingsDialog(QDialog):
    def __init__(self, settings: QSettings, parent: QWidget | None = None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("设置")
        self.setMinimumSize(560, 400)

        self.categories = QListWidget()
        self.categories.addItem("通用设置")
        self.categories.addItem("关于")
        self.categories.setFixedWidth(140)
        self.pages = QStackedWidget()
        self.pages.addWidget(self._general_page())
        self.pages.addWidget(self._about_page())
        self.categories.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.categories.setCurrentRow(0)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        columns = QHBoxLayout()
        columns.addWidget(self.categories)
        columns.addWidget(self.pages, 1)
        lay = QVBoxLayout(self)
        lay.addLayout(columns, 1)
        lay.addWidget(buttons)

    def _general_page(self) -> QWidget:
        hint = QLabel(
            "只统计本地日志里已经算完的轮次。达到上限后再计算新的一场，会释放最早计算、且没有收藏的那场已算完的内存。"
            "收藏的轮次会留下。每行的 × 同样只释放这一场的内存，列表项还在。"
            "WCL 可单独设置上限，默认不限制。自动释放时列表记录会留下，点击可重新加载；"
            "收藏和正在回放的场次会保留。收藏的场次在清除缓存时也会留下，× 会删掉整条记录。"
        )
        hint.setWordWrap(True)
        self.keep = QSpinBox()
        self.keep.setRange(1, 50)
        self.keep.setValue(cached_limit(self.settings))
        self.keep.setSuffix(" 场")
        row = QHBoxLayout()
        row.addWidget(QLabel("本地日志最多保留"))
        row.addWidget(self.keep)
        row.addStretch(1)
        self.wcl_keep = QSpinBox()
        self.wcl_keep.setRange(0, 50)
        self.wcl_keep.setSpecialValueText("不限制")
        self.wcl_keep.setValue(cached_wcl_limit(self.settings))
        self.wcl_keep.setSuffix(" 场")
        wcl_row = QHBoxLayout()
        wcl_row.addWidget(QLabel("WCL 战斗最多保留"))
        wcl_row.addWidget(self.wcl_keep)
        wcl_row.addStretch(1)
        page = QVBoxLayout()
        page.addWidget(hint)
        page.addLayout(row)
        page.addLayout(wcl_row)
        page.addStretch(1)
        body = QWidget()
        body.setLayout(page)
        return body

    def _about_page(self) -> QWidget:
        page = QVBoxLayout()
        page.setSpacing(14)
        name = QLabel(f"WCL Replay {__version__}")
        name.setObjectName("aboutName")
        page.addWidget(name)
        page.addWidget(_about_block("作者", "伐竹取道 (AriesXiao)"))
        page.addWidget(
            _about_block(
                "许可",
                "本软件以 PolyForm Noncommercial License 1.0.0 授权。"
                "个人、公会等非商业用途可以查看、使用、修改和分发源码。"
                "禁止出售、嵌入收费产品，或用于收费服务。",
            )
        )
        page.addWidget(_about_block("源码与更新", "软件源码以及最新的更新可以在这个 GitHub 界面找到。"))
        page.addWidget(_project_link())
        page.addStretch(1)
        body = QWidget()
        body.setLayout(page)
        return body

    def _save(self) -> None:
        self.settings.setValue(MAX_CACHED_FIGHTS, self.keep.value())
        self.settings.setValue(MAX_CACHED_WCL_FIGHTS, self.wcl_keep.value())
        self.accept()


def _about_block(title: str, text: str) -> QWidget:
    heading = QLabel(title)
    heading.setObjectName("aboutHeading")
    body = QLabel(text)
    body.setWordWrap(True)
    lay = QVBoxLayout()
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)
    lay.addWidget(heading)
    lay.addWidget(body)
    block = QWidget()
    block.setLayout(lay)
    return block


def _project_link() -> QLabel:
    link = QLabel(f'<a href="{PROJECT_URL}">{PROJECT_URL}</a>')
    link.setObjectName("aboutLink")
    link.setTextFormat(Qt.TextFormat.RichText)
    link.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
    link.setOpenExternalLinks(True)
    link.setWordWrap(True)
    palette = link.palette()
    palette.setColor(QPalette.ColorRole.Link, QColor(ACCENT))
    link.setPalette(palette)
    return link
