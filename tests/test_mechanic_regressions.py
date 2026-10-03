# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Mechanics preserve event identity and distinguish observations from predictions."""

from __future__ import annotations

import numpy as np
import pytest

from wcl_replay.bosses.base import Analysis, aura_intervals
from wcl_replay.bosses.coiled_altar import constants as C
from wcl_replay.bosses.coiled_altar.p1 import P1Model
from wcl_replay.bosses.coiled_altar.p2 import P2Model
from wcl_replay.core.markers import MarkerEvent, clip_world_markers
from wcl_replay.core.models import Actor, ActorKind, Event, Fight, FightData, Sample
from wcl_replay.core.tracks import Track, Tracks
from wcl_replay.sources.local_log import index_log, parse_encounter


def _fight(
    events: list[Event],
    samples: dict[int, list[Sample]] | None = None,
    actors: dict[int, Actor] | None = None,
    duration: int = 20_000,
) -> FightData:
    return FightData(
        Fight(1, 3429, "盘卷祭坛", 16, 20, duration, False),
        actors or {},
        sorted(events, key=lambda event: event.t),
        samples or {},
    )


def _player(aid: int) -> Actor:
    return Actor(aid, f"Player-{aid}", f"玩家{aid}", ActorKind.PLAYER)


def _npc(aid: int, npc_id: int) -> Actor:
    return Actor(aid, f"Creature-0-0-0-0-{npc_id}-{aid}", f"单位{aid}", ActorKind.NPC, npc_id=npc_id)


def _sample(t: int, x: float, y: float = 0.0) -> Sample:
    return Sample(t, x, y, 0.0, 100, 100)


@pytest.mark.parametrize("player_order", [(0, 1, 2), (2, 1, 0)])
def test_simultaneous_drops_match_the_carrier_by_position_and_color(player_order):
    spells = {0: C.GREEN_CARRY, 1: C.GREEN_CARRY, 2: C.PURPLE_CARRY}
    actors = {aid: _player(aid) for aid in spells}
    actors.update({10: _npc(10, C.NPC_GREEN), 11: _npc(11, C.NPC_GREEN), 12: _npc(12, C.NPC_PURPLE)})
    events = [Event(1000, "SPELL_AURA_APPLIED", dst=aid, spell_id=spells[aid]) for aid in player_order]
    events += [Event(5000, "SPELL_AURA_REMOVED", dst=aid, spell_id=spells[aid]) for aid in player_order]
    events += [
        Event(5010, "SPELL_CAST_SUCCESS", src=10, spell_id=C.GREEN_PLACE),
        Event(5010, "SPELL_CAST_SUCCESS", src=11, spell_id=C.GREEN_PLACE),
        Event(5010, "SPELL_CAST_SUCCESS", src=12, spell_id=C.PURPLE_PLACE),
    ]
    samples = {
        0: [_sample(5000, 0.0)],
        1: [_sample(5000, 20.0)],
        2: [_sample(5000, 0.0)],
        10: [_sample(5010, 20.2)],
        11: [_sample(5010, 0.2)],
        12: [_sample(5010, 0.2)],
    }
    data = _fight(events, samples, actors)
    model = P1Model(data, Tracks(data), [event.t for event in data.events], None)

    assert {globule.aid: globule.dropped_by for globule in model.globules} == {10: 1, 11: 0, 12: 2}
    assert {carry.player: carry.dropped.aid for carry in model.carries} == {0: 11, 1: 10, 2: 12}
    dropped = [entry for entry in model.log() if entry.t == 5010]
    assert len(dropped) == 3
    assert {"".join(segment.text for segment in entry.segments) for entry in dropped} == {
        "玩家0 放下了 绿球",
        "玩家1 放下了 绿球",
        "玩家2 放下了 紫球",
    }


@pytest.mark.parametrize("positions", [((0.0, 0.0), (0.0, 0.0)), (None, None), ((100.0, 0.0), (200.0, 0.0))])
def test_ambiguous_or_distant_drop_does_not_invent_a_carrier(positions):
    events = [Event(1000, "SPELL_AURA_APPLIED", dst=aid, spell_id=C.GREEN_CARRY) for aid in (0, 1)]
    events += [Event(5000, "SPELL_AURA_REMOVED", dst=aid, spell_id=C.GREEN_CARRY) for aid in (0, 1)]
    events.append(Event(5010, "SPELL_CAST_SUCCESS", src=10, spell_id=C.GREEN_PLACE))
    samples = {
        aid: [_sample(5000, *position)] for aid, position in enumerate(positions) if position is not None
    }
    samples[10] = [_sample(5010, 0.0)]
    data = _fight(events, samples, {0: _player(0), 1: _player(1), 10: _npc(10, C.NPC_GREEN)})
    model = P1Model(data, Tracks(data), [event.t for event in data.events], None)

    assert model.globules[0].dropped_by == -1
    assert all(carry.dropped is None for carry in model.carries)
    assert not any("放下了" in "".join(segment.text for segment in entry.segments) for entry in model.log())


def test_reused_orb_actor_keeps_separate_drop_ownership():
    events = [
        Event(1000, "SPELL_AURA_APPLIED", dst=0, spell_id=C.GREEN_CARRY),
        Event(5000, "SPELL_AURA_REMOVED", dst=0, spell_id=C.GREEN_CARRY),
        Event(5010, "SPELL_CAST_SUCCESS", src=10, spell_id=C.GREEN_PLACE),
        Event(6000, "SPELL_AURA_APPLIED", dst=1, spell_id=C.GREEN_CARRY),
        Event(10_000, "SPELL_AURA_REMOVED", dst=1, spell_id=C.GREEN_CARRY),
        Event(10_010, "SPELL_CAST_SUCCESS", src=10, spell_id=C.GREEN_PLACE),
    ]
    samples = {
        0: [_sample(5000, 0.0)],
        1: [_sample(10_000, 20.0)],
        10: [_sample(5010, 0.0), _sample(10_010, 20.0)],
    }
    data = _fight(events, samples, {0: _player(0), 1: _player(1), 10: _npc(10, C.NPC_GREEN)})
    model = P1Model(data, Tracks(data), [event.t for event in data.events], None)

    assert [(globule.start, globule.dropped_by) for globule in model.globules] == [(5010, 0), (10_010, 1)]
    assert model.carries[0].dropped is not model.carries[1].dropped


@pytest.mark.parametrize("fear_times", [[], [10_020, 10_080]])
def test_wail_cast_success_completes_even_when_no_player_is_feared(fear_times):
    actors = {2: _npc(2, C.NPC_SOULCOILER), 3: _npc(3, C.NPC_SOULCOILER), 0: _player(0), 1: _player(1)}
    events = [
        Event(0, "SPELL_CAST_START", src=2, spell_id=C.WAIL),
        Event(10_000, "SPELL_CAST_SUCCESS", src=2, spell_id=C.WAIL),
    ]
    events += [
        Event(t, "SPELL_AURA_APPLIED", src=2, dst=aid, spell_id=C.WAIL) for aid, t in enumerate(fear_times)
    ]
    # Later fear and another caster's fear must not affect this cast's outcome or count.
    events += [
        Event(10_100, "SPELL_AURA_APPLIED", src=3, dst=0, spell_id=C.WAIL),
        Event(10_201, "SPELL_AURA_APPLIED", src=2, dst=1, spell_id=C.WAIL),
    ]
    data = _fight(events, actors=actors)
    model = P2Model(data, Tracks(data), [event.t for event in data.events], 0)

    (wail,) = model.wails
    assert (wail.start, wail.end, wail.outcome, wail.feared) == (0, 10_000, "went_off", len(fear_times))
    assert len(model.lanes()[2].items) == 1
    assert f"恐惧 {len(fear_times)} 人" in model.lanes()[2].items[0].label
    assert model.status(10_100)[-1].note == "累计：0 次打断 · 1 次读条成功"


def test_wail_interrupt_precedes_success_and_preserves_the_kicker():
    events = [
        Event(0, "SPELL_CAST_START", src=2, spell_id=C.WAIL),
        Event(5000, "SPELL_INTERRUPT", src=0, dst=2, extra=C.WAIL),
        Event(10_000, "SPELL_CAST_SUCCESS", src=2, spell_id=C.WAIL),
    ]
    data = _fight(events, actors={0: _player(0), 2: _npc(2, C.NPC_SOULCOILER)})
    model = P2Model(data, Tracks(data), [event.t for event in data.events], 0)
    (wail,) = model.wails
    assert (wail.end, wail.outcome, wail.kicker, wail.feared) == (5000, "kicked", 0, 0)


def test_wail_counts_unique_known_targets_within_the_success_window():
    events = [
        Event(0, "SPELL_CAST_START", src=2, spell_id=C.WAIL),
        Event(10_000, "SPELL_CAST_SUCCESS", src=2, spell_id=C.WAIL),
        Event(10_000, "SPELL_AURA_APPLIED", src=2, dst=0, spell_id=C.WAIL),
        Event(10_010, "SPELL_AURA_APPLIED", src=2, dst=0, spell_id=C.WAIL),
        Event(10_050, "SPELL_AURA_APPLIED", src=2, dst=1, spell_id=C.WAIL),
        Event(10_080, "SPELL_AURA_APPLIED", src=2, dst=-1, spell_id=C.WAIL),
        Event(10_090, "SPELL_AURA_APPLIED", src=3, dst=0, spell_id=C.WAIL),
        Event(10_201, "SPELL_AURA_APPLIED", src=2, dst=4, spell_id=C.WAIL),
    ]
    actors = {
        0: _player(0),
        1: _player(1),
        4: _player(4),
        2: _npc(2, C.NPC_SOULCOILER),
        3: _npc(3, C.NPC_SOULCOILER),
    }
    data = _fight(events, actors=actors)
    model = P2Model(data, Tracks(data), [event.t for event in data.events], 0)

    (wail,) = model.wails
    assert (wail.end, wail.outcome, wail.feared) == (10_000, "went_off", 2)
    assert "恐惧 2 人" in model.lanes()[2].items[0].label


@pytest.mark.parametrize(
    "initial_type,initial_amount", [("SPELL_AURA_APPLIED", 2), ("SPELL_AURA_APPLIED_DOSE", 2)]
)
def test_soul_shield_tracks_reapplications_and_all_stack_changes(initial_type, initial_amount):
    events = [
        Event(1000, initial_type, dst=2, spell_id=C.SOUL_SHIELD, amount=initial_amount),
        Event(2000, "SPELL_AURA_REMOVED_DOSE", dst=2, spell_id=C.SOUL_SHIELD, amount=1),
        Event(3000, "SPELL_AURA_APPLIED_DOSE", dst=2, spell_id=C.SOUL_SHIELD, amount=2),
        Event(4000, "SPELL_AURA_REMOVED", dst=2, spell_id=C.SOUL_SHIELD),
    ]
    data = _fight(events, actors={2: _npc(2, C.NPC_SOULCOILER)})
    model = P2Model(data, Tracks(data), [event.t for event in data.events], 0)
    assert [model.shield_at(2, t) for t in (999, 1000, 2000, 3000, 4000)] == [0, 2, 1, 2, 0]


def test_independent_aura_sources_do_not_close_each_other_on_the_same_player():
    data = _fight(
        [
            Event(1000, "SPELL_AURA_APPLIED", src=2, dst=0, spell_id=C.FIXATE),
            Event(1500, "SPELL_AURA_APPLIED", src=3, dst=0, spell_id=C.FIXATE),
            Event(2000, "SPELL_AURA_APPLIED_DOSE", src=2, dst=0, spell_id=C.FIXATE, amount=2),
            Event(3000, "SPELL_AURA_REMOVED", src=2, dst=0, spell_id=C.FIXATE),
            Event(6000, "SPELL_AURA_REMOVED", src=3, dst=0, spell_id=C.FIXATE),
        ]
    )
    intervals = aura_intervals(data, C.FIXATE, source_independent=True)
    assert [(iv.src, iv.start, iv.end) for iv in intervals] == [(2, 1000, 3000), (3, 1500, 6000)]
    assert {iv.src for iv in intervals.active(2500)} == {2, 3}
    assert {iv.src for iv in intervals.active(4000)} == {3}


def test_source_less_aura_removal_closes_matching_instances_only():
    data = _fight(
        [
            Event(1000, "SPELL_AURA_APPLIED", src=2, dst=0, spell_id=C.FIXATE),
            Event(1500, "SPELL_AURA_APPLIED", src=3, dst=0, spell_id=C.FIXATE),
            Event(2000, "SPELL_AURA_APPLIED", src=2, dst=1, spell_id=C.FIXATE),
            Event(2500, "SPELL_AURA_APPLIED", src=2, dst=0, spell_id=C.POSSESSED),
            Event(3000, "SPELL_AURA_REMOVED", src=-1, dst=0, spell_id=C.FIXATE),
        ]
    )
    intervals = aura_intervals(data, {C.FIXATE, C.POSSESSED}, source_independent=True)
    assert {(iv.src, iv.actor, iv.payload) for iv in intervals.active(4000)} == {
        (2, 1, C.FIXATE),
        (2, 0, C.POSSESSED),
    }
    assert [iv.end for iv in intervals if iv.actor == 0 and iv.payload == C.FIXATE] == [3000, 3000]


def test_two_ghosts_can_fixate_the_same_player_independently():
    data = _fight(
        [
            Event(0, "SPELL_SUMMON", src=0, dst=2),
            Event(0, "SPELL_SUMMON", src=0, dst=3),
            Event(0, "SPELL_AURA_APPLIED", src=2, dst=0, spell_id=C.FIXATE),
            Event(1000, "SPELL_AURA_APPLIED", src=3, dst=0, spell_id=C.FIXATE),
            Event(3000, "SPELL_AURA_REMOVED", src=2, dst=0, spell_id=C.FIXATE),
            Event(6000, "SPELL_AURA_REMOVED", src=3, dst=0, spell_id=C.FIXATE),
        ],
        {0: [_sample(0, 0.0)], 2: [_sample(0, 0.0, 30.0)], 3: [_sample(0, 0.0, -30.0)]},
        {0: _player(0), 2: _npc(2, C.NPC_GHOST), 3: _npc(3, C.NPC_GHOST)},
        duration=8000,
    )
    model = P2Model(data, Tracks(data), [event.t for event in data.events], 0)
    ghosts = {ghost.aid: ghost for ghost in model.ghosts}
    assert [ghosts[aid].target_at(2000) for aid in (2, 3)] == [0, 0]
    assert [ghosts[aid].target_at(4000) for aid in (2, 3)] == [-1, 0]


@pytest.mark.parametrize("zone_unload", [False, True])
def test_fast_marker_removals_are_authoritative_unless_zone_unload_is_explicit(zone_unload):
    events = [MarkerEvent(0, 1000, True, index, float(index), 1.0, 3004) for index in range(5)]
    events += [MarkerEvent(0, 5000, False, index, zone_unload=zone_unload) for index in range(5)]
    markers = clip_world_markers(events, 0, 10_000, 3004)
    assert {marker.end for marker in markers} == ({10_001} if zone_unload else {5000})
    later = clip_world_markers(events, 6000, 1000, 3004)
    assert {marker.index for marker in later} == (set(range(5)) if zone_unload else set())


@pytest.mark.parametrize("zone_change", [False, True])
def test_local_marker_removals_use_actual_zone_context(tmp_path, monkeypatch, zone_change):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    lines = [f"9/28/2026 23:21:50.0000  WORLD_MARKER_PLACED,3004,{index},10,20" for index in range(5)]
    if zone_change:
        lines.append('9/28/2026 23:21:55.0000  ZONE_CHANGE,3004,"副本",16')
    lines += [f"9/28/2026 23:21:55.0000  WORLD_MARKER_REMOVED,{index}" for index in range(5)]
    lines += [
        '9/28/2026 23:22:00.0000  ENCOUNTER_START,3429,"盘卷祭坛",16,20,3004',
        '9/28/2026 23:22:10.0000  ENCOUNTER_END,3429,"盘卷祭坛",16,20,1,10000',
        "",
    ]
    path = tmp_path / "synthetic-markers.txt"
    path.write_text("\n".join(lines), encoding="utf-8")
    (entry,) = index_log(path)
    data = parse_encounter(path, entry)
    assert len(data.markers_at(0)) == (5 if zone_change else 0)
    # The cache must retain the zone-change context as well.
    again = parse_encounter(path, index_log(path)[0])
    assert again.markers == data.markers


def test_rebuilding_ghost_predictions_preserves_observations_and_is_repeatable():
    events = [
        Event(0, "SPELL_SUMMON", src=0, dst=2),
        Event(0, "SPELL_AURA_APPLIED", src=2, dst=0, spell_id=C.FIXATE),
        Event(4000, "SPELL_AURA_REMOVED", src=2, dst=0, spell_id=C.FIXATE),
    ]
    samples = {
        0: [_sample(0, 0.0)],
        2: [_sample(0, 0.0, 30.0), _sample(1000, 0.0, 30.0), _sample(2500, 5.0, 15.0)],
    }
    data = _fight(events, samples, {0: _player(0), 2: _npc(2, C.NPC_GHOST)}, duration=4000)
    tracks = Tracks(data)
    observed = tracks.observed_track(2)
    original = tuple(array.copy() for array in (observed.t, observed.x, observed.y, observed.hp))
    model = P2Model(data, tracks, [event.t for event in data.events], 0)
    baseline = tracks.position(2, 1500)
    assert baseline is not None and baseline[1] < 30.0
    assert tracks.track(2) is not observed

    model.set_motion(speed=6.0, face_deg=0.0, pause_s=0.0)
    assert tracks.position(2, 1500)[1] < baseline[1]
    assert tracks.observed_track(2) is observed
    assert tracks.position(2, 2500) == (5.0, 15.0)
    for expected, actual in zip(original, (observed.t, observed.x, observed.y, observed.hp), strict=True):
        np.testing.assert_array_equal(actual, expected)
    assert data.samples[2] is samples[2] and len(samples[2]) == 3

    # A prior derived path may have a different first point; another analysis still starts at the observation.
    tracks.set_derived(2, Track([_sample(0, 999.0, 999.0)]))
    second = P2Model(data, tracks, [event.t for event in data.events], 0)
    assert (second.ghosts[0].x0, second.ghosts[0].y0) == (0.0, 30.0)
    assert tracks.position(2, 1500) == baseline
    assert tracks.observed_track(2) is observed
    assert len(observed) == 3


@pytest.mark.parametrize("death_t", [None, 3000])
def test_source_less_fixate_removal_keeps_ghost_until_the_aura_or_death_ends(death_t):
    events = [
        Event(0, "SPELL_SUMMON", src=0, dst=2),
        Event(0, "SPELL_AURA_APPLIED", src=2, dst=0, spell_id=C.FIXATE),
        Event(5000, "SPELL_AURA_REMOVED", src=-1, dst=0, spell_id=C.FIXATE),
    ]
    if death_t is not None:
        events.append(Event(death_t, "UNIT_DIED", dst=2))
    actors = {0: _player(0), 2: _npc(2, C.NPC_GHOST)}
    actors[2].hostile = True
    data = _fight(events, {0: [_sample(0, 0.0)], 2: [_sample(0, 0.0, 30.0)]}, actors, duration=8000)
    tracks = Tracks(data)
    model = P2Model(data, tracks, [event.t for event in data.events], 0)
    analysis = Analysis(data, tracks)

    (ghost,) = model.ghosts
    assert ghost.end_t == (death_t if death_t is not None else 5000)
    assert model.ghosts_at(2000) == [ghost]
    assert ghost.target_at(2000) == 0
    assert tracks.position(2, 2000)[1] < 30.0
    assert tracks.present(2, 2000)
    assert 2 in analysis.units_at(2000)
    assert ghost.target_at(5000) == -1
    assert model.ghosts_at(5001) == []
    assert not tracks.present(2, 5001)
    assert 2 not in analysis.units_at(5001)
    assert tracks.observed_track(2).last == 0
    assert tracks.last_seen[2] == (death_t if death_t is not None else 0)
    if death_t is not None:
        assert model.ghosts_at(death_t + 1) == []
        assert not tracks.present(2, death_t + 1)
        assert tracks.is_dead(2, death_t + 1)
        assert 2 not in analysis.units_at(death_t + 1)
