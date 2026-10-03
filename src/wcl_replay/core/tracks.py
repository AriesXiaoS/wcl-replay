# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Per-unit position / health tracks built from the sparse samples of a fight."""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass

import numpy as np

from .cancellation import check_cancelled
from .models import ActorKind, FightData, Sample


@dataclass(slots=True)
class Pose:
    x: float
    y: float
    facing: float
    hp: int
    max_hp: int

    @property
    def hp_frac(self) -> float:
        return self.hp / self.max_hp if self.max_hp > 0 else 0.0


class Track:
    __slots__ = ("t", "x", "y", "facing", "hp", "max_hp")

    def __init__(self, samples: list[Sample]):
        samples = sorted((s for s in samples if s.valid()), key=lambda s: s.t)
        self.t = np.fromiter((s.t for s in samples), dtype=np.int64, count=len(samples))
        self.x = np.fromiter((s.x for s in samples), dtype=np.float64, count=len(samples))
        self.y = np.fromiter((s.y for s in samples), dtype=np.float64, count=len(samples))
        self.facing = np.fromiter(
            (s.facing % (2 * math.pi) for s in samples), dtype=np.float64, count=len(samples)
        )
        self.hp = np.fromiter((s.hp for s in samples), dtype=np.int64, count=len(samples))
        self.max_hp = np.fromiter((s.max_hp for s in samples), dtype=np.int64, count=len(samples))

    def __len__(self) -> int:
        return len(self.t)

    @property
    def first(self) -> int:
        return int(self.t[0])

    @property
    def last(self) -> int:
        return int(self.t[-1])

    def pose(self, t: float) -> Pose:
        return self.pose_span(t, 0, len(self.t))

    def pose_span(self, t: float, lo: int, hi: int) -> Pose:
        """Interpolate using samples ``[lo, hi)`` only. Outside that span the pose holds.

        Position and facing blend. Facing takes the short arc. Health stays on the earlier sample.
        """
        ts = self.t
        i = int(np.searchsorted(ts, t, side="right"))
        if i <= lo:
            j = lo
            x, y = self.x[j], self.y[j]
            facing = float(self.facing[j])
        elif i >= hi:
            j = hi - 1
            x, y = self.x[j], self.y[j]
            facing = float(self.facing[j])
        else:
            j = i - 1
            t0, t1 = ts[j], ts[i]
            f = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
            x = self.x[j] * (1 - f) + self.x[i] * f
            y = self.y[j] * (1 - f) + self.y[i] * f
            facing = _lerp_angle(float(self.facing[j]), float(self.facing[i]), f)
        return Pose(float(x), float(y), facing, int(self.hp[j]), int(self.max_hp[j]))

    def position(self, t: float) -> tuple[float, float]:
        p = self.pose(t)
        return p.x, p.y


# Samples of 1 HP right after UNIT_DIED are the corpse. A later sample with more health,
# separated from that corpse by a long hole, is the same guid coming back.
_RETURN_GAP_MS = 5000
# A player sample at or below this after UNIT_DIED is the corpse, not a battle res.
_CORPSE_HP = 1


class Tracks:
    """Position lookup for every unit of a fight, plus presence (spawn/despawn) and death info."""

    def __init__(self, data: FightData):
        self.data = data
        if not isinstance(data.fight.duration_ms, int) or not 0 <= data.fight.duration_ms <= 2**63 - 1:
            raise ValueError("战斗时长必须为有效的非负毫秒数")
        for aid, samples in data.samples.items():
            check_cancelled()
            valid = [s for s in samples if s.valid()]
            if len(valid) != len(samples):
                data.diagnose("已忽略无效坐标或资源样本", len(samples) - len(valid))
                data.samples[aid] = valid
        events = [e for e in data.events if isinstance(e.t, int) and 0 <= e.t <= 2**63 - 1]
        if len(events) != len(data.events):
            data.diagnose("已忽略无效事件时间", len(data.events) - len(events))
        data.events = sorted(events, key=lambda e: e.t)
        data.markers = [m for m in data.markers if all(math.isfinite(v) for v in (m.x, m.y, m.start, m.end))]
        self.tracks: dict[int, Track] = {
            aid: Track(samples) for aid, samples in data.samples.items() if samples
        }
        self.derived: dict[int, Track] = {}
        self._derived_spans: dict[int, tuple[int, int]] = {}
        self.deaths: dict[int, list[int]] = {}
        self.spawn: dict[int, int] = {}
        self.first_seen: dict[int, int] = {}
        self.last_seen: dict[int, int] = {}
        for e in data.events:
            for aid in (e.src, e.dst):
                if aid >= 0:
                    if aid not in self.first_seen:
                        self.first_seen[aid] = e.t
                    self.last_seen[aid] = max(self.last_seen.get(aid, e.t), e.t)
            if e.type == "UNIT_DIED" and e.dst >= 0:
                self.deaths.setdefault(e.dst, []).append(e.t)
            elif e.type == "SPELL_SUMMON" and e.dst >= 0:
                self.spawn.setdefault(e.dst, e.t)
        for aid, tr in self.tracks.items():
            self.first_seen[aid] = min(self.first_seen.get(aid, tr.first), tr.first)
            self.last_seen[aid] = max(self.last_seen.get(aid, tr.last), tr.last)
        self._returns: dict[int, tuple[tuple[int, int], ...]] = {
            aid: spans for aid, deaths in self.deaths.items() if (spans := self._return_spans(aid, deaths))
        }
        # (sample_lo, sample_hi, death_time or None) per life. While dead, the pose holds
        # at the last sample of that life instead of sliding toward the battle-res sample.
        self._player_lives: dict[int, tuple[tuple[int, int, int | None], ...]] = {}
        for aid, deaths in self.deaths.items():
            actor = data.actors.get(aid)
            tr = self.tracks.get(aid)
            if actor is not None and actor.is_player and tr is not None and deaths:
                self._player_lives[aid] = _player_lives(tr, deaths)

    def has(self, actor_id: int) -> bool:
        return actor_id in self.derived or actor_id in self.tracks

    def track(self, actor_id: int) -> Track | None:
        return self.derived.get(actor_id, self.tracks.get(actor_id))

    def observed_track(self, actor_id: int) -> Track | None:
        """The source samples, unaffected by boss-owned predictions."""
        return self.tracks.get(actor_id)

    def set_derived(self, actor_id: int, track: Track, *, active_span: tuple[int, int] | None = None) -> None:
        """Use a prediction for replay queries while retaining the original observations."""
        if actor_id not in self.data.actors:
            raise ValueError("派生轨迹的单位未登记")
        if not len(track):
            raise ValueError("派生轨迹不能为空")
        if active_span is not None and active_span[0] > active_span[1]:
            raise ValueError("派生轨迹的有效区间起点不能晚于终点")
        self.derived[actor_id] = track
        if active_span is None:
            self._derived_spans.pop(actor_id, None)
        else:
            self._derived_spans[actor_id] = active_span

    def pose(self, actor_id: int, t: float) -> Pose | None:
        if not math.isfinite(t):
            return None
        tr = self.track(actor_id)
        if tr is None:
            return None
        lives = self._player_lives.get(actor_id)
        if not lives:
            position = tr.pose(t)
            actor = self.data.actors.get(actor_id)
            observed = self.observed_track(actor_id)
            if actor is not None and actor.is_player and actor_id in self.derived and observed is not None:
                health = observed.pose(t)
                position.hp, position.max_hp = health.hp, health.max_hp
            return position
        if actor_id in self.derived:
            # Life boundaries and health come from observations; indices belong to the prediction.
            observed = self.tracks[actor_id]
            for i, (lo, hi, death) in enumerate(lives):
                nxt = _next_life_start(observed, lives, i)
                if death is not None and t >= death and nxt is not None and t >= nxt:
                    continue
                start = int(observed.t[lo]) if lo < hi else 0
                end = death if death is not None else None
                d_lo = int(np.searchsorted(tr.t, start, side="left"))
                d_hi = int(np.searchsorted(tr.t, end, side="right")) if end is not None else len(tr)
                if d_hi <= d_lo:
                    return _pose_player(observed, lives, t)
                position = tr.pose_span(min(t, death) if death is not None else t, d_lo, d_hi)
                health = _pose_player(observed, lives, t)
                if health is not None:
                    position.hp, position.max_hp = health.hp, health.max_hp
                return position
            return _pose_player(observed, lives, t)
        return _pose_player(tr, lives, t)

    def position(self, actor_id: int, t: float) -> tuple[float, float] | None:
        pose = self.pose(actor_id, t)
        return (pose.x, pose.y) if pose is not None else None

    def appear_time(self, actor_id: int) -> int:
        return self.spawn.get(actor_id, self.first_seen.get(actor_id, 0))

    def death_time(self, actor_id: int) -> int | None:
        d = self.deaths.get(actor_id)
        return d[-1] if d else None

    def _return_spans(self, actor_id: int, deaths: list[int]) -> tuple[tuple[int, int], ...]:
        """Lives that start after a death: ``(first sample, last sample)`` of each one."""
        tr = self.tracks.get(actor_id)
        if tr is None:
            return ()
        spans: list[tuple[int, int]] = []
        for i, death in enumerate(deaths):
            nxt = deaths[i + 1] if i + 1 < len(deaths) else None
            i0 = int(np.searchsorted(tr.t, death, side="right"))
            i1 = int(np.searchsorted(tr.t, nxt, side="right")) if nxt is not None else len(tr)
            start: int | None = None
            prev = 0
            for j in range(i0, i1):
                ts = int(tr.t[j])
                if start is None:
                    if int(tr.hp[j]) > 1:
                        start = prev = ts
                    continue
                if ts - prev > _RETURN_GAP_MS:
                    spans.append((start, prev))
                    start = ts if int(tr.hp[j]) > 1 else None
                prev = ts if start is not None else prev
            if start is not None:
                spans.append((start, prev))
        return tuple(spans)

    def present(self, actor_id: int, t: float, grace_ms: int = 1500) -> bool:
        """Whether a non-player unit exists at time t (between spawn and death / last activity).

        ``UNIT_DIED`` closes the current life. The combat log keeps writing the corpse at 1 HP,
        then the same guid can come back with a new health pool; those later samples count as present.
        Without a death event, a derived track can extend the inferred presence beyond raw activity.
        An explicit derived active span is authoritative and does not add the activity grace period.
        """
        actor = self.data.actors.get(actor_id)
        if actor is not None and actor.kind is ActorKind.PLAYER:
            return True
        active_span = self._derived_spans.get(actor_id)
        if active_span is not None and not active_span[0] <= t <= active_span[1]:
            return False
        if t < self.appear_time(actor_id):
            return False
        deaths = self.deaths.get(actor_id)
        if not deaths:
            if active_span is not None:
                return True
            last = self.last_seen.get(actor_id, -1)
            derived = self.derived.get(actor_id)
            if derived is not None and len(derived):
                last = max(last, derived.last)
            return t <= last + grace_ms
        idx = bisect.bisect_right(deaths, t) - 1
        if idx < 0 or t <= deaths[idx]:
            return True
        for start, last in self._returns.get(actor_id, ()):
            if start > deaths[idx] and start <= t <= last + grace_ms:
                return True
        return False

    def is_dead(self, actor_id: int, t: float) -> bool:
        lives = self._player_lives.get(actor_id)
        if lives is not None:
            tr = self.tracks[actor_id]
            return _player_dead(tr, lives, t)
        deaths = self.deaths.get(actor_id)
        if not deaths:
            return False
        before = [d for d in deaths if d <= t]
        if not before:
            return False
        tr = self.tracks.get(actor_id)
        if tr is None:
            return True
        # Alive again if a later sample (before t) shows health.
        i0 = int(np.searchsorted(tr.t, before[-1], side="right"))
        i1 = int(np.searchsorted(tr.t, t, side="right"))
        return not bool(np.any(tr.hp[i0:i1] > 0))

    def bounds(
        self, actor_ids: list[int], lo: float = 1.0, hi: float = 99.0
    ) -> tuple[float, float, float, float]:
        """(x_min, x_max, y_min, y_max) percentile bounds of the given units' samples."""
        xs = [self.tracks[a].x for a in actor_ids if a in self.tracks]
        ys = [self.tracks[a].y for a in actor_ids if a in self.tracks]
        if not xs:
            return (-50.0, 50.0, -50.0, 50.0)
        x = np.concatenate(xs)
        y = np.concatenate(ys)
        return (
            float(np.percentile(x, lo)),
            float(np.percentile(x, hi)),
            float(np.percentile(y, lo)),
            float(np.percentile(y, hi)),
        )


def _player_lives(tr: Track, deaths: list[int]) -> tuple[tuple[int, int, int | None], ...]:
    """One entry per life: ``(sample_lo, sample_hi, death_time or None)``.

    Samples at or below corpse health after a death are left out, so the next life
    starts at the battle-res sample instead of sliding across the gap.
    """
    n = len(tr)
    lives: list[tuple[int, int, int | None]] = []
    lo = 0
    for i, death in enumerate(deaths):
        nxt = deaths[i + 1] if i + 1 < len(deaths) else None
        hi = int(np.searchsorted(tr.t, death, side="right"))
        if hi < lo:
            hi = lo
        lives.append((lo, hi, death))
        limit = int(np.searchsorted(tr.t, nxt, side="right")) if nxt is not None else n
        j = hi
        while j < limit and int(tr.hp[j]) <= _CORPSE_HP:
            j += 1
        lo = j
    if lo < n:
        lives.append((lo, n, None))
    return tuple(lives)


def _next_life_start(tr: Track, lives: tuple[tuple[int, int, int | None], ...], i: int) -> int | None:
    n = len(tr)
    for lo, hi, _death in lives[i + 1 :]:
        if lo < hi and lo < n:
            return int(tr.t[lo])
    return None


def _hold_pose(tr: Track, lives: tuple[tuple[int, int, int | None], ...], i: int) -> Pose | None:
    for lo, hi, _death in reversed(lives[: i + 1]):
        if hi > lo:
            j = hi - 1
            return Pose(float(tr.x[j]), float(tr.y[j]), float(tr.facing[j]), int(tr.hp[j]), int(tr.max_hp[j]))
    return None


def _pose_player(tr: Track, lives: tuple[tuple[int, int, int | None], ...], t: float) -> Pose | None:
    for i, (lo, hi, death) in enumerate(lives):
        if death is not None and t >= death:
            nxt = _next_life_start(tr, lives, i)
            if nxt is None or t < nxt:
                return _hold_pose(tr, lives, i)
            continue
        if hi <= lo:
            return None
        return tr.pose_span(t, lo, hi)
    return tr.pose(t)


def _player_dead(tr: Track, lives: tuple[tuple[int, int, int | None], ...], t: float) -> bool:
    for i, (_lo, _hi, death) in enumerate(lives):
        if death is None or t < death:
            return False
        nxt = _next_life_start(tr, lives, i)
        if nxt is None or t < nxt:
            return True
    return False


def _lerp_angle(a: float, b: float, f: float) -> float:
    """Blend two facings along the short arc. A turn past half a circle wraps the other way."""
    delta = (b - a + math.pi) % (2 * math.pi) - math.pi
    return a + delta * f


def distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def angle_to(a: tuple[float, float], b: tuple[float, float]) -> float:
    """WoW facing (0 = +x / north, counter-clockwise towards +y / west) pointing from a to b."""
    return math.atan2(b[1] - a[1], b[0] - a[0]) % (2 * math.pi)
