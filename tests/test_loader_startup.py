# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from __future__ import annotations

import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from fixture_jobs import crash_worker
from PySide6.QtWidgets import QApplication

from wcl_replay.ui import loader
from wcl_replay.ui.loader import TaskRunner
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
