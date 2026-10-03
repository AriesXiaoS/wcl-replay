# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Jobs that run in child processes. This module must not import Qt."""

from __future__ import annotations

import os
import sys
import traceback
from collections.abc import Callable
from multiprocessing.queues import Queue

from .pipeline import analyze
from .sources.local_log import EncounterEntry, index_log, parse_encounter

ProgressFn = Callable[[float, str], None]


def worker_count() -> int:
    """Parallel child processes. One core stays with the UI, and the pool stays small."""
    raw = os.environ.get("WCL_REPLAY_WORKERS")
    if raw:
        return max(1, int(raw))
    cpus = os.cpu_count() or 2
    return max(1, min(4, cpus - 1))


def index_log_job(path: str, progress: ProgressFn) -> list[EncounterEntry]:
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
    return WclClient(client_id, client_secret, host=host or "cn.warcraftlogs.com").rate_limit()


def fetch_wcl_job(url: str, client_id: str, client_secret: str, host: str, progress: ProgressFn) -> tuple:
    """Download one WCL fight and analyse it. Credentials stay arguments; this module does not read Qt settings."""
    from .sources.wcl_api.fetch import fetch_fight

    data = fetch_fight(client_id, client_secret, host, url, lambda frac, msg: progress(frac * 0.84, msg))
    tracks, analysis = analyze(data, progress)
    return data, tracks, analysis


def probe_job(progress: ProgressFn) -> tuple[int, bool]:
    """Process id, and whether this process imported Qt. Tests use this."""
    progress(1.0, "probe")
    qt = "PySide6" in sys.modules or any(name.startswith("PySide6.") for name in sys.modules)
    return os.getpid(), qt


def _report(results: Queue, job_id: int, frac: float, msg: str = "") -> None:
    results.put(("progress", job_id, float(frac), msg))


def _serve(tasks: Queue, results: Queue) -> None:
    while True:
        try:
            item = tasks.get()
        except (EOFError, OSError):
            return
        if item is None:
            return
        job_id, fn, args = item

        def progress(frac: float, msg: str = "", _job: int = job_id) -> None:
            _report(results, _job, frac, msg)

        try:
            result = fn(*args, progress)
        except Exception:
            results.put(("fail", job_id, traceback.format_exc()))
            continue
        try:
            results.put(("done", job_id, result))
        except Exception:
            results.put(("fail", job_id, traceback.format_exc()))
