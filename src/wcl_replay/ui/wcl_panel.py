# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""WCL card: paste one fight URL, compute it, and retain each fight's list entry."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..bosses.base import fmt_time
from ..core.difficulty import DIFFICULTY_LABELS
from .controller import ReplayController
from .log_panel import PullLoads, _PullRow


def split_phase(message: str) -> tuple[str, str]:
    """``phase:download`` or ``phase:compute``, with an optional ``:detail`` tail."""
    for phase in ("download", "compute"):
        prefix = f"phase:{phase}"
        if message == prefix:
            return phase, ""
        if message.startswith(prefix + ":"):
            return phase, message[len(prefix) + 1 :]
    return "download", message


class _Meter:
    """One labeled bar. ``value`` is 0–1000, matching the local-log rows."""

    def __init__(self, caption: str, tip: str):
        self.tip = tip
        self.host = QWidget()
        self.host.setObjectName("wclMeter")
        self.host.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        row = QHBoxLayout(self.host)
        row.setContentsMargins(24, 0, 0, 0)
        row.setSpacing(6)
        self.caption = QLabel(caption)
        self.caption.setObjectName("meterCaption")
        self.caption.setFixedWidth(36)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        self.bar.setToolTip(tip)
        self.percent = QLabel("0%")
        self.percent.setObjectName("meterCaption")
        self.percent.setFixedWidth(40)
        self.percent.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.caption)
        row.addWidget(self.bar, 1)
        row.addWidget(self.percent)
        self.host.hide()

    def set(self, frac: float, detail: str = "") -> None:
        clamped = max(0.0, min(1.0, frac))
        self.bar.setValue(int(clamped * 1000))
        self.percent.setText(f"{int(clamped * 100)}%")
        if detail:
            self.bar.setToolTip(detail)
        self.host.show()

    def reset(self) -> None:
        self.bar.setToolTip(self.tip)
        self.set(0.0)

    def hide(self) -> None:
        self.host.hide()


class _WclRow(_PullRow):
    """Fight row with a download bar and a separate calculation bar."""

    def __init__(
        self,
        label: str,
        parent: QWidget | None = None,
        *,
        action_tip: str = "删除这条记录并释放内存",
        star_tip: str = "收藏。自动释放或清除缓存时会留下这场。",
    ):
        super().__init__(label, parent, action_tip=action_tip, star_tip=star_tip)
        lay = self.layout()
        lay.removeWidget(self.bar)
        self.bar.setParent(None)
        self.download = _Meter("下载", "下载这场战斗的日志")
        self.compute = _Meter("计算", "计算轨迹和机制")
        lay.insertWidget(1, self.download.host)
        lay.insertWidget(2, self.compute.host)

    def reset_progress(self) -> None:
        self.err.hide()
        self.mark.setText("")
        self.mark.setToolTip("")
        self.download.reset()
        self.compute.reset()

    def show_phase(self, phase: str, frac: float, detail: str = "") -> None:
        self.err.hide()
        self.mark.setText("")
        self.mark.setToolTip("")
        self.download.host.show()
        self.compute.host.show()
        meter = self.compute if phase == "compute" else self.download
        meter.set(frac, detail)

    def mark_done(self) -> None:
        self.download.hide()
        self.compute.hide()
        super().mark_done()

    def release_result(self) -> None:
        self.download.hide()
        self.compute.hide()
        super().release_result()

    def mark_error(self, message: str) -> None:
        self.download.hide()
        self.compute.hide()
        super().mark_error(message)


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
    starToggled = Signal(int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("logCard")
        self.setMinimumWidth(340)
        self._rows: list[_WclRow] = []
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

    def clear_url(self) -> None:
        self.url_edit.clear()

    def set_status(self, text: str) -> None:
        self.status_lbl.setText(text)

    def set_quota(self, text: str, tooltip: str = "") -> None:
        self.quota_lbl.setText(text)
        self.quota_lbl.setToolTip(tooltip)

    def add_row(self, label: str) -> int:
        """Insert a row at the top. The click reports wherever that row sits when it is clicked."""
        stretch = self._list.takeAt(self._list.count() - 1)
        row = _WclRow(label, self._body)
        row.clicked.connect(lambda r=row: self.activated.emit(self._rows.index(r)))
        row.deleteClicked.connect(lambda r=row: self.removeRequested.emit(self._rows.index(r)))
        row.starClicked.connect(lambda r=row: self.starToggled.emit(self._rows.index(r)))
        self._rows.insert(0, row)
        self._list.insertWidget(0, row)
        self._list.addItem(stretch)
        self._scroll.verticalScrollBar().setValue(0)
        return 0

    @property
    def rows(self) -> list[_WclRow]:
        return self._rows

    def set_label(self, index: int, label: str) -> None:
        self._rows[index].text.setText(label)
        self._rows[index].text.setToolTip(label)

    def set_selected(self, index: int | None) -> None:
        for i, row in enumerate(self._rows):
            row.set_selected(i == index)

    def reset_progress(self, index: int) -> None:
        self._rows[index].reset_progress()

    def show_progress(self, index: int, frac: float, *, phase: str = "download", detail: str = "") -> None:
        self._rows[index].show_phase(phase, frac, detail)

    def mark_done(self, index: int) -> None:
        self._rows[index].mark_done()

    def mark_error(self, index: int, message: str) -> None:
        self._rows[index].mark_error(message)

    def clear_done(self, index: int) -> None:
        self._rows[index].clear_done()

    def release_result(self, index: int) -> None:
        self._rows[index].release_result()

    def remove_row(self, index: int) -> None:
        row = self._rows.pop(index)
        self._list.removeWidget(row)
        row.setParent(None)
        row.deleteLater()

    def _emit_query(self) -> None:
        self.queryRequested.emit(self.url())


class WclBoard:
    """In-memory list of WCL fights. A new query is inserted at the top."""

    def __init__(
        self,
        ctl: ReplayController,
        panel: WclPanel,
        active: Callable[[], bool] | None = None,
        *,
        limit: Callable[[], int] | None = None,
    ):
        self.ctl = ctl
        self.panel = panel
        self.active = active or (lambda: True)
        self._limit = limit or (lambda: 0)
        self.loads = PullLoads()
        self.pinned: set[tuple] = set()
        self.keys: list[tuple] = []
        self._seq = 0
        self.panel.removeRequested.connect(self.remove)
        self.panel.starToggled.connect(self.toggle_pin)

    def begin(self, url: str) -> tuple[int, tuple]:
        """Insert a row at the top and mark it as the query that is about to run."""
        self._seq += 1
        key = ("wcl", url, self._seq)
        index = self.panel.add_row(url)
        self.keys.insert(0, key)
        self.loads.click(key)
        self.panel.set_selected(index)
        self.panel.reset_progress(index)
        self.panel.set_status(f"已记录 {len(self.keys)} 场")
        return index, key

    def activate(self, index: int) -> str:
        """Show cached results, or restart a cleared/failed row."""
        key = self.keys[index]
        self.panel.set_selected(index)
        action = self.loads.click(key)
        if action == "show" and self.active():
            cached = self.loads.cache[key]
            if self.ctl.session is not cached:
                self.ctl.set_session(cached)
        elif action == "start":
            self.panel.reset_progress(index)
        self.trim_to_limit()
        return action

    def note_progress(self, key: tuple, frac: float, message: str = "", *, run_id: int | None = None) -> None:
        if key not in self.keys or not self.loads.is_current(key, run_id):
            return
        phase, detail = split_phase(message)
        self.panel.show_progress(self.keys.index(key), frac, phase=phase, detail=detail)

    def finish(self, key: tuple, session: object, label: str, *, run_id: int | None = None) -> bool:
        if not self.loads.is_current(key, run_id):
            return False
        if key not in self.keys:
            self.loads.abandon(key)
            return False
        index = self.keys.index(key)
        show = self.loads.complete(key, session)
        show = show and self.active()
        self.panel.set_label(index, label)
        self.panel.mark_done(index)
        if show:
            self.ctl.set_session(session)
        self.trim_to_limit()
        return show

    def trim_to_limit(self) -> None:
        """Release the oldest results, retaining rows, favorites and the current replay."""
        try:
            cap = max(0, int(self._limit()))
        except (TypeError, ValueError):
            cap = 0
        if cap == 0:
            return
        for key in list(self.loads.order):
            if len(self.loads.cache) <= cap:
                break
            if key in self.pinned or key in self.loads.running or key == self.loads.selected:
                continue
            cached = self.loads.cache.get(key)
            if cached is None or self.ctl.session is cached:
                continue
            del self.loads.cache[key]
            self.loads.forget_order(key)
            if key in self.keys:
                self.panel.release_result(self.keys.index(key))

    def toggle_pin(self, index: int) -> None:
        if not 0 <= index < len(self.keys):
            return
        key = self.keys[index]
        if key in self.pinned:
            self.pinned.discard(key)
        else:
            self.pinned.add(key)
        self.panel.rows[index].set_starred(key in self.pinned)
        self.trim_to_limit()

    def remove(self, index: int) -> None:
        """Drop one queried fight and release its computed result."""
        if not 0 <= index < len(self.keys):
            return
        key = self.keys.pop(index)
        self.pinned.discard(key)
        session = self.loads.cache.pop(key, None)
        self.loads.abandon(key)
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
        """Drop finished results. A pinned fight, and one that is still computing, stay."""
        self.loads.cache = {key: session for key, session in self.loads.cache.items() if key in self.pinned}
        self.loads.order = [
            key for key in self.loads.order if key in self.loads.running or key in self.loads.cache
        ]
        if self.loads.selected not in self.loads.cache:
            if self.loads.selected not in self.loads.running:
                self.loads.selected = None
                self.panel.set_selected(None)
            if self.active():
                self.ctl.clear_session()
        for index, key in enumerate(self.keys):
            if key not in self.loads.running and key not in self.loads.cache:
                self.panel.clear_done(index)

    def fail(self, key: tuple, message: str, *, run_id: int | None = None) -> None:
        if not self.loads.is_current(key, run_id):
            return
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
        if self.active() and cached is not None and self.ctl.session is not cached:
            self.ctl.set_session(cached)
