# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""WCL card: paste one fight URL, compute it, and keep every result in the list."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..bosses.base import fmt_time
from ..sources.local_log.index import DIFFICULTY_LABELS
from .controller import ReplayController
from .log_panel import PullLoads, _PullRow


def quota_text(spent: float, limit: int, reset_in: int) -> tuple[str, str]:
    """Label and tooltip for the hourly point counter."""
    shown = f"{spent:.0f}" if abs(spent - round(spent)) < 0.05 else f"{spent:.1f}"
    reset = max(0, int(reset_in))
    tip = f"{reset // 60} 分钟后重置" if reset >= 60 else f"{reset} 秒后重置"
    return f"本小时 {shown} / {limit}", tip


def fight_label(session: object) -> str:
    fight = session.data.fight
    diff = DIFFICULTY_LABELS.get(fight.difficulty, str(fight.difficulty))
    result = "击杀" if fight.kill else "灭团"
    pull = f"pull {fight.pull_number}" if fight.pull_number else f"fight {fight.id}"
    clock = fight.start_label.rsplit(" ", 1)[-1] if fight.start_label else ""
    when = f"{clock} · " if clock else ""
    return f"{fight.name} {diff} · {pull} · {when}{fmt_time(fight.duration_ms)} · {result}"


class WclPanel(QFrame):
    """Same footprint as the local-log card. Finished rows stay until the process exits."""

    queryRequested = Signal(str)
    settingsRequested = Signal()
    quotaRefreshRequested = Signal()
    clearClicked = Signal()
    activated = Signal(int)
    removeRequested = Signal(int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("logCard")
        self.setMinimumWidth(280)
        self._rows: list[_PullRow] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        api_row = QHBoxLayout()
        api_row.setSpacing(6)
        self.quota_lbl = QLabel("额度未刷新")
        self.quota_lbl.setObjectName("quotaLbl")
        self.quota_lbl.setStyleSheet("color: #8b93a1;")
        api_row.addWidget(self.quota_lbl, 1)
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setObjectName("logAction")
        self.refresh_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.refresh_btn.clicked.connect(self.quotaRefreshRequested.emit)
        api_row.addWidget(self.refresh_btn)
        self.settings_btn = QPushButton("API 设置")
        self.settings_btn.setObjectName("logAction")
        self.settings_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.settings_btn.clicked.connect(self.settingsRequested.emit)
        api_row.addWidget(self.settings_btn)
        lay.addLayout(api_row)

        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://cn.warcraftlogs.com/reports/XXXXXXXXXXXXXXXX?fight=3")
        self.url_edit.returnPressed.connect(self._emit_query)
        lay.addWidget(self.url_edit)

        self.query_btn = QPushButton("查询")
        self.query_btn.setObjectName("logAction")
        self.query_btn.clicked.connect(self._emit_query)
        lay.addWidget(self.query_btn)
        self.status_lbl = QLabel("输入一场战斗的链接")
        self.status_lbl.setWordWrap(True)
        self.status_lbl.setStyleSheet("color: #8b93a1;")
        lay.addWidget(self.status_lbl)

        self._scroll = QScrollArea()
        self._scroll.setObjectName("logList")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.viewport().setObjectName("logViewport")
        self._body = QWidget()
        self._body.setObjectName("logListBody")
        self._list = QVBoxLayout(self._body)
        self._list.setContentsMargins(0, 0, 0, 0)
        self._list.setSpacing(2)
        self._list.addStretch(1)
        self._scroll.setWidget(self._body)
        lay.addWidget(self._scroll, 1)
        self.clear_btn = QPushButton("清除缓存")
        self.clear_btn.setObjectName("logAction")
        self.clear_btn.setToolTip("丢掉已算完的战斗，下次打开会重新计算")
        self.clear_btn.clicked.connect(self.clearClicked.emit)
        lay.addWidget(self.clear_btn)

    def url(self) -> str:
        return self.url_edit.text().strip()

    def set_url(self, url: str) -> None:
        self.url_edit.setText(url)

    def set_status(self, text: str) -> None:
        self.status_lbl.setText(text)

    def set_quota(self, text: str, tooltip: str = "") -> None:
        self.quota_lbl.setText(text)
        self.quota_lbl.setToolTip(tooltip)

    def add_row(self, label: str) -> int:
        """Insert a row at the top. The click reports wherever that row sits when it is clicked."""
        stretch = self._list.takeAt(self._list.count() - 1)
        row = _PullRow(label, self._body, action_tip="删除这条记录并释放内存")
        row.clicked.connect(lambda r=row: self.activated.emit(self._rows.index(r)))
        row.deleteClicked.connect(lambda r=row: self.removeRequested.emit(self._rows.index(r)))
        self._rows.insert(0, row)
        self._list.insertWidget(0, row)
        self._list.addItem(stretch)
        self._scroll.verticalScrollBar().setValue(0)
        return 0

    @property
    def rows(self) -> list[_PullRow]:
        return self._rows

    def set_label(self, index: int, label: str) -> None:
        self._rows[index].text.setText(label)
        self._rows[index].text.setToolTip(label)

    def set_selected(self, index: int | None) -> None:
        for i, row in enumerate(self._rows):
            row.set_selected(i == index)

    def show_progress(self, index: int, frac: float) -> None:
        self._rows[index].show_progress(frac)

    def mark_done(self, index: int) -> None:
        self._rows[index].mark_done()

    def mark_error(self, index: int, message: str) -> None:
        self._rows[index].mark_error(message)

    def clear_done(self, index: int) -> None:
        self._rows[index].clear_done()

    def remove_row(self, index: int) -> None:
        row = self._rows.pop(index)
        self._list.removeWidget(row)
        row.setParent(None)
        row.deleteLater()

    def _emit_query(self) -> None:
        self.queryRequested.emit(self.url())


class WclBoard:
    """In-memory list of WCL fights. A new query is inserted at the top."""

    def __init__(self, ctl: ReplayController, panel: WclPanel):
        self.ctl = ctl
        self.panel = panel
        self.loads = PullLoads()
        self.keys: list[tuple] = []
        self._seq = 0
        self.panel.removeRequested.connect(self.remove)

    def begin(self, url: str) -> tuple[int, tuple]:
        """Insert a row at the top and mark it as the query that is about to run."""
        self._seq += 1
        key = ("wcl", url, self._seq)
        index = self.panel.add_row(url)
        self.keys.insert(0, key)
        self.loads.selected = key
        self.loads.running.add(key)
        self.panel.set_selected(index)
        self.panel.show_progress(index, 0.0)
        self.panel.set_status(f"已记录 {len(self.keys)} 场")
        return index, key

    def activate(self, index: int) -> str:
        """Show a finished fight. A row that is still querying just stays selected."""
        key = self.keys[index]
        self.panel.set_selected(index)
        if key in self.loads.cache:
            self.loads.selected = key
            self.ctl.set_session(self.loads.cache[key])
            return "show"
        if key in self.loads.running:
            self.loads.selected = key
            return "wait"
        return "idle"

    def note_progress(self, key: tuple, frac: float) -> None:
        if key not in self.keys or key not in self.loads.running:
            return
        self.panel.show_progress(self.keys.index(key), frac)

    def finish(self, key: tuple, session: object, label: str) -> bool:
        if key not in self.keys:
            self.loads.abandon(key)
            return False
        index = self.keys.index(key)
        show = self.loads.complete(key, session)
        self.panel.set_label(index, label)
        self.panel.mark_done(index)
        if show:
            self.ctl.set_session(session)
        return show

    def remove(self, index: int) -> None:
        """Drop one queried fight and release its computed result."""
        if not 0 <= index < len(self.keys):
            return
        key = self.keys.pop(index)
        session = self.loads.cache.pop(key, None)
        self.loads.running.discard(key)
        if self.loads.selected == key:
            self.loads.selected = None
        if session is not None and self.ctl.session is session:
            self.ctl.clear_session()
        self.panel.remove_row(index)
        if self.loads.selected in self.keys:
            self.panel.set_selected(self.keys.index(self.loads.selected))
        else:
            self.panel.set_selected(None)
        self.panel.set_status(f"已记录 {len(self.keys)} 场" if self.keys else "输入一场战斗的链接")

    def clear_cache(self) -> None:
        """Drop finished results. A fight that is still computing is left alone."""
        self.loads.cache.clear()
        if self.loads.selected not in self.loads.running:
            self.loads.selected = None
            self.panel.set_selected(None)
        self.ctl.clear_session()
        for index, key in enumerate(self.keys):
            if key not in self.loads.running:
                self.panel.clear_done(index)

    def fail(self, key: tuple, message: str) -> None:
        self.loads.abandon(key)
        if key not in self.keys:
            return
        self.panel.mark_error(self.keys.index(key), message)

    def show_selected(self) -> None:
        key = self.loads.selected
        if key not in self.keys:
            return
        self.panel.set_selected(self.keys.index(key))
        cached = self.loads.cache.get(key)
        if cached is not None:
            self.ctl.set_session(cached)
