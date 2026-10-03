# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Indexing and pull analysis run in child processes, and callbacks return to the GUI thread."""

from __future__ import annotations

import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
os.environ.setdefault("WCL_REPLAY_WORKERS", "2")

from PySide6.QtWidgets import QApplication

from wcl_replay.sources.local_log import index_log
from wcl_replay.ui.loader import TaskRunner
from wcl_replay.workers import analyze_pull_job, index_log_job, probe_job


def _wait(app: QApplication, ready, seconds: float = 30.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if ready():
            return True
        time.sleep(0.02)
    return False


def test_jobs_run_in_child_processes(log_path):
    app = QApplication.instance() or QApplication([])
    runner = TaskRunner()
    probed: list[tuple[int, bool]] = []
    indexed: list[list] = []
    analyzed: list[tuple] = []
    failed: list[str] = []
    threads: list[threading.Thread] = []

    def on_probe(result) -> None:
        threads.append(threading.current_thread())
        probed.append(result)

    def on_index(entries) -> None:
        indexed.append(entries)
        runner.run(analyze_pull_job, (str(log_path), entries[0]), analyzed.append, failed.append)

    try:
        runner.run(probe_job, (), on_probe, failed.append)
        runner.run(index_log_job, (str(log_path),), on_index, failed.append)
        runner.run(index_log_job, (str(log_path) + ".missing",), lambda _r: None, failed.append)
        assert _wait(app, lambda: probed and indexed and analyzed and failed), (
            probed,
            indexed,
            analyzed,
            failed,
        )
        pid, imported_qt = probed[0]
        assert pid != os.getpid()
        assert imported_qt is False
        assert threads[0] is threading.main_thread()
        assert indexed[0][0].encounter_id == index_log(log_path)[0].encounter_id
        data, _tracks, analysis = analyzed[0]
        assert data.fight.encounter_id == 3429
        assert any(_tracks.has(aid) for aid in data.samples)
        assert "盘卷祭坛" in analysis.title
        assert any("FileNotFoundError" in item or "No such file" in item for item in failed)
    finally:
        runner.shutdown()
