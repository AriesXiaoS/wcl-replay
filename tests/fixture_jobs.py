# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from __future__ import annotations

import os
import time
from pathlib import Path


def unserializable_result(progress):
    return lambda: None


def crash_worker(progress):
    os._exit(17)


def cooperative_job(started_file: str, seconds: float, progress):
    Path(started_file).write_text("started", encoding="utf-8")
    start = time.monotonic()
    while time.monotonic() - start < seconds:
        progress(0.5, "working")
        time.sleep(0.02)
    return "finished"
