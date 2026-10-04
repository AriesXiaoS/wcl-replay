# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from __future__ import annotations

import pytest

from wcl_replay.bosses.coiled_altar import constants as C
from wcl_replay.bosses.coiled_altar.p1 import P1Model, globules_of
from wcl_replay.core.models import Actor, ActorKind, Event, Fight, FightData, Sample
from wcl_replay.core.tracks import Tracks
from wcl_replay.sources.wcl_api.convert import convert


def _data(events: list[Event], samples: dict[int, list[Sample]]) -> FightData:
    return FightData(
        Fight(1, C.ENCOUNTER_ID, "盘卷祭坛", 16, 20, 10_000, False),
        {
            0: Actor(0, "Player-0", "搬球玩家", ActorKind.PLAYER),
            10: Actor(10, "wave", "波次球", ActorKind.NPC, npc_id=C.NPC_GREEN),
            11: Actor(11, "drop", "放下的球", ActorKind.NPC, npc_id=C.NPC_GREEN),
        },
        events,
        samples,
    )


def _sample(t: int, x: float) -> Sample:
    return Sample(t, x, 0.0, 0.0, 100, 100)


@pytest.mark.parametrize(
    "carry_spell,summon_spell,place_spell",
    [
        (C.GREEN_CARRY, C.GREEN_SUMMON, C.GREEN_PLACE),
        (C.PURPLE_CARRY, C.PURPLE_SUMMON, C.PURPLE_PLACE),
    ],
)
def test_overlap_drop_and_wave_preserves_summon_identity_and_carrier(carry_spell, summon_spell, place_spell):
    data = _data(
        [
            Event(1000, "SPELL_AURA_APPLIED", dst=0, spell_id=carry_spell),
            Event(5000, "SPELL_AURA_REMOVED", dst=0, spell_id=carry_spell),
            Event(5000, "SPELL_SUMMON", dst=10, spell_id=summon_spell),
            Event(5010, "SPELL_CAST_SUCCESS", src=11, spell_id=place_spell),
            Event(5050, "SPELL_CAST_SUCCESS", src=10, spell_id=place_spell),
        ],
        {0: [_sample(5000, 0)], 10: [_sample(5050, 30)], 11: [_sample(5010, 0)]},
    )
    if carry_spell == C.PURPLE_CARRY:
        data.actors[10].npc_id = data.actors[11].npc_id = C.NPC_PURPLE
    tracks = Tracks(data)
    model = P1Model(data, tracks, [e.t for e in data.events], None)
    wave, drop = model.globules

    assert (wave.aid, wave.origin, wave.start) == (10, "spawn", 5000)
    assert (drop.aid, drop.origin, drop.start, drop.dropped_by) == (11, "drop", 5010, 0)
    assert model.carries[0].dropped is drop
    assert model.floor_at(5005) == [wave]
    assert model.floor_at(5050) == [wave, drop]


def test_target_less_summon_falls_back_to_time_and_color():
    data = _data(
        [
            Event(5000, "SPELL_SUMMON", spell_id=C.GREEN_SUMMON),
            Event(5005, "SPELL_CAST_SUCCESS", src=11, spell_id=C.PURPLE_PLACE),
            Event(5020, "SPELL_CAST_SUCCESS", src=10, spell_id=C.GREEN_PLACE),
        ],
        {10: [_sample(5020, 10)], 11: [_sample(5005, 20)]},
    )
    data.actors[11].npc_id = C.NPC_PURPLE
    orbs = globules_of(data, Tracks(data))

    assert [(g.aid, g.origin, g.start) for g in orbs] == [
        (10, "spawn", 5000),
        (11, "drop", 5005),
    ]


def test_target_less_summon_does_not_steal_known_target_cast():
    data = _data(
        [
            Event(5000, "SPELL_SUMMON", dst=10, spell_id=C.GREEN_SUMMON),
            Event(5010, "SPELL_SUMMON", spell_id=C.GREEN_SUMMON),
            Event(5015, "SPELL_CAST_SUCCESS", src=10, spell_id=C.GREEN_PLACE),
            Event(5020, "SPELL_CAST_SUCCESS", src=11, spell_id=C.GREEN_PLACE),
        ],
        {10: [_sample(5015, 10)], 11: [_sample(5020, 20)]},
    )
    orbs = globules_of(data, Tracks(data))

    assert [(g.aid, g.origin, g.start) for g in orbs] == [
        (10, "spawn", 5000),
        (11, "spawn", 5010),
    ]


def test_known_unmatched_target_does_not_claim_other_actors_cast():
    data = _data(
        [
            Event(5000, "SPELL_SUMMON", dst=10, spell_id=C.GREEN_SUMMON),
            Event(5010, "SPELL_CAST_SUCCESS", src=11, spell_id=C.GREEN_PLACE),
        ],
        {11: [_sample(5010, 20)]},
    )
    (orb,) = globules_of(data, Tracks(data))

    assert (orb.aid, orb.origin, orb.start) == (11, "drop", 5010)


def test_reused_actor_retains_separate_waves_and_later_drop():
    data = _data(
        [
            Event(1000, "SPELL_SUMMON", dst=10, spell_id=C.GREEN_SUMMON),
            Event(1020, "SPELL_CAST_SUCCESS", src=10, spell_id=C.GREEN_PLACE),
            Event(5000, "SPELL_SUMMON", dst=10, spell_id=C.GREEN_SUMMON),
            Event(5020, "SPELL_CAST_SUCCESS", src=10, spell_id=C.GREEN_PLACE),
            Event(7000, "SPELL_CAST_SUCCESS", src=10, spell_id=C.GREEN_PLACE),
        ],
        {10: [_sample(1020, 10), _sample(5020, 20), _sample(7000, 30)]},
    )
    orbs = globules_of(data, Tracks(data))

    assert [(g.aid, g.origin, g.start, g.x) for g in orbs] == [
        (10, "spawn", 1000, 10),
        (10, "spawn", 5000, 20),
        (10, "drop", 7000, 30),
    ]
    assert len({id(g) for g in orbs}) == 3


def test_wcl_instances_and_reused_drop_actor_keep_wave_and_carry_identity():
    report = {
        "startTime": 0,
        "masterData": {
            "actors": [
                {"id": 1, "type": "Player", "name": "搬球玩家"},
                {"id": 2, "type": "NPC", "name": "凝结的毒液追踪者", "gameID": C.NPC_GREEN},
                {"id": 3, "type": "NPC", "name": "祖尔加", "gameID": C.NPC_ZULJAN},
            ]
        },
    }
    fight = {
        "id": 1,
        "encounterID": C.ENCOUNTER_ID,
        "name": "盘卷祭坛",
        "difficulty": 5,
        "size": 20,
        "startTime": 0,
        "endTime": 10_000,
    }

    def summon(t, instance):
        return {
            "timestamp": t,
            "type": "summon",
            "sourceID": 3,
            "targetID": 2,
            "targetInstance": instance,
            "abilityGameID": C.GREEN_SUMMON,
        }

    def place(t, instance, north):
        return {
            "timestamp": t,
            "type": "cast",
            "sourceID": 2,
            "sourceInstance": instance,
            "abilityGameID": C.GREEN_PLACE,
            "x": 0,
            "y": north * 100,
            "hitPoints": 1,
            "maxHitPoints": 1,
        }

    data = convert(
        report,
        fight,
        [
            summon(0, 2),
            place(20, 2, 0),
            {
                "timestamp": 1000,
                "type": "applydebuff",
                "sourceID": 2,
                "sourceInstance": 2,
                "targetID": 1,
                "abilityGameID": C.GREEN_CARRY,
            },
            {
                "timestamp": 5000,
                "type": "removedebuff",
                "targetID": 1,
                "abilityGameID": C.GREEN_CARRY,
            },
            {
                "timestamp": 5000,
                "type": "cast",
                "sourceID": 1,
                "x": 0,
                "y": 0,
                "hitPoints": 100,
                "maxHitPoints": 100,
            },
            summon(5000, 1),
            place(5010, 2, 0),
            place(5050, 1, 30),
        ],
    )
    tracks = Tracks(data)
    model = P1Model(data, tracks, [e.t for e in data.events], None)
    first, wave, drop = model.globules

    assert first.aid == drop.aid != wave.aid
    assert (first.origin, first.start, first.end_reason) == ("spawn", 0, "picked")
    assert (wave.origin, wave.start) == ("spawn", 5000)
    assert (drop.origin, drop.start) == ("drop", 5010)
    assert model.carries[0].source is first
    assert model.carries[0].dropped is drop
    assert drop.dropped_by == model.carries[0].player
