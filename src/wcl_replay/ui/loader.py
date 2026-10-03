# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Run log indexing and pull analysis in child processes so the UI stays responsive."""

from __future__ import annotations

import multiprocessing
import threading
import traceback
from collections.abc import Callable
from multiprocessing.queues import Queue

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtWidgets import QApplication

from ..workers import _serve, worker_count

_QUEUED = Qt.ConnectionType.QueuedConnection


class _Job:
    def __init__(
        self,
        on_done: Callable[[object], None],
        on_fail: Callable[[str], None],
        on_progress: Callable[[float, str], None] | None,
    ):
        self.on_done = on_done
        self.on_fail = on_fail
        self.on_progress = on_progress


class TaskRunner(QObject):
    """A small process pool. Callbacks always run on the GUI thread."""

    _progress = Signal(int, float, str)
    _done = Signal(int, object)
    _failed = Signal(int, str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._jobs: dict[int, _Job] = {}
        self._ids = 0
        self._lock = threading.Lock()
        self._closed = False
        self._booting = False
        self._boot_error: str | None = None
        self._pending: list[tuple] = []
        self._tasks: Queue | None = None
        self._results: Queue | None = None
        self._procs: list[multiprocessing.Process] = []
        self._listener: threading.Thread | None = None
        self._progress.connect(self._on_progress, _QUEUED)
        self._done.connect(self._on_done, _QUEUED)
        self._failed.connect(self._on_failed, _QUEUED)
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.shutdown)

    def run(
        self,
        fn: Callable[..., object],
        args: tuple,
        on_done: Callable[[object], None],
        on_fail: Callable[[str], None],
        on_progress: Callable[[float, str], None] | None = None,
    ) -> None:
        """Run ``fn(*args, progress)`` in a child process. ``fn`` must be importable."""
        with self._lock:
            closed = self._closed
            boot_error = self._boot_error
        if closed:
            on_fail("已关闭")
            return
        if boot_error:
            on_fail(boot_error)
            return
        self._ensure_pool()
        self._ids += 1
        job_id = self._ids
        self._jobs[job_id] = _Job(on_done, on_fail, on_progress)
        item = (job_id, fn, args)
        with self._lock:
            if self._closed:
                closed = True
                boot_error = None
                tasks = None
            elif self._boot_error:
                closed = False
                boot_error = self._boot_error
                tasks = None
            else:
                closed = False
                boot_error = None
                tasks = self._tasks
                if tasks is None:
                    self._pending.append(item)
        if closed or boot_error:
            self._jobs.pop(job_id, None)
            on_fail("已关闭" if closed else boot_error or "已关闭")
            return
        if tasks is None:
            return
        try:
            tasks.put(item)
        except Exception:
            self._jobs.pop(job_id, None)
            on_fail(traceback.format_exc())

    def busy(self) -> bool:
        return bool(self._jobs)

    def shutdown(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            procs = list(self._procs)
            tasks = self._tasks
            results = self._results
            listener = self._listener
            self._pending.clear()
        if tasks is not None:
            for _ in procs:
                try:
                    tasks.put(None)
                except Exception:
                    pass
        for proc in procs:
            proc.join(timeout=1.0)
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=1.0)
        if results is not None:
            try:
                results.put(None)
            except Exception:
                pass
        if listener is not None and listener is not threading.current_thread():
            listener.join(timeout=1.0)
        for queue in (tasks, results):
            if queue is None:
                continue
            try:
                queue.close()
            except Exception:
                pass
            try:
                queue.cancel_join_thread()
            except Exception:
                pass

    def _ensure_pool(self) -> None:
        with self._lock:
            if self._closed or self._booting or self._procs or self._boot_error:
                return
            self._booting = True
        threading.Thread(target=self._boot, name="wcl-replay-boot", daemon=True).start()

    def _boot(self) -> None:
        ctx = multiprocessing.get_context("spawn")
        tasks = ctx.Queue()
        results = ctx.Queue()
        procs: list[multiprocessing.Process] = []
        try:
            for index in range(worker_count()):
                proc = ctx.Process(
                    target=_serve,
                    name=f"wcl-replay-{index}",
                    args=(tasks, results),
                    daemon=True,
                )
                proc.start()
                procs.append(proc)
            listener = threading.Thread(
                target=self._listen, args=(results,), name="wcl-replay-results", daemon=True
            )
            listener.start()
        except Exception:
            tb = traceback.format_exc()
            for proc in procs:
                proc.terminate()
            self._fail_pending(tb)
            return
        with self._lock:
            closed = self._closed
            if not closed:
                self._tasks = tasks
                self._results = results
                self._procs = procs
                self._listener = listener
            pending = self._pending
            self._pending = []
        if closed:
            for proc in procs:
                proc.terminate()
            try:
                results.put(None)
            except Exception:
                pass
            listener.join(timeout=1.0)
            for job_id, _fn, _args in pending:
                self._failed.emit(job_id, "已关闭")
            return
        for item in pending:
            try:
                tasks.put(item)
            except Exception:
                self._failed.emit(item[0], traceback.format_exc())

    def _fail_pending(self, tb: str) -> None:
        with self._lock:
            pending = self._pending
            self._pending = []
            self._boot_error = tb
        for job_id, _fn, _args in pending:
            self._failed.emit(job_id, tb)

    def _listen(self, results: Queue) -> None:
        while True:
            try:
                msg = results.get()
            except (EOFError, OSError):
                return
            if msg is None:
                return
            kind = msg[0]
            if kind == "progress":
                _kind, job_id, frac, text = msg
                self._progress.emit(job_id, frac, text)
            elif kind == "done":
                _kind, job_id, result = msg
                self._done.emit(job_id, result)
            elif kind == "fail":
                _kind, job_id, tb = msg
                self._failed.emit(job_id, tb)

    @Slot(int, float, str)
    def _on_progress(self, job_id: int, frac: float, msg: str) -> None:
        job = self._jobs.get(job_id)
        if job is not None and job.on_progress is not None:
            job.on_progress(frac, msg)

    @Slot(int, object)
    def _on_done(self, job_id: int, result: object) -> None:
        job = self._jobs.pop(job_id, None)
        if job is not None:
            job.on_done(result)

    @Slot(int, str)
    def _on_failed(self, job_id: int, tb: str) -> None:
        job = self._jobs.pop(job_id, None)
        if job is not None:
            job.on_fail(tb)
