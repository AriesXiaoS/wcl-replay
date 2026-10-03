# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from __future__ import annotations

import mmap

from fixture_log import BOSS_GUID, DPS_GUID, P2_GUID, TANK_GUID

from wcl_replay.core.models import ActorKind
from wcl_replay.sources.local_log import index_log
from wcl_replay.sources.local_log.fields import split_fields
from wcl_replay.sources.local_log.index import _find_yielding, _rfind_yielding, _scale_progress
from wcl_replay.sources.local_log.timestamps import label_of, parse_ts_ms


def by_guid(data, guid):
    return next(a for a in data.actors.values() if a.guid == guid)


def test_split_fields_keeps_quoted_commas():
    assert split_fields('A,"x, y",3') == ["A", "x, y", "3"]
    assert split_fields('A,"plain",0x1') == ["A", "plain", "0x1"]


def test_timestamps():
    assert parse_ts_ms("9/28/2026 23:22:01.5000") - parse_ts_ms("9/28/2026 23:22:00.0000") == 1500
    assert label_of("9/28/2026 23:22:44.2028") == "2026/9/28 23:22"


def test_index(log_path):
    seen: list[float] = []
    entries = index_log(log_path, seen.append)
    assert len(entries) == 1
    e = entries[0]
    assert (e.encounter_id, e.difficulty, e.kill, e.duration_ms, e.pull_number) == (3429, 16, True, 30000, 1)
    assert e.map_line and "MAP_CHANGE" in e.map_line
    assert seen == sorted(seen)
    assert seen[-1] == 1.0
    # Second call is served from the cache and returns the same thing.
    assert index_log(log_path)[0].start_offset == e.start_offset


def test_chunked_search_matches_mmap(tmp_path):
    needle = b"  ENCOUNTER_"
    chunk = 48
    data = b"q" * (chunk - 4) + needle + b"x" * 40 + needle + b"y" * 30 + needle + b"z" * 80
    path = tmp_path / "blob.bin"
    path.write_bytes(data)
    with path.open("rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
        for start in (0, 10, chunk - 4, chunk, len(data) // 2, len(data) - 1, len(data)):
            assert _find_yielding(mm, needle, start, None, chunk=chunk) == mm.find(needle, start)
        assert _find_yielding(mm, b"NO-SUCH", 0, None, chunk=chunk) == -1
        seen: list[float] = []
        assert _find_yielding(mm, b"NO-SUCH", 0, seen.append, chunk=chunk) == -1
        assert seen[-1] == 1.0
        assert len(seen) >= 2
        assert seen == sorted(seen)
        for end in (len(data), len(data) // 2, len(needle), 0):
            assert _rfind_yielding(mm, needle, 0, end, chunk=chunk) == mm.rfind(needle, 0, end)


def test_scale_progress_maps_a_resume_onto_one_phase():
    seen: list[float] = []
    fn = _scale_progress(seen.append, 0.5, 1.0, 80, 100)
    fn(0.8)
    fn(0.9)
    fn(1.0)
    assert seen == [0.5, 0.75, 1.0]


def test_parse_actors_and_specs(fight_data):
    tank = by_guid(fight_data, TANK_GUID)
    dps = by_guid(fight_data, DPS_GUID)
    boss = by_guid(fight_data, BOSS_GUID)
    assert tank.kind is ActorKind.PLAYER and tank.spec_id == 250 and tank.class_name == "DEATHKNIGHT"
    assert dps.spec_id == 62 and dps.class_name == "MAGE"
    assert tank.short_name == "Tank"
    assert boss.kind is ActorKind.NPC and boss.npc_id == 257911 and boss.hostile
    assert fight_data.map_info is not None and fight_data.map_info.map_id == 2500


def test_parse_positions_and_events(fight_data):
    dps = by_guid(fight_data, DPS_GUID)
    pos = [(s.t, s.x, s.y) for s in fight_data.samples[dps.id]]
    assert pos == [(500, 0.0, -10.0), (8000, -2.0, -10.0), (28050, 0.0, -10.0)]
    drop = by_guid(fight_data, P2_GUID)
    assert fight_data.samples[drop.id][0].t == 7050

    auras = [e for e in fight_data.events if e.spell_id == 1310498]
    assert [e.type for e in auras] == ["SPELL_AURA_APPLIED", "SPELL_AURA_REMOVED"]
    assert auras[0].extra == "DEBUFF" and auras[0].dst == dps.id
    doses = [e.amount for e in fight_data.events if e.type == "SPELL_AURA_APPLIED_DOSE"]
    assert sorted(doses) == [2, 2, 3, 3, 4]
    dmg = next(e for e in fight_data.events if e.type == "SPELL_DAMAGE" and e.t == 19000)
    assert dmg.amount == 5000
    assert fight_data.fight.duration_ms == 30000 and fight_data.fight.kill
