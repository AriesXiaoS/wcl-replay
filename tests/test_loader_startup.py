# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from __future__ import annotations

import os
import queue
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from fixture_jobs import crash_worker
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from wcl_replay.ui import loader
from wcl_replay.ui.loader import TaskRunner
from wcl_replay.ui.log_panel import LogPanel
from wcl_replay.workers import probe_job


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _wait(app, ready, seconds: float = 30.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if ready():
            return True
        time.sleep(0.01)
    return False


@pytest.mark.parametrize("thread_name", ["wcl-replay-boot", "wcl-replay-results"])
@pytest.mark.parametrize("failure_stage", ["construct", "start"])
def test_thread_initialization_failure_reports_on_gui_and_can_recover(
    app, monkeypatch, thread_name, failure_stage
):
    real_thread = threading.Thread
    real_start = real_thread.start
    context = loader.multiprocessing.get_context("spawn")
    spawned = []
    failures_left = 1

    class ObservedContext:
        def Queue(self):
            return context.Queue()

        def Process(self, **kwargs):
            proc = context.Process(**kwargs)
            spawned.append(proc)
            return proc

    def make_thread(*args, **kwargs):
        nonlocal failures_left
        if failure_stage == "construct" and kwargs.get("name") == thread_name and failures_left:
            failures_left -= 1
            raise RuntimeError(f"injected {thread_name} construction failure")
        return real_thread(*args, **kwargs)

    def start_thread(thread):
        nonlocal failures_left
        if failure_stage == "start" and thread.name == thread_name and failures_left:
            failures_left -= 1
            raise RuntimeError(f"injected {thread_name} start failure")
        return real_start(thread)

    monkeypatch.setattr(loader.multiprocessing, "get_context", lambda _method: ObservedContext())
    monkeypatch.setattr(loader, "worker_count", lambda: 1)
    monkeypatch.setattr(real_thread, "start", start_thread)
    monkeypatch.setattr(loader.threading, "Thread", make_thread)
    runner = TaskRunner()
    failed, done, callback_threads = [], [], []

    def on_fail(message):
        callback_threads.append(threading.current_thread())
        failed.append(message)

    try:
        runner.run(probe_job, (), done.append, on_fail)
        assert _wait(app, lambda: failed and not runner.busy()), (failed, runner.busy())
        assert f"injected {thread_name}" in failed[0]
        assert callback_threads == [threading.main_thread()]
        assert not runner._booting
        assert runner._tasks is None
        assert not runner._procs
        assert all(proc.exitcode is not None for proc in spawned)

        runner.run(probe_job, (), done.append, on_fail)
        assert _wait(app, lambda: done and not runner.busy()), (done, failed, runner.busy())
        assert len(failed) == 1
        assert done[0] == (spawned[-1].pid, False)
        assert done[0][0] != os.getpid()
    finally:
        runner.shutdown()


def test_immediate_worker_exit_is_observed_after_pool_publication(app, monkeypatch):
    monkeypatch.setattr(loader, "worker_count", lambda: 1)
    runner = TaskRunner()
    original_listen = runner._listen
    published, failed, done = [], [], []

    def observed_listen(results, procs):
        published.append(
            runner._results is results
            and tuple(runner._procs) == procs
            and runner._tasks is not None
            and runner._listener is threading.current_thread()
        )
        original_listen(results, procs)

    monkeypatch.setattr(runner, "_listen", observed_listen)
    try:
        runner.run(crash_worker, (), done.append, failed.append)
        assert _wait(app, lambda: failed and not runner.busy()), (failed, runner.busy())
        assert "后台进程意外退出" in failed[0]
        assert published == [True]

        runner.run(probe_job, (), done.append, failed.append)
        assert _wait(app, lambda: done and not runner.busy()), (done, failed, runner.busy())
        assert len(failed) == 1
        assert published == [True, True]
    finally:
        runner.shutdown()


def test_shutdown_tolerates_a_closed_results_queue_while_listener_is_reading(app):
    entered = threading.Event()
    release = threading.Event()
    errors, signals = [], []

    class ClosingResultsQueue:
        def get(self, **kwargs):
            entered.set()
            assert release.wait(5.0)
            raise ValueError("Queue is closed")

        def put(self, value):
            release.set()

        def close(self):
            pass

        def cancel_join_thread(self):
            pass

    runner = TaskRunner()
    runner._pool_failed.connect(signals.append)
    results = ClosingResultsQueue()
    runner._results = results

    def listen():
        try:
            runner._listen(results)
        except Exception as exc:
            errors.append(exc)

    listener = threading.Thread(target=listen, daemon=True)
    runner._listener = listener
    listener.start()
    try:
        assert entered.wait(5.0)
        runner.shutdown()
        app.processEvents()
        assert not listener.is_alive()
        assert not errors
        assert not signals
    finally:
        release.set()
        runner.shutdown()
        listener.join(timeout=5.0)


def _slow_listener_start(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    real_start = threading.Thread.start
    queues, procs = [], []

    class FakeQueue(queue.Queue):
        closed = False

        def close(self):
            self.closed = True

        def cancel_join_thread(self):
            pass

    class FakeProcess:
        exitcode = None

        def start(self):
            pass

        def terminate(self):
            self.exitcode = 0

        def join(self, **kwargs):
            pass

        def is_alive(self):
            return self.exitcode is None

    class FakeContext:
        def Queue(self):
            result = FakeQueue()
            queues.append(result)
            return result

        def Process(self, **kwargs):
            result = FakeProcess()
            procs.append(result)
            return result

    def start(thread):
        if thread.name == "wcl-replay-results":
            entered.set()
            assert release.wait(5.0)
        return real_start(thread)

    monkeypatch.setattr(loader.multiprocessing, "get_context", lambda _method: FakeContext())
    monkeypatch.setattr(loader, "worker_count", lambda: 1)
    monkeypatch.setattr(threading.Thread, "start", start)
    return entered, release, queues, procs


def test_slow_listener_start_does_not_block_submission_or_the_loading_spinner(app, monkeypatch):
    entered, release, _queues, _procs = _slow_listener_start(monkeypatch)
    runner = TaskRunner()
    panel = LogPanel()
    panel.set_reading("open")
    ticks, failed = [], []
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.append(1))
    # Unblock even the buggy implementation so a failure cannot hang the test process.
    watchdog = threading.Timer(1.0, release.set)
    try:
        runner._ensure_pool()
        assert _wait(app, entered.is_set)
        watchdog.start()
        handle = runner.run(probe_job, (), lambda _: None, failed.append)
        assert not release.is_set(), "GUI submission waited for the listener's startup"
        assert handle is not None and not handle.done
        assert runner._tasks is None
        angle = panel.open_btn._angle
        timer.start()
        assert _wait(app, lambda: len(ticks) >= 5 and panel.open_btn._angle != angle)
        assert not release.is_set()
        handle.cancel()
        assert handle.done and failed == ["任务已取消"]
    finally:
        watchdog.cancel()
        release.set()
        assert _wait(app, lambda: not runner._booting)
        timer.stop()
        panel.set_reading(None)
        panel.deleteLater()
        runner.shutdown()


def test_shutdown_during_slow_listener_start_returns_and_closes_the_unpublished_pool(app, monkeypatch):
    entered, release, queues, procs = _slow_listener_start(monkeypatch)
    runner = TaskRunner()
    watchdog = threading.Timer(1.0, release.set)
    try:
        runner._ensure_pool()
        assert _wait(app, entered.is_set)
        watchdog.start()
        runner.shutdown()
        assert not release.is_set(), "GUI shutdown waited for the listener's startup"
        release.set()
        assert _wait(app, lambda: queues and all(q.closed for q in queues))
        assert all(proc.exitcode == 0 for proc in procs)
        assert not runner._booting and runner._tasks is None and not runner._procs
    finally:
        watchdog.cancel()
        release.set()
        runner.shutdown()
