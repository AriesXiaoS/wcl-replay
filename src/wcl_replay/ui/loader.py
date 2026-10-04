# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Run log indexing and pull analysis in child processes so the UI stays responsive."""

from __future__ import annotations

import multiprocessing
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from multiprocessing.queues import Queue
from multiprocessing.reduction import ForkingPickler
from queue import Empty

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot
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
        self.slot = -1
        self.deadline = float("inf")
        self.cancelled = False


@dataclass(slots=True)
class JobHandle:
    runner: TaskRunner
    job_id: int

    def cancel(self) -> None:
        self.runner.cancel(self.job_id)

    @property
    def done(self) -> bool:
        return self.job_id not in self.runner._jobs


class TaskRunner(QObject):
    """A small process pool. Callbacks always run on the GUI thread."""

    _progress = Signal(int, float, str)
    _done = Signal(int, object)
    _failed = Signal(int, str)
    _pool_failed = Signal(str)

    def __init__(self, parent: QObject | None = None, *, max_jobs: int = 16):
        super().__init__(parent)
        self._jobs: dict[int, _Job] = {}
        self.max_jobs = max(1, min(64, max_jobs))
        self._cancellations = multiprocessing.Array("b", self.max_jobs)
        self._timeouts = QTimer(self)
        self._timeouts.setInterval(500)
        self._timeouts.timeout.connect(self._expire)
        self._timeouts.start()
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
        self._pool_failed.connect(self._on_pool_failed, _QUEUED)
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
        *,
        timeout: float = 900.0,
    ) -> JobHandle | None:
        """Run ``fn(*args, progress)`` in a child process. ``fn`` must be importable."""
        with self._lock:
            closed = self._closed
            boot_error = self._boot_error
            if boot_error and not closed:
                # A fresh submission may retry after an initialization or worker failure.
                self._boot_error = None
                boot_error = None
        if closed:
            on_fail("已关闭")
            return
        if boot_error:
            on_fail(boot_error)
            return
        if len(self._jobs) >= self.max_jobs:
            on_fail(f"后台任务已达上限 {self.max_jobs}，请等待或取消其他任务")
            return None
        if not self._timeouts.isActive():
            self._timeouts.start()
        self._ensure_pool()
        self._ids += 1
        job_id = self._ids
        self._jobs[job_id] = _Job(on_done, on_fail, on_progress)
        occupied = {job.slot for jid, job in self._jobs.items() if jid != job_id}
        job = self._jobs[job_id]
        job.slot = next(slot for slot in range(self.max_jobs) if slot not in occupied)
        self._cancellations[job.slot] = 0
        job.deadline = time.monotonic() + max(0.0, timeout)
        handle = JobHandle(self, job_id)
        item = (job_id, fn, args, job.slot)
        try:
            ForkingPickler.dumps(item)
        except Exception:
            self._jobs.pop(job_id, None)
            on_fail(traceback.format_exc())
            return
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
            return handle
        try:
            tasks.put(item)
        except Exception:
            self._jobs.pop(job_id, None)
            on_fail(traceback.format_exc())
            return None
        return handle

    def cancel(self, job_id: int, message: str = "任务已取消") -> None:
        job = self._jobs.get(job_id)
        if job is None or job.cancelled:
            return
        job.cancelled = True
        self._cancellations[job.slot] = 1
        with self._lock:
            pending = any(item[0] == job_id for item in self._pending)
            self._pending = [item for item in self._pending if item[0] != job_id]
        if pending:
            self._jobs.pop(job_id, None)
        job.on_fail(message)

    def _expire(self) -> None:
        if self._closed:
            return
        for job_id, job in list(self._jobs.items()):
            if not job.cancelled and time.monotonic() >= job.deadline:
                self.cancel(job_id, "后台任务超时，已请求停止")

    def busy(self) -> bool:
        return bool(self._jobs)

    def shutdown(self) -> None:
        self._timeouts.stop()
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
        if listener is not None and listener.ident is not None and listener is not threading.current_thread():
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
        try:
            threading.Thread(target=self._boot, name="wcl-replay-boot", daemon=True).start()
        except Exception:
            self._fail_pending(traceback.format_exc())

    def _boot(self) -> None:
        tasks = results = None
        listener = None
        published: threading.Event | None = None
        procs: list[multiprocessing.Process] = []
        try:
            ctx = multiprocessing.get_context("spawn")
            tasks = ctx.Queue()
            results = ctx.Queue()
            for index in range(worker_count()):
                proc = ctx.Process(
                    target=_serve,
                    name=f"wcl-replay-{index}",
                    args=(tasks, results, self._cancellations),
                    daemon=True,
                )
                proc.start()
                procs.append(proc)
            published = threading.Event()
            listener = threading.Thread(
                target=self._listen_after_publish,
                args=(results, tuple(procs), published),
                name="wcl-replay-results",
                daemon=True,
            )
            # Thread.start can be slow. Never hold the submission lock while starting
            # it, or the GUI's first run() can block before its spinner gets painted.
            listener.start()
            with self._lock:
                closed = self._closed
                if not closed:
                    # The listener waits for publication, so immediate worker failure
                    # still sees a complete pool and shutdown only sees a started thread.
                    self._tasks = tasks
                    self._results = results
                    self._procs = procs
                    self._listener = listener
                self._booting = False
                pending = self._pending
                self._pending = []
        except Exception:
            tb = traceback.format_exc()
            with self._lock:
                if self._tasks is tasks and tasks is not None:
                    self._tasks = self._results = None
                    self._procs = []
                    self._listener = None
            if published is not None:
                published.set()
            if listener is not None and listener.ident is not None:
                listener.join(timeout=1.0)
            for proc in procs:
                try:
                    proc.terminate()
                    proc.join(timeout=1.0)
                except Exception:
                    pass
            for queue in (tasks, results):
                if queue is not None:
                    try:
                        queue.close()
                    except Exception:
                        pass
                    try:
                        queue.cancel_join_thread()
                    except Exception:
                        pass
            self._fail_pending(tb)
            return
        published.set()
        if closed:
            for proc in procs:
                proc.terminate()
                proc.join(timeout=1.0)
            try:
                results.put(None)
            except Exception:
                pass
            if listener is not None and listener.ident is not None:
                listener.join(timeout=1.0)
            for queue in (tasks, results):
                try:
                    queue.close()
                except Exception:
                    pass
                try:
                    queue.cancel_join_thread()
                except Exception:
                    pass
            for job_id, *_rest in pending:
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
            self._booting = False
        for job_id, *_rest in pending:
            self._failed.emit(job_id, tb)

    def _listen_after_publish(self, results: Queue, procs: tuple, published: threading.Event) -> None:
        published.wait()
        with self._lock:
            active = not self._closed and self._results is results
        if active:
            self._listen(results, procs)

    def _listen(self, results: Queue, procs: tuple = ()) -> None:
        while True:
            try:
                msg = results.get(timeout=0.2)
            except Empty:
                if any(proc.exitcode is not None for proc in procs):
                    self._pool_failed.emit("后台进程意外退出，请重新提交任务")
                    return
                continue
            except (EOFError, OSError, ValueError):
                with self._lock:
                    active = not self._closed and self._results is results
                if active:
                    self._pool_failed.emit("后台通信中断，请重新提交任务")
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
            elif kind == "done_bytes":
                _kind, job_id, payload = msg
                try:
                    result = ForkingPickler.loads(payload)
                except Exception:
                    self._failed.emit(job_id, traceback.format_exc())
                else:
                    self._done.emit(job_id, result)
            elif kind == "fail":
                _kind, job_id, tb = msg
                self._failed.emit(job_id, tb)

    @Slot(str)
    def _on_pool_failed(self, message: str) -> None:
        if self._closed:
            return
        for proc in self._procs:
            if proc.is_alive():
                proc.terminate()
        self.shutdown()
        with self._lock:
            self._closed = False
            self._booting = False
            self._boot_error = message
            self._tasks = self._results = None
            self._procs = []
            self._listener = None
        jobs, self._jobs = self._jobs, {}
        for job in jobs.values():
            if not job.cancelled:
                job.on_fail(message)

    @Slot(int, float, str)
    def _on_progress(self, job_id: int, frac: float, msg: str) -> None:
        job = self._jobs.get(job_id)
        if job is not None and not job.cancelled and job.on_progress is not None:
            job.on_progress(frac, msg)

    @Slot(int, object)
    def _on_done(self, job_id: int, result: object) -> None:
        job = self._jobs.pop(job_id, None)
        if job is not None and not job.cancelled:
            job.on_done(result)

    @Slot(int, str)
    def _on_failed(self, job_id: int, tb: str) -> None:
        job = self._jobs.pop(job_id, None)
        if job is not None and not job.cancelled:
            job.on_fail(tb)
