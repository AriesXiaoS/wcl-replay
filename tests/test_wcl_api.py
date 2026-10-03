# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from __future__ import annotations

import pytest

from wcl_replay.bosses.base import BossModule
from wcl_replay.bosses.coiled_altar.module import CoiledAltar
from wcl_replay.core.models import ActorKind
from wcl_replay.sources.wcl_api import convert, parse_report_url
from wcl_replay.sources.wcl_api.client import parse_rate_limit
from wcl_replay.sources.wcl_api.convert import log_difficulty, pull_numbers


def test_parse_report_url():
    assert parse_report_url("https://cn.warcraftlogs.com/reports/21QYPd4kh3DytWnj?fight=32") == (
        "21QYPd4kh3DytWnj",
        32,
    )
    assert parse_report_url(
        "https://www.warcraftlogs.com/reports/21QYPd4kh3DytWnj#fight=5&type=damage-done"
    ) == ("21QYPd4kh3DytWnj", 5)
    assert parse_report_url("cn.warcraftlogs.com/reports/21QYPd4kh3DytWnj?fight=last") == (
        "21QYPd4kh3DytWnj",
        None,
    )
    assert parse_report_url("21QYPd4kh3DytWnj") == ("21QYPd4kh3DytWnj", None)
    with pytest.raises(ValueError):
        parse_report_url("https://example.com/reports/abc")
    with pytest.raises(ValueError):
        parse_report_url("https://www.warcraftlogs.com/character/cn/x")


REPORT = {
    "title": "t",
    "startTime": 1_790_000_000_000,
    "masterData": {
        "actors": [
            {
                "id": 1,
                "name": "Tank",
                "type": "Player",
                "subType": "DeathKnight",
                "icon": "DeathKnight-Blood",
                "server": "Realm",
            },
            {"id": 9, "name": "Ghost", "type": "NPC", "subType": "NPC", "gameID": 261218},
            {"id": 7, "name": "Ghoul", "type": "Pet", "subType": "Pet", "gameID": 26125, "petOwner": 1},
        ],
        "abilities": [{"gameID": 1285911, "name": "锁定"}],
    },
}
FIGHT = {
    "id": 3,
    "encounterID": 3429,
    "name": "盘卷祭坛",
    "difficulty": 16,
    "kill": False,
    "size": 20,
    "startTime": 10_000,
    "endTime": 70_000,
    "enemyNPCs": [{"id": 9, "gameID": 261218}],
}


def test_convert_instances_positions_and_types():
    events = [
        {"timestamp": 10_500, "type": "combatantinfo", "sourceID": 1, "specID": 250},
        {
            "timestamp": 11_000,
            "type": "cast",
            "sourceID": 9,
            "sourceInstance": 1,
            "abilityGameID": 1285844,
            "resourceActor": 1,
            "x": 12345,
            "y": -6789,
            "facing": 314,
            "hitPoints": 100,
            "maxHitPoints": 100,
        },
        {
            "timestamp": 11_000,
            "type": "cast",
            "sourceID": 9,
            "sourceInstance": 2,
            "abilityGameID": 1285844,
            "resourceActor": 1,
            "x": 100,
            "y": 200,
            "facing": 0,
            "hitPoints": 100,
            "maxHitPoints": 100,
        },
        {
            "timestamp": 12_000,
            "type": "applydebuff",
            "sourceID": 9,
            "sourceInstance": 2,
            "targetID": 1,
            "abilityGameID": 1285911,
        },
        {
            "timestamp": 12_500,
            "type": "damage",
            "sourceID": 9,
            "sourceInstance": 2,
            "targetID": 1,
            "abilityGameID": 5,
            "amount": 777,
            "resourceActor": 2,
            "x": 500,
            "y": 600,
            "hitPoints": 50,
            "maxHitPoints": 100,
        },
        {
            "timestamp": 13_000,
            "type": "applydebuffstack",
            "sourceID": 9,
            "targetID": 1,
            "abilityGameID": 5,
            "stack": 3,
        },
        {
            "timestamp": 14_000,
            "type": "interrupt",
            "sourceID": 1,
            "targetID": 9,
            "abilityGameID": 47528,
            "extraAbilityGameID": 1286399,
        },
        {"timestamp": 15_000, "type": "death", "targetID": 1},
    ]
    data = convert(REPORT, FIGHT, events, pull_number=4)
    assert (
        data.fight.duration_ms == 60_000 and data.fight.pull_number == 4 and data.fight.encounter_id == 3429
    )

    ghosts = data.actors_by_npc(261218)
    assert len(ghosts) == 2 and all(g.hostile for g in ghosts)
    tank = next(a for a in data.actors.values() if a.kind is ActorKind.PLAYER)
    assert tank.name == "Tank-Realm" and tank.class_name == "DEATHKNIGHT" and tank.spec_id == 250

    g1 = next(g for g in ghosts if g.guid.endswith("-1"))
    s = data.samples[g1.id][0]
    # API y is north, API x is east (west is negated), facing is a math angle from east.
    assert (s.t, round(s.x, 2), round(s.y, 2)) == (1000, -67.89, -123.45)
    assert round(s.facing, 2) == round(-3.14 - 1.5707963267948966, 2)
    assert [(x.t, x.x, x.y, x.hp) for x in data.samples[tank.id]] == [(2500, 6.0, -5.0, 50)]

    types = [e.type for e in data.events]
    assert types == [
        "SPELL_CAST_SUCCESS",
        "SPELL_CAST_SUCCESS",
        "SPELL_AURA_APPLIED",
        "SPELL_DAMAGE",
        "SPELL_AURA_APPLIED_DOSE",
        "SPELL_INTERRUPT",
        "UNIT_DIED",
    ]
    fix = data.events[2]
    assert fix.extra == "DEBUFF" and fix.spell_name == "锁定" and fix.src != g1.id
    assert data.events[3].amount == 777 and data.events[4].amount == 3
    assert data.events[5].extra == 1286399
    assert data.events[6].dst == tank.id


def test_pet_owner_is_taken_from_master_data():
    events = [{"timestamp": 11_000, "type": "cast", "sourceID": 7, "abilityGameID": 1}]
    data = convert(REPORT, FIGHT, events)
    ghoul = next(a for a in data.actors.values() if a.name == "Ghoul")
    tank = next(a for a in data.actors.values() if a.kind is ActorKind.PLAYER)
    assert ghoul.kind is ActorKind.PET and ghoul.owner_id == tank.id


def test_wcl_raid_difficulty_maps_onto_the_combat_log():
    assert log_difficulty(5) == 16
    assert log_difficulty(4) == 15
    assert log_difficulty(3) == 14
    assert log_difficulty(1) == 17
    assert log_difficulty(16) == 16
    fight = dict(FIGHT, difficulty=5)
    assert convert(REPORT, fight, [], pull_number=1).fight.difficulty == 16


def test_coiled_altar_declares_its_own_wcl_slices():
    labels = [sl.label for sl in CoiledAltar().wcl_slices({}, {})]
    assert labels[:2] == ["敌方施法", "机制光环"]
    assert "玩家受伤" in labels and "玩家治疗" in labels and "玩家施法" in labels and "毒液球" in labels
    moves = [sl for sl in CoiledAltar().wcl_slices({}, {}) if sl.label.startswith("玩家")]
    assert [(sl.label, sl.data_type) for sl in moves] == [
        ("玩家受伤", "DamageTaken"),
        ("玩家治疗", "Healing"),
        ("玩家施法", "Casts"),
    ]
    assert all(sl.resources and sl.hostility == "Friendlies" for sl in moves)
    orbs = next(sl for sl in CoiledAltar().wcl_slices({}, {}) if sl.label == "毒液球")
    assert "凝结的毒液追踪者" in orbs.filter and "烈毒变异体" in orbs.filter
    generic = [sl.label for sl in BossModule().wcl_slices({}, {})]
    assert generic == ["敌方施法", "玩家受伤", "玩家治疗", "玩家施法", "死亡"]
    assert "毒液球" not in generic


def test_parse_rate_limit():
    spent, limit, reset = parse_rate_limit(
        {"rateLimitData": {"limitPerHour": 3600, "pointsSpentThisHour": 17.4, "pointsResetIn": 120}}
    )
    assert (spent, limit, reset) == (17.4, 3600, 120)
    assert parse_rate_limit({}) == (0.0, 0, 0)


def test_pull_numbers():
    fights = [
        {"id": 1, "encounterID": 5, "difficulty": 16, "startTime": 0},
        {"id": 2, "encounterID": 0, "startTime": 5},
        {"id": 3, "encounterID": 5, "difficulty": 16, "startTime": 9},
        {"id": 4, "encounterID": 5, "difficulty": 15, "startTime": 12},
    ]
    assert pull_numbers(fights) == {1: 1, 3: 2, 4: 1}
