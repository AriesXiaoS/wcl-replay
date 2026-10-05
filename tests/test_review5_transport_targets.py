# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from __future__ import annotations

import gc
import os
import queue
import threading
import time
import weakref
from dataclasses import dataclass, field
from multiprocessing.reduction import ForkingPickler

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from wcl_replay.bosses.base import Analysis, AnalysisParameter
from wcl_replay.core.cancellation import TaskCancelled, cancel_check
from wcl_replay.core.models import Actor, ActorKind, Event, Fight, FightData
from wcl_replay.core.targets import Targets
from wcl_replay.core.tracks import Tracks
from wcl_replay.ui.controller import ReplayController, Session
from wcl_replay.ui.loader import TaskRunner
from wcl_replay.workers import analyze_pull_job, probe_job


@dataclass(slots=True, weakref_slot=True)
class _Result:
    data: bytearray = field(default_factory=lambda: bytearray(1024 * 1024))


@pytest.mark.parametrize("kind", ["done", "done_bytes"])
def test_idle_listener_releases_completed_results_while_preserving_queued_delivery(kind):
    app = QApplication.instance() or QApplication([])
    runner = TaskRunner()
    runner._booting = True
    delivered, failures, references = [], [], []
    idle = threading.Event()

    class ObservedQueue(queue.Queue):
        reads = 0

        def get(self, **kwargs):
            self.reads += 1
            if self.reads > 1:
                idle.set()
            return super().get(**kwargs)

    def done(result):
        assert result.data == bytearray(1024 * 1024)
        delivered.append(threading.current_thread())
        references.append(weakref.ref(result))

    handle = runner.run(probe_job, (), done, failures.append)
    results = ObservedQueue()
    result = _Result()
    payload = result if kind == "done" else bytes(ForkingPickler.dumps(result))
    results.put((kind, handle.job_id, payload))
    del result, payload
    listener = threading.Thread(target=runner._listen, args=(results,), daemon=True)
    try:
        listener.start()
        assert idle.wait(5.0)
        # Delivery must remain queued and its result alive until the GUI receives it.
        assert not delivered and runner.busy()
        deadline = time.monotonic() + 5.0
        while runner.busy() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        app.processEvents()
        gc.collect()
        assert not runner.busy() and not failures
        assert delivered == [threading.main_thread()]
        assert references[0]() is None
        assert listener.is_alive()
    finally:
        results.put(None)
        listener.join(timeout=5.0)
        runner.shutdown()
    assert not listener.is_alive()


def _data(events):
    actors = {
        0: Actor(0, "source", "Source", ActorKind.PLAYER),
        1: Actor(1, "first", "First", ActorKind.NPC),
        2: Actor(2, "second", "Second", ActorKind.NPC),
    }
    return FightData(Fight(1, 999, "Synthetic", 16, 20, 10000, False), actors, events, {})


def test_target_index_compacts_repeated_actions_and_keeps_same_millisecond_order():
    events = [Event(t, "SWING_DAMAGE", 0, 1) for t in range(1000)]
    events += [
        Event(1000, "SWING_DAMAGE", 0, 2),
        Event(1000, "SWING_MISSED", 0, 1),
        Event(1001, "SPELL_PERIODIC_DAMAGE", 0, 2),
        Event(1002, "SPELL_CAST_SUCCESS", 0, 0),
        Event(1003, "SPELL_HEAL", 0, 2),
        Event(1004, "SPELL_HEAL", 0, 2),
    ]
    targets = Targets(_data(events))
    assert len(targets._times[0]) == 4
    assert targets.at(0, -1) is None
    assert targets.at(0, 999) == 1
    assert targets.at(0, 1000) == 1
    assert targets.at(0, 1002) == 1
    assert targets.at(0, 1003) == targets.at(0, 1004) == 2
    assert targets.at(0, 500) == 1


def test_target_index_checks_cancellation_for_large_inputs():
    checks = 0

    def cancelled():
        nonlocal checks
        checks += 1
        if checks == 3:
            raise TaskCancelled("synthetic cancellation")

    token = cancel_check.set(cancelled)
    try:
        with pytest.raises(TaskCancelled, match="synthetic"):
            Targets(_data([Event(t, "SPELL_PERIODIC_DAMAGE", 0, 1) for t in range(10000)]))
    finally:
        cancel_check.reset(token)
    assert checks == 3


def test_worker_result_transports_target_index_without_gui_reconstruction(log_path, monkeypatch):
    from wcl_replay.sources.local_log import index_log

    entry = index_log(log_path)[0]
    result = analyze_pull_job(str(log_path), entry, lambda *_args: None)
    data, tracks, analysis = ForkingPickler.loads(ForkingPickler.dumps(result))
    assert analysis.data is data and analysis.tracks is tracks

    class WorkerTargetsOnly(Targets):
        def __init__(self, *_args):
            pytest.fail("Session rebuilt the target index on the GUI thread")

    # Keep isinstance working for the real index while observing constructor calls.
    monkeypatch.setattr(Targets, "__init__", WorkerTargetsOnly.__init__)
    session = Session(data, tracks, analysis)
    assert session.targets is analysis.targets
    assert any(session.targets._times.values())


def test_analysis_reuses_targets_and_rebuilds_for_changed_events():
    data = _data([Event(1000, "SWING_DAMAGE", 0, 1)])
    analysis = Analysis(data, Tracks(data))
    original = analysis.targets
    analysis.refresh_indexes()
    assert analysis.targets is original
    data.events.append(Event(2000, "SWING_DAMAGE", 0, 2))
    analysis.refresh_indexes()
    assert analysis.targets is not original
    assert analysis.targets.at(0, 2000) == 2
    data.events = [Event(1000, "SWING_DAMAGE", 0, 2)]
    analysis.refresh_indexes()
    assert analysis.targets.at(0, 1000) == 2
    data.events[0].dst = 1
    analysis.refresh_indexes(invalidate_auras=True)
    assert analysis.targets.at(0, 1000) == 1


def test_session_supports_minimal_analysis_without_precomputed_targets():
    data = _data([Event(1000, "SWING_DAMAGE", 0, 1)])
    session = Session(data, Tracks(data), None)
    assert session.targets.at(0, 1000) == 1


def test_parameter_changes_replace_session_target_index_with_analysis():
    class ChangingAnalysis(Analysis):
        parameters = (AnalysisParameter("target", "Target", 1, minimum=1, maximum=2),)

        def apply_parameters(self, values):
            super().apply_parameters(values)
            self.data.events = [Event(1000, "SWING_DAMAGE", 0, int(self.parameter_values["target"]))]

    app = QApplication.instance() or QApplication([])
    data = _data([Event(1000, "SWING_DAMAGE", 0, 1)])
    tracks = Tracks(data)
    ctl = ReplayController()
    ctl.set_session(Session(data, tracks, ChangingAnalysis(data, tracks)))
    ctl.set_parameter("target", 2)
    assert ctl.session.targets is ctl.session.analysis.targets
    assert ctl.session.targets.at(0, 1000) == 2
    app.processEvents()
