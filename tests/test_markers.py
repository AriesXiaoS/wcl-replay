# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from __future__ import annotations

from wcl_replay.core.markers import MarkerEvent, clip_world_markers
from wcl_replay.sources.local_log import index_log, parse_encounter


def _ev(
    abs_ms: int, placed: bool, index: int, x: float = 0.0, y: float = 0.0, instance: int = 3004
) -> MarkerEvent:
    return MarkerEvent(0, abs_ms, placed, index, x, y, 0 if not placed else instance)


def test_clip_keeps_only_beams_up_during_the_pull():
    start = 10_000
    events = [
        _ev(1_000, True, 0, 10, 20),
        _ev(2_000, False, 0),
        _ev(9_000, True, 1, 1, 2),
        _ev(12_000, False, 1),
        _ev(14_000, True, 2, 3, 4),
        _ev(30_000, False, 2),
        _ev(11_000, False, 7),
    ]
    got = clip_world_markers(events, start, 8_000, 3004)
    assert [(m.index, m.x, m.y, m.start, m.end) for m in got] == [
        (1, 1.0, 2.0, 0, 2_000),
        (2, 3.0, 4.0, 4_000, 8_001),
    ]


def test_zone_unload_does_not_clear_the_raid_markers():
    events = [_ev(1_000, True, idx, float(idx), 1.0) for idx in (0, 1, 2, 3, 5)]
    events += [_ev(20_000, False, idx) for idx in (0, 1, 2, 3, 5)]
    events.append(_ev(50_000, False, 2))
    during = clip_world_markers(events, 30_000, 10_000, 3004)
    assert sorted(m.index for m in during) == [0, 1, 2, 3, 5]
    after_one_cleared = clip_world_markers(events, 80_000, 10_000, 3004)
    assert sorted(m.index for m in after_one_cleared) == [0, 1, 3, 5]


def test_log_markers_placed_before_the_pull(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "app"))
    path = tmp_path / "WoWCombatLog-markers.txt"
    path.write_text(
        "\n".join(
            [
                "9/28/2026 23:21:50.0000  WORLD_MARKER_PLACED,3004,0,10.00,20.00",
                '9/28/2026 23:22:00.0000  ENCOUNTER_START,3429,"盘卷祭坛",16,20,3004',
                "9/28/2026 23:22:03.0000  WORLD_MARKER_REMOVED,0",
                "9/28/2026 23:22:04.0000  WORLD_MARKER_PLACED,3004,2,3.00,4.00",
                '9/28/2026 23:22:10.0000  ENCOUNTER_END,3429,"盘卷祭坛",16,20,1,10000',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (entry,) = index_log(path)
    data = parse_encounter(path, entry)
    assert [(m.index, m.start, m.end, m.x, m.y) for m in data.markers] == [
        (0, 0, 3000, 10.0, 20.0),
        (2, 4000, 10001, 3.0, 4.0),
    ]
    assert [m.index for m in data.markers_at(0)] == [0]
    assert [m.index for m in data.markers_at(3000)] == []
    assert [m.index for m in data.markers_at(4000)] == [2]
    assert [m.index for m in data.markers_at(10000)] == [2]
    # Served from the index cache on the second open.
    again = parse_encounter(path, index_log(path)[0])
    assert [(m.index, m.start) for m in again.markers] == [(0, 0), (2, 4000)]
