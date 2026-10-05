# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import partial
from multiprocessing.reduction import ForkingPickler
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox

from wcl_replay.bosses.base import Analysis, AnalysisParameter, Lane, LogEntry, Seg
from wcl_replay.core.models import Actor, ActorKind, Fight, FightData, Sample
from wcl_replay.core.tracks import Tracks
from wcl_replay.sources.local_log import EncounterEntry
from wcl_replay.ui import main_window
from wcl_replay.ui.controller import ReplayController, Session
from wcl_replay.ui.main_window import MainWindow
from wcl_replay.ui.panels import EventLogPanel
from wcl_replay.ui.wcl_dialogs import CredentialsDialog
from wcl_replay.ui.wcl_panel import WclPanel
from wcl_replay.workers import fetch_wcl_job

URL = "https://cn.warcraftlogs.com/reports/ABCDEFGHIJKLMNOP?fight=1"


@dataclass(slots=True)
class _Pending:
    fn: Any
    args: tuple
    done: Any
    fail: Any
    progress: Any
    cancellations: int = 0

    def cancel(self):
        if not self.cancellations:
            self.cancellations += 1
            self.fail("任务已取消")


class _Runner:
    def __init__(self):
        self.jobs = []

    def run(self, fn, args, done, fail, progress=None):
        job = _Pending(fn, args, done, fail, progress)
        self.jobs.append(job)
        return job

    def shutdown(self):
        pass


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(tmp_path):
    return QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)


@pytest.fixture
def window(app, settings, monkeypatch):
    runner = _Runner()
    monkeypatch.setattr(main_window, "TaskRunner", lambda _parent: runner)
    win = MainWindow(settings=settings)
    monkeypatch.setattr(
        win, "_credentials", lambda: ("synthetic-client", "synthetic-secret", "www.warcraftlogs.com")
    )
    monkeypatch.setattr(win, "_refresh_wcl_quota", lambda: None)
    yield win, runner
    win.ctl.pause()
    win.close()
    win.deleteLater()
    app.processEvents()


def _session(source="synthetic", analysis_type=Analysis):
    data = FightData(
        Fight(1, 999, "测试", 16, 20, 90000, False),
        {0: Actor(0, "Player-1", "玩家", ActorKind.PLAYER)},
        [],
        {0: [Sample(0, 0, 0, 0, 100, 100)]},
        source=source,
    )
    tracks = Tracks(data)
    return Session(data, tracks, analysis_type(data, tracks))


def _result(source):
    session = _session(source)
    return session.data, session.tracks, session.analysis


def test_repeated_local_row_click_preserves_playback_selection_and_camera(window, tmp_path):
    win, runner = window
    path = str(tmp_path / "synthetic.txt")
    entry = EncounterEntry(1, 999, "测试", 16, 20, 1, 0, 100, "1/1/2026 12:00:00.000", 90000, False, True, 1)
    gen = win.board.prepare(path)
    win.board.show_entries(gen, path, [entry])
    win.log_panel.rows[0].clicked.emit()
    runner.jobs[-1].done(_result("local"))
    session = win.ctl.session
    win.ctl.seek(12000)
    win.ctl.select(0)
    win.map_view._zoom = 1.8
    win.ctl.play()
    changes = []
    win.ctl.sessionChanged.connect(lambda: changes.append(1))
    job_count = len(runner.jobs)
    win.log_panel.rows[0].clicked.emit()
    assert win.ctl.session is session
    assert (win.ctl.t, win.ctl.playing, win.ctl.selected, win.map_view._zoom) == (12000, True, 0, 1.8)
    assert not changes and len(runner.jobs) == job_count


@pytest.mark.parametrize("error", [None, "synthetic connection failure"])
def test_credentials_inputs_freeze_during_test_and_restore_after_result(app, settings, monkeypatch, error):
    runner = _Runner()
    dialog = CredentialsDialog(settings, runner=runner)
    dialog.client_id.setText("synthetic-client")
    dialog.secret.setText("synthetic-secret")
    popups = []
    monkeypatch.setattr(QMessageBox, "information", lambda _parent, title, _text: popups.append(title))
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, title, _text: popups.append(title))
    try:
        dialog._test()
        controls = (dialog.client_id, dialog.secret, dialog.host, dialog.test_btn)
        assert all(not control.isEnabled() for control in controls)
        if error:
            runner.jobs[-1].fail(error)
        else:
            runner.jobs[-1].done(True)
        assert all(control.isEnabled() for control in controls)
        assert dialog.test_btn.text() == "测试连接"
        assert popups == ["连接失败" if error else "连接成功"]
    finally:
        dialog.reject()
        dialog.deleteLater()


def test_credentials_discard_changed_values_and_results_after_closing(app, settings, monkeypatch):
    runner = _Runner()
    dialog = CredentialsDialog(settings, runner=runner)
    dialog.client_id.setText("synthetic-client")
    dialog.secret.setText("synthetic-A")
    popups = []
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: popups.append("success"))
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: popups.append("failure"))
    dialog._test()
    first = runner.jobs[-1]
    # Programmatic changes must be guarded too, even with user input disabled.
    dialog.secret.setText("synthetic-B")
    first.done(True)
    assert dialog.secret.isEnabled() and not popups
    dialog._test()
    second = runner.jobs[-1]
    dialog.reject()
    assert second.cancellations == 1
    second.done(True)
    second.fail("late failure")
    assert not popups
    assert dialog.test_btn.isEnabled()
    dialog.deleteLater()


class _ConfigurableAnalysis(Analysis):
    parameters = (AnalysisParameter("radius", "半径", 8.0, minimum=0, maximum=40),)

    def apply_parameters(self, values):
        super().apply_parameters(values)
        radius = self.parameter_values["radius"]
        self.lanes = [Lane("main", "机制", "#e0a030")]
        self.log = [LogEntry(1000, [Seg(f"半径 {radius:g}")], "main"), LogEntry(2000, [Seg("常驻记录")])]
        if radius >= 10:
            self.lanes.append(Lane("extra", "新增机制", "#e0a030"))
            self.log.append(LogEntry(3000, [Seg("新增图层记录")], "extra"))


def test_parameter_refresh_builds_log_once_and_keeps_layer_filtering_correct(app, monkeypatch):
    ctl = ReplayController()
    panel = EventLogPanel(ctl)
    ctl.set_session(_session(analysis_type=_ConfigurableAnalysis))
    builds = []
    original = panel.setHtml

    def set_html(text):
        builds.append(text)
        original(text)

    monkeypatch.setattr(panel, "setHtml", set_html)
    ctl.set_parameter("radius", 9)
    assert len(builds) == 1 and "半径 9" in panel.toPlainText()
    ctl.toggle_layer("main")
    assert "半径 9" not in panel.toPlainText()
    assert "常驻记录" in panel.toPlainText()
    builds.clear()
    ctl.set_parameter("radius", 10)
    assert len(builds) == 1
    assert not ctl.layer_on("main") and ctl.layer_on("extra")
    assert "半径 10" not in panel.toPlainText() and "新增图层记录" in panel.toPlainText()
    ctl.toggle_layer("extra")
    assert "新增图层记录" not in panel.toPlainText()
    builds.clear()
    ctl.toggle_layer("unrelated")
    assert not builds
    panel.deleteLater()


def test_wcl_selection_updates_only_changed_rows_and_survives_insertion_and_removal(app, monkeypatch):
    panel = WclPanel()
    for index in range(100):
        panel.add_row(f"战斗 {index}")
    changes = []
    for row in panel.rows:
        original = row.set_selected

        def set_selected(on, row=row, original=original):
            changes.append((row, on))
            original(on)

        monkeypatch.setattr(row, "set_selected", set_selected)
    first, second = panel.rows[0], panel.rows[1]
    panel.set_selected(0)
    panel.set_selected(0)
    panel.set_selected(1)
    assert changes == [(first, True), (first, False), (second, True)]
    changes.clear()
    panel.add_row("新战斗")
    panel.set_selected(2)
    assert not changes and second.property("selected")
    panel.remove_row(0)
    panel.set_selected(1)
    assert not changes and second.property("selected")
    removed = panel.rows[1]
    panel.remove_row(1)
    requested = []
    panel.reloadRequested.connect(requested.append)
    removed.reloadClicked.emit()
    assert not requested
    panel.set_selected(0)
    assert first.property("selected")
    panel.deleteLater()


def _start_wcl(win, runner):
    win._set_source("wcl")
    win.query_wcl(URL)
    runner.jobs[-1].done(_result("original"))
    win.ctl.seek(12000)
    win.ctl.play()


def test_wcl_reload_bypasses_cache_preserves_replay_and_rejects_cancelled_callbacks(window):
    win, runner = window
    _start_wcl(win, runner)
    original = win.ctl.session
    row = win.wcl_panel.rows[0]
    row.reload_btn.click()
    first = runner.jobs[-1]
    assert isinstance(first.fn, partial) and first.fn.func is fetch_wcl_job
    assert first.fn.keywords == {"force_refresh": True}
    assert first.args == (URL, "synthetic-client", "synthetic-secret", "cn.warcraftlogs.com")
    restored = ForkingPickler.loads(ForkingPickler.dumps(first.fn))
    assert restored.func is fetch_wcl_job and restored.keywords == {"force_refresh": True}
    assert win.ctl.session is original and win.ctl.t == 12000 and win.ctl.playing
    row.reload_btn.click()
    second = runner.jobs[-1]
    assert first.cancellations == 1
    second.progress(0.4, "phase:download:当前任务")
    first.progress(0.9, "phase:download:旧任务")
    first.fail("旧错误")
    first.done(_result("stale"))
    assert win.ctl.session is original
    assert row.download.bar.value() == 400 and row.err.isHidden()
    second.done(_result("fresh"))
    assert win.ctl.session.data.source == "fresh"
    assert len(win.wcl_board.keys) == 1 and row.mark.text() == "✓"


def test_wcl_failed_reload_keeps_old_result_and_deleting_a_retry_cancels_it(window):
    win, runner = window
    _start_wcl(win, runner)
    original = win.ctl.session
    key = win.wcl_board.keys[0]
    row = win.wcl_panel.rows[0]
    row.reload_btn.click()
    runner.jobs[-1].fail("synthetic offline")
    assert win.ctl.session is original and win.wcl_board.loads.cache[key] is original
    assert key not in win.wcl_board.loads.running
    row.reload_btn.click()
    retry = runner.jobs[-1]
    row.delete_btn.click()
    assert retry.cancellations == 1
    retry.done(_result("deleted"))
    assert not win.wcl_board.keys and not win.wcl_board.loads.cache
    assert win.ctl.session is None
