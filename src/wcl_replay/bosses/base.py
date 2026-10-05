# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Boss module interface and the Qt-free drawing / panel primitives an analysis produces.

A boss module turns FightData into an Analysis. The UI only knows these primitives, so modules stay
testable without Qt and new bosses never touch UI code. All positions are world coordinates (yards).
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import ClassVar

from ..core.models import ActorKind, Event, FightData
from ..core.specs import class_color
from ..core.targets import Targets
from ..core.tracks import Tracks

# ---------------------------------------------------------------------------- map primitives


@dataclass(slots=True)
class Circle:
    x: float
    y: float
    r: float
    color: str
    fill_alpha: float = 0.15
    line_alpha: float = 0.8
    width: float = 1.5
    dashed: bool = False
    label: str = ""
    layer: str = ""


@dataclass(slots=True)
class Cone:
    x: float
    y: float
    direction: float  # WoW facing, radians
    angle_deg: float
    radius: float
    color: str
    fill_alpha: float = 0.18
    line_alpha: float = 0.7
    dashed: bool = True
    label: str = ""
    layer: str = ""


@dataclass(slots=True)
class Line:
    x1: float
    y1: float
    x2: float
    y2: float
    color: str
    width: float = 1.5
    alpha: float = 0.8
    dashed: bool = False
    layer: str = ""


@dataclass(slots=True)
class Path:
    """A polyline. ``dashed`` routes are drawn as dense screen-space dots, not a dash stroke."""

    points: tuple[tuple[float, float], ...]
    color: str
    width: float = 1.1
    alpha: float = 0.2
    dashed: bool = True
    layer: str = ""


@dataclass(slots=True)
class Dot:
    """A marker with a fixed on-screen size (e.g. a globule)."""

    x: float
    y: float
    color: str
    size_px: float = 12.0
    outline: str = "#101010"
    glow: bool = False
    dashed_ring: bool = False
    label: str = ""
    layer: str = ""
    stack: str = ""  # occlusion group, e.g. "orb"; empty stays under every unit


@dataclass(slots=True)
class Label:
    x: float
    y: float
    text: str
    color: str = "#e8e8e8"
    size_px: float = 10.0
    dy_px: float = 0.0
    boxed: bool = False
    layer: str = ""


@dataclass(slots=True)
class UnitRing:
    """A ring around a drawn unit. A solid ring stays whole; otherwise ``progress`` eats the arc."""

    actor_id: int
    color: str
    progress: float = 1.0
    layer: str = ""
    width: float = 3.0
    solid: bool = False


@dataclass(slots=True)
class UnitTag:
    """A short colored tag drawn next to a unit."""

    actor_id: int
    text: str
    color: str
    layer: str = ""


@dataclass(slots=True)
class UnitFlash:
    """A pulsing glow drawn on top of a unit marker."""

    actor_id: int
    color: str = "#ff2430"
    layer: str = ""


Prim = Circle | Cone | Line | Path | Dot | Label | UnitRing | UnitTag | UnitFlash


@dataclass(slots=True)
class UnitStyle:
    color: str
    radius_px: float = 11.0
    label: str = ""
    border: str = "#000000"
    show_facing: bool = False
    hp_bar: bool = False
    group: str = ""  # Boss-owned stack group; bosses and players are classified separately.
    fill_alpha: float = 1.0
    group_label: str = ""


# ---------------------------------------------------------------------------- HUD / panels


@dataclass(slots=True)
class HudLine:
    text: str
    color: str = "#e8e8e8"
    key: str = ""  # stable id so a later phase can pick lines without matching the label text


@dataclass(slots=True)
class Bar:
    label: str
    frac: float
    color: str = "#d9534f"
    sub: str = ""
    dim: bool = False


@dataclass(slots=True)
class Cell:
    text: str
    color: str | None = None
    bold: bool = False
    align: str = "left"
    badge: bool = False
    bar: float | None = None


@dataclass(slots=True)
class StatusSection:
    title: str
    color: str = "#e8a33d"
    rows: list[list[Cell]] = field(default_factory=list)
    note: str = ""
    key: str = ""


@dataclass(slots=True)
class Seg:
    text: str
    color: str | None = None
    badge: bool = False
    bold: bool = False


@dataclass(slots=True)
class LogEntry:
    t: int
    segments: list[Seg]
    lane: str = ""
    sub: list[Seg] = field(default_factory=list)


@dataclass(slots=True)
class LaneItem:
    t: int
    t_end: int | None = None
    shape: str = "tick"  # tick | span | triangle | diamond | circle
    color: str | None = None
    label: str = ""


@dataclass(slots=True)
class Lane:
    id: str
    name: str
    color: str
    items: list[LaneItem] = field(default_factory=list)
    series: list[tuple[int, float]] | None = None
    default_on: bool = True
    help: str = ""


@dataclass(slots=True)
class Phase:
    t: int
    name: str
    short: str = ""


# ---------------------------------------------------------------------------- helpers


class Times:
    """Sorted event times with payloads: next / previous lookups."""

    def __init__(self, items: list[tuple[int, object]] | None = None):
        items = sorted(items or [], key=lambda it: it[0])
        self.t = [it[0] for it in items]
        self.payload = [it[1] for it in items]

    def __len__(self) -> int:
        return len(self.t)

    def next(self, t: float) -> tuple[int, object] | None:
        i = bisect.bisect_right(self.t, t)
        return (self.t[i], self.payload[i]) if i < len(self.t) else None

    def prev(self, t: float) -> tuple[int, object] | None:
        i = bisect.bisect_right(self.t, t)
        return (self.t[i - 1], self.payload[i - 1]) if i > 0 else None

    def count_until(self, t: float) -> int:
        return bisect.bisect_right(self.t, t)


@dataclass(slots=True)
class Interval:
    start: int
    end: int
    actor: int = -1
    src: int = -1
    payload: object = None


class Intervals:
    def __init__(self, items: list[Interval] | None = None):
        self.items = sorted(items or [], key=lambda iv: iv.start)
        self._starts = [iv.start for iv in self.items]
        self._max_len = max((iv.end - iv.start for iv in self.items), default=0)

    def __iter__(self):
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def active(self, t: float) -> list[Interval]:
        hi = bisect.bisect_right(self._starts, t)
        lo = bisect.bisect_left(self._starts, t - self._max_len)
        return [iv for iv in self.items[lo:hi] if iv.start <= t < iv.end]


def aura_intervals(
    data: FightData,
    spell_ids: set[int] | int,
    end_t: int | None = None,
    dst_filter: Callable[[int], bool] | None = None,
    *,
    source_independent: bool = False,
) -> Intervals:
    """Aura presence intervals; opt into source identity for independently applied instances.

    A first dose or refresh starts at that observation, without inventing an earlier application.
    """
    ids = {spell_ids} if isinstance(spell_ids, int) else set(spell_ids)
    end_t = data.fight.duration_ms if end_t is None else end_t
    open_: dict[tuple[int, ...], Interval] = {}
    out: list[Interval] = []
    for e in data.events:
        if e.spell_id not in ids or (dst_filter and not dst_filter(e.dst)):
            continue
        key = (e.dst, e.spell_id, e.src) if source_independent else (e.dst, e.spell_id)
        if e.type in ("SPELL_AURA_APPLIED", "SPELL_AURA_APPLIED_DOSE", "SPELL_AURA_REFRESH"):
            # A dose or refresh keeps an open aura continuous. Without an apply, it is still
            # evidence that the aura is up, starting only at this observation.
            if e.type in ("SPELL_AURA_APPLIED_DOSE", "SPELL_AURA_REFRESH") and key in open_:
                continue
            if key in open_:
                out.append(open_.pop(key))
                out[-1].end = e.t
            open_[key] = Interval(e.t, end_t, e.dst, e.src, e.spell_id)
        elif e.type == "SPELL_AURA_REMOVED":
            keys = [key] if key in open_ else []
            if source_independent and e.src < 0:
                # A source-less removal reports the target's aura cleared, not one known source.
                keys = [candidate for candidate in open_ if candidate[:2] == (e.dst, e.spell_id)]
            for removed in keys:
                iv = open_.pop(removed)
                iv.end = e.t
                out.append(iv)
    out.extend(open_.values())
    return Intervals(out)


def fmt_time(ms: float) -> str:
    s = max(0, int(ms // 1000))
    return f"{s // 60}:{s % 60:02d}"


def fmt_secs(ms: float) -> str:
    return f"{max(0.0, ms) / 1000:.0f} s" if ms >= 10000 else f"{max(0.0, ms) / 1000:.1f} s"


@dataclass(slots=True, frozen=True)
class FrameAura:
    """One debuff the raid frames can draw. Nothing is shown until the user selects its key.

    ``spells`` is ``(spell id, icon stem)`` in the order icons are drawn, left to right.
    The stem is a PNG in ``assets/auras`` without the extension.
    """

    key: str
    label: str
    spells: tuple[tuple[int, str], ...]
    tip: str = ""
    source_independent: bool = False


# ---------------------------------------------------------------------------- analysis / module


type ParameterValue = float | str


@dataclass(slots=True, frozen=True)
class AnalysisParameter:
    """A boss-owned numeric or choice control; no Qt or UI callbacks belong in this description.

    ``choices`` contains ``(value, label)`` pairs. Every ``visible_when`` pair must match for the
    control to appear. An explicit ``settings_key`` preserves older settings; other parameters
    are stored under the encounter id so two bosses can use the same local parameter id.
    """

    id: str
    label: str
    default: ParameterValue
    minimum: float = 0.0
    maximum: float = 100.0
    choices: tuple[tuple[str, str], ...] = ()
    tooltip: str = ""
    visible_when: tuple[tuple[str, ParameterValue], ...] = ()
    step: float = 0.1
    decimals: int = 1
    suffix: str = ""
    prefix: str = ""
    settings_key: str = ""

    def normalize(self, value: object) -> ParameterValue:
        if self.choices:
            return str(value) if str(value) in {item[0] for item in self.choices} else self.default
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = float(self.default)
        if not math.isfinite(number):
            number = float(self.default)
        return max(self.minimum, min(self.maximum, number))

    def is_visible(self, values: Mapping[str, ParameterValue]) -> bool:
        return all(values.get(key) == expected for key, expected in self.visible_when)

    def storage_key(self, encounter_id: int) -> str:
        return self.settings_key or f"analysis_parameters/{encounter_id}/{self.id}"


class Analysis:
    """Result of analysing one fight. Subclasses override the *_at(t) hooks."""

    title: str = ""
    boss_npc_ids: tuple[int, ...] = ()
    hidden_npc_ids: frozenset[int] = frozenset()
    # Extra occlusion cards that are not actors, such as floor orbs: (group id, label).
    stack_extras: tuple[tuple[str, str], ...] = ()
    # Debuffs the raid frames offer in the filter. Empty until a boss lists them.
    frame_auras: ClassVar[tuple[FrameAura, ...]] = ()
    parameters: ClassVar[tuple[AnalysisParameter, ...]] = ()
    parameter_note: ClassVar[str] = ""

    def __init__(self, data: FightData, tracks: Tracks):
        self.data = data
        self.tracks = tracks
        self.parameter_values = {
            parameter.id: parameter.normalize(parameter.default) for parameter in self.parameters
        }
        self.phases: list[Phase] = [Phase(0, "Fight", "P1")]
        self.lanes: list[Lane] = []
        self.log: list[LogEntry] = []
        self.unit_styles: dict[int, UnitStyle] = {}
        self.arena: tuple[float, float, float, float] | None = None  # x_min, x_max, y_min, y_max
        self._npcs = [
            a.id
            for a in data.actors.values()
            if a.kind is ActorKind.NPC
            and a.hostile
            and tracks.has(a.id)
            and a.npc_id not in self.hidden_npc_ids
        ]
        self._bosses = self._pick_bosses()
        self._deaths = sorted(
            (e.t, e.dst)
            for e in data.events
            if e.type == "UNIT_DIED" and e.dst in data.actors and data.actors[e.dst].is_player
        )
        self._frame_events: list[Event] | None = None
        self._frame_key: tuple | None = None
        self._index_frame_auras()
        self._target_events: list[Event] | None = None
        self._target_key: tuple | None = None
        self._index_targets()

    # -- defaults -----------------------------------------------------------------------------

    def apply_parameters(self, values: Mapping[str, ParameterValue]) -> None:
        """Apply a partial parameter update. Subclasses rebuild their derived data after this call."""
        for parameter in self.parameters:
            if parameter.id in values:
                self.parameter_values[parameter.id] = parameter.normalize(values[parameter.id])

    def refresh_indexes(self, *, invalidate_auras: bool = False) -> None:
        """Refresh result indexes, reusing auras and targets when inputs have not changed.

        Plugins that edit existing events in place must pass ``invalidate_auras=True``.
        Replacing/appending events or changing aura declarations rebuilds the index automatically.
        """
        self.log.sort(key=lambda entry: entry.t)
        self.phases.sort(key=lambda phase: phase.t)
        if invalidate_auras:
            self._frame_key = None
            self._target_key = None
        self._index_frame_auras()
        self._index_targets()

    def _index_targets(self) -> None:
        key = (len(self.data.events), frozenset(self.data.actors))
        if self.data.events is self._target_events and key == self._target_key:
            return
        self.targets = Targets(self.data)
        self._target_events = self.data.events
        self._target_key = key

    @property
    def boss_ids(self) -> list[int]:
        """Actor ids treated as this encounter's bosses, in display order."""
        return self._bosses

    def _pick_bosses(self) -> list[int]:
        if self.boss_npc_ids:
            order = {nid: i for i, nid in enumerate(self.boss_npc_ids)}
            ids = [a.id for a in self.data.actors.values() if a.npc_id in order and self.tracks.has(a.id)]
            return sorted(ids, key=lambda aid: order[self.data.actors[aid].npc_id])
        scored = []
        for aid in self._npcs:
            tr = self.tracks.track(aid)
            scored.append((int(tr.max_hp.max()) if tr is not None and len(tr) else 0, aid))
        scored.sort(reverse=True)
        return [aid for _hp, aid in scored[:2]]

    def default_lanes(self) -> list[Lane]:
        A = self.data.actors
        return [
            Lane(
                "deaths",
                "死亡",
                "#c9c9c9",
                [LaneItem(t, shape="diamond", label=A[aid].short_name) for t, aid in self._deaths],
                help="玩家死亡。",
            )
        ]

    def default_log(self) -> list[LogEntry]:
        A = self.data.actors
        out = [
            LogEntry(0, [Seg("数据提示", "#e0a030", badge=True), Seg(f" {message}（{count} 条）")])
            for message, count in self.data.diagnostics.items()
        ]
        for t, aid in self._deaths:
            a = A[aid]
            out.append(
                LogEntry(
                    t,
                    [
                        Seg("死亡", "#c9c9c9", badge=True),
                        Seg(" " + a.short_name, class_color(a.class_name), bold=True),
                    ],
                    "deaths",
                )
            )
        return out

    def phase_at(self, t: float) -> Phase:
        cur = self.phases[0]
        for p in self.phases:
            if p.t <= t:
                cur = p
        return cur

    def unit_glyph_at(self, aid: int, t: float) -> tuple[str, str] | None:
        """Optional letter drawn inside a unit icon: ``(text, color)``."""
        return None

    def in_arena(self, x: float, y: float) -> bool:
        """World point on the encounter floor. No arena means the whole map is in play."""
        if not math.isfinite(x) or not math.isfinite(y):
            return False
        box = self.arena
        if box is None:
            return True
        x0, x1, y0, y1 = box
        return x0 <= x <= x1 and y0 <= y <= y1

    def _index_frame_auras(self) -> None:
        players = frozenset(actor.id for actor in self.data.players())
        declarations = tuple(self.frame_auras)
        key = (len(self.data.events), self.data.fight.duration_ms, players, declarations)
        if self.data.events is self._frame_events and key == self._frame_key:
            return
        indexed: dict[str, Intervals] = {}
        for aura in declarations:
            spell_ids = {spell_id for spell_id, _icon in aura.spells}
            indexed[aura.key] = aura_intervals(
                self.data,
                spell_ids,
                dst_filter=players.__contains__,
                source_independent=aura.source_independent,
            )
        self._frame_iv = indexed
        self._frame_events = self.data.events
        self._frame_key = key

    def active_frame_auras(self, actor_id: int, t: float) -> tuple[tuple[str, str], ...]:
        """``(key, icon stem)`` for debuffs in ``frame_auras`` that are up on this player."""
        out: list[tuple[str, str]] = []
        for aura in self.frame_auras:
            intervals = self._frame_iv.get(aura.key)
            if intervals is None:
                continue
            up = {iv.payload for iv in intervals.active(t) if iv.actor == actor_id}
            for spell_id, icon in aura.spells:
                if spell_id in up:
                    out.append((aura.key, icon))
        return tuple(out)

    def facing_at(self, actor_id: int, t: float) -> float | None:
        """Direction to draw for this unit, or None to keep the logged facing.

        Logged facing on an incoming hit is not the way a boss is cleaving. A module
        overrides this when the combat target is known.
        """
        del actor_id, t
        return None

    def units_at(self, t: float) -> list[int]:
        """Non-player units to draw at time t."""
        return [aid for aid in self._npcs if self.tracks.present(aid, t) and not self.tracks.is_dead(aid, t)]

    def overlays_at(self, t: float) -> list[Prim]:
        return []

    def hud_at(self, t: float) -> list[HudLine]:
        p = self.phase_at(t)
        return [HudLine(p.name, "#b58cff")]

    def bars_at(self, t: float) -> list[Bar]:
        out = []
        for aid in self._bosses:
            pose = self.tracks.pose(aid, t)
            if pose is None:
                continue
            present = self.tracks.present(aid, t) and t >= self.tracks.first_seen.get(aid, 0)
            dead = self.tracks.is_dead(aid, t)
            frac = 0.0 if dead else pose.hp_frac
            out.append(
                Bar(
                    self.data.actors[aid].name, frac, "#d9534f", f"{frac * 100:.0f}%", dim=not present or dead
                )
            )
        return out

    def status_at(self, t: float) -> list[StatusSection]:
        return []


@dataclass(slots=True)
class WclSlice:
    """One WCL ``events`` stream a boss needs. The same module analyses local logs and API pulls.

    ``data_type`` is a WCL ``EventDataType`` (``Casts``, ``DamageTaken``, ``All``, ...).
    ``hostility`` is ``Friendlies`` or ``Enemies``; empty means both.
    ``filter`` is a WCL ``filterExpression``. ``resources`` asks for coordinates and facing.
    """

    label: str
    data_type: str
    hostility: str = ""
    resources: bool = False
    filter: str = ""


class BossModule:
    """Factory product: ``registry.module_for(encounter_id)`` returns one of these.

    A new boss is a subclass with ``encounter_ids``, ``analyze``, and ``wcl_slices``.
    """

    encounter_ids: ClassVar[tuple[int, ...]] = ()
    name: ClassVar[str] = "Generic"

    def analyze(self, data: FightData, tracks: Tracks) -> Analysis:
        an = Analysis(data, tracks)
        an.title = data.fight.name
        an.lanes = an.default_lanes()
        an.log = an.default_log()
        return an

    def analyze_with_parameters(
        self,
        data: FightData,
        tracks: Tracks,
        parameters: Mapping[str, ParameterValue] | None = None,
    ) -> Analysis:
        """Build a configured result, retaining compatibility with existing ``analyze`` hooks.

        Expensive modules can override this to build derived data with final parameters once.
        """
        analysis = self.analyze(data, tracks)
        analysis.apply_parameters(parameters if parameters is not None else analysis.parameter_values)
        return analysis

    def wcl_slices(self, report: dict, fight: dict) -> tuple[WclSlice, ...]:
        """Events to download for this boss. The default is enough to draw units and deaths."""
        del report, fight
        return (
            WclSlice("敌方施法", "Casts", hostility="Enemies", resources=True),
            WclSlice("玩家受伤", "DamageTaken", hostility="Friendlies", resources=True),
            WclSlice("玩家治疗", "Healing", hostility="Friendlies", resources=True),
            WclSlice("玩家施法", "Casts", hostility="Friendlies", resources=True),
            WclSlice("死亡", "Deaths"),
        )
