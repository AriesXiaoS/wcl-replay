# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from __future__ import annotations

import math

from wcl_replay.core.models import Actor, ActorKind, Event, Fight, FightData, Sample
from wcl_replay.core.tracks import Track, Tracks, angle_to


def test_interpolation_and_steps():
    tr = Track(
        [
            Sample(1000, 0.0, 0.0, 0.5, 100, 100),
            Sample(0, -10.0, 4.0, 0.1, 50, 100),
            Sample(2000, 10.0, 0.0, 1.0, 10, 100),
        ]
    )
    assert tr.first == 0 and tr.last == 2000
    p = tr.pose(500)
    assert (p.x, p.y) == (-5.0, 2.0)
    assert math.isclose(p.facing, 0.3) and p.hp == 50  # facing blends, hp stays on the earlier sample
    assert math.isclose(tr.pose(1500).facing, 0.75)
    assert tr.position(1500) == (5.0, 0.0)
    assert tr.position(-100) == (-10.0, 4.0)  # clamped before the first sample
    assert tr.position(9999) == (10.0, 0.0)
    assert tr.pose(1999).hp_frac == 1.0


def test_facing_interpolation_takes_the_short_arc():
    tr = Track(
        [
            Sample(0, 0.0, 0.0, 0.2, 100, 100),
            Sample(1000, 0.0, 0.0, 0.2 + 2 * math.pi - 0.4, 100, 100),
        ]
    )
    mid = tr.pose(500).facing
    assert math.isclose(mid, 0.0, abs_tol=1e-9)


def test_npc_facing_holds_and_a_death_does_not_slide_into_the_next_life():
    boss = Actor(0, "Creature-0-0-0-0-1-1", "首领", ActorKind.NPC, npc_id=1, hostile=True)
    data = FightData(
        Fight(1, 1, "x", 16, 20, 20000, False),
        {0: boss},
        [Event(5000, "UNIT_DIED", dst=0)],
        {
            0: [
                Sample(0, 0.0, 0.0, 0.0, 100, 100),
                Sample(4000, 10.0, 0.0, 1.0, 80, 100),
                Sample(5100, 10.0, 0.0, 1.0, 1, 100),
                Sample(12000, 40.0, 8.0, 2.0, 90, 100),
                Sample(16000, 50.0, 8.0, 2.5, 70, 100),
            ]
        },
    )
    tracks = Tracks(data)
    mid = tracks.pose(0, 2000)
    assert mid is not None and mid.x == 5.0 and mid.y == 0.0
    assert math.isclose(mid.facing, 0.0)
    assert math.isclose(tracks.pose(0, 4000).facing, 1.0)
    assert tracks.pose(0, 4500).x == 10.0
    assert not tracks.present(0, 8000) and tracks.pose(0, 8000) is None
    back = tracks.pose(0, 12000)
    assert back is not None and (back.x, back.y) == (40.0, 8.0)
    assert math.isclose(back.facing, 2.0)
    later = tracks.pose(0, 14000)
    assert later is not None and later.x == 45.0
    assert math.isclose(later.facing, 2.0)


def test_angle_to_follows_wow_facing():
    assert angle_to((0, 0), (1, 0)) == 0.0  # north
    assert math.isclose(angle_to((0, 0), (0, 1)), math.pi / 2)  # west
    assert math.isclose(angle_to((0, 0), (0, -1)), 3 * math.pi / 2)


def test_npc_return_holds_its_last_position_through_activity_grace():
    boss = Actor(0, "Creature-0-0-0-0-1-1", "首领", ActorKind.NPC, npc_id=1, hostile=True)
    data = FightData(
        Fight(1, 1, "x", 16, 20, 40000, False),
        {0: boss},
        [Event(5000, "UNIT_DIED", dst=0)],
        {
            0: [
                Sample(0, 0.0, 0.0, 0.0, 100, 100),
                Sample(4000, 0.0, 0.0, 0.0, 80, 100),
                Sample(10000, 10.0, 0.0, 1.0, 100, 100),
                Sample(12000, 20.0, 0.0, 1.5, 80, 100),
                Sample(30000, 100.0, 8.0, 2.0, 100, 100),
                Sample(32000, 120.0, 8.0, 2.5, 80, 100),
            ]
        },
    )
    tracks = Tracks(data)
    assert tracks.position(0, 11000) == (15.0, 0.0)
    assert tracks.present(0, 13000) and tracks.position(0, 13000) == (20.0, 0.0)
    assert tracks.position(0, 13500) == (20.0, 0.0)
    assert not tracks.present(0, 13501) and tracks.position(0, 13501) is None
    assert tracks.position(0, 29999) is None
    assert tracks.position(0, 30000) == (100.0, 8.0)
    assert tracks.position(0, 31000) == (110.0, 8.0)


def test_presence_and_death(fight_data):
    tracks = Tracks(fight_data)
    boss = next(a.id for a in fight_data.actors.values() if a.npc_id == 257911)
    g1 = next(a.id for a in fight_data.actors.values() if a.guid.endswith("0000000010"))
    assert tracks.appear_time(g1) == 1000  # from SPELL_SUMMON
    assert not tracks.present(g1, 900) and tracks.present(g1, 1200)
    assert tracks.death_time(boss) == 20000
    assert not tracks.is_dead(boss, 19999) and tracks.is_dead(boss, 20001)
    assert tracks.present(boss, 15000)
    assert not tracks.present(boss, 21000)  # phase 2: the death closed his first life
    assert tracks.present(boss, 25000)  # same guid is back, with a new position sample
    players = [a.id for a in fight_data.players()]
    assert all(tracks.present(p, 0) for p in players)


def test_dead_player_holds_still_until_battle_res():
    player = Actor(0, "Player-1-0001", "A-Realm", ActorKind.PLAYER)
    data = FightData(
        Fight(1, 1, "x", 16, 20, 60000, False),
        {0: player},
        [Event(10_000, "UNIT_DIED", dst=0), Event(40_000, "UNIT_DIED", dst=0)],
        {
            0: [
                Sample(0, 0.0, 0.0, 0.0, 100, 100),
                Sample(9_000, 10.0, 0.0, 0.2, 40, 100),
                Sample(12_000, 11.0, 1.0, 0.0, 1, 100),
                Sample(20_000, 30.0, 0.0, 1.0, 70, 100),
                Sample(25_000, 40.0, 0.0, 1.1, 60, 100),
                Sample(50_000, 80.0, 4.0, 0.4, 90, 100),
            ]
        },
    )
    tracks = Tracks(data)
    assert tracks.position(0, 4_500) == (5.0, 0.0)
    assert tracks.position(0, 9_500) == (10.0, 0.0)
    assert tracks.is_dead(0, 10_000) and tracks.position(0, 15_000) == (10.0, 0.0)
    assert tracks.is_dead(0, 19_999) and tracks.position(0, 19_999) == (10.0, 0.0)
    assert not tracks.is_dead(0, 20_000)
    assert tracks.position(0, 20_000) == (30.0, 0.0)
    assert tracks.position(0, 22_500) == (35.0, 0.0)
    assert tracks.position(0, 30_000) == (40.0, 0.0)
    assert tracks.is_dead(0, 40_000) and tracks.position(0, 45_000) == (40.0, 0.0)
    assert not tracks.is_dead(0, 50_000)
    assert tracks.position(0, 50_000) == (80.0, 4.0)


def test_corpse_ticks_do_not_count_as_a_return():
    boss = Actor(0, "Creature-0-0-0-0-1-1", "Boss", ActorKind.NPC, npc_id=1, hostile=True)
    data = FightData(
        Fight(1, 1, "x", 16, 20, 60000, False),
        {0: boss},
        [Event(10000, "UNIT_DIED", dst=0)],
        {
            0: [
                Sample(0, 0.0, 0.0, 0.0, 100, 100),
                Sample(10050, 0.0, 0.0, 0.0, 1, 100),
                Sample(10100, 0.0, 0.0, 0.0, 1, 100),
                Sample(30000, 0.0, 0.0, 0.0, 1, 100),
                Sample(50000, 1.0, 1.0, 0.0, 2, 200),
                Sample(51000, 1.0, 1.0, 0.0, 500, 200),
            ]
        },
    )
    tracks = Tracks(data)
    assert tracks.present(0, 5000) and tracks.present(0, 10000)
    assert not tracks.present(0, 12000) and not tracks.present(0, 30000)
    assert tracks.present(0, 50500) and tracks.present(0, 52000)
    assert not tracks.present(0, 60000)
