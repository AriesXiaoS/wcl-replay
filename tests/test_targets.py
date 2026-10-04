# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from __future__ import annotations

import pytest

from wcl_replay.core.models import Actor, ActorKind, Event, Fight, FightData
from wcl_replay.core.targets import Targets

HEALER = 1
DPS = 2
BOSS = 3


def _fight(events: list[Event]) -> Targets:
    actors = {
        HEALER: Actor(HEALER, "Player-1", "Healer-Realm", ActorKind.PLAYER, class_name="Priest"),
        DPS: Actor(DPS, "Player-2", "Dps-Realm", ActorKind.PLAYER, class_name="Mage"),
        BOSS: Actor(BOSS, "Creature-1", "Zuljan", ActorKind.NPC, npc_id=257911, hostile=True),
    }
    fight = Fight(1, 0, "test", 16, 20, 3000, False)
    return Targets(FightData(fight, actors, events, {}))


def test_target_follows_casts_heals_and_rewinds():
    targets = _fight(
        [
            Event(1000, "SPELL_CAST_START", HEALER, BOSS, spell_name="Smite"),
            Event(1500, "SPELL_CAST_SUCCESS", HEALER, HEALER, spell_name="Shield"),
            Event(1600, "SPELL_PERIODIC_DAMAGE", HEALER, DPS, spell_name="Pain"),
            Event(1700, "SPELL_CAST_SUCCESS", HEALER, -1, spell_name="Mass Dispel"),
            Event(1800, "SWING_DAMAGE", HEALER, 99),
            Event(2000, "SPELL_HEAL", HEALER, DPS, amount=1200),
            Event(2500, "SPELL_PERIODIC_HEAL", HEALER, BOSS, spell_name="Renew"),
        ]
    )
    assert targets.at(HEALER, 500) is None
    assert targets.at(HEALER, 1000) == BOSS
    assert targets.at(HEALER, 1900) == BOSS
    assert targets.at(HEALER, 2000) == DPS
    assert targets.at(HEALER, 3000) == DPS
    assert targets.at(HEALER, 1200) == BOSS


def test_swing_and_ranged_retarget():
    targets = _fight(
        [
            Event(100, "SWING_DAMAGE", DPS, BOSS, amount=400),
            Event(200, "RANGE_DAMAGE", DPS, HEALER, amount=10),
        ]
    )
    assert targets.at(DPS, 100) == BOSS
    assert targets.at(DPS, 200) == HEALER
    assert targets.at(BOSS, 200) is None


@pytest.mark.parametrize(
    "hit_type,miss_type", [("SWING_DAMAGE", "SWING_MISSED"), ("RANGE_DAMAGE", "RANGE_MISSED")]
)
def test_missed_attacks_retarget_without_periodic_damage_retargeting(hit_type, miss_type):
    targets = _fight(
        [
            Event(100, hit_type, BOSS, HEALER, amount=400),
            Event(200, miss_type, BOSS, DPS, extra="DODGE"),
            Event(300, "SPELL_PERIODIC_DAMAGE", BOSS, HEALER, amount=10),
            Event(400, "SPELL_PERIODIC_MISSED", BOSS, HEALER, extra="IMMUNE"),
        ]
    )
    assert targets.at(BOSS, 100) == HEALER
    assert targets.at(BOSS, 200) == DPS
    assert targets.at(BOSS, 500) == DPS
    assert targets.at(BOSS, 150) == HEALER
