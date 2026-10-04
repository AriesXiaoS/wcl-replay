from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from fixture_jobs import cooperative_job
from PySide6.QtWidgets import QApplication

from wcl_replay.core.cancellation import TaskCancelled, cancel_check
from wcl_replay.sources.wcl_api.client import WclClient, WclError
from wcl_replay.storage import prune_cache
from wcl_replay.ui import loader
from wcl_replay.ui.loader import TaskRunner
from wcl_replay.ui.log_panel import PullLoads
from wcl_replay.workers import probe_job, worker_count

_spec = importlib.util.spec_from_file_location(
    "budget_probe", Path(__file__).parents[1] / "tools/probe_wcl_budget.py"
)
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def wait(app, ready, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if ready():
            return True
        time.sleep(0.01)
    return False


def test_running_and_queued_tasks_can_be_cancelled_and_pool_remains_usable(app, tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "worker_count", lambda: 1)
    runner = TaskRunner(max_jobs=2)
    done, failed = [], []
    first, second = tmp_path / "first", tmp_path / "second"
    try:
        running = runner.run(cooperative_job, (str(first), 20), done.append, failed.append)
        assert wait(app, first.exists)
        queued = runner.run(cooperative_job, (str(second), 20), done.append, failed.append)
        assert runner.run(probe_job, (), done.append, failed.append) is None
        assert "上限 2" in failed[-1]
        queued.cancel()
        running.cancel()
        assert wait(app, lambda: not runner.busy())
        assert not second.exists() and not done
        assert running.done and queued.done
        assert failed.count("任务已取消") == 2
        runner.run(probe_job, (), done.append, failed.append)
        assert wait(app, lambda: done and not runner.busy())
        assert done[0][1] is False
    finally:
        runner.shutdown()


def test_timeout_stops_cooperative_worker(app, tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "worker_count", lambda: 1)
    runner = TaskRunner()
    done, failed = [], []
    try:
        handle = runner.run(
            cooperative_job, (str(tmp_path / "started"), 20), done.append, failed.append, timeout=0.8
        )
        assert wait(app, lambda: handle.done)
        assert len(failed) == 1 and "超时" in failed[0]
        assert not done
    finally:
        runner.shutdown()


def test_abandoned_or_reset_pull_cancels_its_job_handle():
    loads = PullLoads()

    class Handle:
        cancelled = 0

        def cancel(self):
            self.cancelled += 1

    first, second = Handle(), Handle()
    loads.click((1,))
    loads.bind((1,), first)
    loads.abandon((1,))
    loads.click((2,))
    loads.bind((2,), second)
    loads.reset()
    assert first.cancelled == second.cancelled == 1


def test_cancellation_before_query_prevents_network_request(monkeypatch):
    client = WclClient("synthetic", "synthetic")

    def cancelled():
        raise TaskCancelled("cancelled")

    monkeypatch.setattr(client.http, "post", lambda *a, **kw: pytest.fail("network request made"))
    token = cancel_check.set(cancelled)
    try:
        with pytest.raises(TaskCancelled):
            client.query("query {}", {})
    finally:
        cancel_check.reset(token)
        client.close()


@pytest.mark.parametrize("value,expected", [("999", 8), ("0", 1), ("-8", 1), ("invalid", None)])
def test_worker_environment_is_bounded(value, expected, monkeypatch):
    monkeypatch.setenv("WCL_REPLAY_WORKERS", value)
    assert worker_count() == expected if expected is not None else 1 <= worker_count() <= 4


def test_source_entry_does_not_import_qt_in_spawn_child(tmp_path):
    script = tmp_path / "entry.py"
    script.write_text(
        """from wcl_replay.app import main
from wcl_replay.workers import _serve, probe_job
import multiprocessing
from multiprocessing.reduction import ForkingPickler
import json
if __name__ == "__main__":
    multiprocessing.freeze_support()
    context = multiprocessing.get_context("spawn")
    tasks, results = context.Queue(), context.Queue()
    process = context.Process(target=_serve, args=(tasks, results))
    process.start()
    try:
        tasks.put((1, probe_job, ()))
        while True:
            message = results.get(timeout=10)
            if message[0] == "done_bytes":
                print(json.dumps(ForkingPickler.loads(message[2])))
                break
            if message[0] == "fail":
                raise RuntimeError(message[2])
    finally:
        tasks.put(None)
        process.join(5)
        if process.is_alive():
            process.terminate()
            process.join(5)
        tasks.close()
        results.close()
""",
        encoding="utf-8",
    )
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)[1] is False


def test_budget_uses_response_baseline_and_accumulates_across_reset():
    budget = probe.Budget(0, 0, 1200)
    probe.note_points(budget, {"limitPerHour": 3600, "pointsSpentThisHour": 2400})
    assert budget.used == 0 and not budget.over()
    probe.note_points(budget, {"limitPerHour": 3600, "pointsSpentThisHour": 2410})
    assert budget.used == 10
    probe.note_points(budget, {"limitPerHour": 3600, "pointsSpentThisHour": 5})
    assert budget.used == 15
    probe.note_points(budget, {"limitPerHour": 3600, "pointsSpentThisHour": 15})
    assert budget.used == 25


class FakeProbe:
    def __init__(self, pages):
        self.pages = iter(pages)
        self.calls = []

    def call(self, label, query, variables):
        self.calls.append(variables)
        return {"reportData": {"report": {"events": next(self.pages)}}}


@pytest.mark.parametrize("bundled", [False, True])
def test_probe_pagination_handles_empty_pages_and_retains_same_slice_duplicates(bundled):
    first = {"data": [], "nextPageTimestamp": 100}
    second = {"data": [{"timestamp": 101}, {"timestamp": 101}], "nextPageTimestamp": None}
    fake = FakeProbe([second] if bundled else [first, second])
    found = probe.paginate_with_code(
        fake,
        "synthetic",
        "测试",
        "Casts",
        {"id": 1, "startTime": 0, "endTime": 1000},
        first_page=first if bundled else None,
    )
    assert found == second["data"]
    assert len(fake.calls) == (1 if bundled else 2)


@pytest.mark.parametrize("bundled", [False, True])
def test_probe_stalled_cursor_is_error(bundled):
    page = {"data": [], "nextPageTimestamp": 0}
    with pytest.raises(WclError, match="未推进"):
        probe.paginate_with_code(
            FakeProbe([page]),
            "synthetic",
            "测试",
            "Casts",
            {"id": 1, "startTime": 0, "endTime": 1000},
            first_page=page if bundled else None,
        )


def test_probe_page_cap_does_not_return_partial_success():
    pages = [{"data": [], "nextPageTimestamp": i + 1} for i in range(40)]
    with pytest.raises(WclError, match="超过 40 页"):
        probe.paginate_with_code(
            FakeProbe(pages), "synthetic", "测试", "Casts", {"id": 1, "startTime": 0, "endTime": 1000}
        )


def test_cache_retention_only_removes_disposable_entries(tmp_path):
    directory = tmp_path / "events"
    directory.mkdir()
    oldest, keep, token = directory / "old.json.gz", directory / "new.json.gz", tmp_path / "token.json"
    oldest.write_bytes(b"1234")
    keep.write_bytes(b"5678")
    token.write_bytes(b"synthetic")
    os.utime(oldest, (0, 0))
    prune_cache(directory, keep=keep, max_bytes=4)
    assert not oldest.exists() and keep.exists() and token.exists()
    prune_cache(tmp_path, keep=token, max_bytes=0)
    assert token.exists()
