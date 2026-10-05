# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Cancellation during result transport must avoid decoding and preserve slot ownership."""

from __future__ import annotations

import os
import queue
import threading
import weakref
from dataclasses import dataclass
from multiprocessing.reduction import ForkingPickler

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from wcl_replay.ui.loader import TaskRunner
from wcl_replay.workers import _serve, probe_job


class _CancelDuringPickle:
    def __init__(self, cancellations):
        self.cancellations = cancellations

    def __reduce__(self):
        self.cancellations[0] = 1
        return str, ("discarded",)


def _serializing_result(cancellations, progress):
    return _CancelDuringPickle(cancellations)


def _successful_result(progress):
    return {"accepted": [1, 2]}


@dataclass(slots=True, weakref_slot=True)
class _UnwantedResult:
    payload: bytearray


def _cancel_when_computed(cancellations, references, progress):
    result = _UnwantedResult(bytearray(1024 * 1024))
    references.append(weakref.ref(result))
    cancellations[0] = 1
    return result


def test_worker_acknowledges_cancellation_during_serialization_and_runs_next_job():
    cancellations = [0, 0]
    tasks, results = queue.Queue(), queue.Queue()
    tasks.put((1, _serializing_result, (cancellations,), 0))
    tasks.put((2, _successful_result, (), 1))
    tasks.put(None)

    _serve(tasks, results, cancellations)

    kind, job_id, message = results.get_nowait()
    assert (kind, job_id) == ("fail", 1)
    assert "TaskCancelled" in message
    kind, job_id, payload = results.get_nowait()
    assert (kind, job_id) == ("done_bytes", 2)
    assert ForkingPickler.loads(payload) == {"accepted": [1, 2]}
    assert results.empty()


def test_worker_releases_result_cancelled_after_computation_while_waiting_for_next_job():
    idle = threading.Event()

    class ObservedTasks(queue.Queue):
        reads = 0

        def get(self, *args, **kwargs):
            self.reads += 1
            if self.reads == 2:
                idle.set()
            return super().get(*args, **kwargs)

    tasks, results = ObservedTasks(), queue.Queue()
    references, cancellations = [], [0]
    tasks.put((1, _cancel_when_computed, (cancellations, references), 0))
    worker = threading.Thread(target=_serve, args=(tasks, results, cancellations), daemon=True)
    worker.start()
    try:
        kind, job_id, message = results.get(timeout=5)
        assert (kind, job_id) == ("fail", 1) and "TaskCancelled" in message
        assert idle.wait(5)
        assert references[0]() is None
        assert worker.is_alive()
        cancellations[0] = 0
        tasks.put((2, _successful_result, (), 0))
        kind, job_id, payload = results.get(timeout=5)
        assert (kind, job_id) == ("done_bytes", 2)
        assert ForkingPickler.loads(payload) == {"accepted": [1, 2]}
    finally:
        tasks.put(None)
        worker.join(timeout=5)
    assert not worker.is_alive()


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def runner(app):
    runner = TaskRunner(max_jobs=1)
    # A published queue lets cancellation wait for worker acknowledgement without
    # starting processes; protocol delivery can then be controlled deterministically.
    runner._booting = True
    runner._tasks = queue.Queue()
    yield runner
    runner.shutdown()
    app.processEvents()


@pytest.mark.parametrize("message_kind", ["done", "done_bytes"])
def test_cancelled_result_drops_payload_and_releases_slot_only_after_acknowledgement(
    app, runner, monkeypatch, message_kind
):
    successes, failures, decodes = [], [], []
    handle = runner.run(probe_job, (), successes.append, failures.append)
    handle.cancel()

    def observe_decode(payload):
        decodes.append(payload)
        raise AssertionError("cancelled result must not be decoded")

    monkeypatch.setattr(ForkingPickler, "loads", observe_decode)
    runner._deliver((message_kind, handle.job_id, b"unneeded payload"))
    assert not decodes and not successes
    assert failures == ["任务已取消"]
    assert not handle.done and runner.busy()
    app.processEvents()
    assert handle.done and not runner.busy()

    # A late duplicate for the old ID must not free the new job's reused slot.
    replacement = runner.run(probe_job, (), successes.append, failures.append)
    runner._deliver(("done_bytes", handle.job_id, b"stale duplicate"))
    app.processEvents()
    assert not decodes and not replacement.done
    runner._deliver(("done", replacement.job_id, "new result"))
    app.processEvents()
    assert replacement.done and successes == ["new result"]
    assert failures == ["任务已取消"]


@pytest.mark.parametrize("state", ["unknown", "shutdown"])
def test_unknown_or_shutdown_payload_is_never_decoded(app, runner, monkeypatch, state):
    successes, failures, decodes = [], [], []
    if state == "shutdown":
        handle = runner.run(probe_job, (), successes.append, failures.append)
        job_id = handle.job_id
        runner.shutdown()
    else:
        job_id = 999

    def observe_decode(payload):
        decodes.append(payload)
        raise AssertionError("inactive result must not be decoded")

    monkeypatch.setattr(ForkingPickler, "loads", observe_decode)
    runner._deliver(("done_bytes", job_id, b"unneeded payload"))
    app.processEvents()
    assert not decodes and not successes and not failures
    if state == "shutdown":
        assert handle.done


def test_active_payload_keeps_queued_gui_delivery_and_shared_result_identity(app, runner):
    successes, failures, callback_threads = [], [], []

    def on_done(result):
        successes.append(result)
        callback_threads.append(threading.current_thread())

    handle = runner.run(probe_job, (), on_done, failures.append)
    shared = ["shared"]
    payload = bytes(ForkingPickler.dumps((shared, shared)))
    listener = threading.Thread(target=runner._deliver, args=(("done_bytes", handle.job_id, payload),))
    listener.start()
    listener.join(timeout=5)
    assert not listener.is_alive()
    assert not handle.done and not successes
    app.processEvents()
    assert handle.done and not failures
    assert callback_threads == [threading.main_thread()]
    assert successes[0][0] is successes[0][1]
