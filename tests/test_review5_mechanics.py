# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Sub-millisecond local records keep their order after conversion to replay milliseconds."""

from __future__ import annotations

import pytest
from fixture_log import BOSS, BOSS_GUID, DPS, DPS_GUID, NONE, TANK, TANK_GUID, cast

from wcl_replay.bosses.coiled_altar import constants as C
from wcl_replay.bosses.coiled_altar.module import CoiledAltarAnalysis
from wcl_replay.bosses.coiled_altar.p2 import P2Model
from wcl_replay.core.tracks import Tracks
from wcl_replay.sources.local_log import index_log, parse_encounter

GHOST_GUID = f"Creature-0-1-2950-1-{C.NPC_GHOST}-0000000010"
GHOST = f'{GHOST_GUID},"鬼魂",0xa48,0x0'
COIL_GUID = f"Creature-0-1-2950-1-{C.NPC_SOULCOILER}-0000000011"
COIL = f'{COIL_GUID},"盘魂者",0xa48,0x0'


def _parse(tmp_path, monkeypatch, events):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    events = [
        ("00.0000", 'ENCOUNTER_START,3429,"盘卷祭坛",16,20,2950'),
        *events,
        ("05.0000", 'ENCOUNTER_END,3429,"盘卷祭坛",16,20,0,5000'),
    ]
    path = tmp_path / "same-ms.txt"
    path.write_text(
        "\n".join(f"9/28/2026 23:22:{stamp}  {event}" for stamp, event in events) + "\n",
        encoding="utf-8",
    )
    data = parse_encounter(path, index_log(path)[0])
    return data, Tracks(data)


def _p2(data, tracks):
    return P2Model(data, tracks, [event.t for event in data.events], 0)


def _wail_start():
    return f'SPELL_CAST_START,{COIL},{NONE},{C.WAIL},"恐惧哀嚎",0x1'


def _interrupt():
    return f'SPELL_INTERRUPT,{TANK},{COIL},1766,"脚踢",0x1,{C.WAIL},"恐惧哀嚎",0x1'


@pytest.mark.parametrize("anchor_ms", [0, 2500, 2501])
def test_ghost_uses_last_local_coordinate_in_the_same_millisecond(tmp_path, monkeypatch, anchor_ms):
    stamp = f"{anchor_ms // 1000:02d}.{anchor_ms % 1000:03d}"
    data, tracks = _parse(
        tmp_path,
        monkeypatch,
        [
            ("00.0000", cast(TANK, TANK_GUID, 1, 0.0, 0.0)),
            ("00.0000", cast(GHOST, GHOST_GUID, 1, 0.0, 30.0)),
            ("00.0000", f'SPELL_AURA_APPLIED,{GHOST},{TANK},{C.FIXATE},"凝视",0x1,DEBUFF'),
            (stamp + "0", cast(GHOST, GHOST_GUID, 1, 5.0, 15.0)),
            (stamp + "1", cast(GHOST, GHOST_GUID, 1, 6.0, 14.0)),
            ("04.0000", f'SPELL_AURA_REMOVED,{GHOST},{TANK},{C.FIXATE},"凝视",0x1,DEBUFF'),
        ],
    )
    ghost = data.actors_by_npc(C.NPC_GHOST)[0].id
    observed = tracks.observed_track(ghost)
    assert tracks.position(ghost, anchor_ms) == (6.0, 14.0)
    model = _p2(data, tracks)
    assert tracks.position(ghost, anchor_ms) == (6.0, 14.0)
    model.set_motion(speed=6.0, face_deg=0.0, pause_s=0.0)
    assert tracks.position(ghost, anchor_ms) == (6.0, 14.0)
    assert tracks.observed_track(ghost) is observed
    assert [(sample.t, sample.x, sample.y) for sample in data.samples[ghost]][-2:] == [
        (anchor_ms, 5.0, 15.0),
        (anchor_ms, 6.0, 14.0),
    ]


@pytest.mark.parametrize("outcome", ["kicked", "died"])
def test_wail_ends_on_a_later_local_record_in_the_same_millisecond(tmp_path, monkeypatch, outcome):
    result = _interrupt() if outcome == "kicked" else f"UNIT_DIED,{NONE},{COIL},0"
    data, tracks = _parse(tmp_path, monkeypatch, [("01.0000", _wail_start()), ("01.0001", result)])
    assert [event.t for event in data.events] == [1000, 1000]
    (wail,) = _p2(data, tracks).wails
    assert (wail.start, wail.end, wail.outcome) == (1000, 1000, outcome)
    if outcome == "kicked":
        assert data.actors[wail.kicker].guid == TANK_GUID
    else:
        assert wail.kicker == -1


def test_same_millisecond_interrupt_before_wail_start_is_not_reused(tmp_path, monkeypatch):
    data, tracks = _parse(tmp_path, monkeypatch, [("01.0000", _interrupt()), ("01.0001", _wail_start())])
    (wail,) = _p2(data, tracks).wails
    assert (wail.start, wail.end, wail.outcome, wail.kicker) == (1000, 5000, "unknown", -1)


@pytest.mark.parametrize("first_interrupted", [False, True])
def test_same_millisecond_wail_restart_keeps_each_cast_boundary(tmp_path, monkeypatch, first_interrupted):
    events = [("01.0000", _wail_start())]
    if first_interrupted:
        events.append(("01.0001", _interrupt()))
    events.extend([("01.0002", _wail_start()), ("01.0003", _interrupt())])
    data, tracks = _parse(tmp_path, monkeypatch, events)
    first, second = _p2(data, tracks).wails
    assert (first.start, first.end, first.outcome) == (
        1000,
        1000,
        "kicked" if first_interrupted else "unknown",
    )
    assert (second.start, second.end, second.outcome) == (1000, 1000, "kicked")
    assert data.actors[second.kicker].guid == TANK_GUID
    if not first_interrupted:
        assert first.kicker == -1


def test_melee_facing_preserves_same_millisecond_local_attack_order(tmp_path, monkeypatch):
    data, tracks = _parse(
        tmp_path,
        monkeypatch,
        [
            ("00.0000", cast(TANK, TANK_GUID, 1, 10.0, 0.0)),
            ("00.0000", cast(DPS, DPS_GUID, 1, 0.0, 10.0)),
            ("00.0000", cast(BOSS, BOSS_GUID, 1, 0.0, 0.0)),
            ("01.0000", f"SWING_DAMAGE,{BOSS},{DPS},100,0,1,0,0,0,nil,nil,nil"),
            ("01.0001", f"SWING_MISSED,{BOSS},{TANK},DODGE"),
        ],
    )
    analysis = CoiledAltarAnalysis(data, tracks)
    boss = data.actors_by_npc(C.NPC_ZULJAN)[0].id
    tank = next(actor.id for actor in data.players() if actor.guid == TANK_GUID)
    assert analysis.p1._melee_target(1000) == tank
    assert analysis._melee_at(boss, 1000) == tank
    assert analysis.facing_at(boss, 1000) == pytest.approx(0.0)
    assert not analysis.unit_styles[boss].show_facing
