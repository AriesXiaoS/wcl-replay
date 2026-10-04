# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Phase 1 (Zul'jan): globules / mutations on the floor, carriers, Sever, Guillotine, Deluge.

Globule model, as observed in the log:
- a floor orb is one place-cast (``GREEN_PLACE`` / ``PURPLE_PLACE``), not one actor id. The cast
  carries the coordinates. A wave orb also has a summon a few dozen milliseconds earlier; a drop
  (the new unit that appears when a carry ends) is the same cast with no summon. WCL reuses
  ``sourceInstance`` for that drop, so both sources identify each orb by its place-cast,
- a pickup is a carry debuff on a player (the floor unit gets no event, so the nearest one of that
  color is taken), and when the 5 s debuff ends a *new* unit appears under the player (the drop),
- Sever gives the whole raid one Rupture stack per popped globule, so the popped count is exact;
  which globules popped is chosen geometrically: those in front of Zul'jan towards the tank.
- P3 凋零撕裂 uses that same Rupture signal, with the longer frontal range.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ...core.models import FightData
from ...core.tracks import Tracks, angle_to, distance
from ..base import (
    Cell,
    Circle,
    Cone,
    Dot,
    HudLine,
    Interval,
    Intervals,
    Lane,
    LaneItem,
    LogEntry,
    Path,
    Prim,
    Seg,
    StatusSection,
    UnitRing,
    UnitTag,
    aura_intervals,
    fmt_secs,
    fmt_time,
)
from . import constants as C
from .common import angle_diff, events_between, movement_times, name_seg, short, stack_rounds, trail_points

COLOR_OF = {"green": C.C_GREEN, "purple": C.C_PURPLE}
WORD_OF = {"green": "绿球", "purple": "紫球"}
_PLACE = {C.GREEN_PLACE: "green", C.PURPLE_PLACE: "purple"}
_SUMMON = {C.GREEN_SUMMON: "green", C.PURPLE_SUMMON: "purple"}
# Summon-to-cast gap on the Oct 2 pull: local max 55 ms, WCL max 42 ms.
_SPAWN_WINDOW_MS = 100


@dataclass(slots=True)
class _Place:
    t: int
    src: int
    color: str
    x: float
    y: float


def globules_of(data: FightData, tracks: Tracks) -> list[Globule]:
    """Floor orbs from place-casts. A summon within ``_SPAWN_WINDOW_MS`` marks a wave orb.

    Known summon targets match their own place-cast before target-less summons use timing alone.
    Drops have no summon. WCL stamps the drop with an instance id that already belongs to an
    earlier orb, so the same actor can own several of these casts; each cast is still its own orb.
    """
    casts: list[_Place] = []
    for e in data.events:
        color = _PLACE.get(e.spell_id)
        if color is None or e.type != "SPELL_CAST_SUCCESS" or e.src < 0:
            continue
        pos = tracks.position(e.src, e.t)
        if pos is None:
            continue
        casts.append(_Place(e.t, e.src, color, pos[0], pos[1]))
    summons = [e for e in data.events if e.type == "SPELL_SUMMON" and e.spell_id in _SUMMON]
    pairs: list[tuple[bool, int, int, int]] = []
    for si, summon in enumerate(summons):
        color = _SUMMON[summon.spell_id]
        target_less = summon.dst < 0
        for i, cast in enumerate(casts):
            if not target_less and summon.dst != cast.src:
                continue
            dt = cast.t - summon.t
            if cast.color == color and 0 <= dt <= _SPAWN_WINDOW_MS:
                # Resolve known identities first; a closer target-less summon must not steal them.
                pairs.append((target_less, dt, si, i))
    pairs.sort()
    spawn_at: dict[int, int] = {}
    used: set[int] = set()
    for _target_less, _dt, si, i in pairs:
        if si in used or i in spawn_at:
            continue
        used.add(si)
        spawn_at[i] = summons[si].t
    out = [
        Globule(
            cast.src,
            cast.color,
            cast.x,
            cast.y,
            spawn_at.get(i, cast.t),
            origin="spawn" if i in spawn_at else "drop",
        )
        for i, cast in enumerate(casts)
    ]
    out.sort(key=lambda g: g.start)
    return out


@dataclass(slots=True, eq=False)
class Globule:
    aid: int
    color: str
    x: float
    y: float
    start: int
    end: int | None = None
    origin: str = "spawn"  # spawn | drop
    dropped_by: int = -1
    picked_by: int = -1
    popped_by: int | None = None  # sever index, -1 = phase transition
    end_reason: str = ""

    def on_floor(self, t: float) -> bool:
        return self.start <= t and (self.end is None or t < self.end)


@dataclass(slots=True, eq=False)
class Carry:
    player: int
    color: str
    start: int
    end: int
    src: int = -1  # the globule that applied the carry debuff, when the log names it
    source: Globule | None = None
    dropped: Globule | None = None


@dataclass(slots=True, eq=False)
class SeverRec:
    idx: int
    cast_start: int
    t: int
    target: int
    bx: float
    by: float
    direction: float
    stacks: int = 0
    reach: float = C.SEVER_RANGE
    floor_before: int = 0
    floor_after: int = 0
    popped: list[Globule] = field(default_factory=list)
    inferred_pops: int = 0


class P1Model:
    def __init__(self, data: FightData, tracks: Tracks, times: list[int], p2_start: int | None):
        self.data = data
        self.tracks = tracks
        self.times = times
        self.p2_start = p2_start
        self.end_t = p2_start if p2_start is not None else data.fight.duration_ms
        self.boss = next((a.id for a in data.actors_by_npc(C.NPC_ZULJAN)), -1)
        self.globules = self._globules()
        self.carries = self._carries()
        self.severs = self._severs()
        self.blighted = self._blighted_severs()
        self.guillotines = aura_intervals(data, {C.GUILLOTINE, C.GRIM_GUILLOTINE})
        self.guillotine_hits = self._guillotine_hits()
        self.deluges = [e.t for e in data.events if e.spell_id == C.DELUGE and e.type == "SPELL_CAST_SUCCESS"]
        self.phase_pop_stacks = 0
        self.phase_popped: list[Globule] = []
        self._simulate()
        self.carry_iv = Intervals([_iv(c) for c in self.carries])
        self._routes = self._carry_routes()

    # -- extraction ---------------------------------------------------------------------------

    def _globules(self) -> list[Globule]:
        return globules_of(self.data, self.tracks)

    def _carries(self) -> list[Carry]:
        ivs = aura_intervals(self.data, {C.GREEN_CARRY, C.PURPLE_CARRY})
        return [
            Carry(iv.actor, "green" if iv.payload == C.GREEN_CARRY else "purple", iv.start, iv.end, iv.src)
            for iv in ivs
        ]

    def _severs(self) -> list[SeverRec]:
        return self._frontal_casts(C.SEVER, lambda e: self._sever_target(e.t), C.SEVER_RANGE)

    def _blighted_severs(self) -> list[SeverRec]:
        return self._frontal_casts(
            C.BLIGHTED_SEVER,
            lambda e: e.dst if self._is_player(e.dst) else self._blighted_target(e.t),
            C.BLIGHTED_SEVER_RANGE,
        )

    def _frontal_casts(self, spell_id: int, target_of, reach: float) -> list[SeverRec]:
        out = []
        last_start = None
        for e in self.data.events:
            if e.spell_id != spell_id or e.src != self.boss:
                continue
            if e.type == "SPELL_CAST_START":
                last_start = e.t
            elif e.type == "SPELL_CAST_SUCCESS":
                target = target_of(e)
                bpos = self.tracks.position(self.boss, e.t) or (0.0, 0.0)
                tpos = self.tracks.position(target, e.t) if target >= 0 else None
                pose = self.tracks.pose(self.boss, e.t)
                direction = angle_to(bpos, tpos) if tpos else (pose.facing if pose else 0.0)
                rec = SeverRec(
                    len(out),
                    last_start if last_start is not None else e.t - 3000,
                    e.t,
                    target,
                    bpos[0],
                    bpos[1],
                    direction,
                    reach=reach,
                )
                rec.stacks = stack_rounds(self.data, self.times, C.RUPTURE, e.t - 30, e.t + 300)
                out.append(rec)
                last_start = None
        return out

    def _blighted_target(self, t: int) -> int:
        for e in events_between(self.data, self.times, t - 20, t + 300):
            if (
                e.spell_id == C.BLIGHTED_SEVER
                and e.type in ("SPELL_AURA_APPLIED", "SPELL_DAMAGE")
                and self._is_player(e.dst)
            ):
                return e.dst
        return -1

    def _is_player(self, aid: int) -> bool:
        actor = self.data.actors.get(aid)
        return actor is not None and actor.is_player

    def _sever_target(self, t: int) -> int:
        for e in events_between(self.data, self.times, t - 20, t + 300):
            if (
                e.spell_id in (C.SEVER_DEBUFF, C.SEVER)
                and e.type in ("SPELL_AURA_APPLIED", "SPELL_DAMAGE")
                and e.dst >= 0
            ):
                return e.dst
        return -1

    def _soak_done(self, iv: Interval) -> bool:
        """The mark fell. An aura still up at the wipe is clamped to the fight end and has no result."""
        return iv.end < self.data.fight.duration_ms

    def _guillotine_hits(self) -> dict[int, list[int]]:
        """Guillotine mark start -> players hit by the soak."""
        hits: dict[int, list[int]] = {}
        for iv in self.guillotines:
            got = []
            hit_id = C.GRIM_GUILLOTINE_HIT if iv.payload == C.GRIM_GUILLOTINE else C.GUILLOTINE_HIT
            for e in events_between(self.data, self.times, iv.end - 50, iv.end + 500):
                if e.spell_id == hit_id and e.type in ("SPELL_DAMAGE", "SPELL_MISSED") and e.dst not in got:
                    got.append(e.dst)
            hits[iv.start] = got
        return hits

    # -- state machine ------------------------------------------------------------------------

    def _match_drops(self) -> None:
        pairs: list[tuple[float, int, int, int]] = []
        for gi, globule in enumerate(self.globules):
            if globule.origin != "drop":
                continue
            options = []
            for ci, carry in enumerate(self.carries):
                dt = globule.start - carry.end
                if carry.dropped is not None or carry.color != globule.color or not -100 <= dt <= 400:
                    continue
                position = self.tracks.position(carry.player, carry.end)
                separation = distance(position, (globule.x, globule.y)) if position else math.inf
                if separation <= C.PICKUP_MAX_DIST or position is None:
                    options.append((separation, abs(dt), ci))
            options.sort()
            if len(options) == 1 or (len(options) > 1 and options[0][:2] != options[1][:2]):
                pairs.extend((separation, dt, gi, ci) for separation, dt, ci in options)
        matched: set[int] = set()
        for _separation, _dt, gi, ci in sorted(pairs):
            globule, carry = self.globules[gi], self.carries[ci]
            if gi not in matched and carry.dropped is None:
                carry.dropped = globule
                globule.dropped_by = carry.player
                matched.add(gi)

    def _simulate(self) -> None:
        self._match_drops()
        queue: list[tuple[int, int, str, object]] = []
        queue += [(g.start, 0, "appear", g) for g in self.globules]
        queue += [(c.start, 1, "pickup", c) for c in self.carries]
        queue += [(s.t, 2, "sever", s) for s in self.severs]
        queue += [(s.t, 2, "blighted", s) for s in self.blighted]
        if self.p2_start is not None:
            queue.append((self.p2_start, 3, "phase", None))
        queue.sort(key=lambda q: (q[0], q[1]))
        floor: list[Globule] = []
        for t, _o, kind, obj in queue:
            if kind == "appear":
                floor.append(obj)
            elif kind == "pickup":
                c: Carry = obj
                ppos = self.tracks.position(c.player, t)
                cands = [g for g in floor if g.color == c.color]
                named = next((g for g in cands if g.aid == c.src), None)
                if named is not None:
                    g = named
                elif cands and ppos is not None:
                    g = min(cands, key=lambda g_: distance((g_.x, g_.y), ppos))
                    if distance((g.x, g.y), ppos) > C.PICKUP_MAX_DIST:
                        continue
                else:
                    continue
                floor.remove(g)
                g.end, g.picked_by, g.end_reason = t, c.player, "picked"
                c.source = g
            elif kind in ("sever", "blighted"):
                s: SeverRec = obj
                s.floor_before = len(floor)
                popped = self._pick_popped(s, floor)
                reason = "popped" if kind == "sever" else "blighted"
                for g in popped:
                    floor.remove(g)
                    g.end, g.popped_by, g.end_reason = t, s.idx, reason
                s.popped = popped
                s.floor_after = len(floor)
            elif kind == "phase":
                self.phase_pop_stacks = stack_rounds(self.data, self.times, C.RUPTURE, t - 100, t + 600)
                for g in floor:
                    g.end, g.popped_by, g.end_reason = t, -1, "phase"
                self.phase_popped = list(floor)
                floor.clear()
        for g in floor:
            g.end = None

    def _pick_popped(self, s: SeverRec, floor: list[Globule]) -> list[Globule]:
        half = C.SEVER_CONE_DEG / 2
        scored = []
        for g in floor:
            dev = angle_diff(angle_to((s.bx, s.by), (g.x, g.y)), s.direction)
            scored.append((dev, distance((s.bx, s.by), (g.x, g.y)), g))
        scored.sort(key=lambda it: it[0])
        in_cone = [g for dev, d, g in scored if math.degrees(dev) <= half and d <= s.reach]
        n = s.stacks
        chosen = in_cone[:n]
        if len(chosen) < n:
            inferred = [g for _dev, _d, g in scored if g not in chosen][: n - len(chosen)]
            s.inferred_pops = len(inferred)
            chosen += inferred
        return chosen

    # -- queries ------------------------------------------------------------------------------

    def floor_at(self, t: float) -> list[Globule]:
        return [g for g in self.globules if g.on_floor(t)]

    def floor_series(self) -> list[tuple[int, float]]:
        changes: list[tuple[int, int]] = []
        for g in self.globules:
            changes.append((g.start, 1))
            if g.end is not None:
                changes.append((g.end, -1))
        changes.sort()
        out: list[tuple[int, float]] = [(0, 0.0)]
        n = 0
        for t, d in changes:
            n += d
            out.append((t, float(n)))
        return out

    def next_sever(self, t: float) -> SeverRec | None:
        return next((s for s in self.severs if s.t > t), None)

    # -- outputs ------------------------------------------------------------------------------

    def lanes(self) -> list[Lane]:
        globule_items = [LaneItem(t, shape="circle", label="新一波") for t in self.deluges]
        sever_items = [
            LaneItem(s.t, shape="tick", label=f"撕裂 → {short(self.data, s.target)} +{s.stacks}")
            for s in self.severs
        ]
        guil_items = [
            LaneItem(
                iv.start,
                iv.end,
                shape="triangle",
                label=f"{'冷酷处斩' if iv.payload == C.GRIM_GUILLOTINE else '处斩'} → {short(self.data, iv.actor)}",
            )
            for iv in self.guillotines
        ]
        return [
            Lane(
                "globules",
                "毒液球",
                C.C_GREEN,
                globule_items,
                self.floor_series(),
                help="地上的球数量；圆圈 = 剧毒洪流新一波。地图上：小绿点=绿球，大紫点=紫球，玩家外圈=手上球剩余时间",
            ),
            Lane(
                "severs",
                "撕裂",
                C.C_SEVER,
                sever_items,
                help="撕裂（坦克）：引爆 boss 朝坦克方向扇形内的球，每个球全团 +1 毒液爆裂",
            ),
            Lane("guillotine", "处斩", C.C_GUILLOTINE, guil_items, help="分摊圈，跟点名玩家走，半径 9 码"),
        ]

    def log(self) -> list[LogEntry]:
        d = self.data
        out: list[LogEntry] = []
        for c in self.carries:
            out.append(
                LogEntry(
                    c.start,
                    [
                        name_seg(d, c.player),
                        Seg(" 拾取了 "),
                        Seg(WORD_OF[c.color], COLOR_OF[c.color], badge=True),
                    ],
                    "globules",
                )
            )
        for g in self.globules:
            if g.origin != "drop" or g.dropped_by < 0:
                continue
            out.append(
                LogEntry(
                    g.start,
                    [
                        name_seg(d, g.dropped_by),
                        Seg(" 放下了 "),
                        Seg(WORD_OF[g.color], COLOR_OF[g.color], badge=True),
                    ],
                    "globules",
                    sub=[self._fate(g)],
                )
            )
        for s in self.severs:
            segs = [
                Seg("撕裂", C.C_SEVER, badge=True),
                Seg(" → "),
                name_seg(d, s.target),
                Seg(f" · +{s.stacks} 毒液爆裂 · 地面 {s.floor_before} → {s.floor_after}"),
            ]
            untouched = sum(1 for g in s.popped if g.origin == "spawn")
            if untouched:
                segs.append(Seg(f" · {untouched} 个未拾取的被引爆", "#e8a33d"))
            if s.inferred_pops:
                segs.append(Seg(f" · {s.inferred_pops} 个球位置由层数补推，扇形观测不足", "#e8a33d"))
            out.append(LogEntry(s.t, segs, "severs"))
        for s in self.blighted:
            segs = [
                Seg("凋零撕裂", C.C_SEVER, badge=True),
                Seg(" → "),
                name_seg(d, s.target),
                Seg(f" · +{s.stacks} 毒液爆裂 · 地面 {s.floor_before} → {s.floor_after}"),
            ]
            if s.inferred_pops:
                segs.append(Seg(f" · {s.inferred_pops} 个球位置由层数补推，扇形观测不足", "#e8a33d"))
            out.append(LogEntry(s.t, segs, "severs"))
        for t in self.deluges:
            wave = [g for g in self.globules if g.origin == "spawn" and t <= g.start <= t + 6000]
            ng = sum(1 for g in wave if g.color == "green")
            out.append(
                LogEntry(
                    t,
                    [
                        Seg("剧毒洪流", C.C_DELUGE, badge=True),
                        Seg(f" 新一波：{ng} 绿球 · {len(wave) - ng} 紫球"),
                    ],
                    "globules",
                )
            )
        for iv in self.guillotines:
            hits = self.guillotine_hits.get(iv.start, [])
            name = "冷酷处斩" if iv.payload == C.GRIM_GUILLOTINE else "处斩"
            out.append(
                LogEntry(
                    iv.start,
                    [
                        Seg(name, C.C_GUILLOTINE, badge=True),
                        Seg(" → "),
                        name_seg(d, iv.actor),
                        Seg(f" · {len(hits)} 人分摊" if self._soak_done(iv) else ""),
                    ],
                    "guillotine",
                )
            )
        if self.p2_start is not None:
            out.append(
                LogEntry(
                    self.p2_start,
                    [
                        Seg("阶段二", C.C_PHASE, badge=True),
                        Seg(
                            f" 地面全部引爆（{len(self.phase_popped)} 个）：+{self.phase_pop_stacks} 毒液爆裂"
                        ),
                    ],
                )
            )
        return out

    def _fate(self, g: Globule) -> Seg:
        if g.end_reason == "popped" and g.popped_by is not None and g.popped_by >= 0:
            return Seg(f"被 {fmt_time(self.severs[g.popped_by].t)} 的撕裂引爆", C.C_SEVER, badge=True)
        if g.end_reason == "blighted" and g.popped_by is not None and g.popped_by >= 0:
            return Seg(f"被 {fmt_time(self.blighted[g.popped_by].t)} 的凋零撕裂引爆", C.C_SEVER, badge=True)
        if g.end_reason == "picked":
            return Seg(f"{fmt_time(g.end)} 被 {short(self.data, g.picked_by)} 拾取", "#99aadd", badge=True)
        if g.end_reason == "phase":
            return Seg("转阶段时引爆", C.C_PHASE, badge=True)
        return Seg("留在地上", "#888888", badge=True)

    def _carry_routes(self) -> list[tuple[Carry, tuple[tuple[int, float, float], ...]]]:
        out = []
        for c in self.carries:
            pts = movement_times(self.tracks.track(c.player), c.start, c.end)
            if pts:
                out.append((c, pts))
        return out

    def route_overlays(self, t: float) -> list[Path]:
        """Dotted trail of a carry, grown up to t and removed the moment the debuff drops."""
        out = []
        for c, samples in self._routes:
            if not c.start <= t < c.end:
                continue
            pts = trail_points(samples, t, self.tracks.position(c.player, t))
            if pts:
                out.append(Path(pts, COLOR_OF[c.color], C.PATH_WIDTH, C.PATH_ALPHA, True, "globules"))
        return out

    def overlays(self, t: float) -> list[Prim]:
        out: list[Prim] = []
        nxt = self.next_sever(t - 600)
        cone = self._frontal(nxt, t, C.SEVER_RANGE, "撕裂") if nxt is not None else None
        if cone is not None:
            out.append(cone)
        for rec in self.blighted:
            cone = self._frontal(rec, t, C.BLIGHTED_SEVER_RANGE, "凋零撕裂")
            if cone is not None:
                out.append(cone)
        frontals = (*self.severs, *self.blighted)
        doomed = {id(g) for s in frontals if s.cast_start <= t < s.t for g in s.popped}
        for g in self.globules:
            if g.on_floor(t):
                # Purple mutations read larger on the ground than the green globules.
                size = 20 if g.color == "purple" else 12
                out.append(
                    Dot(
                        g.x,
                        g.y,
                        COLOR_OF[g.color],
                        size,
                        glow=id(g) in doomed,
                        dashed_ring=g.origin == "drop",
                        layer="globules",
                        stack="orb",
                    )
                )
        for iv in self.carry_iv.active(t):
            c: Carry = iv.payload
            frac = 1 - (t - c.start) / max(1, c.end - c.start)
            out.append(UnitRing(c.player, COLOR_OF[c.color], frac, layer="globules"))
        for iv in self.guillotines.active(t):
            pos = self.tracks.position(iv.actor, t)
            if pos:
                name = "冷酷处斩" if iv.payload == C.GRIM_GUILLOTINE else "处斩"
                out.append(
                    Circle(
                        pos[0],
                        pos[1],
                        C.GUILLOTINE_RADIUS,
                        C.C_GUILLOTINE,
                        0.18,
                        0.9,
                        1.5,
                        True,
                        f"{name} {fmt_secs(iv.end - t)}",
                        layer="guillotine",
                    )
                )
                out.append(UnitTag(iv.actor, "分摊", C.C_GUILLOTINE, layer="guillotine"))
        for iv in self.guillotines:
            if iv.end <= t < iv.end + 900 and self._soak_done(iv):
                pos = self.tracks.position(iv.actor, iv.end)
                if pos:
                    out.append(
                        Circle(
                            pos[0],
                            pos[1],
                            C.GUILLOTINE_RADIUS,
                            C.C_GUILLOTINE,
                            0.4,
                            1.0,
                            2.0,
                            label=f"{len(self.guillotine_hits.get(iv.start, []))} 人",
                            layer="guillotine",
                        )
                    )
        return out

    def _frontal(self, rec: SeverRec, t: float, radius: float, name: str) -> Cone | None:
        if not (rec.t - C.SEVER_PREVIEW_MS <= t <= rec.t + 600):
            return None
        bpos = self.tracks.position(self.boss, t) or (rec.bx, rec.by)
        tpos = self.tracks.position(rec.target, t) if rec.target >= 0 else None
        direction = angle_to(bpos, tpos) if (tpos and t < rec.t) else rec.direction
        casting = t >= rec.cast_start
        return Cone(
            bpos[0],
            bpos[1],
            direction,
            C.SEVER_CONE_DEG,
            radius,
            C.C_SEVER,
            fill_alpha=0.22 if casting else 0.08,
            line_alpha=0.8 if casting else 0.4,
            dashed=not casting,
            label=f"{name} {fmt_secs(rec.t - t)}" if t < rec.t else name,
            layer="severs",
        )

    def hud(self, t: float) -> list[HudLine]:
        out = [HudLine("P1 · 祖尔加", C.C_PHASE, key="phase")]
        nxt = self.next_sever(t)
        if nxt:
            out.append(
                HudLine(
                    f"撕裂 {fmt_secs(nxt.t - t)} → {short(self.data, nxt.target)}", C.C_SEVER, key="sever"
                )
            )
        if self.p2_start is not None and t >= self.p2_start:
            nb = next((s for s in self.blighted if s.t > t), None)
            if nb:
                out.append(
                    HudLine(
                        f"凋零撕裂 {fmt_secs(nb.t - t)} → {short(self.data, nb.target)}",
                        C.C_SEVER,
                        key="blighted",
                    )
                )
        nd = next((x for x in self.deluges if x > t), None)
        if nd is not None:
            out.append(HudLine(f"剧毒洪流 {fmt_secs(nd - t)}", C.C_DELUGE, key="deluge"))
        shown = self.guillotines
        if self.p2_start is not None and t < self.p2_start:
            shown = Intervals([iv for iv in self.guillotines if iv.payload != C.GRIM_GUILLOTINE])
        active = shown.active(t)
        if active:
            iv = active[0]
            name = "冷酷处斩" if iv.payload == C.GRIM_GUILLOTINE else "处斩"
            out.append(
                HudLine(
                    f"{name} → {short(self.data, iv.actor)} {fmt_secs(iv.end - t)}",
                    C.C_GUILLOTINE,
                    key="guillotine",
                )
            )
        else:
            ng = next((iv for iv in shown if iv.start > t), None)
            if ng is not None:
                name = "冷酷处斩" if ng.payload == C.GRIM_GUILLOTINE else "处斩"
                out.append(
                    HudLine(
                        f"{name} {fmt_secs(ng.start - t)} → {short(self.data, ng.actor)}",
                        C.C_GUILLOTINE,
                        key="guillotine",
                    )
                )
        return out

    def status(self, t: float) -> list[StatusSection]:
        d = self.data
        secs = []
        nxt = self.next_sever(t)
        sev = StatusSection("撕裂", C.C_SEVER, key="sever")
        if nxt:
            sev.rows.append(
                [
                    Cell("下一次撕裂 →"),
                    Cell(short(d, nxt.target), name_seg(d, nxt.target).color, bold=True),
                    Cell(f"{fmt_secs(nxt.t - t)} 后", "#8b93a1", align="right"),
                ]
            )
        last = next((s for s in reversed(self.severs) if s.t <= t), None)
        if last:
            sev.note = (
                f"上一次 {fmt_time(last.t)}：+{last.stacks} 层，地面 {last.floor_before} → {last.floor_after}"
            )
        secs.append(sev)

        floor = self.floor_at(t)
        hands = [iv.payload for iv in self.carry_iv.active(t)]
        fg = sum(1 for g in floor if g.color == "green")
        hg = sum(1 for c in hands if c.color == "green")
        gl = StatusSection("毒液球", C.C_GREEN, key="orbs")
        gl.rows.append(
            [
                Cell(""),
                Cell("● 绿", C.C_GREEN, align="right"),
                Cell("● 紫", C.C_PURPLE, align="right"),
                Cell("合计", "#8b93a1", align="right"),
            ]
        )
        gl.rows.append(
            [
                Cell("地上"),
                Cell(str(fg), C.C_GREEN, True, "right"),
                Cell(str(len(floor) - fg), C.C_PURPLE, True, "right"),
                Cell(str(len(floor)), None, True, "right"),
            ]
        )
        gl.rows.append(
            [
                Cell("手上"),
                Cell(str(hg), C.C_GREEN, True, "right"),
                Cell(str(len(hands) - hg), C.C_PURPLE, True, "right"),
                Cell(str(len(hands)), None, True, "right"),
            ]
        )
        gl.rows.append(
            [
                Cell("全部", "#8b93a1"),
                Cell(str(fg + hg), C.C_GREEN, align="right"),
                Cell(str(len(floor) + len(hands) - fg - hg), C.C_PURPLE, align="right"),
                Cell(str(len(floor) + len(hands)), align="right"),
            ]
        )
        secs.append(gl)
        if hands:
            carry = StatusSection("手上的球", "#8b93a1", key="carry")
            for c in sorted(hands, key=lambda c_: c_.end):
                left = c.end - t
                carry.rows.append(
                    [
                        name_cell(d, c.player),
                        Cell("", COLOR_OF[c.color], bar=left / max(1, c.end - c.start)),
                        Cell(WORD_OF[c.color], COLOR_OF[c.color], badge=True),
                        Cell(fmt_secs(left), "#8b93a1", align="right"),
                    ]
                )
            secs.append(carry)
        return secs


def name_cell(data: FightData, aid: int) -> Cell:
    s = name_seg(data, aid)
    return Cell(s.text, s.color, bold=True)


def _iv(c: Carry) -> Interval:
    return Interval(c.start, c.end, c.player, -1, c)
