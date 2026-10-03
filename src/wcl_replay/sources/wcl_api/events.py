# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Shared event pagination and versioned slice caches for both WCL entry points."""

from __future__ import annotations

import gzip
import hashlib
import json
import zlib
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict
from typing import Protocol

from ...bosses.base import WclSlice
from ...storage import cache_dir, cache_warning, write_json
from .client import WclError

Progress = Callable[[float, str], None]
CACHE_VERSION = 2
EVENTS_QUERY = """
query($code: String!, $fight: [Int]!, $start: Float!, $end: Float!, $kind: EventDataType,
      $hostility: HostilityType, $resources: Boolean!, $filter: String) {
  reportData {
    report(code: $code) {
      events(fightIDs: $fight, startTime: $start, endTime: $end, dataType: $kind,
             hostilityType: $hostility, includeResources: $resources, filterExpression: $filter,
             limit: 10000, translate: false) {
        data
        nextPageTimestamp
      }
    }
  }
}
"""


class QueryClient(Protocol):
    def query(self, q: str, variables: dict) -> dict: ...


def cached_events(
    client: QueryClient,
    host: str,
    code: str,
    fight: dict,
    slices: tuple[WclSlice, ...],
    boss: str = "WCL",
    progress: Progress | None = None,
) -> list[dict]:
    identity = [host, code, int(fight["id"]), [asdict(slice_) for slice_ in slices]]
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    path = cache_dir() / "wcl" / "events" / f"{key}.json.gz"
    time_range = [fight["startTime"], fight["endTime"]]
    try:
        with gzip.open(path, "rt", encoding="utf-8") as file:
            blob = json.load(file)
        if (
            isinstance(blob, dict)
            and blob.get("version") == CACHE_VERSION
            and blob.get("range") == time_range
        ):
            events = blob["events"]
            if isinstance(events, list) and all(isinstance(event, dict) for event in events):
                if progress:
                    progress(0.84, "使用本地事件缓存")
                return events
    except (OSError, ValueError, KeyError, TypeError, EOFError, zlib.error):
        pass
    events = download(client, code, fight, slices, boss, progress)
    try:
        write_json(path, {"version": CACHE_VERSION, "range": time_range, "events": events}, compressed=True)
    except OSError as exc:
        cache_warning(exc)
        if progress:
            progress(0.84, "下载完成；缓存未保存，本次仍可分析")
    return events


def download(
    client: QueryClient,
    code: str,
    fight: dict,
    slices: tuple[WclSlice, ...],
    boss: str = "WCL",
    progress: Progress | None = None,
) -> list[dict]:
    start, end = float(fight["startTime"]), float(fight["endTime"])
    seen: Counter[str] = Counter()
    out: list[dict] = []
    total = max(1, len(slices))
    for index, slice_ in enumerate(slices):
        cursor: float | None = start
        pages = 0
        counts: Counter[str] = Counter()
        while cursor is not None:
            data = client.query(
                EVENTS_QUERY,
                {
                    "code": code,
                    "fight": [int(fight["id"])],
                    "start": cursor,
                    "end": end,
                    "kind": slice_.data_type,
                    "hostility": slice_.hostility or None,
                    "resources": slice_.resources,
                    "filter": slice_.filter or None,
                },
            )
            report = data["reportData"]["report"]
            if report is None:
                raise WclError(f"报告 {code} 已不可访问")
            page = report["events"]
            for event in page.get("data") or []:
                key = json.dumps(event, sort_keys=True, ensure_ascii=False)
                counts[key] += 1
                # De-duplicate overlapping slices, retaining repeated events within each slice.
                if counts[key] > seen[key]:
                    out.append(event)
            pages += 1
            nxt = page.get("nextPageTimestamp")
            if nxt is not None and float(nxt) <= cursor:
                raise WclError(f"{slice_.label} 分页未推进，已停止；请稍后重试")
            cursor = float(nxt) if nxt is not None else None
            if pages >= 40 and cursor is not None:
                raise WclError(f"{slice_.label} 超过 40 页，已停止")
            if progress:
                walked = index + (1.0 if cursor is None else pages / (pages + 1))
                progress(0.06 + 0.78 * walked / total, f"{boss} · {slice_.label}… {len(out)} 条")
        seen |= counts
    out.sort(key=lambda event: event.get("timestamp", 0))
    return out
