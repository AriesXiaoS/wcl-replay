from __future__ import annotations

import pytest

from wcl_replay.core.markers import MarkerEvent, clip_world_markers
from wcl_replay.sources.local_log import index_log, parse_encounter


@pytest.mark.parametrize("removed_last", [True, False])
def test_marker_ties_follow_byte_offsets_even_when_input_is_reversed(removed_last):
    placed = MarkerEvent(10 if removed_last else 20, 1000, True, 0, 10, 20, 3004)
    removed = MarkerEvent(20 if removed_last else 10, 1000, False, 0)
    markers = clip_world_markers([removed, placed], 2000, 10000, 3004)
    assert len(markers) == (0 if removed_last else 1)
    if markers:
        assert (markers[0].start, markers[0].end) == (0, 10001)


@pytest.mark.parametrize("removed_last", [True, False])
def test_submillisecond_marker_order_survives_index_and_cache(tmp_path, monkeypatch, removed_last):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    placed = "WORLD_MARKER_PLACED,3004,0,10.00,20.00"
    removed = "WORLD_MARKER_REMOVED,0"
    first, second = (placed, removed) if removed_last else (removed, placed)
    log = tmp_path / "markers.txt"
    log.write_text(
        f"9/28/2026 23:21:50.0000  {first}\n"
        f"9/28/2026 23:21:50.0001  {second}\n"
        '9/28/2026 23:22:00.0000  ENCOUNTER_START,999,"测试",16,20,3004\n'
        '9/28/2026 23:22:10.0000  ENCOUNTER_END,999,"测试",16,20,0,10000\n',
        encoding="utf-8",
    )
    for _ in range(2):
        (entry,) = index_log(log)
        data = parse_encounter(log, entry)
        assert len(data.markers) == (0 if removed_last else 1)
        assert bool(data.markers_at(5000)) is not removed_last


def test_marker_ties_without_file_offsets_keep_supplied_order():
    placed = MarkerEvent(0, 1000, True, 0, 10, 20, 3004)
    removed = MarkerEvent(0, 1000, False, 0)
    assert clip_world_markers([placed, removed], 2000, 10000, 3004) == []
    assert len(clip_world_markers([removed, placed], 2000, 10000, 3004)) == 1
