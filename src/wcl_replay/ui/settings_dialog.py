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
PROJECT_URL = "https://github.com/AriesXiaoS/wcl-replay"


def cached_limit(settings: QSettings) -> int:
    """How many finished local-log pulls stay in memory. WCL queries are not capped."""
    try:
        value = int(settings.value(MAX_CACHED_FIGHTS, 10))
    except (TypeError, ValueError):
        return 10
    return max(1, min(50, value))


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
            "只统计本地日志里已经算完的轮次。达到上限后再计算新的一场，会释放列表最下面那场已算完的内存。"
            "每行的 × 同样只释放这一场的内存，列表项还在。"
            "WCL 列表不自动清理，× 会删掉整条记录，也可以用底部的清除缓存。"
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
        page = QVBoxLayout()
        page.addWidget(hint)
        page.addLayout(row)
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
        page.addWidget(_about_block("更新", "新版本发布在下面的地址。"))
        page.addWidget(_project_link())
        page.addStretch(1)
        body = QWidget()
        body.setLayout(page)
        return body

    def _save(self) -> None:
        self.settings.setValue(MAX_CACHED_FIGHTS, self.keep.value())
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
