# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from __future__ import annotations

import mmap

import pytest

from wcl_replay.core.cancellation import TaskCancelled, cancel_check
from wcl_replay.core.tracks import Tracks
from wcl_replay.sources.local_log import index as local_index
from wcl_replay.sources.local_log import index_log, parse_encounter
from wcl_replay.sources.wcl_api.convert import convert


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "synthetic-cache"))


@pytest.mark.parametrize("events", [[], [{"timestamp": 100, "type": "cast", "sourceID": 2}]])
def test_wcl_roster_includes_inactive_current_fight_players_without_changing_event_ids(events):
    report = {
        "masterData": {
            "actors": [
                {"id": 1, "type": "Player", "name": "Idle", "subType": "Mage", "icon": "Mage-Fire"},
                {"id": 2, "type": "Player", "name": "Active"},
                {"id": 3, "type": "Player", "name": "Other fight"},
            ]
        }
    }
    fight = {"id": 1, "startTime": 0, "endTime": 1000, "friendlyPlayers": [1, 2], "size": 2}
    data = convert(report, fight, events)
    assert {player.name for player in data.players()} == {"Idle", "Active"}
    assert len(data.actors) == 2
    idle = next(player for player in data.players() if player.name == "Idle")
    assert idle.class_name == "MAGE" and idle.spec_id == 63
    assert not Tracks(data).has(idle.id)
    if events:
        assert data.events[0].src == 0
        assert data.actors[0].name == "Active"


def map_line(name, size=100):
    return f'10/04/2026 10:00:00.000  MAP_CHANGE,2500,"{name}",{size},-100,100,-100'


def start_line(number):
    return f'10/04/2026 10:00:00.000  ENCOUNTER_START,{number},"Synthetic",16,20,1'


def end_line(number):
    return f'10/04/2026 10:00:01.000  ENCOUNTER_END,{number},"Synthetic",16,20,0,1000'


def write_lines(path, lines):
    path.write_bytes(("\r\n".join(lines) + "\r\n").encode("utf-8"))
    return path


def test_local_maps_follow_latest_change_before_each_pull(tmp_path):
    first = map_line("首张,地图")
    second = map_line("第二张", 200)
    third = map_line("战斗中变更", 300)
    path = write_lines(
        tmp_path / "maps.txt",
        [
            first,
            start_line(1),
            end_line(1),
            second,
            start_line(2),
            third,
            end_line(2),
            start_line(3),
            end_line(3),
        ],
    )
    entries = index_log(path)
    assert [entry.map_line for entry in entries] == [first, second, third]
    assert parse_encounter(path, entries[0]).map_info.name == "首张,地图"
    assert parse_encounter(path, entries[1]).map_info.x_max == 300


@pytest.mark.parametrize("appended_map", [False, True])
def test_incremental_index_recovers_map_once_and_tracks_appended_changes(tmp_path, monkeypatch, appended_map):
    first = map_line("Original")
    changed = map_line("Appended", 200)
    path = write_lines(tmp_path / "append.txt", [first, start_line(1), end_line(1)])
    original = index_log(path)[0]
    appended = [changed] if appended_map else []
    for number in (2, 3, 4):
        appended.extend([start_line(number), end_line(number)])
    with path.open("ab") as file:
        file.write(("\r\n".join(appended) + "\r\n").encode("utf-8"))
    recoveries = []
    original_rfind = local_index._rfind_yielding

    def recover(mm, needle, start, end, **kwargs):
        recoveries.append((needle, start, end))
        return original_rfind(mm, needle, start, end, **kwargs)

    monkeypatch.setattr(local_index, "_rfind_yielding", recover)
    progress = []
    entries = index_log(path, progress.append)
    assert [entry.encounter_id for entry in entries] == [1, 2, 3, 4]
    assert entries[0].analysis_digest == original.analysis_digest
    assert [entry.map_line for entry in entries[1:]] == [changed if appended_map else first] * 3
    assert recoveries == [(b"  MAP_CHANGE,", 0, original.end_offset)]
    assert progress == sorted(progress) and progress[-1] == 1.0
    fresh = tmp_path / "fresh.txt"
    fresh.write_bytes(path.read_bytes())
    assert entries == index_log(fresh)


def test_map_change_completed_after_append_is_used(tmp_path):
    first = map_line("Original")
    changed = map_line("Completed", 200)
    path = write_lines(tmp_path / "partial-map.txt", [first, start_line(1), end_line(1)])
    split = changed.index(",200")
    with path.open("ab") as file:
        file.write(changed[:split].encode("utf-8"))
    original = index_log(path)[0]
    with path.open("ab") as file:
        file.write((changed[split:] + "\r\n" + start_line(2) + "\r\n" + end_line(2) + "\r\n").encode())
    refreshed = index_log(path)
    assert refreshed[0].analysis_digest == original.analysis_digest
    assert refreshed[1].map_line == changed


def test_bounded_chunked_search_respects_end_and_reports_absolute_progress(tmp_path):
    needle = b"  MAP_CHANGE,"
    data = b"q" * 14 + needle + b"r" * 10 + needle + b"s" * 20
    path = tmp_path / "bounded.bin"
    path.write_bytes(data)
    with path.open("rb") as file, mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ) as mm:
        for start in (0, 14, 15, 32, len(data)):
            for end in (0, 14, 15, 27, 28, 43, len(data)):
                assert local_index._find_yielding(mm, needle, start, None, chunk=16, end=end) == mm.find(
                    needle, start, end
                )
        progress = []
        assert local_index._find_yielding(mm, b"absent", 0, progress.append, chunk=16, end=32) == -1
        assert progress == [16 / len(data), 32 / len(data)]


def test_bounded_search_checks_cancellation_between_chunks(tmp_path):
    path = tmp_path / "cancel.bin"
    path.write_bytes(b"q" * 100)
    checks = 0

    def check():
        nonlocal checks
        checks += 1
        if checks == 3:
            raise TaskCancelled("synthetic cancellation")

    token = cancel_check.set(check)
    try:
        with path.open("rb") as file, mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            with pytest.raises(TaskCancelled, match="synthetic"):
                local_index._find_yielding(mm, b"absent", 0, None, chunk=16, end=90)
    finally:
        cancel_check.reset(token)
    assert checks == 3


class _MapSearchTrace:
    def __init__(self, mm):
        self.mm = mm
        self.forward_bytes = 0
        self.reverse_calls = 0

    def __len__(self):
        return len(self.mm)

    def __getitem__(self, key):
        return self.mm[key]

    def find(self, needle, start=0, end=None):
        end = len(self.mm) if end is None else end
        if needle == b"  MAP_CHANGE,":
            self.forward_bytes += end - start
        return self.mm.find(needle, start, end)

    def rfind(self, needle, start=0, end=None):
        end = len(self.mm) if end is None else end
        if needle == b"  MAP_CHANGE,":
            self.reverse_calls += 1
        return self.mm.rfind(needle, start, end)


def test_many_pulls_search_maps_with_linear_work(tmp_path):
    searches = []
    for count in (32, 64, 128):
        lines = [map_line("Original")]
        for number in range(1, count + 1):
            lines.extend([start_line(number), "#" * 4096, end_line(number)])
        path = write_lines(tmp_path / f"many-{count}.txt", lines)
        with path.open("rb") as file, mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            trace = _MapSearchTrace(mm)
            entries = []
            local_index._scan(trace, 0, entries, None)
            assert len(entries) == count and all(entry.map_line == lines[0] for entry in entries)
            assert trace.reverse_calls == 0
            assert trace.forward_bytes <= len(mm) + len(lines[0])
            searches.append(trace.forward_bytes)
    assert searches[2] < searches[0] * 4.2
