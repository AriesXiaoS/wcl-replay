# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Phase 2 (Hex Lord Malacrass): Dreadmarch ghosts, Gloombomb, Soulcoilers (Wail / kicks / shields).

Ghosts usually log only their spawn position, so the whole path is simulated up front: each one
walks in a straight line toward the player it fixates, and stands still on any step where that
player is looking at it. Constant speed uses one rate the whole way, and after the player looks
away it waits out the pause before the next step moves. Accel speed starts at another rate when
that fixate begins, reaches the end rate over the accel time, then holds it, with no pause. A later
combat-log sample with different coordinates replaces the simulated position at that timestamp.
Resonance does not move them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ...core.cancellation import check_cancelled
from ...core.models import FightData, Sample
from ...core.specs import class_color
from ...core.tracks import Track, Tracks, angle_to, distance
from ..base import (
    Cell,
    Circle,
    Cone,
    HudLine,
    Interval,
    Lane,
    LaneItem,
    Line,
    LogEntry,
    Path,
    Prim,
    Seg,
    StatusSection,
    UnitFlash,
    UnitRing,
    UnitStyle,
    UnitTag,
    aura_intervals,
    fmt_secs,
    fmt_time,
)
from . import constants as C
from .common import angle_diff, color_of, events_between, movement_times, name_seg, short, trail_points


@dataclass(slots=True, eq=False)
class Ghost:
    aid: int
    spawn_t: int
    x0: float
    y0: float
    end_t: int
    from_player: int = -1
    fixates: list[Interval] = field(default_factory=list)

    def target_at(self, t: float) -> int:
        for iv in self.fixates:
            if iv.start <= t < iv.end:
                return iv.actor
        return -1

    def mark_at(self, data: FightData, t: float) -> tuple[str, str] | None:
        """First character of the fixated player's name, colored with their class."""
        actor = data.actors.get(self.target_at(t))
        if actor is None or not actor.short_name:
            return None
        return actor.short_name[:1], class_color(actor.class_name)


@dataclass(slots=True, eq=False)
class FixateEnd:
    t: int
    ghost: int
    player: int
    reason: str  # severed | resonance | died | reached | unknown


@dataclass(slots=True, eq=False)
class Dreadmarch:
    cast_start: int
    t: int
    possessed: list[Interval]
    ghosts: list[Ghost]


@dataclass(slots=True, eq=False)
class WailCast:
    src: int
    start: int
    end: int
    outcome: str  # kicked | went_off | died | unknown
    kicker: int = -1
    feared: int = 0


def _cast_mult(cursed: list[tuple[int, int]], t: float) -> float:
    for start, end in cursed:
        if start <= t < end:
            return C.TONGUES_CAST_MULT
    return 1.0


def wail_remaining_ms(start: int, t: float, base_ms: int, cursed: list[tuple[int, int]]) -> float:
    """Milliseconds left on the cast bar, the way the client redraws it.

    Time already spent is kept. Applying or removing Curse of Tongues only
    rescales the unfinished part, so the remaining time jumps immediately.
    """
    if t <= start:
        return float(base_ms) * _cast_mult(cursed, start)
    points = [float(start), float(t)]
    for a, b in cursed:
        if start < a < t:
            points.append(float(a))
        if start < b < t:
            points.append(float(b))
    points.sort()
    progress = 0.0
    for a, b in zip(points, points[1:], strict=False):
        if b > a:
            progress += (b - a) / _cast_mult(cursed, a)
    left = base_ms - progress
    if left <= 0:
        return 0.0
    return left * _cast_mult(cursed, t)


@dataclass(slots=True, eq=False)
class Bomb:
    t: int  # explosion
    targets: list[int]
    cracked: int = 0
    evidence: str = "aura_end"


class P2Model:
    def __init__(
        self,
        data: FightData,
        tracks: Tracks,
        times: list[int],
        p2_start: int,
        *,
        defer_motion: bool = False,
    ):
        self.data = data
        self.tracks = tracks
        self.times = times
        self.start = p2_start
        self.end_t = data.fight.duration_ms
        self.boss = next((a.id for a in data.actors_by_npc(C.NPC_MALACRASS)), -1)
        self.soulcoilers = [a.id for a in data.actors_by_npc(C.NPC_SOULCOILER)]
        self.player_deaths = sorted(
            (e.t, e.dst)
            for e in data.events
            if e.type == "UNIT_DIED" and e.dst in data.actors and data.actors[e.dst].is_player
        )

        self.ghosts = self._ghosts()
        self.dreadmarches = self._dreadmarches()
        self.resonances = self._resonances()
        self.soul_severs = self._soul_severs()
        players = {a.id for a in data.players()}
        self.possessed_iv = aura_intervals(data, C.POSSESSED, dst_filter=players.__contains__)
        self.fixate_ends = self._fixate_ends()
        self.motion_mode = "constant"
        self.speed = C.GHOST_SPEED
        self.pause_s = C.GHOST_PAUSE_S
        self.face_deg = C.GHOST_FACE_DEG
        self.start_speed = C.GHOST_START_SPEED
        self.end_speed = C.GHOST_END_SPEED
        self.accel_s = C.GHOST_ACCEL_S
        self._log_pos = self._logged_positions()
        self._routes: dict[int, tuple[tuple[int, float, float], ...]] = {}
        self._simulated_motion: tuple | None = None
        if not defer_motion:
            self.set_motion()
        self.bomb_iv = aura_intervals(data, C.GLOOMBOMB)
        self.resonance_iv = aura_intervals(data, {C.RESONANCE, C.RESONANCE_MARK})
        self.bombs = self._bombs()
        self.bomb_casts = [
            e.t for e in data.events if e.spell_id == C.GLOOMBOMB_CAST and e.type == "SPELL_CAST_SUCCESS"
        ]
        self.wail_cast_ms = C.WAIL_CAST_MS.get(data.fight.difficulty, C.WAIL_CAST_MS_DEFAULT)
        self._tongues = self._tongue_windows()
        self.wails = self._wails()
        self.shield_changes = self._shields()

    # -- extraction ---------------------------------------------------------------------------

    def _ghosts(self) -> list[Ghost]:
        fix = aura_intervals(self.data, C.FIXATE, source_independent=True)
        out = []
        for a in self.data.actors_by_npc(C.NPC_GHOST):
            tr = self.tracks.observed_track(a.id)
            if tr is None or not len(tr):
                continue
            g = Ghost(
                a.id,
                self.tracks.appear_time(a.id),
                float(tr.x[0]),
                float(tr.y[0]),
                self.tracks.last_seen.get(a.id, self.end_t),
            )
            g.fixates = [iv for iv in fix if iv.src == a.id]
            # A source-less removal still closes this ghost's known fixate. It carries no actor
            # activity for Tracks.last_seen, so the interval is additional lifetime evidence.
            g.end_t = max(g.end_t, max((iv.end for iv in g.fixates), default=g.end_t))
            if self.tracks.death_time(a.id) is not None:
                g.end_t = self.tracks.death_time(a.id)
            out.append(g)
        out.sort(key=lambda g: g.spawn_t)
        return out

    def _dreadmarches(self) -> list[Dreadmarch]:
        possessed = aura_intervals(self.data, C.POSSESSED)
        out = []
        start = None
        for e in self.data.events:
            if e.spell_id != C.DREADMARCH or e.src != self.boss:
                continue
            if e.type == "SPELL_CAST_START":
                start = e.t
            elif e.type == "SPELL_CAST_SUCCESS":
                pos = [iv for iv in possessed if e.t - 500 <= iv.start <= e.t + 2500]
                ghosts = [g for g in self.ghosts if e.t <= g.spawn_t <= e.t + 10000]
                out.append(Dreadmarch(start if start is not None else e.t - 2000, e.t, pos, ghosts))
                self._assign_owners(pos, ghosts)
                start = None
        return out

    def _assign_owners(self, possessed: list[Interval], ghosts: list[Ghost]) -> None:
        if not possessed:
            return
        per = {iv.actor: 0 for iv in possessed}
        cap = max(1, -(-len(ghosts) // len(possessed)))
        pairs = []
        for g in ghosts:
            for iv in possessed:
                p = self.tracks.position(iv.actor, iv.end)
                if p:
                    pairs.append((distance(p, (g.x0, g.y0)), g, iv.actor))
        pairs.sort(key=lambda it: it[0])
        for _d, g, pid in pairs:
            if g.from_player < 0 and per[pid] < cap:
                g.from_player = pid
                per[pid] += 1

    def _resonances(self) -> list[tuple[int, list[int]]]:
        out: list[tuple[int, list[int]]] = []
        for e in self.data.events:
            if e.spell_id == C.RESONANCE and e.type == "SPELL_AURA_APPLIED":
                if out and e.t - out[-1][0] <= 100:
                    out[-1][1].append(e.dst)
                else:
                    out.append((e.t, [e.dst]))
        return out

    def _soul_severs(self) -> list[tuple[int, int, int, int]]:
        """(cast_start, t, tank, ghosts severed)."""
        out = []
        start = None
        for e in self.data.events:
            if e.spell_id != C.SOUL_SEVER or e.src != self.boss:
                continue
            if e.type == "SPELL_CAST_START":
                start = e.t
            elif e.type == "SPELL_CAST_SUCCESS":
                tank, severed = -1, 0
                for x in events_between(self.data, self.times, e.t - 10, e.t + 300):
                    if x.spell_id == C.SOUL_SEVER_GHOST and x.type == "SPELL_AURA_APPLIED":
                        a = self.data.actors.get(x.dst)
                        if a and a.is_player:
                            tank = x.dst
                        elif a and a.npc_id == C.NPC_GHOST:
                            severed += 1
                    elif x.spell_id == C.SOUL_SEVER and x.type == "SPELL_DAMAGE" and tank < 0:
                        tank = x.dst
                out.append((start if start is not None else e.t - 4000, e.t, tank, severed))
                start = None
        return out

    def cleave_target_at(self, t: float) -> int | None:
        """Who the visible soul-sever cone is aimed at."""
        active = [
            (cs, st, tank)
            for cs, st, tank, _n in self.soul_severs
            if tank >= 0 and st - C.SEVER_PREVIEW_MS <= t <= st + 600
        ]
        if not active:
            return None
        active.sort(key=lambda row: (t < row[0], abs(row[1] - t)))
        return active[0][2]

    def _fixate_ends(self) -> list[FixateEnd]:
        sever_ts = [t for _s, t, _tank, _n in self.soul_severs]
        out = []
        for g in self.ghosts:
            for iv in g.fixates:
                if iv.end >= self.end_t:
                    continue
                if any(abs(iv.end - st) <= 150 for st in sever_ts):
                    reason = "severed"
                elif any(0 <= iv.end - rt <= 4000 and iv.actor in ps for rt, ps in self.resonances):
                    reason = "resonance"
                elif any(abs(iv.end - dt) <= 1500 and pid == iv.actor for dt, pid in self.player_deaths):
                    reason = "died"
                elif any(abs(iv.end - p.start) <= 400 and p.actor == iv.actor for p in self.possessed_iv):
                    reason = "reached"
                else:
                    reason = "unknown"
                out.append(FixateEnd(iv.end, g.aid, iv.actor, reason))
        out.sort(key=lambda fe: fe.t)
        return out

    def set_motion(
        self,
        speed: float | None = None,
        face_deg: float | None = None,
        mode: str | None = None,
        start_speed: float | None = None,
        end_speed: float | None = None,
        accel_s: float | None = None,
        pause_s: float | None = None,
    ) -> None:
        """Rebuild every ghost path from spawn to despawn. The log has neither speed nor a facing cone."""
        if speed is not None:
            self.speed = max(0.0, float(speed))
        if face_deg is not None:
            self.face_deg = max(0.0, float(face_deg))
        if mode is not None:
            self.motion_mode = "accel" if mode == "accel" else "constant"
        if start_speed is not None:
            self.start_speed = max(0.0, float(start_speed))
        if end_speed is not None:
            self.end_speed = max(0.0, float(end_speed))
        if accel_s is not None:
            self.accel_s = max(0.0, float(accel_s))
        if pause_s is not None:
            self.pause_s = max(0.0, float(pause_s))
        if self._motion_key() == self._simulated_motion:
            return
        self._simulate_ghosts()
        self._simulated_motion = self._motion_key()

    def _motion_key(self) -> tuple:
        face = round(self.face_deg, 4)
        if self.motion_mode != "accel":
            return ("constant", round(self.speed, 4), round(self.pause_s, 4), face)
        return (
            "accel",
            round(self.start_speed, 4),
            round(self.end_speed, 4),
            round(self.accel_s, 4),
            face,
        )

    def _chase_speed(self, g: Ghost, t: float) -> float:
        """Yards per second. Accel time runs from the start of the fixate covering ``t``."""
        if self.motion_mode != "accel":
            return self.speed
        origin = g.spawn_t
        for iv in g.fixates:
            if iv.start <= t < iv.end:
                origin = iv.start
                break
        span = self.accel_s * 1000.0
        if span <= 0:
            return self.end_speed
        u = min(1.0, max(0.0, (t - origin) / span))
        return self.start_speed + (self.end_speed - self.start_speed) * u

    def _logged_positions(self) -> dict[int, list[tuple[int, float, float]]]:
        """Combat-log coordinates, dropping repeats of the same point."""
        out: dict[int, list[tuple[int, float, float]]] = {}
        for g in self.ghosts:
            tr = self.tracks.observed_track(g.aid)
            if tr is None:
                continue
            pts: list[tuple[int, float, float]] = []
            for i in range(len(tr)):
                t, x, y = int(tr.t[i]), float(tr.x[i]), float(tr.y[i])
                if pts and abs(pts[-1][1] - x) < 1e-3 and abs(pts[-1][2] - y) < 1e-3:
                    continue
                pts.append((t, x, y))
            out[g.aid] = pts
        return out

    def _simulate_ghosts(self) -> None:
        if not self.ghosts:
            return
        step = C.GHOST_STEP_MS
        pos: dict[int, tuple[float, float]] = {}
        samples: dict[int, list[Sample]] = {g.aid: [] for g in self.ghosts}
        log_i = {g.aid: 0 for g in self.ghosts}
        held: dict[int, bool] = {}
        released: dict[int, float] = {}
        chased: dict[int, int] = {}
        t0 = min(g.spawn_t for g in self.ghosts)
        t1 = max(g.end_t for g in self.ghosts)
        step = max(step, math.ceil((t1 - t0) / 100_000))
        by_id = {g.aid: g for g in self.ghosts}
        t = t0 - t0 % step
        while t <= t1 + step:
            check_cancelled()
            for g in self.ghosts:
                if not g.spawn_t <= t <= g.end_t + step:
                    continue
                pts = self._log_pos.get(g.aid, ())
                i = log_i[g.aid]
                anchored = False
                while i < len(pts) and pts[i][0] <= t:
                    ts, x, y = pts[i]
                    pos[g.aid] = (x, y)
                    lst = samples[g.aid]
                    if not lst or lst[-1].t < ts:
                        lst.append(Sample(ts, x, y, 0.0, 1, 1))
                    i += 1
                    anchored = True
                log_i[g.aid] = i
                if g.aid not in pos:
                    pos[g.aid] = (g.x0, g.y0)
                    samples[g.aid].append(Sample(g.spawn_t, g.x0, g.y0, 0.0, 1, 1))
                    continue
                if anchored:
                    continue
                target = g.target_at(t)
                if chased.get(g.aid) != target:
                    chased[g.aid] = target
                    held.pop(g.aid, None)
                    released.pop(g.aid, None)
                pose = self.tracks.pose(target, t) if target >= 0 else None
                if pose is not None:
                    x, y = pos[g.aid]
                    tp = (pose.x, pose.y)
                    staring = math.degrees(angle_diff(pose.facing, angle_to(tp, (x, y)))) <= self.face_deg
                    # Constant mode starts the pause on the step the stare ends. A new stare cancels it.
                    if staring:
                        held[g.aid] = True
                        released.pop(g.aid, None)
                    elif self.motion_mode != "accel" and self.pause_s > 0 and held.get(g.aid):
                        released[g.aid] = t
                        held[g.aid] = False
                    release = released.get(g.aid)
                    pausing = (
                        self.motion_mode != "accel"
                        and release is not None
                        and t < release + self.pause_s * 1000.0
                    )
                    if not staring and not pausing:
                        d = distance((x, y), tp)
                        move = min(d, self._chase_speed(g, t) * step / 1000.0)
                        if d > 1e-6 and d > 1.0:
                            pos[g.aid] = (x + (tp[0] - x) / d * move, y + (tp[1] - y) / d * move)
            for gid, p in pos.items():
                g = by_id[gid]
                if g.spawn_t < t <= g.end_t + step:
                    lst = samples[gid]
                    if not lst or lst[-1].t < t:
                        lst.append(Sample(t, p[0], p[1], 0.0, 1, 1))
            t += step
        for gid, lst in samples.items():
            if lst:
                g = by_id[gid]
                self.tracks.set_derived(gid, Track(lst), active_span=(g.spawn_t, g.end_t))
        self._routes = {
            g.aid: movement_times(self.tracks.track(g.aid), g.spawn_t, g.end_t) for g in self.ghosts
        }

    def route_overlays(self, t: float) -> list[Path]:
        """Dotted trail behind a living ghost. The whole line goes away when the ghost does."""
        out = []
        for g in self.ghosts:
            if not g.spawn_t <= t < g.end_t:
                continue
            pts = trail_points(self._routes.get(g.aid, ()), t, self.tracks.position(g.aid, t))
            if pts:
                out.append(Path(pts, C.C_GHOST, C.PATH_WIDTH, C.PATH_ALPHA, True, "ghosts"))
        return out

    def _bombs(self) -> list[Bomb]:
        groups: list[Bomb] = []
        for iv in sorted(self.bomb_iv, key=lambda iv: iv.end):
            if iv.end >= self.end_t:
                continue
            if groups and iv.end - groups[-1].t <= 200:
                groups[-1].targets.append(iv.actor)
            else:
                groups.append(Bomb(iv.end, [iv.actor]))
        for b in groups:
            for e in events_between(self.data, self.times, b.t - 20, b.t + 200):
                if e.spell_id == C.SOUL_SHIELD and e.type in (
                    "SPELL_AURA_REMOVED_DOSE",
                    "SPELL_AURA_REMOVED",
                ):
                    b.cracked += 1
        return groups

    def _tongue_windows(self) -> dict[int, list[tuple[int, int]]]:
        coil = set(self.soulcoilers)
        out: dict[int, list[tuple[int, int]]] = {}
        for iv in aura_intervals(self.data, C.TONGUES, dst_filter=coil.__contains__):
            out.setdefault(iv.actor, []).append((iv.start, iv.end))
        return out

    def wail_remaining(self, w: WailCast, t: float) -> float:
        return wail_remaining_ms(w.start, t, self.wail_cast_ms, self._tongues.get(w.src, []))

    def _wails(self) -> list[WailCast]:
        coil = set(self.soulcoilers)
        starts = [
            e
            for e in self.data.events
            if e.type == "SPELL_CAST_START" and e.spell_id == C.WAIL and e.src in coil
        ]
        out = []
        seen: set[tuple[int, int]] = set()
        # Cursed bar, plus a little room for damage pushback. The next cast start still ends the search.
        span = int(self.wail_cast_ms * C.TONGUES_CAST_MULT) + 4000
        for s in starts:
            if (s.src, s.t) in seen:
                continue
            seen.add((s.src, s.t))
            limit = s.t + span
            end, outcome, kicker, feared = min(limit, self.end_t), "unknown", -1, 0
            died = self.tracks.death_time(s.src)
            for e in events_between(self.data, self.times, s.t + 1, limit):
                if e.type == "SPELL_INTERRUPT" and e.dst == s.src and e.extra == C.WAIL:
                    end, outcome, kicker = e.t, "kicked", e.src
                    break
                if (
                    e.type in ("SPELL_CAST_SUCCESS", "SPELL_AURA_APPLIED")
                    and e.spell_id == C.WAIL
                    and e.src == s.src
                ):
                    end, outcome = e.t, "went_off"
                    feared = len(
                        {
                            x.dst
                            for x in events_between(self.data, self.times, e.t, e.t + 200)
                            if x.type == "SPELL_AURA_APPLIED"
                            and x.spell_id == C.WAIL
                            and x.src == s.src
                            and x.dst >= 0
                        }
                    )
                    break
                if e.type == "SPELL_CAST_START" and e.src == s.src and e.spell_id == C.WAIL:
                    end = e.t
                    break
                if died is not None and e.t >= died:
                    end, outcome = died, "died"
                    break
            out.append(WailCast(s.src, s.t, end, outcome, kicker, feared))
        return out

    def _shields(self) -> dict[int, list[tuple[int, int]]]:
        out: dict[int, list[tuple[int, int]]] = {}
        coil = set(self.soulcoilers)
        for e in self.data.events:
            if e.spell_id != C.SOUL_SHIELD or e.dst not in coil:
                continue
            lst = out.setdefault(e.dst, [])
            if e.type == "SPELL_AURA_APPLIED":
                lst.append((e.t, e.amount or 2))
            elif e.type in ("SPELL_AURA_APPLIED_DOSE", "SPELL_AURA_REMOVED_DOSE"):
                lst.append((e.t, e.amount))
            elif e.type == "SPELL_AURA_REMOVED":
                lst.append((e.t, 0))
        return out

    # -- queries ------------------------------------------------------------------------------

    def shield_at(self, aid: int, t: float) -> int:
        v = 0
        for ct, n in self.shield_changes.get(aid, []):
            if ct <= t:
                v = n
        return v

    def ghosts_at(self, t: float) -> list[Ghost]:
        return [g for g in self.ghosts if g.spawn_t <= t <= g.end_t]

    def unit_styles(self) -> dict[int, UnitStyle]:
        styles = {
            g.aid: UnitStyle(C.C_GHOST_FILL, 8, "", C.C_GHOST, group="ghost", fill_alpha=0.9)
            for g in self.ghosts
        }
        for aid in self.soulcoilers:
            styles[aid] = UnitStyle("#2a8f8a", 10, "怨毒盘魂者", "#7ff0e8", hp_bar=True)
        return styles

    # -- outputs ------------------------------------------------------------------------------

    def lanes(self) -> list[Lane]:
        ghost_items = []
        for dm in self.dreadmarches:
            end = max((g.end_t for g in dm.ghosts), default=dm.t)
            ghost_items.append(LaneItem(dm.t, end, "span", label=f"恐惧行军 · {len(dm.ghosts)} 鬼魂"))
        for rt, ps in self.resonances:
            ghost_items.append(
                LaneItem(
                    rt,
                    shape="diamond",
                    color=C.C_RESONANCE,
                    label="恶毒共鸣 " + ", ".join(short(self.data, p) for p in ps),
                )
            )
        bomb_items = [
            LaneItem(b.t, shape="circle", label=", ".join(short(self.data, p) for p in b.targets))
            for b in self.bombs
        ]
        kick_items = []
        for w in self.wails:
            if w.outcome == "kicked":
                kick_items.append(LaneItem(w.end, label=f"{short(self.data, w.kicker)} 打断"))
            elif w.outcome == "went_off":
                kick_items.append(
                    LaneItem(w.end, color="#ff4040", label=f"恐惧哀嚎读条成功（恐惧 {w.feared} 人）")
                )
        return [
            Lane(
                "ghosts",
                "鬼魂",
                C.C_GHOST,
                ghost_items,
                help="恐惧行军：被附身的玩家放出鬼魂，鬼魂追向被点名的玩家（轨迹为模拟）；粉色菱形=恶毒共鸣",
            ),
            Lane(
                "gloombomb",
                "幽暗炸弹",
                C.C_GLOOMBOMB,
                bomb_items,
                help="幽暗炸弹爆炸（预设半径），可打碎盘魂者护盾",
            ),
            Lane(
                "kicks", "打断", C.C_KICK, kick_items, help="怨毒盘魂者的恐惧哀嚎：青色=打断，红色=读条成功"
            ),
        ]

    def log(self) -> list[LogEntry]:
        d = self.data
        out: list[LogEntry] = []
        for dm in self.dreadmarches:
            segs = [Seg("恐惧行军", C.C_GHOST, badge=True), Seg(" ")]
            for i, iv in enumerate(dm.possessed):
                if i:
                    segs.append(Seg(", "))
                segs.append(name_seg(d, iv.actor))
            durs = [(iv.end - iv.start) / 1000 for iv in dm.possessed]
            freed = f"{min(durs):.1f}-{max(durs):.1f} s" if durs else "?"
            segs.append(Seg(f" 被附身 · {freed} 后解除 · {len(dm.ghosts)} 个鬼魂"))
            out.append(LogEntry(dm.t, segs, "ghosts"))
        for b in self.bombs:
            segs = [Seg("幽暗炸弹", C.C_GLOOMBOMB, badge=True), Seg(" ")]
            for i, p in enumerate(b.targets):
                if i:
                    segs.append(Seg(", "))
                segs.append(name_seg(d, p))
            if b.cracked:
                segs.append(Seg(f" · 打掉 {b.cracked} 层护盾", "#7ff0e8"))
            segs.append(Seg(" · 光环结束，爆炸时间为推定", "#e8a33d"))
            out.append(LogEntry(b.t, segs, "gloombomb"))
        for w in self.wails:
            if w.outcome == "kicked":
                out.append(
                    LogEntry(
                        w.end,
                        [
                            Seg("打断", C.C_KICK, badge=True),
                            Seg(" "),
                            name_seg(d, w.kicker),
                            Seg(f" 打断了恐惧哀嚎（读条 {(w.end - w.start) / 1000:.1f} s）"),
                        ],
                        "kicks",
                    )
                )
            elif w.outcome == "went_off":
                out.append(
                    LogEntry(
                        w.end,
                        [
                            Seg("恐惧哀嚎", "#ff4040", badge=True),
                            Seg(f" 读条成功 · 恐惧 {w.feared} 人", "#ff8080"),
                        ],
                        "kicks",
                    )
                )
        for rt, ps in self.resonances:
            segs = [Seg("恶毒共鸣", C.C_RESONANCE, badge=True), Seg(" 两个鬼魂相撞：")]
            for i, p in enumerate(ps):
                if i:
                    segs.append(Seg(", "))
                segs.append(name_seg(d, p))
            out.append(LogEntry(rt, segs, "ghosts"))
        for _cs, t, tank, n in self.soul_severs:
            out.append(
                LogEntry(
                    t,
                    [
                        Seg("灵魂撕裂", C.C_SEVER, badge=True),
                        Seg(" → "),
                        name_seg(d, tank),
                        Seg(f" · 斩断 {n} 个鬼魂" if n else ""),
                    ],
                    "ghosts",
                )
            )
        for fe in self.fixate_ends:
            if fe.reason in ("reached", "unknown"):
                out.append(
                    LogEntry(
                        fe.t,
                        [
                            Seg("鬼魂", C.C_GHOST, badge=True),
                            Seg(" 推定追上了 " if fe.reason == "reached" else " 凝视解除，原因未知 → "),
                            name_seg(d, fe.player),
                        ],
                        "ghosts",
                    )
                )
        return out

    def overlays(self, t: float) -> list[Prim]:
        out: list[Prim] = []
        for cs, st, tank, _n in self.soul_severs:
            if st - C.SEVER_PREVIEW_MS <= t <= st + 600 and tank >= 0:
                bp = self.tracks.position(self.boss, t)
                tp = self.tracks.position(tank, t)
                if bp and tp:
                    casting = t >= cs
                    out.append(
                        Cone(
                            bp[0],
                            bp[1],
                            angle_to(bp, tp),
                            C.SEVER_CONE_DEG,
                            C.SOUL_SEVER_RANGE,
                            C.C_SEVER,
                            fill_alpha=0.22 if casting else 0.08,
                            line_alpha=0.8 if casting else 0.4,
                            dashed=t < cs,
                            label=f"灵魂撕裂 {fmt_secs(st - t)}" if t < st else "灵魂撕裂",
                            layer="ghosts",
                        )
                    )
        for g in self.ghosts_at(t):
            target = g.target_at(t)
            gp = self.tracks.position(g.aid, t)
            tp = self.tracks.position(target, t) if target >= 0 else None
            if gp and tp:
                out.append(
                    Line(gp[0], gp[1], tp[0], tp[1], color_of(self.data, target), 1.2, 0.6, True, "ghosts")
                )
        for iv in self.possessed_iv.active(t):
            out.append(UnitRing(iv.actor, C.C_PURPLE, layer="ghosts", width=2.2, solid=True))
        seen: set[int] = set()
        for iv in self.resonance_iv.active(t):
            if iv.actor not in seen:
                seen.add(iv.actor)
                out.append(UnitFlash(iv.actor, "#ff2430"))
        for iv in self.bomb_iv.active(t):
            p = self.tracks.position(iv.actor, t)
            if p:
                out.append(
                    Circle(
                        p[0],
                        p[1],
                        C.GLOOMBOMB_RADIUS,
                        C.C_GLOOMBOMB,
                        0.12,
                        0.85,
                        1.5,
                        True,
                        f"幽暗炸弹 {fmt_secs(iv.end - t)}",
                        "gloombomb",
                    )
                )
        for b in self.bombs:
            if b.t <= t < b.t + 700:
                for pid in b.targets:
                    p = self.tracks.position(pid, b.t)
                    if p:
                        out.append(
                            Circle(
                                p[0],
                                p[1],
                                C.GLOOMBOMB_RADIUS,
                                C.C_GLOOMBOMB,
                                0.35,
                                1.0,
                                2.0,
                                layer="gloombomb",
                            )
                        )
        for aid in self.soulcoilers:
            if not self.tracks.present(aid, t) or self.tracks.is_dead(aid, t):
                continue
            parts = []
            sh = self.shield_at(aid, t)
            if sh:
                parts.append(f"盾 {sh}/2")
            w = next((w for w in self.wails if w.src == aid and w.start <= t < w.end), None)
            if w:
                parts.append(f"哀嚎 {fmt_secs(self.wail_remaining(w, t))}")
            if parts:
                out.append(UnitTag(aid, " · ".join(parts), C.C_KICK if w else "#7ff0e8", "kicks"))
        return out

    def hud(self, t: float) -> list[HudLine]:
        out = [HudLine("P2 · 妖术领主玛拉卡斯", C.C_PHASE, key="phase")]
        marked = []
        for iv in self.resonance_iv.active(t):
            if iv.actor not in marked:
                marked.append(iv.actor)
        if marked:
            out.append(
                HudLine(
                    "恶毒共鸣 " + ", ".join(short(self.data, aid) for aid in marked),
                    "#ff2430",
                    key="resonance",
                )
            )
        ns = next((s for s in self.soul_severs if s[1] > t), None)
        if ns:
            out.append(
                HudLine(
                    f"灵魂撕裂 {fmt_secs(ns[1] - t)} → {short(self.data, ns[2])}",
                    C.C_SEVER,
                    key="soul_sever",
                )
            )
        nd = next((dm for dm in self.dreadmarches if dm.t > t), None)
        if nd:
            out.append(HudLine(f"恐惧行军 {fmt_secs(nd.t - t)}", C.C_GHOST, key="dreadmarch"))
        active = self.bomb_iv.active(t)
        if active:
            out.append(
                HudLine(f"幽暗炸弹 {fmt_secs(min(iv.end for iv in active) - t)}", C.C_GLOOMBOMB, key="bomb")
            )
        else:
            nb = next((x for x in self.bomb_casts if x > t), None)
            if nb is not None:
                out.append(HudLine(f"幽暗炸弹 {fmt_secs(nb - t)}", C.C_GLOOMBOMB, key="bomb"))
        return out

    def status(self, t: float) -> list[StatusSection]:
        d = self.data
        secs = []
        out_now = [g for g in self.ghosts_at(t) if g.spawn_t <= t]
        ends = [fe for fe in self.fixate_ends if fe.t <= t]
        severed = sum(n for _cs, st, _tk, n in self.soul_severs if st <= t)
        reached = sum(1 for fe in ends if fe.reason == "reached")
        unknown = sum(1 for fe in ends if fe.reason == "unknown")
        res = sum(1 for rt, _ps in self.resonances if rt <= t)
        gh = StatusSection("鬼魂", C.C_GHOST)
        gh.rows.append([Cell(f"{len(out_now)} 个鬼魂在场", "#ffffff", bold=True)])
        gh.note = (
            f"累计：{severed} 被灵魂撕裂斩断 · {reached} 推定追上玩家 · {unknown} 原因未知 · {res} 次恶毒共鸣"
        )
        secs.append(gh)
        if out_now:
            lst = StatusSection("在场鬼魂 · 追谁 · 来自 · 已出现", "#8b93a1")
            for g in out_now:
                target = g.target_at(t)
                lst.rows.append(
                    [
                        Cell(
                            f"{short(d, target)}" if target >= 0 else "（无目标）",
                            color_of(d, target) if target >= 0 else "#888",
                            True,
                        ),
                        Cell(f"来自 {short(d, g.from_player)}" if g.from_player >= 0 else "", "#8b93a1"),
                        Cell(fmt_secs(t - g.spawn_t), "#8b93a1", align="right"),
                    ]
                )
            secs.append(lst)

        bombs = self.bomb_iv.active(t)
        coils = [a for a in self.soulcoilers if self.tracks.present(a, t) and not self.tracks.is_dead(a, t)]
        if bombs or coils:
            gb = StatusSection("幽暗炸弹", C.C_GLOOMBOMB)
            if bombs:
                row = [
                    Cell(", ".join(short(d, iv.actor) for iv in bombs), "#e8e8e8", True),
                    Cell(
                        f"预计 {fmt_secs(max(iv.end for iv in bombs) - t)} 后结束", "#8b93a1", align="right"
                    ),
                ]
                gb.rows.append(row)
            for a in coils:
                pose = self.tracks.pose(a, t)
                hp = f"{pose.hp_frac * 100:.0f}%" if pose else ""
                gb.rows.append(
                    [
                        Cell("怨毒盘魂者", "#7ff0e8"),
                        Cell(f"盾 {self.shield_at(a, t)}/2", "#7ff0e8"),
                        Cell(hp, "#8b93a1", align="right"),
                    ]
                )
            secs.append(gb)

        casting = [w for w in self.wails if w.start <= t < w.end]
        done = [w for w in self.wails if w.end <= t]
        if casting or done:
            k = StatusSection("打断", C.C_KICK)
            for w in casting:
                k.rows.append(
                    [
                        Cell("怨毒盘魂者 读条恐惧哀嚎", "#7ff0e8"),
                        Cell(f"剩 {fmt_secs(self.wail_remaining(w, t))}", "#e8e8e8", align="right"),
                    ]
                )
            for w in [w for w in done if w.outcome == "kicked"][-4:]:
                k.rows.append(
                    [
                        Cell(short(d, w.kicker), color_of(d, w.kicker), True),
                        Cell("打断", "#8b93a1"),
                        Cell(fmt_time(w.end), "#8b93a1", align="right"),
                    ]
                )
            kicked = sum(1 for w in done if w.outcome == "kicked")
            off = sum(1 for w in done if w.outcome == "went_off")
            k.note = f"累计：{kicked} 次打断 · {off} 次读条成功"
            secs.append(k)
        return secs
