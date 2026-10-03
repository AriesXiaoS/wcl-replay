# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from __future__ import annotations

import bisect
import math

from ...core.models import FightData
from ...core.specs import class_color
from ..base import Seg


def movement_times(track, t0: int, t1: int, step: float = 1.5) -> tuple[tuple[int, float, float], ...]:
    """Thinned (time, x, y) samples from t0 to t1, so a trail can be cut at the playhead."""
    if track is None or len(track) == 0 or t1 <= t0:
        return ()
    raw = [(t0, *track.position(t0))]
    for i in range(len(track)):
        ts = int(track.t[i])
        if ts <= t0:
            continue
        if ts >= t1:
            break
        raw.append((ts, float(track.x[i]), float(track.y[i])))
    raw.append((t1, *track.position(t1)))
    kept = [raw[0]]
    limit = step * step
    for ts, x, y in raw[1:-1]:
        _t, px, py = kept[-1]
        if (x - px) ** 2 + (y - py) ** 2 >= limit:
            kept.append((ts, x, y))
    ts, x, y = raw[-1]
    _t, px, py = kept[-1]
    if (x - px) ** 2 + (y - py) ** 2 >= 0.04:
        kept.append((ts, x, y))
    return tuple(kept) if len(kept) >= 2 else ()


def trail_points(
    samples: tuple[tuple[int, float, float], ...],
    t: float,
    tip: tuple[float, float] | None,
) -> tuple[tuple[float, float], ...]:
    """The part of a route already walked by t, with the unit's current point on the end."""
    pts = [(x, y) for ts, x, y in samples if ts <= t]
    if tip is not None:
        if not pts:
            pts = [tip]
        else:
            px, py = pts[-1]
            if (tip[0] - px) ** 2 + (tip[1] - py) ** 2 >= 0.04:
                pts.append(tip)
    return tuple(pts) if len(pts) >= 2 else ()


def angle_diff(a: float, b: float) -> float:
    d = (a - b) % (2 * math.pi)
    return min(d, 2 * math.pi - d)


def name_seg(data: FightData, aid: int, bold: bool = True) -> Seg:
    a = data.actors.get(aid)
    if a is None:
        return Seg("?", "#aaaaaa")
    return Seg(a.short_name, class_color(a.class_name, "#e0e0e0"), bold=bold)


def short(data: FightData, aid: int) -> str:
    a = data.actors.get(aid)
    return a.short_name if a else "?"


def color_of(data: FightData, aid: int) -> str:
    a = data.actors.get(aid)
    return class_color(a.class_name if a else None, "#e0e0e0")


def events_between(data: FightData, times: list[int], t0: int, t1: int):
    lo = bisect.bisect_left(times, t0)
    hi = bisect.bisect_right(times, t1)
    return data.events[lo:hi]


def stack_rounds(data: FightData, times: list[int], spell_id: int, t0: int, t1: int) -> int:
    """Largest number of applied/dose events of an aura on one unit inside [t0, t1]."""
    per: dict[int, int] = {}
    for e in events_between(data, times, t0, t1):
        if e.spell_id == spell_id and e.type in ("SPELL_AURA_APPLIED", "SPELL_AURA_APPLIED_DOSE"):
            per[e.dst] = per.get(e.dst, 0) + 1
    return max(per.values(), default=0)
