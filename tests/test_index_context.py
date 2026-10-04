# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from wcl_replay.sources.local_log import index as local_index
from wcl_replay.sources.local_log import index_log, parse_encounter
from wcl_replay.sources.local_log import parser as local_parser
from wcl_replay.sources.local_log.index import marker_events


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))


def _log(*, marker_x: int = 10, map_x: int = 100, removal: bool = False) -> str:
    lines = [
        f'9/28/2026 23:21:58.000  MAP_CHANGE,2500,"Test",{map_x},-100,200,0',
        f"9/28/2026 23:21:59.000  WORLD_MARKER_PLACED,3004,0,{marker_x},20",
        '9/28/2026 23:22:00.000  ENCOUNTER_START,999,"Test",16,20,3004',
    ]
    if removal:
        lines.append("9/28/2026 23:22:00.600  WORLD_MARKER_REMOVED,0")
    lines.append('9/28/2026 23:22:00.900  ENCOUNTER_END,999,"Test",16,20,1,900')
    return "\n".join(lines) + "\n"


def test_appended_zone_change_updates_cached_removal_and_analysis_identity(tmp_path):
    path = tmp_path / "zone.txt"
    path.write_text(_log(removal=True), encoding="utf-8")
    original = index_log(path)[0]
    assert parse_encounter(path, original).markers[0].end == 600

    with path.open("a", encoding="utf-8") as file:
        file.write('9/28/2026 23:22:01.000  ZONE_CHANGE,2501,"Elsewhere"\n')
    assert parse_encounter(path, original).markers[0].end == 600
    refreshed = index_log(path)[0]
    assert marker_events(path)[-1].zone_unload
    assert parse_encounter(path, refreshed).markers[0].end == 901
    assert refreshed.content_digest == original.content_digest
    assert refreshed.analysis_digest != original.analysis_digest
    assert parse_encounter(path, original).markers[0].end == 600

    fresh_path = tmp_path / "fresh-zone.txt"
    fresh_path.write_bytes(path.read_bytes())
    assert index_log(fresh_path)[0].analysis_digest == refreshed.analysis_digest


def test_marker_context_before_pull_invalidates_analysis_without_changing_pull_bytes(tmp_path):
    path = tmp_path / "marker-context.txt"
    path.write_text(_log(), encoding="utf-8")
    original = index_log(path)[0]

    path.write_text(_log(marker_x=99), encoding="utf-8")
    assert parse_encounter(path, original).markers[0].x == 10
    refreshed = index_log(path)[0]
    assert refreshed.start_offset == original.start_offset
    assert refreshed.content_digest == original.content_digest
    assert refreshed.analysis_digest != original.analysis_digest
    assert parse_encounter(path, refreshed).markers[0].x == 99


def test_map_context_before_pull_invalidates_analysis_without_changing_pull_bytes(tmp_path):
    path = tmp_path / "map-context.txt"
    path.write_text(_log(), encoding="utf-8")
    original = index_log(path)[0]

    path.write_text(_log(map_x=200), encoding="utf-8")
    assert parse_encounter(path, original).map_info.x_max == 100
    refreshed = index_log(path)[0]
    assert refreshed.start_offset == original.start_offset
    assert refreshed.content_digest == original.content_digest
    assert refreshed.analysis_digest != original.analysis_digest
    assert parse_encounter(path, refreshed).map_info.x_max == 200


@pytest.mark.parametrize("with_marker", [False, True])
def test_indexed_marker_snapshot_survives_json_cache_without_rescanning(tmp_path, monkeypatch, with_marker):
    path = tmp_path / "snapshot.txt"
    text = _log(removal=True)
    if not with_marker:
        text = "\n".join(line for line in text.splitlines() if "WORLD_MARKER_" not in line) + "\n"
    path.write_text(text, encoding="utf-8")
    original = index_log(path)[0]
    assert original.marker_snapshot is not None
    assert bool(original.marker_snapshot) == with_marker

    def forbid_scan(*args, **kwargs):
        pytest.fail("indexed marker context must not rescan the log")

    monkeypatch.setattr(local_index, "_scan", forbid_scan)
    monkeypatch.setattr(local_parser, "marker_events", forbid_scan)
    restored = index_log(path)[0]
    assert restored == original
    data = parse_encounter(path, restored)
    assert tuple(data.markers) == original.marker_snapshot
    if with_marker:
        data.markers[0].x = 999
        assert parse_encounter(path, restored).markers[0].x == 10
        assert original.marker_snapshot[0].x == 10


def test_legacy_entry_without_marker_snapshot_keeps_current_marker_fallback(tmp_path):
    path = tmp_path / "legacy.txt"
    path.write_text(_log(removal=True), encoding="utf-8")
    indexed = index_log(path)[0]
    legacy = replace(indexed, marker_snapshot=None, analysis_digest="")
    with path.open("a", encoding="utf-8") as file:
        file.write('9/28/2026 23:22:01.000  ZONE_CHANGE,2501,"Elsewhere"\n')
    assert parse_encounter(path, indexed).markers[0].end == 600
    assert parse_encounter(path, legacy).markers[0].end == 901


@pytest.mark.parametrize("corruption", ["missing", "null", "coordinate", "duration"])
def test_invalid_marker_snapshot_cache_is_rebuilt(tmp_path, corruption):
    path = tmp_path / "corrupt-snapshot.txt"
    path.write_text(_log(removal=True), encoding="utf-8")
    original = index_log(path)[0]
    cache_file = local_index._cache_file(path)
    cached = json.loads(cache_file.read_text("utf-8"))
    entry = cached["entries"][0]
    if corruption == "missing":
        entry.pop("marker_snapshot")
    elif corruption == "null":
        entry["marker_snapshot"] = None
    elif corruption == "coordinate":
        entry["marker_snapshot"][0]["x"] = 99
    else:
        entry["marker_snapshot"][0]["end"] = 9999
    cache_file.write_text(json.dumps(cached), encoding="utf-8")
    rebuilt = index_log(path)[0]
    assert rebuilt == original
    assert parse_encounter(path, rebuilt).markers[0].x == 10


def test_unrelated_later_markers_and_pulls_preserve_closed_analysis_identity(tmp_path):
    path = tmp_path / "append.txt"
    path.write_text(_log(), encoding="utf-8")
    original = index_log(path)[0]
    with path.open("a", encoding="utf-8") as file:
        file.write(
            "9/28/2026 23:22:30.000  WORLD_MARKER_PLACED,3004,0,99,88\n"
            '9/28/2026 23:22:31.000  ZONE_CHANGE,2501,"Elsewhere"\n'
            '9/28/2026 23:22:32.000  ENCOUNTER_START,999,"Test",16,20,3004\n'
            '9/28/2026 23:22:33.000  ENCOUNTER_END,999,"Test",16,20,1,1000\n'
        )
    refreshed, later = index_log(path)
    assert refreshed.content_digest == original.content_digest
    assert refreshed.analysis_digest == original.analysis_digest
    assert later.analysis_digest != refreshed.analysis_digest
    assert parse_encounter(path, refreshed).markers[0].x == 10
    assert parse_encounter(path, later).markers[0].x == 99


def test_unfinished_pull_context_uses_the_same_duration_as_parsing(tmp_path):
    path = tmp_path / "unfinished.txt"
    lines = _log(removal=True).splitlines()[:-1]
    lines.append('9/28/2026 23:22:01.500  EMOTE,"Test"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    original = index_log(path)[0]
    data = parse_encounter(path, original)
    assert data.fight.duration_ms == 1500
    assert data.markers[0].end == 600
    assert original.analysis_digest

    path.write_text(path.read_text("utf-8").replace(",0,10,20", ",0,99,20"), encoding="utf-8")
    refreshed = index_log(path)[0]
    assert refreshed.content_digest == original.content_digest
    assert refreshed.analysis_digest != original.analysis_digest
