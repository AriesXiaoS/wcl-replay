# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Impact residues and partial observations preserve the evidence available in a fight."""

from __future__ import annotations

import pytest

from wcl_replay.bosses.base import Analysis, Cone, FrameAura, aura_intervals
from wcl_replay.bosses.coiled_altar import constants as C
from wcl_replay.bosses.coiled_altar.p1 import globules_of
from wcl_replay.bosses.coiled_altar.p2 import P2Model
from wcl_replay.core.models import Actor, ActorKind, Event, Fight, FightData, Sample
from wcl_replay.core.tracks import Tracks, angle_to
from wcl_replay.sources.wcl_api.convert import convert


def _data(events, samples=None):
    return FightData(
        Fight(1, C.ENCOUNTER_ID, "盘卷祭坛", 16, 20, 10000, False),
        {
            0: Actor(0, "Player-0", "玩家", ActorKind.PLAYER),
            1: Actor(1, "boss", "玛拉卡斯", ActorKind.NPC, npc_id=C.NPC_MALACRASS, hostile=True),
            2: Actor(2, "source2", "来源二", ActorKind.NPC),
            3: Actor(3, "source3", "来源三", ActorKind.NPC),
        },
        events,
        samples or {},
    )


def _sample(t, x, y=0.0, hp=100):
    return Sample(t, x, y, 0.0, hp, 100)


@pytest.mark.parametrize("boss_dies", [False, True])
def test_soul_sever_preview_follows_but_impact_residue_stays_fixed_when_seeking(boss_dies):
    events = [
        Event(0, "SPELL_CAST_START", src=1, spell_id=C.SOUL_SEVER),
        Event(1000, "SPELL_CAST_SUCCESS", src=1, spell_id=C.SOUL_SEVER),
        Event(1000, "SPELL_DAMAGE", src=1, dst=0, spell_id=C.SOUL_SEVER),
    ]
    if boss_dies:
        events.append(Event(1200, "UNIT_DIED", dst=1))
    data = _data(
        events,
        {
            0: [_sample(0, 10), _sample(1000, 10, 10), _sample(1500, 50)],
            1: [_sample(0, 0), _sample(1000, 10), _sample(1500, 20, hp=1 if boss_dies else 100)],
        },
    )
    tracks = Tracks(data)
    model = P2Model(data, tracks, [event.t for event in data.events], 0)
    impact = (*tracks.position(1, 1000), angle_to(tracks.position(1, 1000), tracks.position(0, 1000)))
    for t in (1500, 500, 1400, 0, 1000, 1600):
        (cone,) = [prim for prim in model.overlays(t) if isinstance(prim, Cone)]
        expected = impact
        if t < 1000:
            boss = tracks.position(1, t)
            expected = (*boss, angle_to(boss, tracks.position(0, t)))
        assert (cone.x, cone.y, cone.direction) == pytest.approx(expected)
    assert not any(isinstance(prim, Cone) for prim in model.overlays(1601))


def test_later_summon_does_not_hide_an_earlier_wcl_instance_drop():
    report = {
        "masterData": {
            "actors": [
                {"id": 1, "type": "NPC", "name": "球", "gameID": C.NPC_GREEN},
                {"id": 2, "type": "NPC", "name": "祖尔加", "gameID": C.NPC_ZULJAN},
            ]
        }
    }

    def place(t, north):
        return {
            "timestamp": t,
            "type": "cast",
            "sourceID": 1,
            "sourceInstance": 2,
            "abilityGameID": C.GREEN_PLACE,
            "x": 0,
            "y": north * 100,
            "hitPoints": 1,
            "maxHitPoints": 1,
        }

    data = convert(
        report,
        {"id": 1, "encounterID": C.ENCOUNTER_ID, "startTime": 0, "endTime": 10000},
        [
            place(1000, 10),
            {
                "timestamp": 5000,
                "type": "summon",
                "sourceID": 2,
                "targetID": 1,
                "targetInstance": 2,
                "abilityGameID": C.GREEN_SUMMON,
            },
            place(5010, 20),
        ],
    )
    tracks = Tracks(data)
    orb = data.actors_by_npc(C.NPC_GREEN)[0].id
    assert tracks.spawn[orb] == 5000
    assert tracks.appear_time(orb) == 1000
    assert not tracks.present(orb, 999)
    assert tracks.position(orb, 1000) == (10.0, 0.0)
    assert [(globule.origin, globule.start, globule.x) for globule in globules_of(data, tracks)] == [
        ("drop", 1000, 10.0),
        ("spawn", 5000, 20.0),
    ]


@pytest.mark.parametrize("earlier_sample", [False, True])
def test_appearance_respects_earlier_activity_or_coordinate_evidence(earlier_sample):
    events = [Event(5000, "SPELL_SUMMON", dst=1)]
    samples = {1: [_sample(1000 if earlier_sample else 5500, 10)]}
    if not earlier_sample:
        events.insert(0, Event(1000, "SPELL_CAST_START", src=1, spell_id=1))
    tracks = Tracks(_data(events, samples))
    assert tracks.appear_time(1) == 1000
    assert tracks.present(1, 1000)
    assert tracks.appear_time(999) == 0


def test_first_wcl_aura_refresh_starts_at_observation_and_ends_on_removal():
    data = convert(
        {"masterData": {"actors": [{"id": 1, "type": "Player", "name": "玩家"}]}},
        {"id": 1, "encounterID": C.ENCOUNTER_ID, "startTime": 0, "endTime": 10000},
        [
            {"timestamp": 1000, "type": "refreshdebuff", "targetID": 1, "abilityGameID": C.GREEN_CARRY},
            {"timestamp": 2000, "type": "removedebuff", "targetID": 1, "abilityGameID": C.GREEN_CARRY},
        ],
    )
    intervals = aura_intervals(data, C.GREEN_CARRY)
    assert [(iv.start, iv.end) for iv in intervals] == [(1000, 2000)]
    assert not intervals.active(999)
    assert len(intervals.active(1000)) == len(intervals.active(1999)) == 1
    assert not intervals.active(2000)


def test_refresh_keeps_an_applied_aura_continuous_without_resetting_its_source():
    data = _data(
        [
            Event(500, "SPELL_AURA_APPLIED", src=2, dst=0, spell_id=C.GREEN_CARRY),
            Event(1000, "SPELL_AURA_REFRESH", src=3, dst=0, spell_id=C.GREEN_CARRY),
            Event(1500, "SPELL_AURA_REFRESH", src=2, dst=0, spell_id=C.GREEN_CARRY),
            Event(2000, "SPELL_AURA_REMOVED", dst=0, spell_id=C.GREEN_CARRY),
        ]
    )
    assert [(iv.start, iv.end, iv.src) for iv in aura_intervals(data, C.GREEN_CARRY)] == [(500, 2000, 2)]


def test_refresh_preserves_independent_aura_sources_and_source_less_removal():
    data = _data(
        [
            Event(500, "SPELL_AURA_APPLIED", src=2, dst=0, spell_id=C.FIXATE),
            Event(1000, "SPELL_AURA_REFRESH", src=3, dst=0, spell_id=C.FIXATE),
            Event(1500, "SPELL_AURA_REFRESH", src=2, dst=0, spell_id=C.FIXATE),
            Event(2000, "SPELL_AURA_REMOVED", src=2, dst=0, spell_id=C.FIXATE),
            Event(2500, "SPELL_AURA_REMOVED", dst=0, spell_id=C.FIXATE),
        ]
    )
    intervals = aura_intervals(data, C.FIXATE, source_independent=True)
    assert [(iv.src, iv.start, iv.end) for iv in intervals] == [(2, 500, 2000), (3, 1000, 2500)]

    class FixateAnalysis(Analysis):
        frame_auras = (FrameAura("fixate", "凝视", ((C.FIXATE, "fixate"),), source_independent=True),)

    analysis = FixateAnalysis(data, Tracks(data))
    assert analysis.active_frame_auras(0, 1500) == (("fixate", "fixate"),)
    assert analysis.active_frame_auras(0, 2000) == (("fixate", "fixate"),)
    assert not analysis.active_frame_auras(0, 2500)


def test_unremoved_first_refresh_is_clamped_to_the_fight_end():
    data = _data([Event(1000, "SPELL_AURA_REFRESH", dst=0, spell_id=C.GREEN_CARRY)])
    assert [(iv.start, iv.end) for iv in aura_intervals(data, C.GREEN_CARRY)] == [(1000, 10000)]
