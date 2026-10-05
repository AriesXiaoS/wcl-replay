# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Jobs that run in child processes. This module must not import Qt."""

from __future__ import annotations

import os
import sys
import traceback
from collections.abc import Callable
from multiprocessing.queues import Queue
from multiprocessing.reduction import ForkingPickler

from .core.cancellation import TaskCancelled, cancel_check
from .pipeline import analyze
from .sources.local_log import EncounterEntry, index_log, parse_encounter

ProgressFn = Callable[[float, str], None]


def worker_count() -> int:
    """Parallel child processes. One core stays with the UI, and the pool stays small."""
    raw = os.environ.get("WCL_REPLAY_WORKERS")
    if raw:
        try:
            return max(1, min(8, int(raw)))
        except ValueError:
            pass
    cpus = os.cpu_count() or 2
    return max(1, min(4, cpus - 1))


def index_log_job(path: str, progress: ProgressFn) -> list[EncounterEntry]:
    progress(0.0, "正在读取轮次…")
    return index_log(path, lambda frac: progress(frac, "正在读取轮次…"))


def analyze_pull_job(path: str, entry: EncounterEntry, progress: ProgressFn) -> tuple:
    data = parse_encounter(path, entry, lambda frac: progress(frac * 0.85, "解析日志…"))
    tracks, analysis = analyze(data, progress)
    return data, tracks, analysis


def rate_limit_job(
    client_id: str, client_secret: str, host: str, progress: ProgressFn
) -> tuple[float, int, int]:
    """Hourly quota for the saved WCL client. Does not download a fight."""
    from .sources.wcl_api.client import WclClient

    progress(1.0, "")
    client = WclClient(client_id, client_secret, host=host or "cn.warcraftlogs.com")
    try:
        return client.rate_limit()
    finally:
        client.close()


def test_credentials_job(client_id: str, client_secret: str, host: str, progress: ProgressFn) -> bool:
    from .sources.wcl_api.client import WclClient

    progress(0.0, "正在测试连接…")
    client = WclClient(client_id, client_secret, host=host)
    try:
        client.token(force=True)
        return True
    finally:
        client.close()


# fetch_fight uses 0.84 as "events are in hand". analyze() then reports 0.9 and 0.95
# on that same shared scale. The two bars below each run from 0 to 1.
_DOWNLOAD_COMPLETE = 0.84


def _download_bar(frac: float) -> float:
    return min(1.0, max(0.0, frac / _DOWNLOAD_COMPLETE))


def _compute_bar(frac: float) -> float:
    if frac >= 1.0:
        return 1.0
    if frac >= 0.95:
        return 0.7
    if frac >= 0.9:
        return 0.35
    return 0.0


def _phase(name: str, detail: str = "") -> str:
    return f"phase:{name}:{detail}" if detail else f"phase:{name}"


def fetch_wcl_job(
    url: str,
    client_id: str,
    client_secret: str,
    host: str,
    progress: ProgressFn,
    *,
    force_refresh: bool = False,
) -> tuple:
    """Download one WCL fight and analyse it. Credentials stay arguments; this module does not read Qt settings."""
    from .sources.wcl_api.fetch import fetch_fight

    def report(phase: str, frac: float, detail: str = "") -> None:
        progress(frac, _phase(phase, detail))

    data = fetch_fight(
        client_id,
        client_secret,
        host,
        url,
        lambda frac, msg: report("download", _download_bar(frac), msg),
        force_refresh=force_refresh,
    )
    report("download", 1.0)

    def on_compute(frac: float, msg: str = "") -> None:
        report("compute", _compute_bar(frac), msg)

    tracks, analysis = analyze(data, on_compute)
    report("compute", 1.0)
    return data, tracks, analysis


def probe_job(progress: ProgressFn) -> tuple[int, bool]:
    """Process id, and whether this process imported Qt. Tests use this."""
    progress(1.0, "probe")
    qt = "PySide6" in sys.modules or any(name.startswith("PySide6.") for name in sys.modules)
    return os.getpid(), qt


def _report(results: Queue, job_id: int, frac: float, msg: str = "") -> None:
    results.put(("progress", job_id, float(frac), msg))


def _serve(tasks: Queue, results: Queue, cancellations=None) -> None:
    while True:
        try:
            item = tasks.get()
        except (EOFError, OSError):
            return
        if item is None:
            return
        job_id, fn, args, *slots = item

        def check(_slot: int | None = slots[0] if slots else None) -> None:
            if cancellations is not None and _slot is not None and cancellations[_slot]:
                raise TaskCancelled("任务已取消")

        def progress(frac: float, msg: str = "", _job: int = job_id) -> None:
            check()
            _report(results, _job, frac, msg)

        result = payload = None
        token = cancel_check.set(check)
        try:
            check()
            result = fn(*args, progress)
            check()
            # Validate once and send those bytes. Queue's feeder must not walk the
            # potentially large analysis object a second time.
            payload = bytes(ForkingPickler.dumps(result))
            # Cancellation can arrive during serialization; acknowledge it without
            # transporting a result the GUI will discard.
            check()
            results.put(("done_bytes", job_id, payload))
        except Exception:
            results.put(("fail", job_id, traceback.format_exc()))
        finally:
            cancel_check.reset(token)
            result = payload = None
