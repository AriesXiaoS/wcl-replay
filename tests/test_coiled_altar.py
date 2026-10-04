# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from __future__ import annotations

import math

import pytest
from fixture_log import (
    BOSS_GUID,
    DPS_GUID,
    G1_GUID,
    G2_GUID,
    G3_GUID,
    MAL_GUID,
    P1_GUID,
    P2_GUID,
    P3_GUID,
    TANK_GUID,
)

from wcl_replay.bosses import discover, module_for
from wcl_replay.bosses.base import Circle, Cone, Dot, Line, Path, UnitTag, aura_intervals
from wcl_replay.bosses.coiled_altar import CoiledAltar, CoiledAltarAnalysis
from wcl_replay.bosses.coiled_altar import constants as C
from wcl_replay.bosses.coiled_altar.common import angle_diff
from wcl_replay.bosses.coiled_altar.p1 import globules_of
from wcl_replay.core.models import Actor, ActorKind, Event, Fight, FightData, Sample
from wcl_replay.core.tracks import Tracks, angle_to
from wcl_replay.pipeline import analyze


def aid(data, guid):
    return next(a.id for a in data.actors.values() if a.guid == guid)


def test_dose_keeps_an_open_aura_and_can_start_one():
    fight = Fight(1, 1, "x", 16, 20, 10_000, False)
    events = [
        Event(1000, "SPELL_AURA_APPLIED", dst=1, spell_id=5),
        Event(2000, "SPELL_AURA_APPLIED_DOSE", dst=1, spell_id=5, amount=2),
        Event(4000, "SPELL_AURA_REMOVED", dst=1, spell_id=5),
        Event(5000, "SPELL_AURA_APPLIED_DOSE", dst=2, spell_id=5, amount=1),
    ]
    ivs = list(aura_intervals(FightData(fight, {}, events, {}), 5))
    assert [(iv.actor, iv.start, iv.end) for iv in ivs] == [(1, 1000, 4000), (2, 5000, 10_000)]


def test_registry_picks_module():
    discover()
    assert isinstance(module_for(3429), CoiledAltar)


def test_globule_state_machine(fight_data):
    _tracks, an = analyze(fight_data)
    assert isinstance(an, CoiledAltarAnalysis)
    p1 = an.p1
    g = {gl.aid: gl for gl in p1.globules}
    g1, g2, pu, drop = (g[aid(fight_data, x)] for x in (G1_GUID, G2_GUID, P1_GUID, P2_GUID))
    dps = aid(fight_data, DPS_GUID)

    assert [gl.origin for gl in (g1, g2, pu, drop)] == ["spawn", "spawn", "spawn", "drop"]
    assert (pu.end, pu.picked_by, pu.end_reason) == (2000, dps, "picked")
    (carry,) = p1.carries
    assert carry.source is pu and carry.dropped is drop and drop.dropped_by == dps

    (sever,) = p1.severs
    assert sever.target == aid(fight_data, TANK_GUID)
    assert (sever.cast_start, sever.stacks, sever.floor_before, sever.floor_after) == (10000, 1, 3, 2)
    assert sever.popped == [g1] and g1.popped_by == 0

    assert an.p2_start == 20000 and an.p3_start == 24000
    assert [p.short for p in an.phases] == ["P1", "P2", "P3"]
    assert set(map(id, p1.phase_popped)) == {id(g2), id(drop)} and p1.phase_pop_stacks == 2
    assert [len(p1.floor_at(t)) for t in (500, 1500, 5000, 8000, 15000, 25000)] == [0, 3, 2, 3, 2, 2]

    g3, side = (g[aid(fight_data, x)] for x in (G3_GUID, P3_GUID))
    (blight,) = p1.blighted
    assert blight.stacks == 1 and blight.popped == [g3]
    assert (g3.end, g3.end_reason) == (29000, "blighted")
    assert side.end is None and len(p1.floor_at(29500)) == 1


def test_p1_outputs(fight_data):
    _tracks, an = analyze(fight_data)
    prims = an.overlays_at(12000)
    cones = [p for p in prims if isinstance(p, Cone)]
    dots = [p for p in prims if isinstance(p, Dot)]
    assert len(cones) == 1 and cones[0].radius == 35 and not cones[0].dashed  # Sever is being cast
    assert len(dots) == 3 and sum(d.glow for d in dots) == 1  # G1 is about to pop
    assert any(isinstance(p, Cone) and p.dashed for p in an.overlays_at(8000))  # preview before the cast

    hud = [h.text for h in an.hud_at(11000)]
    assert any(t.startswith("撕裂 2.0 s → Tank") for t in hud)
    titles = [s.title for s in an.status_at(5000)]
    assert "毒液球" in titles and "手上的球" in titles

    texts = ["".join(s.text for s in e.segments) for e in an.log]
    assert any("+1 毒液爆裂 · 地面 3 → 2" in t for t in texts)
    assert any("放下了" in t for t in texts)
    assert {lane.id for lane in an.lanes} >= {"globules", "severs", "guillotine", "deaths"}
    series = next(lane.series for lane in an.lanes if lane.id == "globules")
    assert (20000, 0.0) in series and series[-1][1] == 1.0


def test_cleave_aims_at_the_tank_when_logged_facing_points_elsewhere(fight_data):
    boss = aid(fight_data, BOSS_GUID)
    tank = aid(fight_data, TANK_GUID)
    fight_data.events.append(Event(4000, "SWING_DAMAGE", src=boss, dst=tank))
    fight_data.samples[boss].append(Sample(4500, 0.0, 0.0, math.pi, 100, 100))
    tracks, an = analyze(fight_data)
    assert isinstance(an, CoiledAltarAnalysis)
    origin = tracks.position(boss, 6000)
    dest = tracks.position(tank, 6000)
    logged = tracks.pose(boss, 6000)
    assert origin and dest and logged is not None
    toward_tank = angle_to(origin, dest)
    assert angle_diff(logged.facing, math.pi) < 0.01
    aimed = an.facing_at(boss, 6000)
    assert aimed is not None and angle_diff(aimed, toward_tank) < 0.01
    cone = next(p for p in an.overlays_at(12000) if isinstance(p, Cone))
    aimed = an.facing_at(boss, 12000)
    assert aimed is not None and angle_diff(cone.direction, aimed) < 0.01
    assert angle_diff(cone.direction, math.pi) > 1


def test_later_phases_draw_their_own_cleave(fight_data):
    _tracks, an = analyze(fight_data)
    soul = [p for p in an.overlays_at(21500) if isinstance(p, Cone)]
    assert len(soul) == 1 and soul[0].radius == 45 and not soul[0].dashed
    assert soul[0].label.startswith("灵魂撕裂")
    mal, tank = aid(fight_data, MAL_GUID), aid(fight_data, TANK_GUID)
    origin, dest = _tracks.position(mal, 21500), _tracks.position(tank, 21500)
    aimed = an.facing_at(mal, 21500)
    assert origin and dest and aimed is not None
    assert angle_diff(aimed, angle_to(origin, dest)) < 0.01
    assert angle_diff(soul[0].direction, aimed) < 0.01
    blight = [p for p in an.overlays_at(27500) if isinstance(p, Cone)]
    assert len(blight) == 1 and blight[0].radius == 45 and not blight[0].dashed
    assert blight[0].label.startswith("凋零撕裂")
    assert any(h.text.startswith("凋零撕裂") for h in an.hud_at(27500))
    dots = [p for p in an.overlays_at(27500) if isinstance(p, Dot)]
    assert len(dots) == 2 and sum(d.glow for d in dots) == 1


@pytest.mark.parametrize("spell_id", [C.SEVER, C.BLIGHTED_SEVER])
def test_completed_frontals_keep_their_impact_geometry(spell_id):
    data = FightData(
        Fight(1, C.ENCOUNTER_ID, "盘卷祭坛", 16, 20, 20000, False),
        {
            0: Actor(0, "boss", "祖尔加", ActorKind.NPC, npc_id=C.NPC_ZULJAN, hostile=True),
            1: Actor(1, "tank", "坦克", ActorKind.PLAYER),
        },
        [
            Event(7000, "SPELL_CAST_START", src=0, spell_id=spell_id),
            Event(10000, "SPELL_CAST_SUCCESS", src=0, dst=1, spell_id=spell_id),
            Event(10000, "SPELL_AURA_APPLIED", src=0, dst=1, spell_id=C.SEVER_DEBUFF),
        ],
        {
            0: [
                Sample(9000, 0.0, 0.0, math.pi, 100, 100),
                Sample(10000, 0.0, 0.0, math.pi, 100, 100),
                Sample(10300, 5.0, 0.0, math.pi, 100, 100),
            ],
            1: [
                Sample(9000, 0.0, 5.0, 0.0, 100, 100),
                Sample(10000, 5.0, 0.0, 0.0, 100, 100),
                Sample(10300, 0.0, 5.0, 0.0, 100, 100),
            ],
        },
    )
    _, analysis = analyze(data)
    before = next(p for p in analysis.overlays_at(9000) if isinstance(p, Cone))
    assert angle_diff(before.direction, math.pi / 2) < 0.01
    impact = next(p for p in analysis.overlays_at(10000) if isinstance(p, Cone))
    assert (impact.x, impact.y, impact.direction) == (0.0, 0.0, 0.0)
    for t in (10300, 10599):
        after = next(p for p in analysis.overlays_at(t) if isinstance(p, Cone))
        assert (after.x, after.y, after.direction) == (impact.x, impact.y, impact.direction)
    assert not any(isinstance(p, Cone) for p in analysis.overlays_at(10601))


def test_platform_square_is_drawn_around_the_spawn(fight_data):
    _tracks, an = analyze(fight_data)
    assert an.arena == (*C.ALTAR_X, *C.ALTAR_Y)
    assert an.in_arena(*C.PLATFORM_CENTER)
    assert not an.in_arena(1582.8, 11.5)
    edges = [p for p in an.overlays_at(1000) if isinstance(p, Line) and p.color == C.C_PLATFORM]
    assert len(edges) == 4
    xs = {p.x1 for p in edges} | {p.x2 for p in edges}
    ys = {p.y1 for p in edges} | {p.y2 for p in edges}
    assert max(xs) - min(xs) == C.PLATFORM_SIDE
    assert max(ys) - min(ys) == C.PLATFORM_SIDE


def test_zuljan_is_drawn_again_after_he_returns(fight_data):
    _tracks, an = analyze(fight_data)
    zul, mal = aid(fight_data, BOSS_GUID), aid(fight_data, MAL_GUID)
    assert zul in an.units_at(10000) and mal not in an.units_at(10000)
    assert zul not in an.units_at(21000) and mal in an.units_at(21000)
    assert zul in an.units_at(25000)


def test_p3_draws_orbs(fight_data):
    _tracks, an = analyze(fight_data)
    before = [p for p in an.overlays_at(21000) if isinstance(p, Dot)]
    assert before == []
    dots = [p for p in an.overlays_at(25000) if isinstance(p, Dot)]
    assert sorted(d.size_px for d in dots) == [12, 20]
    assert {d.color for d in dots} == {"#5fd35f", "#b06cff"}
    assert an.hud_at(25000)[0].text.startswith("P3")
    assert "毒液球" in [s.title for s in an.status_at(25000)]


def test_p3_soak_mark(fight_data):
    _tracks, an = analyze(fight_data)
    dps = aid(fight_data, DPS_GUID)
    prims = an.overlays_at(26000)
    circles = [p for p in prims if isinstance(p, Circle) and p.layer == "guillotine"]
    assert len(circles) == 1 and circles[0].r == 9.0
    assert any(isinstance(p, UnitTag) and p.actor_id == dps and p.text == "分摊" for p in prims)
    assert any("冷酷处斩 → Dps" in h.text for h in an.hud_at(26000))


def test_p3_guillotine_counts_the_soak_after_it_lands(fight_data):
    _tracks, an = analyze(fight_data)
    texts = ["".join(s.text for s in e.segments) for e in an.log if e.lane == "guillotine"]
    assert any("冷酷处斩" in text and "2 人分摊" in text for text in texts)
    landed = [p for p in an.overlays_at(28100) if isinstance(p, Circle) and p.layer == "guillotine"]
    assert len(landed) == 1 and landed[0].label == "2 人"


def test_carry_and_ghost_routes_follow_the_mover(fight_data):
    _tracks, an = analyze(fight_data)

    def paths(t: float, color: str) -> list[Path]:
        return [p for p in an.overlays_at(t) if isinstance(p, Path) and p.color == color]

    def span(route: Path) -> float:
        return abs(route.points[-1][0] - route.points[0][0])

    assert paths(1000, C.C_PURPLE) == []  # not picked up yet
    mid = paths(4000, C.C_PURPLE)
    late = paths(6500, C.C_PURPLE)
    assert len(mid) == 1 and len(late) == 1
    route = late[0]
    assert route.dashed and route.alpha == C.PATH_ALPHA and route.layer == "globules"
    assert span(late[0]) > span(mid[0]) + 0.2
    assert late[0].points[-1][0] > -1.75  # still short of where the carry ends
    assert paths(7000, C.C_PURPLE) == []  # debuff dropped, the line is gone
    assert paths(12000, C.C_PURPLE) == []
    assert paths(21000, C.C_PURPLE) == []
    assert paths(25000, C.C_PURPLE) == []
    assert paths(21500, C.C_GHOST) == []
    assert paths(25000, C.C_GHOST) == []


def test_one_place_cast_is_one_orb_even_when_the_actor_id_is_reused():
    """WCL reuses sourceInstance for a dropped orb. The place-cast is the identity, for both sources."""
    actor = Actor(1, "wcl-44-1", "凝结的毒液追踪者", ActorKind.NPC, npc_id=C.NPC_GREEN)
    events = [
        Event(1000, "SPELL_SUMMON", dst=1, spell_id=C.GREEN_SUMMON),
        Event(1011, "SPELL_CAST_SUCCESS", src=1, spell_id=C.GREEN_PLACE),
        Event(5000, "SPELL_SUMMON", dst=1, spell_id=C.GREEN_SUMMON),
        Event(5012, "SPELL_CAST_SUCCESS", src=1, spell_id=C.GREEN_PLACE),
        Event(5012, "SPELL_CAST_SUCCESS", src=1, spell_id=C.PURPLE_PLACE_EXTRA),
        Event(9000, "SPELL_CAST_SUCCESS", src=1, spell_id=C.GREEN_PLACE),
    ]
    samples = {
        1: [
            Sample(1011, 5.0, 10.0, 0.0, 1, 1),
            Sample(5012, 8.0, 8.0, 0.0, 1, 1),
            Sample(9000, 1.0, -3.0, 0.0, 1, 1),
        ]
    }
    fight = Fight(1, 3429, "盘卷祭坛", 16, 20, 20_000, False)
    data = FightData(fight, {1: actor}, events, samples)
    got = [(g.origin, g.start, g.x, g.y) for g in globules_of(data, Tracks(data))]
    assert got == [("spawn", 1000, 5.0, 10.0), ("spawn", 5000, 8.0, 8.0), ("drop", 9000, 1.0, -3.0)]
