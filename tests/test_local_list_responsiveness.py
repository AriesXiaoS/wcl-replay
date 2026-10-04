# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from wcl_replay.sources.local_log import EncounterEntry
from wcl_replay.ui.controller import ReplayController
from wcl_replay.ui.log_panel import LogPanel, PullBoard


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _wait(ready) -> None:
    loop = QEventLoop()
    check = QTimer()
    check.setInterval(1)
    check.timeout.connect(lambda: loop.quit() if ready() else None)
    timeout = QTimer()
    timeout.setSingleShot(True)
    timeout.timeout.connect(loop.quit)
    check.start()
    timeout.start(5000)
    loop.exec()
    check.stop()
    timeout.stop()
    assert ready()


def _entries(count: int) -> list[EncounterEntry]:
    return [
        EncounterEntry(
            seq=index + 1,
            encounter_id=3183,
            name="盘卷祭坛",
            difficulty=16,
            group_size=20,
            instance_id=3215,
            start_offset=index * 1000,
            end_offset=index * 1000 + 900,
            start_ts="10/04/2026 12:00:00.0000",
            duration_ms=100000,
            kill=False,
            closed=True,
            pull_number=index + 1,
        )
        for index in range(count)
    ]


def test_small_list_is_ready_synchronously_and_keeps_row_actions(app):
    panel = LogPanel()
    ready, actions = [], []
    panel.pullsReady.connect(lambda: ready.append(True))
    panel.activated.connect(actions.append)
    panel.set_pulls(["first", "second"])
    assert ready == [True]
    assert [row.text.text() for row in panel.rows] == ["first", "second"]
    panel.rows[1].clicked.emit()
    assert actions == [1]
    panel.deleteLater()


def test_large_list_yields_between_batches_and_keeps_spinner_until_ready(app, monkeypatch):
    monkeypatch.setattr(LogPanel, "_ROWS_PER_BATCH", 3)
    panel = LogPanel()
    board = PullBoard(ReplayController(), panel)
    gen = board.prepare("synthetic.txt", "open")
    board.show_entries(gen, "synthetic.txt", _entries(150))
    assert panel.rows == []
    assert panel.open_btn.busy and not panel.open_btn.isEnabled()
    counts = []
    heartbeat = QTimer()
    heartbeat.setInterval(1)
    heartbeat.timeout.connect(lambda: counts.append(len(panel.rows)))
    heartbeat.start()
    _wait(lambda: len(panel.rows) == 150 and not panel.open_btn.busy)
    heartbeat.stop()
    assert any(0 < count < 150 for count in counts)
    assert panel.open_btn.isEnabled()
    panel.deleteLater()


def test_replacing_partial_list_discards_stale_actions_and_population(app, monkeypatch):
    monkeypatch.setattr(LogPanel, "_ROWS_PER_BATCH", 3)
    panel = LogPanel()
    actions = []
    panel.activated.connect(actions.append)
    panel.set_pulls([f"old {index}" for index in range(150)])
    panel._add_rows()
    old_row = panel.rows[0]
    assert len(panel.rows) == 3
    panel.set_pulls(["replacement"])
    old_row.clicked.emit()
    old_row.starClicked.emit()
    old_row.deleteClicked.emit()
    assert actions == []
    panel.rows[0].clicked.emit()
    assert actions == [0]
    _wait(lambda: not panel._retired)
    assert [row.text.text() for row in panel.rows] == ["replacement"]
    assert not panel._populate.isActive()
    panel.deleteLater()


def test_callbacks_before_rows_exist_restore_progress_error_cache_pin_and_selection(app):
    panel = LogPanel()
    board = PullBoard(ReplayController(), panel, active=lambda: False, limit=lambda: 100)
    path = "synthetic.txt"
    gen = board.prepare(path, "open")
    board.show_entries(gen, path, _entries(180))
    assert panel.rows == []

    assert board.activate(140) == "start"
    running = board.keys[140]
    board.note_progress(gen, path, running, 0.4, run_id=board.loads.run_id(running))
    assert board.activate(141) == "start"
    failed = board.keys[141]
    board.fail(gen, path, failed, "synthetic failure", run_id=board.loads.run_id(failed))
    assert board.activate(142) == "start"
    completed = board.keys[142]
    session = object()
    board.finish(gen, path, completed, session, run_id=board.loads.run_id(completed))
    board.toggle_pin(143)
    assert board.activate(140) == "wait"

    _wait(lambda: len(panel.rows) == 180)
    assert panel.rows[140].bar.value() == 400
    assert not panel.rows[140].bar.isHidden()
    assert panel.rows[140].property("selected") is True
    assert panel.rows[141].err.text() == "synthetic failure"
    assert panel.rows[141].mark.text() == "!"
    assert panel.rows[142].mark.text() == "✓"
    assert panel.rows[143].star_btn.text() == "★"
    assert board.loads.cache[completed] is session
    panel.deleteLater()


def test_same_log_refresh_restores_cached_and_pinned_rows_with_completion_during_population(app):
    panel = LogPanel()
    board = PullBoard(ReplayController(), panel, active=lambda: False, limit=lambda: 100)
    path = "synthetic.txt"
    entries = _entries(180)
    gen = board.prepare(path, "open")
    board.show_entries(gen, path, entries)
    board.activate(150)
    cached = board.keys[150]
    board.finish(gen, path, cached, object(), run_id=board.loads.run_id(cached))
    board.toggle_pin(150)
    board.activate(160)
    running = board.keys[160]
    run_id = board.loads.run_id(running)
    _wait(lambda: len(panel.rows) == 180)

    refreshed = board.prepare(path, "refresh")
    board.show_entries(refreshed, path, entries)
    assert panel.rows == []
    assert board.loads.run_id(running) == run_id
    board.finish(gen, path, running, object(), run_id=run_id)
    _wait(lambda: len(panel.rows) == 180)
    assert panel.rows[150].mark.text() == "✓"
    assert panel.rows[150].star_btn.text() == "★"
    assert panel.rows[160].mark.text() == "✓"
    assert panel.rows[160].property("selected") is True
    assert not panel.refresh_btn.busy
    panel.deleteLater()


def test_release_eviction_and_clear_cache_before_materialization_leave_correct_rows(app):
    panel = LogPanel()
    board = PullBoard(ReplayController(), panel, active=lambda: False, limit=lambda: 1)
    path = "synthetic.txt"
    gen = board.prepare(path, "open")
    board.show_entries(gen, path, _entries(180))
    board.activate(140)
    first = board.keys[140]
    board.finish(gen, path, first, object(), run_id=board.loads.run_id(first))
    board.activate(141)  # Evicts first before its widget exists.
    second = board.keys[141]
    board.finish(gen, path, second, object(), run_id=board.loads.run_id(second))
    board.toggle_pin(141)
    board.clear_cache()
    board.activate(142)
    board.release(142)  # Cancels a pending calculation whose widget does not exist.
    _wait(lambda: len(panel.rows) == 180)
    assert panel.rows[140].mark.text() == ""
    assert panel.rows[141].mark.text() == "✓"
    assert panel.rows[141].star_btn.text() == "★"
    assert panel.rows[142].mark.text() == ""
    assert panel.rows[142].bar.isHidden()
    assert first not in board.loads.cache
    assert second in board.loads.cache
    assert board.keys[142] not in board.loads.running
    panel.deleteLater()


def test_retired_rows_are_deleted_in_bounded_batches_without_destroying_replacement(app, monkeypatch):
    monkeypatch.setattr(LogPanel, "_ROWS_PER_BATCH", 3)
    panel = LogPanel()
    panel.set_pulls([f"old {index}" for index in range(16)])
    old_rows = list(panel.rows)
    panel.set_pulls(["replacement"])
    replacement = panel.rows[0]
    assert len(panel._retired[0]) == 16
    assert all(isValid(row) for row in old_rows)
    panel._dispose_rows()
    assert len(panel._retired[0]) == 13
    _wait(lambda: not panel._retired and all(not isValid(row) for row in old_rows))
    assert isValid(replacement)
    assert replacement.text.text() == "replacement"
    panel.deleteLater()
