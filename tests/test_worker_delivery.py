# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Result transport serializes analyses once and recovers from invalid results."""

from __future__ import annotations

import multiprocessing
import os
import queue
import threading
import time
from multiprocessing.reduction import ForkingPickler

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from wcl_replay.ui.loader import TaskRunner
from wcl_replay.workers import _serve


class _CountingResult:
    reductions = 0

    def __init__(self, value):
        self.value = value

    def __reduce__(self):
        type(self).reductions += 1
        return type(self), (self.value,)


def _nested_result(progress):
    shared = _CountingResult(["nested", 3])
    return {"value": shared}, [shared, {"again": shared}]


def _unserializable_result(progress):
    return lambda: None


def _raise_on_decode():
    raise ValueError("result reconstruction failed")


class _BrokenDecode:
    def __reduce__(self):
        return _raise_on_decode, ()


def _assert_nested(result):
    first, second = result
    assert first["value"].value == ["nested", 3]
    assert first["value"] is second[0] is second[1]["again"]


def test_worker_serializes_nested_result_only_once_including_the_queue_feeder():
    tasks = queue.Queue()
    results = multiprocessing.get_context("spawn").Queue()
    tasks.put((1, _nested_result, ()))
    tasks.put(None)
    _CountingResult.reductions = 0
    try:
        _serve(tasks, results)
        kind, job_id, payload = results.get(timeout=5.0)
        assert (kind, job_id) == ("done_bytes", 1)
        assert type(payload) is bytes
        _assert_nested(ForkingPickler.loads(payload))
        assert _CountingResult.reductions == 1
    finally:
        results.close()
        results.join_thread()


def test_unserializable_result_reports_failure_and_worker_delivers_the_next_result():
    tasks, results = queue.Queue(), queue.Queue()
    tasks.put((1, _unserializable_result, ()))
    tasks.put((2, _nested_result, ()))
    tasks.put(None)
    _serve(tasks, results)
    kind, job_id, message = results.get_nowait()
    assert (kind, job_id) == ("fail", 1)
    assert "pickle" in message.lower()
    kind, job_id, payload = results.get_nowait()
    assert (kind, job_id) == ("done_bytes", 2)
    _assert_nested(ForkingPickler.loads(payload))
    assert results.empty()


@pytest.mark.parametrize("bad_payload", [b"not a pickle", bytes(ForkingPickler.dumps(_BrokenDecode()))])
def test_listener_reports_decode_failure_and_keeps_delivering_on_the_gui_thread(bad_payload):
    app = QApplication.instance() or QApplication([])
    runner = TaskRunner()
    # Leave submissions pending so this test can deliver both protocol messages
    # deterministically without creating child processes.
    runner._booting = True
    failures, successes, callback_threads = [], [], []

    def on_fail(message):
        failures.append(message)
        callback_threads.append(threading.current_thread())

    def on_done(result):
        successes.append(result)
        callback_threads.append(threading.current_thread())

    first = runner.run(_nested_result, (), on_done, on_fail)
    second = runner.run(_nested_result, (), on_done, on_fail)
    results = queue.Queue()
    results.put(("done_bytes", first.job_id, bad_payload))
    results.put(("done_bytes", second.job_id, bytes(ForkingPickler.dumps(_nested_result(None)))))
    results.put(None)
    listener = threading.Thread(target=runner._listen, args=(results,), daemon=True)
    try:
        listener.start()
        deadline = time.monotonic() + 5.0
        while runner.busy() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        listener.join(timeout=1.0)
        assert not listener.is_alive()
        assert not runner.busy()
        assert len(failures) == len(successes) == 1
        assert "Traceback" in failures[0]
        _assert_nested(successes[0])
        assert callback_threads == [threading.main_thread(), threading.main_thread()]
    finally:
        runner.shutdown()
