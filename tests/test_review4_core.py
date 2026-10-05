# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from __future__ import annotations

import pytest

from wcl_replay.bosses import base
from wcl_replay.bosses.base import Analysis, FrameAura
from wcl_replay.bosses.coiled_altar import constants as C
from wcl_replay.bosses.coiled_altar.p1 import P1Model
from wcl_replay.bosses.coiled_altar.p2 import P2Model
from wcl_replay.core.models import Actor, ActorKind, Event, Fight, FightData, Sample
from wcl_replay.core.tracks import Tracks


def _data(events, samples=None, actors=None):
    return FightData(
        Fight(1, C.ENCOUNTER_ID, "盘卷祭坛", 16, 20, 20000, False),
        actors or {0: Actor(0, "player", "玩家", ActorKind.PLAYER)},
        sorted(events, key=lambda event: event.t),
        samples or {},
    )


def test_sparse_carry_route_does_not_interpolate_toward_battle_res():
    data = _data(
        [
            Event(4500, "SPELL_AURA_APPLIED", dst=0, spell_id=C.GREEN_CARRY),
            Event(5000, "SPELL_AURA_REMOVED", dst=0, spell_id=C.GREEN_CARRY),
            Event(5000, "UNIT_DIED", dst=0),
        ],
        {0: [Sample(t, x, 0, 0, hp, 100) for t, x, hp in [(0, 0, 100), (4000, 10, 100), (10000, 100, 80)]]},
    )
    tracks = Tracks(data)
    model = P1Model(data, tracks, [event.t for event in data.events], None)
    assert tracks.position(0, 4500) == (10.0, 0.0)
    assert model.route_overlays(4500) == []
    assert model.route_overlays(4999) == []


def test_carry_route_keeps_death_and_resurrection_paths_separate():
    data = _data(
        [
            Event(1000, "SPELL_AURA_APPLIED", dst=0, spell_id=C.GREEN_CARRY),
            Event(5000, "UNIT_DIED", dst=0),
            Event(15000, "SPELL_AURA_REMOVED", dst=0, spell_id=C.GREEN_CARRY),
        ],
        {
            0: [
                Sample(t, x, 0, 0, hp, 100)
                for t, x, hp in [
                    (0, 0, 100),
                    (4000, 10, 100),
                    (5500, 20, 1),
                    (10000, 100, 80),
                    (14000, 110, 80),
                ]
            ]
        },
    )
    tracks = Tracks(data)
    model = P1Model(data, tracks, [event.t for event in data.events], None)
    before = model.route_overlays(6000)
    assert len(before) == 1
    assert before[0].points[-1] == (10.0, 0.0)
    after = model.route_overlays(12000)
    assert len(after) == 2
    assert all(x < 20 for x, _y in after[0].points)
    assert all(x >= 100 for x, _y in after[1].points)
    assert after[1].points[-1] == tracks.position(0, 12000)
    assert model.route_overlays(15000) == []


@pytest.mark.parametrize(
    "removed_at,expected",
    [
        (4979, (0, 0)),
        (4980, (1, 0)),
        (5189, (1, 0)),
        (5190, (0, 1)),
        (5195, (0, 1)),
        (5200, (0, 1)),
        (5410, (0, 1)),
        (5411, (0, 0)),
    ],
)
def test_each_shield_removal_belongs_to_one_nearest_bomb(removed_at, expected):
    data = _data(
        [
            Event(0, "SPELL_AURA_APPLIED", dst=2, spell_id=C.SOUL_SHIELD, amount=2),
            Event(1000, "SPELL_AURA_APPLIED", dst=0, spell_id=C.GLOOMBOMB),
            Event(1210, "SPELL_AURA_APPLIED", dst=1, spell_id=C.GLOOMBOMB),
            Event(5000, "SPELL_AURA_REMOVED", dst=0, spell_id=C.GLOOMBOMB),
            Event(5210, "SPELL_AURA_REMOVED", dst=1, spell_id=C.GLOOMBOMB),
            Event(removed_at, "SPELL_AURA_REMOVED_DOSE", dst=2, spell_id=C.SOUL_SHIELD, amount=1),
        ],
        actors={
            0: Actor(0, "player0", "玩家一", ActorKind.PLAYER),
            1: Actor(1, "player1", "玩家二", ActorKind.PLAYER),
            2: Actor(2, "coil", "盘魂者", ActorKind.NPC, npc_id=C.NPC_SOULCOILER),
        },
    )
    model = P2Model(data, Tracks(data), [event.t for event in data.events], 0)
    assert tuple(bomb.cracked for bomb in model.bombs) == expected
    assert model.shield_at(2, removed_at) == 1


class _AuraAnalysis(Analysis):
    frame_auras = (FrameAura("fixate", "凝视", ((C.FIXATE, "fixate_icon"),)),)


def test_frame_aura_indexes_are_reused_until_the_declaration_changes(monkeypatch):
    data = _data([Event(1000, "SPELL_AURA_APPLIED", dst=0, spell_id=C.FIXATE)])
    calls = []
    original = base.aura_intervals

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(base, "aura_intervals", counted)
    analysis = _AuraAnalysis(data, Tracks(data))
    original_index = analysis._frame_iv
    analysis.refresh_indexes()
    analysis.refresh_indexes()
    assert calls == [1]
    assert analysis._frame_iv is original_index
    assert analysis.active_frame_auras(0, 2000) == (("fixate", "fixate_icon"),)

    analysis.frame_auras = (FrameAura("renamed", "凝视", ((C.FIXATE, "changed_icon"),)),)
    analysis.refresh_indexes()
    assert calls == [1, 1]
    assert analysis.active_frame_auras(0, 2000) == (("renamed", "changed_icon"),)


def test_frame_aura_indexes_detect_new_event_lists_and_support_explicit_invalidation():
    data = _data([Event(1000, "SPELL_AURA_APPLIED", dst=0, spell_id=C.FIXATE)])
    analysis = _AuraAnalysis(data, Tracks(data))
    data.events = [Event(3000, "SPELL_AURA_APPLIED", dst=0, spell_id=C.FIXATE)]
    analysis.refresh_indexes()
    assert analysis.active_frame_auras(0, 2000) == ()
    assert analysis.active_frame_auras(0, 4000) == (("fixate", "fixate_icon"),)

    data.events[0].spell_id = C.GREEN_CARRY
    analysis.refresh_indexes(invalidate_auras=True)
    assert analysis.active_frame_auras(0, 4000) == ()


def test_frame_aura_indexes_follow_appended_events_and_changed_duration():
    data = _data([Event(1000, "SPELL_AURA_APPLIED", dst=0, spell_id=C.FIXATE)])
    analysis = _AuraAnalysis(data, Tracks(data))
    assert analysis.active_frame_auras(0, 21000) == ()
    data.fight.duration_ms = 30000
    analysis.refresh_indexes()
    assert analysis.active_frame_auras(0, 21000) == (("fixate", "fixate_icon"),)
    data.events.append(Event(2500, "SPELL_AURA_REMOVED", dst=0, spell_id=C.FIXATE))
    analysis.refresh_indexes()
    assert analysis.active_frame_auras(0, 3000) == ()
