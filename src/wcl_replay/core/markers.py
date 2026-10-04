# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Raid world markers (光柱): combat-log index, color, and clipping to a fight.

The log writes ``WORLD_MARKER_PLACED,instance,index,x,y`` with ``index`` in 0–7.
That index is ``/wm`` 1–8 minus one (blue square through white skull), not the
unit raid-target order (star, circle, diamond, ...).
"""

from __future__ import annotations

from dataclasses import dataclass

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
