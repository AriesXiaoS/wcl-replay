# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Loading boundaries: source ownership, retry identity, process failures and the snapshot CLI."""

from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

import pytest
from fixture_jobs import crash_worker, unserializable_result
from fixture_log import write_log
from PySide6.QtCore import QSettings
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QMessageBox

from wcl_replay.core.models import Fight, FightData
from wcl_replay.pipeline import analyze
from wcl_replay.sources.local_log import EncounterEntry
from wcl_replay.ui import loader, main_window
from wcl_replay.ui.controller import ReplayController, Session
from wcl_replay.ui.loader import TaskRunner
from wcl_replay.ui.log_panel import LogPanel, PullBoard
from wcl_replay.ui.main_window import MainWindow
from wcl_replay.ui.wcl_dialogs import CredentialsDialog
from wcl_replay.ui.wcl_panel import WclBoard, WclPanel
from wcl_replay.workers import (
    analyze_pull_job,
    fetch_wcl_job,
    probe_job,
)
from wcl_replay.workers import (
    test_credentials_job as credential_job,
)

URL = "https://cn.warcraftlogs.com/reports/ABCDEFGHIJKLMNOP?fight=1"
ROOT = Path(__file__).resolve().parents[1]


@dataclass(slots=True)
class _Submission:
    fn: Any
    args: tuple
    done: Any
    fail: Any
    progress: Any


class _ManualRunner:
    """Keep jobs pending until a test delivers their callbacks in the chosen order."""

    def __init__(self):
        self.jobs: list[_Submission] = []
        self.closed = False

    def run(self, fn, args, on_done, on_fail, on_progress=None):
        self.jobs.append(_Submission(fn, args, on_done, on_fail, on_progress))

    def shutdown(self):
        self.closed = True


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(tmp_path):
    return QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)


@pytest.fixture
def window(app, settings, monkeypatch):
    runner = _ManualRunner()
    monkeypatch.setattr(main_window, "TaskRunner", lambda _parent: runner)
    win = MainWindow(settings=settings)
    monkeypatch.setattr(win, "_credentials", lambda: ("test-client", "test-secret", "www.warcraftlogs.com"))
    monkeypatch.setattr(win, "_refresh_wcl_quota", lambda: None)
    yield win, runner
    win.close()
    win.deleteLater()
    app.processEvents()


def _entry(*, end: int = 100, digest: str = "first") -> EncounterEntry:
    return EncounterEntry(
        seq=1,
        encounter_id=1,
        name="测试首领",
        difficulty=16,
        group_size=20,
        instance_id=1,
        start_offset=0,
        end_offset=end,
        start_ts="1/1/2026 12:00:00.000",
        duration_ms=90_000,
        kill=True,
        closed=True,
        pull_number=1,
        content_digest=digest,
    )


def _result(source: str) -> tuple:
    data = FightData(Fight(1, 1, "测试首领", 16, 20, 90_000, True), {}, [], {}, source=source)
    tracks, analysis = analyze(data)
    return data, tracks, analysis


def _session(source: str = "test") -> Session:
    return Session(*_result(source))


def _start_local(win: MainWindow, runner: _ManualRunner, path: Path) -> _Submission:
    win.open_log(str(path))
    runner.jobs[-1].done([_entry()])
    win.log_panel.activated.emit(0)
    job = runner.jobs[-1]
    assert job.fn is analyze_pull_job
    return job


@pytest.mark.parametrize("active", ["local", "wcl"])
@pytest.mark.parametrize("order", [("local", "wcl"), ("wcl", "local")])
def test_main_window_shows_only_the_active_source_when_jobs_finish(window, tmp_path, active, order):
    win, runner = window
    local = _start_local(win, runner, tmp_path / "synthetic.txt")
    win._set_source("wcl")
    win.query_wcl(URL)
    wcl = runner.jobs[-1]
    assert wcl.fn is fetch_wcl_job
    win._set_source(active)
    completed = set()
    for source in order:
        {"local": local, "wcl": wcl}[source].done(_result(source))
        completed.add(source)
        if active in completed:
            assert win.ctl.session.data.source == active
        else:
            assert win.ctl.session is None
    assert len(win.board.loads.cache) == len(win.wcl_board.loads.cache) == 1
    other = "local" if active == "wcl" else "wcl"
    win._set_source(other)
    assert win.ctl.session.data.source == other


@pytest.mark.parametrize("new_path", [False, True])
def test_hidden_local_refresh_does_not_clear_or_replace_wcl_session(window, tmp_path, new_path):
    win, runner = window
    path = tmp_path / "synthetic.txt"
    local = _start_local(win, runner, path)
    local.done(_result("local"))
    win._set_source("wcl")
    win.query_wcl(URL)
    runner.jobs[-1].done(_result("wcl"))
    visible = win.ctl.session
    win.ctl.seek(12_345)
    refreshed_path = tmp_path / "replacement.txt" if new_path else path
    win.open_log(str(refreshed_path), source="refresh")
    assert win.ctl.session is visible
    runner.jobs[-1].done([_entry(end=200, digest="replacement")])
    assert win.ctl.session is visible
    assert win.ctl.t == 12_345
    win.board.clear_cache()
    assert win.ctl.session is visible
    assert win.ctl.t == 12_345


def test_hidden_wcl_cache_clear_keeps_the_local_replay(window, tmp_path):
    win, runner = window
    win._set_source("wcl")
    win.query_wcl(URL)
    runner.jobs[-1].done(_result("wcl"))
    win._set_source("local")
    local = _start_local(win, runner, tmp_path / "synthetic.txt")
    local.done(_result("local"))
    visible = win.ctl.session
    win.ctl.seek(1234)
    win.wcl_board.clear_cache()
    assert win.ctl.session is visible
    assert win.ctl.t == 1234


def test_released_local_job_cannot_finish_fail_or_report_progress_for_its_retry(app, settings, tmp_path):
    ctl, panel = ReplayController(), LogPanel()
    board = PullBoard(ctl, panel, settings=settings)
    path = str(tmp_path / "synthetic.txt")
    gen = board.prepare(path)
    board.show_entries(gen, path, [_entry()])
    assert board.activate(0) == "start"
    key = board.keys[0]
    old_id = board.loads.run_id(key)
    board.release(0)
    assert board.activate(0) == "start"
    new_id = board.loads.run_id(key)
    assert new_id != old_id
    board.note_progress(gen, path, key, 0.4, run_id=new_id)
    board.note_progress(gen, path, key, 0.9, run_id=old_id)
    board.fail(gen, path, key, "stale error", run_id=old_id)
    assert not board.finish(gen, path, key, _session("old"), run_id=old_id)
    assert panel.rows[0].bar.value() == 400
    assert panel.rows[0].mark.text() == ""
    assert key in board.loads.running
    current = _session("new")
    assert board.finish(gen, path, key, current, run_id=new_id)
    assert ctl.session is current
    assert board.loads.cache[key] is current
    panel.deleteLater()
    app.processEvents()


def test_local_run_tokens_are_not_reused_after_switching_away_and_back(app, settings, tmp_path):
    ctl, panel = ReplayController(), LogPanel()
    board = PullBoard(ctl, panel, settings=settings)
    path = str(tmp_path / "synthetic.txt")
    gen = board.prepare(path)
    board.show_entries(gen, path, [_entry()])
    board.activate(0)
    key = board.keys[0]
    old_id = board.loads.run_id(key)
    board.prepare(str(tmp_path / "other.txt"))
    new_gen = board.prepare(path)
    board.show_entries(new_gen, path, [_entry()])
    board.activate(0)
    assert board.loads.run_id(key) != old_id
    assert not board.finish(gen, path, key, _session("old"), run_id=old_id)
    assert ctl.session is None
    assert key in board.loads.running
    panel.deleteLater()
    app.processEvents()


def test_wcl_retry_rejects_old_completion_failure_and_progress(app):
    ctl, panel = ReplayController(), WclPanel()
    board = WclBoard(ctl, panel)
    _index, key = board.begin(URL)
    old_id = board.loads.run_id(key)
    board.fail(key, "initial failure", run_id=old_id)
    assert board.activate(0) == "start"
    new_id = board.loads.run_id(key)
    assert new_id != old_id
    board.note_progress(key, 0.4, run_id=new_id)
    board.note_progress(key, 0.9, run_id=old_id)
    board.fail(key, "stale error", run_id=old_id)
    assert not board.finish(key, _session("old"), "old", run_id=old_id)
    assert panel.rows[0].download.bar.value() == 400
    assert panel.rows[0].mark.text() == ""
    assert panel.rows[0].err.isHidden()
    assert key in board.loads.running
    current = _session("new")
    assert board.finish(key, current, "new", run_id=new_id)
    assert ctl.session is current
    assert panel.rows[0].text.text() == "new"
    panel.deleteLater()
    app.processEvents()


def test_main_window_local_callbacks_capture_the_started_run_token(window, tmp_path):
    win, runner = window
    old = _start_local(win, runner, tmp_path / "synthetic.txt")
    win.board.release(0)
    win.log_panel.activated.emit(0)
    current = runner.jobs[-1]
    current.progress(0.4, "new progress")
    old.progress(0.9, "old progress")
    old.fail("old error")
    old.done(_result("old"))
    assert win.log_panel.rows[0].bar.value() == 400
    assert win.log_panel.rows[0].mark.text() == ""
    assert win.ctl.session is None
    current.done(_result("new"))
    assert win.ctl.session.data.source == "new"


@pytest.mark.parametrize("reason", ["cleared", "failed"])
def test_main_window_restarts_a_cleared_or_failed_wcl_row_with_fresh_callbacks(window, reason):
    win, runner = window
    win._set_source("wcl")
    win.query_wcl(URL)
    old = runner.jobs[-1]
    key = win.wcl_board.keys[0]
    if reason == "cleared":
        old.done(_result("original"))
        win.wcl_panel.clearClicked.emit()
    else:
        old.fail("initial failure")
    count = len(runner.jobs)
    win.wcl_panel.activated.emit(0)
    assert len(runner.jobs) == count + 1
    assert win.wcl_board.keys == [key]
    current = runner.jobs[-1]
    assert current.fn is fetch_wcl_job
    assert current.args == (URL, "test-client", "test-secret", "cn.warcraftlogs.com")
    current.progress(0.4, "new progress")
    old.progress(0.9, "old progress")
    old.fail("old error")
    old.done(_result("old"))
    assert win.wcl_panel.rows[0].download.bar.value() == 400
    assert win.wcl_panel.rows[0].mark.text() == ""
    current.done(_result("retry"))
    assert win.ctl.session.data.source == "retry"
    assert win.wcl_panel.rows[0].mark.text() == "✓"


def test_fetch_wcl_job_reports_download_and_compute_on_separate_scales(monkeypatch):
    notes: list[tuple[float, str]] = []

    def fake_fetch(client_id, client_secret, host, url, progress=None):
        progress(0.42, "盘卷祭坛 · 伤害… 10 条")
        progress(0.84, "使用本地事件缓存")
        return "data"

    def fake_analyze(data, progress):
        assert data == "data"
        progress(0.9, "构建坐标轨迹")
        progress(0.95, "分析机制（盘卷祭坛）")
        return "tracks", "analysis"

    monkeypatch.setattr("wcl_replay.sources.wcl_api.fetch.fetch_fight", fake_fetch)
    monkeypatch.setattr("wcl_replay.workers.analyze", fake_analyze)
    result = fetch_wcl_job("url", "id", "secret", "host", lambda frac, msg="": notes.append((frac, msg)))
    assert result == ("data", "tracks", "analysis")
    assert notes == [
        (0.5, "phase:download:盘卷祭坛 · 伤害… 10 条"),
        (1.0, "phase:download:使用本地事件缓存"),
        (1.0, "phase:download"),
        (0.35, "phase:compute:构建坐标轨迹"),
        (0.7, "phase:compute:分析机制（盘卷祭坛）"),
        (1.0, "phase:compute"),
    ]


def test_query_clears_the_url_field(window):
    win, runner = window
    win.wcl_panel.set_url(URL)
    win.query_wcl(URL)
    assert win.wcl_panel.url() == ""
    assert runner.jobs[-1].fn is fetch_wcl_job
    assert win.settings.value("wcl_last_url", "") in ("", None)

    win.wcl_panel.set_url(URL)
    win.query_wcl(URL)
    assert win.wcl_panel.url() == ""
    assert len(runner.jobs) == 1


def test_rejected_url_stays_in_the_field(window, messages):
    win, runner = window
    win.wcl_panel.set_url("https://example.com/nope")
    win.query_wcl("https://example.com/nope")
    assert win.wcl_panel.url() == "https://example.com/nope"
    assert runner.jobs == []
    assert messages and messages[-1][0] == "链接无效"


def test_saved_fight_url_is_not_restored(app, settings, monkeypatch):
    settings.setValue("wcl_last_url", URL)
    monkeypatch.setattr(main_window, "TaskRunner", lambda _parent: _ManualRunner())
    win = MainWindow(settings=settings)
    assert win.wcl_panel.url() == ""
    win.close()
    win.deleteLater()
    app.processEvents()


@pytest.fixture
def messages(monkeypatch):
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, title, text: seen.append((title, text)))
    monkeypatch.setattr(QMessageBox, "information", lambda _parent, title, text: seen.append((title, text)))
    return seen


def _credentials_dialog(settings, runner):
    dialog = CredentialsDialog(settings, runner=runner)
    dialog.client_id.setText("test-client")
    dialog.secret.setText("test-secret")
    dialog.host.setCurrentText("cn.warcraftlogs.com")
    return dialog


def test_credentials_connection_test_is_pending_until_the_runner_callback(app, settings, messages):
    runner = _ManualRunner()
    dialog = _credentials_dialog(settings, runner)
    dialog._test()
    assert len(runner.jobs) == 1
    job = runner.jobs[0]
    assert job.fn is credential_job
    assert job.args == ("test-client", "test-secret", "cn.warcraftlogs.com")
    assert not dialog.test_btn.isEnabled()
    assert dialog.test_btn.text() == "正在测试…"
    assert not messages
    app.processEvents()
    assert not messages
    job.done(True)
    assert dialog.test_btn.isEnabled()
    assert dialog.test_btn.text() == "测试连接"
    assert messages[-1][0] == "连接成功"
    dialog._test()
    message_count = len(messages)
    job.done(True)
    job.fail("stale failure")
    assert not dialog.test_btn.isEnabled()
    assert len(messages) == message_count
    runner.jobs[-1].fail("Traceback\nValueError: invalid credentials")
    assert dialog.test_btn.isEnabled()
    assert messages[-1] == ("连接失败", "ValueError: invalid credentials")
    dialog.reject()
    assert not runner.closed
    dialog.deleteLater()
    app.processEvents()


@pytest.mark.parametrize("callback", ["done", "fail"])
def test_closed_credentials_dialog_ignores_pending_callbacks(app, settings, messages, callback):
    runner = _ManualRunner()
    dialog = _credentials_dialog(settings, runner)
    dialog._test()
    job = runner.jobs[-1]
    dialog.reject()
    getattr(job, callback)(True if callback == "done" else "stale failure")
    assert not messages
    assert not runner.closed
    dialog.deleteLater()
    app.processEvents()


@pytest.mark.parametrize("host", ["evilwarcraftlogs.com", "www.warcraftlogs.com.attacker.invalid"])
def test_invalid_credentials_host_never_submits_a_test_job(app, settings, messages, host):
    runner = _ManualRunner()
    dialog = _credentials_dialog(settings, runner)
    dialog.host.setCurrentText(host)
    dialog._test()
    assert not runner.jobs
    assert dialog.test_btn.isEnabled()
    assert messages[-1][0] == "连接失败"
    dialog.reject()
    dialog.deleteLater()
    app.processEvents()


def _wait(app, ready, seconds: float = 30.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if ready():
            return True
        time.sleep(0.01)
    return False


@pytest.mark.parametrize("failing_queue", [1, 2])
def test_queue_initialization_failure_reports_failure_and_a_new_submission_recovers(
    app, monkeypatch, failing_queue
):
    context = loader.multiprocessing.get_context("spawn")

    class ContextWithOneFailure:
        def __init__(self):
            self.calls = 0

        def Queue(self):
            self.calls += 1
            if self.calls == failing_queue:
                raise OSError(f"injected Queue failure {failing_queue}")
            return context.Queue()

        def Process(self, **kwargs):
            return context.Process(**kwargs)

    failing_context = ContextWithOneFailure()
    monkeypatch.setattr(loader.multiprocessing, "get_context", lambda _method: failing_context)
    monkeypatch.setattr(loader, "worker_count", lambda: 1)
    runner = TaskRunner()
    failed, done, callback_threads = [], [], []

    def on_fail(message):
        callback_threads.append(threading.current_thread())
        failed.append(message)

    try:
        runner.run(probe_job, (), done.append, on_fail)
        assert _wait(app, lambda: failed and not runner.busy()), (failed, runner.busy())
        assert f"injected Queue failure {failing_queue}" in failed[0]
        assert callback_threads == [threading.main_thread()]
        runner.run(probe_job, (), done.append, on_fail)
        assert _wait(app, lambda: done and not runner.busy()), (done, failed, runner.busy())
        assert len(failed) == 1
        assert done[0][0] != os.getpid()
    finally:
        runner.shutdown()


@pytest.mark.parametrize("cleanup_failure", ["close", "cancel_join_thread"])
def test_queue_cleanup_errors_do_not_hide_the_original_initialization_failure(
    app, monkeypatch, cleanup_failure
):
    calls = []

    class QueueWithBrokenCleanup:
        def close(self):
            calls.append("close")
            if cleanup_failure == "close":
                raise OSError("injected close failure")

        def cancel_join_thread(self):
            calls.append("cancel_join_thread")
            if cleanup_failure == "cancel_join_thread":
                raise OSError("injected cancel_join_thread failure")

    class ContextWithBrokenResultsQueue:
        def __init__(self):
            self.calls = 0

        def Queue(self):
            self.calls += 1
            if self.calls == 1:
                return QueueWithBrokenCleanup()
            raise OSError("injected results Queue failure")

    context = ContextWithBrokenResultsQueue()
    monkeypatch.setattr(loader.multiprocessing, "get_context", lambda _method: context)
    runner = TaskRunner()
    failed, done = [], []
    try:
        runner.run(probe_job, (), done.append, failed.append)
        assert _wait(app, lambda: failed and not runner.busy()), (failed, runner.busy())
        assert not done
        assert "injected results Queue failure" in failed[0]
        assert calls == ["close", "cancel_join_thread"]
    finally:
        runner.shutdown()


def test_unserializable_result_reports_failure_and_the_same_pool_accepts_another_job(app, monkeypatch):
    monkeypatch.setattr(loader, "worker_count", lambda: 1)
    runner = TaskRunner()
    failed, done = [], []
    try:
        runner.run(unserializable_result, (), done.append, failed.append)
        assert _wait(app, lambda: failed and not runner.busy()), (failed, runner.busy())
        assert not done
        assert "pickle" in failed[0].lower()
        runner.run(probe_job, (), done.append, failed.append)
        assert _wait(app, lambda: done and not runner.busy()), (done, failed, runner.busy())
        assert len(failed) == 1
        assert done[0][0] != os.getpid()
    finally:
        runner.shutdown()


def test_worker_exit_reports_failure_clears_busy_and_allows_a_fresh_pool(app, monkeypatch):
    monkeypatch.setattr(loader, "worker_count", lambda: 1)
    runner = TaskRunner()
    failed, done, callback_threads = [], [], []

    def on_fail(message):
        callback_threads.append(threading.current_thread())
        failed.append(message)

    try:
        runner.run(crash_worker, (), done.append, on_fail)
        assert _wait(app, lambda: failed and not runner.busy()), (failed, runner.busy())
        assert "后台进程意外退出" in failed[0]
        assert callback_threads == [threading.main_thread()]
        runner.run(probe_job, (), done.append, on_fail)
        assert _wait(app, lambda: done and not runner.busy()), (done, failed, runner.busy())
        assert len(failed) == 1
        assert done[0][0] != os.getpid()
    finally:
        runner.shutdown()


def test_snapshot_cli_renders_valid_pngs_with_isolated_settings(tmp_path):
    log = write_log(tmp_path / "WoWCombatLog-synthetic.txt")
    output = tmp_path / "snapshots"
    isolated_settings = tmp_path / "snapshot-settings.ini"
    env = {**os.environ, "LOCALAPPDATA": str(tmp_path / "appdata"), "QT_QPA_PLATFORM": "offscreen"}
    completed = subprocess.run(
        [
            "uv",
            "--cache-dir",
            ".ruff_cache/uv-audit",
            "run",
            "--offline",
            "--no-sync",
            "python",
            str(ROOT / "tools" / "snapshot.py"),
            str(log),
            "--seq",
            "1",
            "--at",
            "0",
            "29",
            "--out",
            str(output),
            "--settings",
            str(isolated_settings),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    images = sorted(output.glob("*.png"))
    assert len(images) == 2
    for path in images:
        assert path.stat().st_size > 10_000
        image = QImage(str(path))
        assert not image.isNull()
        assert (image.width(), image.height()) == (1500, 980)
