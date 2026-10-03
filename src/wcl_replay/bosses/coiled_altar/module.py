# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from __future__ import annotations

from collections.abc import Mapping

from ...core.models import FightData
from ...core.tracks import Tracks
from ..base import (
    Analysis,
    AnalysisParameter,
    Bar,
    BossModule,
    FrameAura,
    HudLine,
    Line,
    LogEntry,
    ParameterValue,
    Phase,
    Prim,
    Seg,
    StatusSection,
    UnitStyle,
    WclSlice,
    fmt_secs,
)
from ..registry import register
from . import constants as C
from .p1 import P1Model
from .p2 import P2Model
from .wcl import slices as wcl_slices

# Icons are the retail spell icons. Labels are the spell name plus the raid's nickname.
# 恐惧行军 is the march itself, and the same aura again after a ghost reaches its target.
FRAME_AURAS = (
    FrameAura(
        "entombed",
        "墓缚（点名大圈）",
        ((C.ENTOMBED, "ability_demonhunter_shatteredsouls"),),
        "点名大圈。",
    ),
    FrameAura(
        "fixate",
        "令人不安的凝视（被魂钉）",
        ((C.FIXATE, "ability_fixated_state_purple"),),
        "魂正在看这个玩家。",
        source_independent=True,
    ),
    FrameAura(
        "resonance",
        "恶毒共鸣（撞魂）",
        ((C.RESONANCE, "spell_malevolent_resonance"),),
        "两个魂撞在一起之后，被盯上的玩家每秒受伤。地图上这个人会红色闪光。",
    ),
    FrameAura(
        "caught",
        "恐惧行军（被心控）",
        ((C.POSSESSED, "spell_nzinsanity_fearofdeath"),),
        "被附身走向台边，以及魂追上之后，身上都是这个效果。",
    ),
    FrameAura(
        "carry",
        "烈性毒液 / 诱变毒液（搬球）",
        (
            (C.GREEN_CARRY, "ability_creature_disease_02"),
            (C.PURPLE_CARRY, "ability_creature_disease_03"),
        ),
        "手上的烈性毒液（绿）或诱变毒液（紫）。",
    ),
)

GHOST_PARAMETERS = (
    AnalysisParameter(
        "ghost_motion",
        "移动模型",
        "accel",
        choices=(("constant", "匀速模型"), ("accel", "加速模型")),
        tooltip=(
            "匀速模型：整段追人用同一个速度。面向从魂身上移开后，先停顿再走。"
            "加速模型：这次凝视一开始用起始速度，在加速时间内线性变到终止速度，之后保持终止速度。"
            "玩家看着魂时魂仍然停下，加速计时不因此暂停。"
        ),
        settings_key="ghost_motion",
    ),
    AnalysisParameter(
        "ghost_speed",
        "魂速度",
        C.GHOST_SPEED,
        maximum=10.0,
        suffix="码/秒",
        tooltip="日志没有记录恐惧具象的移动速度。改数字即生效，滚轮每格 0.1 码/秒。",
        visible_when=(("ghost_motion", "constant"),),
        settings_key="ghost_speed",
    ),
    AnalysisParameter(
        "ghost_pause_s",
        "停顿时间",
        C.GHOST_PAUSE_S,
        maximum=10.0,
        suffix="秒",
        tooltip="玩家把朝向从魂身上移开之后，再停这么多秒才开始走。看着魂时仍然马上停下。改数字即生效，滚轮每格 0.1 秒。",
        visible_when=(("ghost_motion", "constant"),),
        settings_key="ghost_pause_s",
    ),
    AnalysisParameter(
        "ghost_start_speed",
        "起始速度",
        C.GHOST_START_SPEED,
        maximum=10.0,
        suffix="码/秒",
        tooltip="这次凝视刚开始时的速度。改数字即生效，滚轮每格 0.1 码/秒。",
        visible_when=(("ghost_motion", "accel"),),
        settings_key="ghost_start_speed",
    ),
    AnalysisParameter(
        "ghost_end_speed",
        "终止速度",
        C.GHOST_END_SPEED,
        maximum=10.0,
        suffix="码/秒",
        tooltip="加速时间走完之后保持的速度。改数字即生效，滚轮每格 0.1 码/秒。",
        visible_when=(("ghost_motion", "accel"),),
        settings_key="ghost_end_speed",
    ),
    AnalysisParameter(
        "ghost_accel_s",
        "加速时间",
        C.GHOST_ACCEL_S,
        maximum=20.0,
        suffix="秒",
        tooltip="从这次凝视开始，用这么多秒从起始速度线性变到终止速度。改数字即生效，滚轮每格 0.1 秒。",
        visible_when=(("ghost_motion", "accel"),),
        settings_key="ghost_accel_s",
    ),
    AnalysisParameter(
        "ghost_face_deg",
        "面对角度",
        C.GHOST_FACE_DEG,
        maximum=90.0,
        suffix="°",
        prefix="±",
        step=1.0,
        tooltip="玩家朝向落在魂方向左右各这么多度以内时，这一拍魂不动。改数字即生效，精确到 0.1°；滚轮每格 1°。日志里没有这个角度。",
        settings_key="ghost_face_deg",
    ),
)


class CoiledAltarAnalysis(Analysis):
    title = "盘卷祭坛"
    boss_npc_ids = (C.NPC_ZULJAN, C.NPC_MALACRASS)
    hidden_npc_ids = frozenset({C.NPC_GREEN, C.NPC_PURPLE, C.NPC_AXE})
    frame_auras = FRAME_AURAS
    parameters = GHOST_PARAMETERS
    parameter_note = "日志不记录魂相关数据。图中的魂由上面这些参数推算而来，不一定真实，仅供参考。"

    def __init__(self, data: FightData, tracks: Tracks):
        super().__init__(data, tracks)
        self.arena = (*C.ALTAR_X, *C.ALTAR_Y)
        orbs = (C.NPC_GREEN, C.NPC_PURPLE)
        self.stack_extras = (("orb", "球"),) if any(a.npc_id in orbs for a in data.actors.values()) else ()
        self.times = [e.t for e in data.events]
        self.p2_start = self._p2_start()
        self.p3_start = self._p3_start()
        self.p1 = P1Model(data, tracks, self.times, self.p2_start)
        self.p2 = P2Model(data, tracks, self.times, self.p2_start) if self.p2_start is not None else None
        self.phases = [Phase(0, "P1 · 祖尔加", "P1")]
        if self.p2_start is not None:
            self.phases.append(Phase(self.p2_start, "P2 · 妖术领主玛拉卡斯", "P2"))
        if self.p3_start is not None:
            self.phases.append(Phase(self.p3_start, "P3 · 盘卷联合", "P3"))
        self.title = f"{data.fight.name} · {'击杀' if data.fight.kill else '灭团'}"

        self.lanes = self.p1.lanes() + (self.p2.lanes() if self.p2 else []) + self.default_lanes()
        log = self.p1.log() + (self.p2.log() if self.p2 else []) + self.default_log()
        if self.p3_start is not None:
            log.append(
                LogEntry(
                    self.p3_start,
                    [Seg("阶段三", C.C_PHASE, badge=True), Seg(" 祖尔加回来，场上重新出现毒液球")],
                )
            )
        self.log = sorted(log, key=lambda e: e.t)
        self.unit_styles = self._styles()
        if self.p2:
            self._npcs += [g.aid for g in self.p2.ghosts if g.aid not in self._npcs]
        self.apply_parameters(self.parameter_values)

    def _p2_start(self) -> int | None:
        zul = {a.id for a in self.data.actors_by_npc(C.NPC_ZULJAN)}
        for e in self.data.events:
            if e.type == "UNIT_DIED" and e.dst in zul:
                return e.t
        mal = [a.id for a in self.data.actors_by_npc(C.NPC_MALACRASS) if self.tracks.has(a.id)]
        if mal:
            t = self.tracks.track(mal[0]).first
            return int(t) if t > 5000 else None
        return None

    def _p3_start(self) -> int | None:
        if self.p2_start is None:
            return None
        for e in self.data.events:
            if e.spell_id == C.DEFILEMENT and e.type == "SPELL_CAST_SUCCESS" and e.t >= self.p2_start:
                return e.t
        return None

    def _styles(self) -> dict[int, UnitStyle]:
        styles: dict[int, UnitStyle] = {}
        for a in self.data.actors_by_npc(C.NPC_ZULJAN):
            styles[a.id] = UnitStyle("#c0392b", 15, "祖尔加", "#ffb0a0", show_facing=True, hp_bar=True)
        for a in self.data.actors_by_npc(C.NPC_MALACRASS):
            styles[a.id] = UnitStyle("#8e44ad", 15, "玛拉卡斯", "#e0b0ff", show_facing=True, hp_bar=True)
        if self.p2:
            styles.update(self.p2.unit_styles())
        return styles

    def apply_ghost_motion(
        self,
        speed: float,
        face_deg: float,
        mode: str = "constant",
        start_speed: float | None = None,
        end_speed: float | None = None,
        accel_s: float | None = None,
        pause_s: float | None = None,
    ) -> None:
        if self.p2 is not None:
            self.p2.set_motion(speed, face_deg, mode, start_speed, end_speed, accel_s, pause_s)

    def apply_parameters(self, values: Mapping[str, ParameterValue]) -> None:
        super().apply_parameters(values)
        if self.p2 is not None:
            params = self.parameter_values
            self.p2.set_motion(
                speed=float(params["ghost_speed"]),
                face_deg=float(params["ghost_face_deg"]),
                mode=str(params["ghost_motion"]),
                start_speed=float(params["ghost_start_speed"]),
                end_speed=float(params["ghost_end_speed"]),
                accel_s=float(params["ghost_accel_s"]),
                pause_s=float(params["ghost_pause_s"]),
            )

    def set_ghost_speed(self, speed: float) -> None:
        if self.p2 is not None:
            self.p2.set_motion(speed=speed)

    def set_ghost_facing(self, face_deg: float) -> None:
        if self.p2 is not None:
            self.p2.set_motion(face_deg=face_deg)

    def unit_glyph_at(self, aid: int, t: float) -> tuple[str, str] | None:
        if self.p2 is None:
            return None
        for ghost in self.p2.ghosts:
            if ghost.aid == aid:
                return ghost.mark_at(self.data, t)
        return None

    def in_p2(self, t: float) -> bool:
        return self.p2 is not None and self.p2_start is not None and t >= self.p2_start

    def in_p3(self, t: float) -> bool:
        return self.in_p2(t) and self.p3_start is not None and t >= self.p3_start

    def _platform(self) -> list[Line]:
        x0, x1 = C.PLATFORM_X
        y0, y1 = C.PLATFORM_Y
        c, w = C.C_PLATFORM, 2.4
        return [
            Line(x0, y0, x1, y0, c, w, 0.95),
            Line(x1, y0, x1, y1, c, w, 0.95),
            Line(x1, y1, x0, y1, c, w, 0.95),
            Line(x0, y1, x0, y0, c, w, 0.95),
        ]

    def overlays_at(self, t: float) -> list[Prim]:
        if not self.in_p2(t):
            routes = self.p1.route_overlays(t)
            prims = self.p1.overlays(t)
        elif not self.in_p3(t):
            routes = self.p2.route_overlays(t) if self.p2 else []
            prims = self.p2.overlays(t) if self.p2 else []
        else:
            routes = self.p1.route_overlays(t)
            if self.p2:
                routes += self.p2.route_overlays(t)
            prims = self.p2.overlays(t) if self.p2 else []
            prims += [p for p in self.p1.overlays(t) if p.layer in ("globules", "guillotine", "severs")]
        return routes + self._platform() + prims

    def hud_at(self, t: float) -> list[HudLine]:
        if not self.in_p2(t):
            return self.p1.hud(t)
        if not self.in_p3(t):
            return self.p2.hud(t)
        lines = [HudLine("P3 · 盘卷联合", C.C_PHASE, key="phase")]
        lines += [h for h in self.p1.hud(t) if h.key in ("guillotine", "blighted")]
        lines += [h for h in self.p2.hud(t) if h.key != "phase"]
        nd = next((x for x in self.p1.deluges if x > t), None)
        if nd is not None:
            lines.append(HudLine(f"剧毒洪流 {fmt_secs(nd - t)}", C.C_DELUGE))
        floor = self.p1.floor_at(t)
        hands = [iv.payload for iv in self.p1.carry_iv.active(t)]
        if floor or hands:
            ng = sum(g.color == "green" for g in floor)
            hg = sum(c.color == "green" for c in hands)
            lines.append(
                HudLine(
                    f"毒液球 地上 {ng} 绿/{len(floor) - ng} 紫 · 手上 {hg} 绿/{len(hands) - hg} 紫",
                    C.C_GREEN,
                )
            )
        return lines

    def status_at(self, t: float) -> list[StatusSection]:
        if not self.in_p2(t):
            return self.p1.status(t)
        if not self.in_p3(t):
            return self.p2.status(t)
        orbs = [s for s in self.p1.status(t) if s.key in ("orbs", "carry")]
        return self.p2.status(t) + orbs

    def bars_at(self, t: float) -> list[Bar]:
        bars = super().bars_at(t)
        if self.p2 and self.in_p2(t):
            for aid in self.p2.soulcoilers:
                if self.tracks.present(aid, t) and not self.tracks.is_dead(aid, t):
                    pose = self.tracks.pose(aid, t)
                    if pose:
                        bars.append(
                            Bar(
                                f"怨毒盘魂者 · 盾 {self.p2.shield_at(aid, t)}/2",
                                pose.hp_frac,
                                "#2a8f8a",
                                f"{pose.hp_frac * 100:.0f}%",
                            )
                        )
        return bars


@register
class CoiledAltar(BossModule):
    encounter_ids = (C.ENCOUNTER_ID,)
    name = "盘卷祭坛"

    def analyze(self, data: FightData, tracks: Tracks) -> Analysis:
        return CoiledAltarAnalysis(data, tracks)

    def wcl_slices(self, report: dict, fight: dict) -> tuple[WclSlice, ...]:
        return wcl_slices(report, fight)
