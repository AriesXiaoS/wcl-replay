# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Main window: local-log column on the left, map in the middle, mechanic panels on the right."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..bosses.base import fmt_time
from ..sources.local_log.index import DIFFICULTY_LABELS
from ..sources.wcl_api.fetch import report_host
from ..sources.wcl_api.urls import parse_report_url
from ..workers import analyze_pull_job, fetch_wcl_job, index_log_job, rate_limit_job
from .controller import ReplayController, Session
from .loader import TaskRunner
from .log_panel import LogPanel, PullBoard, short_error
from .map_view import MapView
from .panels import EventLogPanel, StatusPanel
from .playback import GhostSpeedBar, PlaybackBar
from .raid_frames import AuraFilter, RaidFrames
from .settings_dialog import SettingsDialog, cached_limit
from .stack_panel import UnitStackPanel
from .timeline import TimelineWidget
from .wcl_dialogs import CredentialsDialog
from .wcl_panel import WclBoard, WclPanel, fight_label, quota_text

DEFAULT_LOG_DIRS = (
    r"G:\World of Warcraft\_retail_\Logs",
    r"C:\Program Files (x86)\World of Warcraft\_retail_\Logs",
)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"WCL Replay {__version__} · 战斗复盘")
        self.resize(1680, 980)
        self.statusBar().hide()
        self.settings = QSettings("wcl_replay", "wcl_replay")
        self._quota_gen = 0
        self.ctl = ReplayController(self)
        self.tasks = TaskRunner(self)
        self.log_panel = LogPanel()
        self.board = PullBoard(self.ctl, self.log_panel, limit=lambda: cached_limit(self.settings))
        self.log_panel.openClicked.connect(self.open_log_dialog)
        self.log_panel.refreshClicked.connect(self.reload_log)
        self.log_panel.clearClicked.connect(self.board.clear_cache)
        self.log_panel.activated.connect(self._on_activated)
        self.wcl_panel = WclPanel()
        self.wcl_board = WclBoard(self.ctl, self.wcl_panel)
        self.wcl_panel.queryRequested.connect(self.query_wcl)
        self.wcl_panel.settingsRequested.connect(self.edit_wcl_credentials)
        self.wcl_panel.clearClicked.connect(self.wcl_board.clear_cache)
        self.wcl_panel.quotaRefreshRequested.connect(self._refresh_wcl_quota)
        self.wcl_panel.activated.connect(self._on_wcl_activated)
        last_url = str(self.settings.value("wcl_last_url", "") or "")
        if last_url:
            self.wcl_panel.set_url(last_url)

        self._build_body()
        self._shortcuts()

    def _build_body(self) -> None:
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(6, 6, 6, 6)
        self.title_lbl = QLabel("")
        self.title_lbl.setObjectName("title")
        lv.addWidget(self.title_lbl)
        self.map_view = MapView(self.ctl)
        lv.addWidget(self.map_view, 1)
        self.stack_panel = UnitStackPanel(self.ctl, self.settings, self.map_view)
        lv.addWidget(PlaybackBar(self.ctl))
        lv.addWidget(GhostSpeedBar(self.ctl, self.settings))
        lv.addWidget(TimelineWidget(self.ctl))

        self.aura_filter = AuraFilter(self.ctl, self.settings)
        self.raid_frames = RaidFrames(self.ctl, self.aura_filter.selected_keys)
        self.aura_filter.changed.connect(self.raid_frames.grid.update)
        middle = QSplitter(Qt.Orientation.Vertical)
        middle.addWidget(self.raid_frames)
        middle.addWidget(self.aura_filter)
        middle.addWidget(StatusPanel(self.ctl))
        middle.addWidget(EventLogPanel(self.ctl))
        middle.setStretchFactor(0, 0)
        middle.setStretchFactor(1, 0)
        middle.setStretchFactor(2, 1)
        middle.setStretchFactor(3, 2)
        middle.setSizes([300, 42, 240, 400])

        self._source = QStackedWidget()
        self._source.addWidget(self.log_panel)
        self._source.addWidget(self.wcl_panel)
        source_column = QWidget()
        source_lay = QVBoxLayout(source_column)
        source_lay.setContentsMargins(0, 0, 0, 0)
        source_lay.setSpacing(6)
        source_lay.addWidget(self._mode_switch())
        source_lay.addWidget(self._source, 1)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(source_column)
        split.addWidget(left)
        split.addWidget(middle)
        split.setSizes([300, 980, 400])
        shell = QWidget()
        shell_lay = QVBoxLayout(shell)
        shell_lay.setContentsMargins(4, 4, 4, 4)
        shell_lay.setSpacing(0)
        shell_lay.addWidget(split)
        self.setCentralWidget(shell)
        self.ctl.sessionChanged.connect(self._update_title)
        if str(self.settings.value("source_mode", "local") or "local") == "wcl":
            self._wcl_mode.setChecked(True)
            self._source.setCurrentWidget(self.wcl_panel)

    def _mode_switch(self) -> QWidget:
        bar = QWidget()
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self._local_mode = QPushButton("本地日志")
        self._wcl_mode = QPushButton("WCL API")
        group = QButtonGroup(bar)
        group.setExclusive(True)
        for button in (self._local_mode, self._wcl_mode):
            button.setObjectName("modeBtn")
            button.setCheckable(True)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            group.addButton(button)
        self._local_mode.setChecked(True)
        self._local_mode.clicked.connect(lambda: self._set_source("local"))
        self._wcl_mode.clicked.connect(lambda: self._set_source("wcl"))
        self.settings_btn = QPushButton("设置")
        self.settings_btn.setObjectName("modeBtn")
        self.settings_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.settings_btn.clicked.connect(self.open_settings)
        lay.addWidget(self._local_mode, 1)
        lay.addWidget(self._wcl_mode, 1)
        lay.addWidget(self.settings_btn)
        return bar

    def open_settings(self) -> None:
        if SettingsDialog(self.settings, self).exec():
            self.board.trim_to_limit()

    def _set_source(self, mode: str) -> None:
        self.settings.setValue("source_mode", mode)
        self._source.setCurrentWidget(self.wcl_panel if mode == "wcl" else self.log_panel)
        if mode == "local":
            self.board.show_selected()
        else:
            self.wcl_board.show_selected()

    def _shortcuts(self) -> None:
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, self.ctl.toggle_play)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, lambda: self.ctl.step(-5000))
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, lambda: self.ctl.step(5000))
        QShortcut(QKeySequence("Shift+Left"), self, lambda: self.ctl.step(-1000))
        QShortcut(QKeySequence("Shift+Right"), self, lambda: self.ctl.step(1000))

    # -- local log ----------------------------------------------------------------------------

    def open_log_dialog(self) -> None:
        start = self.settings.value("last_dir", "") or next(
            (d for d in DEFAULT_LOG_DIRS if os.path.isdir(d)), ""
        )
        path, _ = QFileDialog.getOpenFileName(
            self, "选择战斗日志", start, "Combat logs (WoWCombatLog*.txt);;All (*)"
        )
        if path:
            self.settings.setValue("last_dir", str(Path(path).parent))
            self.open_log(path, source="open")

    def reload_log(self) -> None:
        if self.board.path and os.path.exists(self.board.path):
            self.open_log(self.board.path, source="refresh")

    def open_log(self, path: str, source: str = "open") -> None:
        self.settings.setValue("last_log", path)
        gen = self.board.prepare(path, source)

        self.tasks.run(
            index_log_job,
            (path,),
            lambda entries, g=gen: self._on_indexed(g, path, entries),
            lambda tb, g=gen: self.board.index_failed(g, "读取失败：" + short_error(tb)),
            lambda frac, msg, g=gen: self._on_index_progress(g, frac, msg),
        )

    def _on_index_progress(self, gen: int, frac: float, msg: str) -> None:
        if gen != self.board.gen or not msg:
            return
        pct = int(max(0.0, min(1.0, frac)) * 100)
        self.log_panel.set_status(f"{msg} {pct}%")

    def _on_indexed(self, gen: int, path: str, entries: list) -> None:
        self.board.show_entries(gen, path, entries)

    def _on_pull(self, gen: int, path: str, key: tuple, result: tuple) -> None:
        data, tracks, analysis = result
        self.board.finish(gen, path, key, Session(data, tracks, analysis))

    def _stored_credentials(self) -> tuple[str, str, str] | None:
        client_id = str(self.settings.value("wcl_client_id", "") or "").strip()
        secret = str(self.settings.value("wcl_client_secret", "") or "").strip()
        host = (
            str(self.settings.value("wcl_host", "cn.warcraftlogs.com") or "").strip() or "cn.warcraftlogs.com"
        )
        if not client_id or not secret:
            return None
        return client_id, secret, host

    def _credentials(self) -> tuple[str, str, str] | None:
        stored = self._stored_credentials()
        if stored is not None:
            return stored
        if CredentialsDialog(self.settings, self).exec() != CredentialsDialog.DialogCode.Accepted:
            return None
        return self._stored_credentials()

    def edit_wcl_credentials(self) -> None:
        CredentialsDialog(self.settings, self).exec()

    def _refresh_wcl_quota(self) -> None:
        creds = self._stored_credentials()
        self._quota_gen += 1
        gen = self._quota_gen
        if creds is None:
            self.wcl_panel.set_quota("未设置 API")
            return
        if self.wcl_panel.quota_lbl.text() in ("额度未刷新", "未设置 API", "额度读取失败"):
            self.wcl_panel.set_quota("正在读取额度…")
        client_id, secret, host = creds
        self.tasks.run(
            rate_limit_job,
            (client_id, secret, host),
            lambda result, g=gen: self._on_quota(g, result),
            lambda tb, g=gen: self._on_quota_fail(g, tb),
        )

    def _on_quota(self, gen: int, result: tuple) -> None:
        if gen != self._quota_gen:
            return
        spent, limit, reset_in = result
        text, tip = quota_text(float(spent), int(limit), int(reset_in))
        self.wcl_panel.set_quota(text, tip)

    def _on_quota_fail(self, gen: int, tb: str) -> None:
        if gen != self._quota_gen:
            return
        self.wcl_panel.set_quota("额度读取失败", short_error(tb))

    def query_wcl(self, url: str) -> None:
        url = url.strip()
        try:
            _code, fight_id = parse_report_url(url)
        except ValueError as exc:
            QMessageBox.warning(self, "链接无效", str(exc))
            return
        if fight_id is None:
            QMessageBox.warning(self, "链接无效", "请使用带 fight= 的单场战斗链接。")
            return
        creds = self._credentials()
        if creds is None:
            return
        client_id, secret, host = creds
        host = report_host(url) or host
        self.settings.setValue("wcl_last_url", url)
        _index, key = self.wcl_board.begin(url)
        self.tasks.run(
            fetch_wcl_job,
            (url, client_id, secret, host),
            lambda result, k=key: self._on_wcl(k, result),
            lambda tb, k=key: (self.wcl_board.fail(k, short_error(tb)), self._refresh_wcl_quota()),
            lambda frac, _msg, k=key: self.wcl_board.note_progress(k, frac),
        )

    def _on_wcl(self, key: tuple, result: tuple) -> None:
        data, tracks, analysis = result
        session = Session(data, tracks, analysis)
        self.wcl_board.finish(key, session, fight_label(session))
        self._refresh_wcl_quota()

    def _on_wcl_activated(self, index: int) -> None:
        self.wcl_board.activate(index)

    def _on_activated(self, index: int) -> None:
        action = self.board.activate(index)
        if action != "start":
            return
        gen = self.board.gen
        path = self.board.path
        entry = self.board.entries[index]
        key = self.board.keys[index]

        self.tasks.run(
            analyze_pull_job,
            (path, entry),
            lambda result, g=gen, p=path, k=key: self._on_pull(g, p, k, result),
            lambda tb, g=gen, p=path, k=key: self.board.fail(g, p, k, short_error(tb)),
            lambda frac, _msg, g=gen, p=path, k=key: self.board.note_progress(g, p, k, frac),
        )

    def _update_title(self) -> None:
        s = self.ctl.session
        if s is None:
            self.title_lbl.setText("")
            return
        fight = s.data.fight
        diff = DIFFICULTY_LABELS.get(fight.difficulty, str(fight.difficulty))
        result = "击杀" if fight.kill else "灭团"
        pull = f"pull {fight.pull_number}" if fight.pull_number else f"fight {fight.id}"
        self.title_lbl.setText(
            f"{fight.name} · {diff} · {pull} · {fight.start_label} · {fmt_time(fight.duration_ms)} · {result}"
            f"   [{s.analysis.title or '通用'}]  ({s.data.source})"
        )
