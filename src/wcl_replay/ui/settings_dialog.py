# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Application settings. Categories sit on the left; each page edits one group."""

from __future__ import annotations

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

MAX_CACHED_FIGHTS = "max_cached_fights"


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
        self.setMinimumSize(520, 320)

        self.categories = QListWidget()
        self.categories.addItem("通用设置")
        self.categories.setFixedWidth(140)
        self.categories.setCurrentRow(0)

        hint = QLabel(
            "只统计本地日志里已经算完的轮次。达到上限后再计算新的一场，会释放列表最下面那场已算完的内存。"
            "每行的 × 同样只释放这一场的内存，列表项还在。"
            "WCL 列表不自动清理，× 会删掉整条记录，也可以用底部的清除缓存。"
        )
        hint.setWordWrap(True)
        self.keep = QSpinBox()
        self.keep.setRange(1, 50)
        self.keep.setValue(cached_limit(settings))
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

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        columns = QHBoxLayout()
        columns.addWidget(self.categories)
        columns.addWidget(body, 1)
        lay = QVBoxLayout(self)
        lay.addLayout(columns, 1)
        lay.addWidget(buttons)

    def _save(self) -> None:
        self.settings.setValue(MAX_CACHED_FIGHTS, self.keep.value())
        self.accept()
