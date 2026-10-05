# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

import pytest
from PySide6.QtCore import QSettings, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from wcl_replay.bosses.base import Analysis, AnalysisParameter, Cell, Lane, StatusSection
from wcl_replay.core.models import Fight, FightData
from wcl_replay.core.tracks import Tracks
from wcl_replay.sources.local_log import EncounterEntry
from wcl_replay.ui import main_window
from wcl_replay.ui.controller import ReplayController, Session
from wcl_replay.ui.log_panel import LogPanel, PullBoard
from wcl_replay.ui.main_window import MainWindow
from wcl_replay.ui.panels import StatusPanel
from wcl_replay.ui.wcl_dialogs import CredentialsDialog
from wcl_replay.ui.wcl_panel import WclBoard, WclPanel
from wcl_replay.workers import rate_limit_job


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(tmp_path):
    return QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat)


def _session(analysis_type=Analysis):
    data = FightData(Fight(1, 999, "测试", 16, 20, 90_000, False), {}, [], {})
    tracks = Tracks(data)
    return Session(data, tracks, analysis_type(data, tracks))


def _finished_wcl(board, url):
    _, key = board.begin(url)
    result = _session()
    assert board.finish(key, result, url, run_id=board.loads.run_id(key))
    return key, result


def test_failed_wcl_refresh_keeps_the_previous_result_in_the_eviction_order(app):
    ctl, panel = ReplayController(), WclPanel()
    board = WclBoard(ctl, panel, limit=lambda: 1)
    key, previous = _finished_wcl(board, "first")
    ctl.seek(12_000)
    assert board.reload(0) == key
    board.fail(key, "synthetic offline", run_id=board.loads.run_id(key))
    assert ctl.session is previous and ctl.t == 12_000
    assert board.loads.cache[key] is previous and board.loads.order == [key]

    newest, _result = _finished_wcl(board, "second")
    assert list(board.loads.cache) == [newest]
    assert board.loads.order == [newest]
    assert panel.rows[1].mark.text() == "" and panel.rows[1].err.isHidden()
    panel.deleteLater()


def test_wcl_refresh_retry_preserves_order_and_delete_and_clear_forget_released_results(app):
    ctl, panel = ReplayController(), WclPanel()
    board = WclBoard(ctl, panel, limit=lambda: 1)
    first, _previous = _finished_wcl(board, "first")
    board.toggle_pin(0)
    newest, _result = _finished_wcl(board, "second")
    board.reload(1)
    first_run = board.loads.run_id(first)
    board.reload(1)
    second_run = board.loads.run_id(first)
    assert second_run != first_run and board.loads.order == [first, newest]
    board.fail(first, "stale failure", run_id=first_run)
    assert first in board.loads.running
    board.fail(first, "current failure", run_id=second_run)
    assert first not in board.loads.running and board.loads.order == [first, newest]
    board.activate(0)
    board.toggle_pin(1)
    assert first not in board.loads.cache and board.loads.order == [newest]

    board.reload(0)
    board.remove(0)
    assert not board.loads.cache and not board.loads.order and not board.loads.running
    kept, _result = _finished_wcl(board, "third")
    board.clear_cache()
    assert kept not in board.loads.cache and not board.loads.order
    panel.deleteLater()


def test_local_release_forgets_cache_order_and_allows_a_fresh_attempt(app):
    ctl, panel = ReplayController(), LogPanel()
    board = PullBoard(ctl, panel)
    path = "synthetic.txt"
    entry = EncounterEntry(1, 999, "测试", 16, 20, 1, 0, 100, "1/1/2026 12:00:00.000", 90_000, False, True, 1)
    generation = board.prepare(path)
    board.show_entries(generation, path, [entry])
    assert board.activate(0) == "start"
    key = board.keys[0]
    first_run = board.loads.run_id(key)
    assert board.finish(generation, path, key, _session(), run_id=first_run)
    board.release(0)
    assert not board.loads.cache and not board.loads.order
    assert ctl.session is None and panel.rows[0].mark.text() == ""
    assert board.activate(0) == "start"
    assert board.loads.run_id(key) != first_run
    board.release(0)
    assert not board.loads.running and not board.loads.order
    panel.deleteLater()


@dataclass(slots=True)
class _Pending:
    fn: Any
    args: tuple
    done: Any
    fail: Any
    progress: Any = None
    cancellations: int = 0

    def cancel(self):
        if not self.cancellations:
            self.cancellations += 1
            self.fail("任务已取消")


class _Runner:
    def __init__(self):
        self.jobs = []

    def run(self, *args):
        job = _Pending(*args)
        self.jobs.append(job)
        return job

    def shutdown(self):
        pass


@pytest.fixture
def window(app, settings, monkeypatch):
    runner = _Runner()
    monkeypatch.setattr(main_window, "TaskRunner", lambda _parent: runner)
    win = MainWindow(settings=settings)
    yield win, runner
    win.close()
    win.deleteLater()
    app.processEvents()


def _store_credentials(settings, values):
    for key, value in zip(("wcl_client_id", "wcl_client_secret", "wcl_host"), values, strict=True):
        settings.setValue(key, value)


def _finish_credentials_dialog(values=None, *, accepted=True):
    dialog = QApplication.activeModalWidget()
    assert isinstance(dialog, CredentialsDialog)
    if values is not None:
        cid, secret, host = values
        dialog.client_id.setText(cid)
        dialog.secret.setText(secret)
        dialog.host.setCurrentText(host)
    dialog._save() if accepted else dialog.reject()


@pytest.mark.parametrize("changed_index", [0, 1, 2])
def test_changed_credentials_cancel_old_quota_and_ignore_its_late_callbacks(window, changed_index):
    win, runner = window
    original = ("synthetic-id-A", "synthetic-secret-A", "www.warcraftlogs.com")
    replacement = list(original)
    replacement[changed_index] = ("synthetic-id-B", "synthetic-secret-B", "cn.warcraftlogs.com")[
        changed_index
    ]
    _store_credentials(win.settings, original)
    win._refresh_wcl_quota()
    old = runner.jobs[-1]
    win.wcl_panel.set_quota("本小时 123 / 1000", "账户 A")
    QTimer.singleShot(0, lambda: _finish_credentials_dialog(replacement))
    win.edit_wcl_credentials()
    current = runner.jobs[-1]
    assert current is not old and current.fn is rate_limit_job
    assert current.args == tuple(replacement) and old.cancellations == 1
    assert win.wcl_panel.quota_lbl.text() == "正在读取额度…"
    assert win.wcl_panel.quota_lbl.toolTip() == ""
    old.done((123, 1000, 120))
    old.fail("stale failure")
    assert win.wcl_panel.quota_lbl.text() == "正在读取额度…"
    current.done((7, 1000, 60))
    assert win.wcl_panel.quota_lbl.text() == "本小时 7 / 1000"
    assert win.wcl_panel.quota_lbl.toolTip() == "1 分钟后重置"
    old.done((999, 1000, 120))
    assert win.wcl_panel.quota_lbl.text() == "本小时 7 / 1000"


@pytest.mark.parametrize("accepted", [True, False])
def test_unchanged_or_rejected_credentials_keep_the_current_quota_job(window, accepted):
    win, runner = window
    original = ("synthetic-id", "synthetic-secret", "cn.warcraftlogs.com")
    _store_credentials(win.settings, original)
    win._refresh_wcl_quota()
    job = runner.jobs[-1]
    values = original if accepted else ("different-id", "different-secret", "www.warcraftlogs.com")
    QTimer.singleShot(0, lambda: _finish_credentials_dialog(values, accepted=accepted))
    win.edit_wcl_credentials()
    assert runner.jobs == [job] and job.cancellations == 0
    assert win._stored_credentials() == original
    job.done((7, 1000, 60))
    assert win.wcl_panel.quota_lbl.text() == "本小时 7 / 1000"


@pytest.mark.parametrize("accepted", [True, False])
def test_missing_credentials_dialog_refreshes_quota_only_when_saved(window, accepted):
    win, runner = window
    values = ("synthetic-id", "synthetic-secret", "cn.warcraftlogs.com")
    QTimer.singleShot(0, lambda: _finish_credentials_dialog(values, accepted=accepted))
    result = win._credentials()
    if accepted:
        assert result == values
        assert len(runner.jobs) == 1 and runner.jobs[0].fn is rate_limit_job
        assert runner.jobs[0].args == values
    else:
        assert result is None and not runner.jobs


class _StatusAnalysis(Analysis):
    parameters = (AnalysisParameter("radius", "半径", 8.0, minimum=0, maximum=40),)

    def __init__(self, data, tracks):
        super().__init__(data, tracks)
        self.lanes = [Lane("main", "机制", "#ff0000")]

    def status_at(self, t):
        return [
            StatusSection(
                "当前机制", rows=[[Cell(f"半径 {self.parameter_values['radius']:g}"), Cell(f"时间 {t:g}")]]
            )
        ]


def _record_status_builds(panel, monkeypatch):
    builds = []
    original = panel.setHtml

    def set_html(text):
        builds.append(text)
        original(text)

    monkeypatch.setattr(panel, "setHtml", set_html)
    return builds


def test_status_parameter_refresh_coalesces_analysis_layers_and_time_into_one_build(app, monkeypatch):
    ctl = ReplayController()
    panel = StatusPanel(ctl)
    builds = _record_status_builds(panel, monkeypatch)
    ctl.set_session(_session(_StatusAnalysis))
    assert len(builds) == 1 and "半径 8" in panel.toPlainText()
    assert not panel._timer.isActive()
    builds.clear()
    ctl.set_parameter("radius", 9)
    assert not builds
    app.processEvents()
    assert len(builds) == 1 and "半径 9" in panel.toPlainText()
    assert not panel._timer.isActive()
    app.processEvents()
    assert len(builds) == 1
    panel.deleteLater()


def test_status_coalescing_keeps_latest_layer_and_time_and_snapshot_can_flush_it(app, monkeypatch):
    ctl = ReplayController()
    panel = StatusPanel(ctl)
    session = _session(_StatusAnalysis)
    session.analysis.status_at = lambda t: [
        StatusSection(f"图层 {ctl.layer_on('main')}", rows=[[Cell(f"时间 {t:g}")]])
    ]
    ctl.set_session(session)
    builds = _record_status_builds(panel, monkeypatch)
    ctl.seek(1000)
    ctl.toggle_layer("main")
    ctl.seek(2000)
    app.processEvents()
    assert len(builds) == 1
    assert "图层 False" in panel.toPlainText() and "时间 2000" in panel.toPlainText()
    assert not panel._timer.isActive()
    ctl.seek(3000)
    assert panel._timer.isActive()
    panel._render()
    assert len(builds) == 2 and "时间 3000" in panel.toPlainText()
    assert not panel._timer.isActive()
    panel.deleteLater()


def test_status_switch_and_clear_render_immediately_and_drop_pending_old_refresh(app, monkeypatch):
    ctl = ReplayController()
    panel = StatusPanel(ctl)
    ctl.set_session(_session(_StatusAnalysis))
    ctl.seek(7000)
    assert panel._timer.isActive()
    builds = _record_status_builds(panel, monkeypatch)
    replacement = _session(_StatusAnalysis)
    replacement.analysis.status_at = lambda t: [StatusSection("新场次", rows=[[Cell(f"时间 {t:g}")]])]
    ctl.set_session(replacement)
    assert len(builds) == 1 and "新场次" in panel.toPlainText() and "时间 0" in panel.toPlainText()
    assert not panel._timer.isActive()
    ctl.set_parameter("radius", 12)
    assert panel._timer.isActive()
    ctl.clear_session()
    assert len(builds) == 2 and "半径" not in panel.toPlainText()
    assert not panel._timer.isActive()
    app.processEvents()
    assert len(builds) == 2
    panel.deleteLater()


def test_status_time_refresh_skips_identical_html_and_preserves_document_and_scroll(app, monkeypatch):
    ctl = ReplayController()
    panel = StatusPanel(ctl)
    panel.resize(300, 100)
    panel.show()
    session = _session()
    times = []

    def status_at(t):
        times.append(t)
        return [StatusSection("固定状态", rows=[[Cell(f"玩家 {i}")] for i in range(80)])]

    session.analysis.status_at = status_at
    ctl.set_session(session)
    app.processEvents()
    panel.verticalScrollBar().setValue(panel.verticalScrollBar().maximum() // 2)
    position = panel.verticalScrollBar().value()
    revision = panel.document().revision()
    builds = _record_status_builds(panel, monkeypatch)
    for t in (1000, 2000, 3000):
        ctl.seek(t)
    QTest.qWait(150)
    assert times == [0, 3000] and not panel._timer.isActive()
    assert not builds and panel.document().revision() == revision
    assert panel.verticalScrollBar().value() == position
    panel.close()
    panel.deleteLater()
