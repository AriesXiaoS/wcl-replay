# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Raid world markers (光柱): combat-log index, color, and clipping to a fight.

The log writes ``WORLD_MARKER_PLACED,instance,index,x,y`` with ``index`` in 0–7.
That index is ``/wm`` 1–8 minus one (blue square through white skull), not the
unit raid-target order (star, circle, diamond, ...).
"""

from __future__ import annotations

import bisect
import heapq
from collections.abc import Iterable
from dataclasses import dataclass

from .cancellation import check_cancelled
from .models import WorldMarker


@dataclass(frozen=True, slots=True)
class MarkerStyle:
    name: str
    color: str
    shape: str


@dataclass(slots=True)
class MarkerEvent:
    """One raw place/remove line, with an absolute timestamp."""

    offset: int
    abs_ms: int
    placed: bool
    index: int
    x: float = 0.0
    y: float = 0.0
    instance_id: int = 0
    zone_unload: bool = False


# /wm 1..8, stored 0-based as the combat log writes them.
_STYLES: dict[int, MarkerStyle] = {
    0: MarkerStyle("蓝方", "#3d8bff", "square"),
    1: MarkerStyle("绿三", "#3dcc4a", "triangle"),
    2: MarkerStyle("紫菱", "#c45cff", "diamond"),
    3: MarkerStyle("红叉", "#ff4040", "cross"),
    4: MarkerStyle("黄星", "#ffe14a", "star"),
    5: MarkerStyle("橙饼", "#ff9a2a", "circle"),
    6: MarkerStyle("月亮", "#9fd4ff", "moon"),
    7: MarkerStyle("骷髅", "#f2f2f2", "skull"),
}


def style_of(index: int) -> MarkerStyle:
    return _STYLES.get(index, MarkerStyle(f"标记{index}", "#dddddd", "circle"))


def _zone_unload_removes(events: list[MarkerEvent]) -> set[tuple[int, int]]:
    """Only the source's explicit zone-change context makes a removal non-authoritative."""
    return {(event.abs_ms, event.index) for event in events if not event.placed and event.zone_unload}


@dataclass(frozen=True, slots=True)
class _MarkerSpan:
    start: int
    end: int | None
    x: float
    y: float


def _marker_spans(
    ordered: Iterable[tuple[int, MarkerEvent]], unload: set[tuple[int, int]]
) -> dict[int, list[_MarkerSpan]]:
    open_: dict[int, MarkerEvent] = {}
    spans: dict[int, list[_MarkerSpan]] = {}
    for count, (_rank, event) in enumerate(ordered):
        if count % 1024 == 0:
            check_cancelled()
        if not event.placed and (event.abs_ms, event.index) in unload:
            continue
        previous = open_.pop(event.index, None)
        if previous is not None and event.abs_ms > previous.abs_ms:
            spans.setdefault(event.index, []).append(
                _MarkerSpan(previous.abs_ms, event.abs_ms, previous.x, previous.y)
            )
        if event.placed:
            open_[event.index] = event
    for index, event in open_.items():
        spans.setdefault(index, []).append(_MarkerSpan(event.abs_ms, None, event.x, event.y))
    return spans


def clip_world_markers_many(
    events: list[MarkerEvent], fights: list[tuple[int, int, int]]
) -> list[list[WorldMarker]]:
    """Clip many (start, duration, instance) windows without rescanning marker history per pull."""
    check_cancelled()
    unload = _zone_unload_removes(events)
    grouped: dict[int, list[tuple[int, MarkerEvent]]] = {}
    # Preserve supplied order when even the byte offsets tie.
    for rank, event in enumerate(sorted(events, key=lambda event: (event.abs_ms, event.offset))):
        grouped.setdefault(event.instance_id, []).append((rank, event))
    timelines = {}
    shared = grouped.get(0, [])
    for instance in {instance for _start, _duration, instance in fights}:
        ordered = (
            shared
            if instance == 0
            else heapq.merge(shared, grouped.get(instance, []), key=lambda item: item[0])
        )
        timelines[instance] = {
            index: ([span.start for span in spans], spans)
            for index, spans in _marker_spans(ordered, unload).items()
        }

    results: list[list[WorldMarker]] = []
    for start, duration, instance in fights:
        check_cancelled()
        out: list[WorldMarker] = []
        end = start + max(0, duration)
        if duration > 0:
            for index, (starts, spans) in timelines[instance].items():
                at = max(0, bisect.bisect_right(starts, start) - 1)
                while at < len(spans) and spans[at].start < end:
                    span = spans[at]
                    if span.end is None or span.end > start:
                        until = duration + 1 if span.end is None or span.end > end else span.end - start
                        out.append(WorldMarker(index, span.x, span.y, max(0, span.start - start), until))
                    at += 1
        out.sort(key=lambda marker: (marker.start, marker.index))
        results.append(out)
    return results


def clip_world_markers(
    events: list[MarkerEvent],
    fight_start_ms: int,
    duration_ms: int,
    instance_id: int,
) -> list[WorldMarker]:
    """Markers whose beam is up during ``[fight_start, fight_start + duration]``."""
    fight_end = fight_start_ms + max(0, duration_ms)
    unload = _zone_unload_removes(events)
    ordered = sorted(
        (e for e in events if e.instance_id in (0, instance_id)),
        # Millisecond timestamps can tie after truncating the log's finer precision.
        # The byte offset preserves whether the beam was placed or removed last.
        key=lambda e: (e.abs_ms, e.offset),
    )
    open_: dict[int, tuple[float, float, int]] = {}
    out: list[WorldMarker] = []

    def emit(index: int, x: float, y: float, placed_abs: int, until_abs: int, *, through_end: bool) -> None:
        if until_abs <= fight_start_ms or placed_abs >= fight_end:
            return
        start = max(0, placed_abs - fight_start_ms)
        end = duration_ms + 1 if through_end else until_abs - fight_start_ms
        if end > start:
            out.append(WorldMarker(index, x, y, start, end))

    for e in ordered:
        if e.abs_ms > fight_end:
            break
        if e.placed:
            prev = open_.get(e.index)
            if prev:
                emit(e.index, prev[0], prev[1], prev[2], e.abs_ms, through_end=False)
            open_[e.index] = (e.x, e.y, e.abs_ms)
        elif (e.abs_ms, e.index) in unload:
            continue
        else:
            prev = open_.pop(e.index, None)
            if prev:
                emit(e.index, prev[0], prev[1], prev[2], e.abs_ms, through_end=False)
    for index, (x, y, placed) in open_.items():
        emit(index, x, y, placed, fight_end, through_end=True)
    out.sort(key=lambda m: (m.start, m.index))
    return out
