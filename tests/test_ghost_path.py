# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Ghost paths follow the simulation, and only a new combat-log coordinate relocates them."""

from __future__ import annotations

import math

from wcl_replay.bosses.base import UnitRing
from wcl_replay.bosses.coiled_altar.constants import (
    FIXATE,
    GHOST_SPEED,
    GHOST_STEP_MS,
    NPC_GHOST,
    PATH_ALPHA,
    POSSESSED,
    RESONANCE,
)
from wcl_replay.bosses.coiled_altar.p2 import P2Model
from wcl_replay.core.models import Actor, ActorKind, Event, Fight, FightData, Sample
from wcl_replay.core.tracks import Tracks, distance


def test_resonance_does_not_move_ghosts_and_log_coordinate_does():
    actors = {
        0: Actor(0, "Player-1", "A", ActorKind.PLAYER),
        1: Actor(1, "Player-2", "B", ActorKind.PLAYER),
        2: Actor(2, "Creature-0-0-0-0-261218-1", "恐惧具象", ActorKind.NPC, npc_id=NPC_GHOST, hostile=True),
        3: Actor(3, "Creature-0-0-0-0-261218-2", "恐惧具象", ActorKind.NPC, npc_id=NPC_GHOST, hostile=True),
    }
    events = [
        Event(0, "SPELL_SUMMON", src=0, dst=2),
        Event(0, "SPELL_SUMMON", src=1, dst=3),
        Event(0, "SPELL_AURA_APPLIED", src=2, dst=0, spell_id=FIXATE),
        Event(0, "SPELL_AURA_APPLIED", src=3, dst=1, spell_id=FIXATE),
        Event(3000, "SPELL_AURA_APPLIED", src=2, dst=0, spell_id=RESONANCE),
        Event(3000, "SPELL_AURA_APPLIED", src=3, dst=1, spell_id=RESONANCE),
        Event(10000, "SPELL_AURA_REMOVED", src=2, dst=0, spell_id=FIXATE),
        Event(10000, "SPELL_AURA_REMOVED", src=3, dst=1, spell_id=FIXATE),
    ]
    samples = {
        0: [Sample(0, 0.0, 0.0, 0.0, 100, 100), Sample(10000, 0.0, 0.0, 0.0, 100, 100)],
        1: [Sample(0, 100.0, 0.0, 0.0, 100, 100), Sample(10000, 100.0, 0.0, 0.0, 100, 100)],
        # Second sample repeats the spawn point and must not yank the ghost back.
        2: [
            Sample(0, 0.0, 10.0, 0.0, 1, 1),
            Sample(1000, 0.0, 10.0, 0.0, 1, 1),
            Sample(8000, 20.0, 20.0, 0.0, 1, 1),
        ],
        3: [Sample(0, 0.0, -10.0, 0.0, 1, 1)],
    }
    data = FightData(
        Fight(1, 3429, "盘卷祭坛", 16, 20, 10000, False),
        actors,
        events,
        samples,
    )
    p2 = P2Model(data, Tracks(data), [e.t for e in events], 0)
    a_before = p2.tracks.position(2, 2900)
    a_hit = p2.tracks.position(2, 3000)
    b_hit = p2.tracks.position(3, 3000)
    assert a_before is not None and a_hit is not None and b_hit is not None
    assert distance(a_hit, b_hit) > 5
    assert distance(a_before, a_hit) < GHOST_SPEED * GHOST_STEP_MS / 1000 + 0.05
    # Still on the chase path one second in, not reset to the repeated spawn sample.
    mid = p2.tracks.position(2, 1000)
    assert mid is not None and mid[1] < 9.0
    landed = p2.tracks.position(2, 8000)
    before_log = p2.tracks.position(2, 7900)
    assert landed is not None and before_log is not None
    assert math.hypot(landed[0] - 20, landed[1] - 20) < 0.05
    assert math.hypot(before_log[0] - 20, before_log[1] - 20) > 5
    early = p2.route_overlays(1000)
    assert len(early) == 2
    assert all(r.dashed and r.alpha == PATH_ALPHA and r.layer == "ghosts" for r in early)
    assert all(not any(abs(x - 20) < 1 and abs(y - 20) < 1 for x, y in r.points) for r in early)
    landed_routes = p2.route_overlays(8000)
    landed_route = next(
        r for r in landed_routes if any(abs(x - 20) < 1 and abs(y - 20) < 1 for x, y in r.points)
    )
    assert landed_route.points[0][1] > 8
    assert len(landed_route.points) >= len(next(r for r in early if r.points[0][1] > 8).points)
    assert p2.route_overlays(10000) == []


def _one_ghost(duration: int, events: list[Event]) -> P2Model:
    actors = {
        0: Actor(0, "Player-1", "A", ActorKind.PLAYER),
        2: Actor(2, "Creature-0-0-0-0-261218-1", "恐惧具象", ActorKind.NPC, npc_id=NPC_GHOST, hostile=True),
    }
    samples = {
        0: [Sample(0, 0.0, 0.0, 0.0, 100, 100), Sample(duration, 0.0, 0.0, 0.0, 100, 100)],
        2: [Sample(0, 0.0, 30.0, 0.0, 1, 1)],
    }
    data = FightData(Fight(1, 3429, "盘卷祭坛", 16, 20, duration, False), actors, events, samples)
    return P2Model(data, Tracks(data), [e.t for e in events], 0)


def _ghost_facing(duration: int, facing: list[Sample]) -> P2Model:
    actors = {
        0: Actor(0, "Player-1", "A", ActorKind.PLAYER),
        2: Actor(2, "Creature-0-0-0-0-261218-1", "恐惧具象", ActorKind.NPC, npc_id=NPC_GHOST, hostile=True),
    }
    events = [
        Event(0, "SPELL_AURA_APPLIED", src=2, dst=0, spell_id=FIXATE),
        Event(duration, "SPELL_AURA_REMOVED", src=2, dst=0, spell_id=FIXATE),
    ]
    samples = {0: facing, 2: [Sample(0, 0.0, 30.0, 0.0, 1, 1)]}
    data = FightData(Fight(1, 3429, "盘卷祭坛", 16, 20, duration, False), actors, events, samples)
    return P2Model(data, Tracks(data), [e.t for e in events], 0)


def test_constant_speed_pauses_after_the_player_looks_away():
    west = math.pi / 2  # ghost sits on +y, so this facing looks straight at it
    facing = [
        Sample(0, 0.0, 0.0, west, 100, 100),
        Sample(999, 0.0, 0.0, west, 100, 100),
        Sample(1000, 0.0, 0.0, 0.0, 100, 100),
        Sample(1399, 0.0, 0.0, 0.0, 100, 100),
        Sample(1400, 0.0, 0.0, west, 100, 100),
        Sample(1999, 0.0, 0.0, west, 100, 100),
        Sample(2000, 0.0, 0.0, 0.0, 100, 100),
        Sample(4000, 0.0, 0.0, 0.0, 100, 100),
    ]
    p2 = _ghost_facing(4000, facing)
    p2.set_motion(mode="constant", speed=3.0, pause_s=0.5)
    still = p2.tracks.position(2, 1400)
    second = p2.tracks.position(2, 2400)
    moved = p2.tracks.position(2, 2500)
    assert still is not None and second is not None and moved is not None
    assert abs(still[1] - 30.0) < 0.02
    assert abs(second[1] - 30.0) < 0.02
    assert abs(moved[1] - 29.7) < 0.02

    p2.set_motion(pause_s=0.0)
    immediate = p2.tracks.position(2, 1000)
    assert immediate is not None and abs(immediate[1] - 29.7) < 0.02


def test_accel_does_not_pause_after_a_look_away():
    west = math.pi / 2
    facing = [
        Sample(0, 0.0, 0.0, west, 100, 100),
        Sample(999, 0.0, 0.0, west, 100, 100),
        Sample(1000, 0.0, 0.0, 0.0, 100, 100),
        Sample(2000, 0.0, 0.0, 0.0, 100, 100),
    ]
    p2 = _ghost_facing(2000, facing)
    p2.set_motion(mode="accel", start_speed=1.0, end_speed=3.0, accel_s=2.0, pause_s=0.5)
    held = p2.tracks.position(2, 900)
    stepped = p2.tracks.position(2, 1000)
    assert held is not None and stepped is not None
    assert abs(held[1] - 30.0) < 0.02
    assert abs(stepped[1] - 29.8) < 0.02


def test_accel_speed_ramps_from_the_fixate_then_holds():
    events = [
        Event(0, "SPELL_AURA_APPLIED", src=2, dst=0, spell_id=FIXATE),
        Event(4000, "SPELL_AURA_REMOVED", src=2, dst=0, spell_id=FIXATE),
    ]
    p2 = _one_ghost(4000, events)
    p2.set_motion(mode="accel", start_speed=1.0, end_speed=3.0, accel_s=2.0)
    early = p2.tracks.position(2, 1000)
    assert early is not None
    # Steps at 100..1000 ms use 1.1..2.0 yd/s: 1.55 yards, not the 3 yards of constant speed.
    assert abs((30.0 - early[1]) - 1.55) < 0.02
    cruise_a = p2.tracks.position(2, 2000)
    cruise_b = p2.tracks.position(2, 2100)
    assert cruise_a is not None and cruise_b is not None
    assert abs((cruise_a[1] - cruise_b[1]) - 0.3) < 0.02


def test_a_new_fixate_starts_the_ramp_again():
    events = [
        Event(0, "SPELL_AURA_APPLIED", src=2, dst=0, spell_id=FIXATE),
        Event(1500, "SPELL_AURA_REMOVED", src=2, dst=0, spell_id=FIXATE),
        Event(3000, "SPELL_AURA_APPLIED", src=2, dst=0, spell_id=FIXATE),
        Event(5000, "SPELL_AURA_REMOVED", src=2, dst=0, spell_id=FIXATE),
    ]
    p2 = _one_ghost(5000, events)
    p2.set_motion(mode="accel", start_speed=1.0, end_speed=3.0, accel_s=2.0)
    before = p2.tracks.position(2, 2900)
    opened = p2.tracks.position(2, 3000)
    assert before is not None and opened is not None
    assert abs((before[1] - opened[1]) - 0.1) < 0.02


def test_a_ghost_catch_reapplies_the_same_possession():
    actors = {0: Actor(0, "Player-1", "天镜阁", ActorKind.PLAYER, class_name="PRIEST")}
    events = [
        Event(5000, "SPELL_AURA_APPLIED", dst=0, spell_id=POSSESSED),
        Event(11000, "SPELL_AURA_REMOVED", dst=0, spell_id=POSSESSED),
    ]
    data = FightData(
        Fight(1, 3429, "盘卷祭坛", 16, 20, 12000, False),
        actors,
        events,
        {0: [Sample(0, 0.0, 0.0, 0.0, 100, 100)]},
    )
    p2 = P2Model(data, Tracks(data), [e.t for e in events], 0)
    rings = [p for p in p2.overlays(8000) if isinstance(p, UnitRing)]
    assert len(rings) == 1 and rings[0].actor_id == 0 and rings[0].solid and rings[0].width == 2.2
    assert not any(isinstance(p, UnitRing) for p in p2.overlays(12000))
