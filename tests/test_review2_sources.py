from __future__ import annotations

import copy
import gzip
import json
import math

import httpx
import pytest
from fixture_log import TANK, TANK_GUID, cast, ts

from wcl_replay.bosses.base import WclSlice
from wcl_replay.core.models import Actor, ActorKind, Fight, FightData, Sample
from wcl_replay.core.tracks import Track, Tracks
from wcl_replay.sources.local_log import index as local_index
from wcl_replay.sources.local_log import index_log, parse_encounter
from wcl_replay.sources.wcl_api import client as transport
from wcl_replay.sources.wcl_api import events as event_store
from wcl_replay.sources.wcl_api.client import WclClient
from wcl_replay.sources.wcl_api.convert import convert


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))


def fail_write(*args, **kwargs):
    raise PermissionError("synthetic unwritable cache")


def write_pull(path, extra="", end=True):
    lines = [
        ts(0) + '  ENCOUNTER_START,999,"测试",16,20,1',
        ts(10000) + "  " + cast(TANK, TANK_GUID, 1, 0, 1),
    ]
    if extra:
        lines.append(extra)
    if end:
        lines.append(ts(10000) + '  ENCOUNTER_END,999,"测试",16,20,0,10000')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_local_nonfinite_coordinates_are_diagnosed(tmp_path, value):
    path = write_pull(tmp_path / "log.txt", ts(1000) + "  " + cast(TANK, TANK_GUID, 1, value, 1))
    data = parse_encounter(path, index_log(path)[0])
    assert data.diagnostics["已忽略无效坐标或资源样本"] == 1
    assert all(s.valid() for samples in data.samples.values() for s in samples)
    assert all(math.isfinite(v) for v in Tracks(data).bounds(list(data.actors)))


@pytest.mark.parametrize(
    "field,value",
    [("x", float("nan")), ("y", float("inf")), ("facing", float("nan")), ("hitPoints", float("inf"))],
)
def test_wcl_bad_position_preserves_the_valid_event(field, value):
    report = {"masterData": {"actors": [{"id": 1, "type": "Player", "name": "玩家"}]}}
    fight = {"id": 1, "startTime": 0, "endTime": 10000}
    event = {"type": "cast", "timestamp": 100, "sourceID": 1, "x": 0, "y": 0, field: value}
    data = convert(report, fight, [event])
    assert len(data.events) == 1
    assert not data.samples
    assert sum(data.diagnostics.values()) == 1


@pytest.mark.parametrize("time", [float("nan"), float("inf"), -1, 2**70])
def test_core_rejects_invalid_sample_times(time):
    data = FightData(
        Fight(1, 999, "测试", 16, 20, 10000, False),
        {0: Actor(0, "p", "玩家", ActorKind.PLAYER)},
        [],
        {0: [Sample(time, 0, 0, 0, 1, 1)]},
    )
    tracks = Tracks(data)
    assert not tracks.has(0)
    assert data.diagnostics


def test_extreme_finite_interpolation_does_not_overflow():
    track = Track([Sample(0, -1e308, 0, 0, 1, 1), Sample(1000, 1e308, 0, 0, 1, 1)])
    assert track.pose(500).x == 0
    track = Track([Sample(0, 0, 0, -1e308, 1, 1), Sample(1000, 1, 1, 1e308, 1, 1)])
    assert math.isfinite(track.pose(500).facing)


@pytest.mark.parametrize("coordinate", [None, "wrong", [], 2**10000])
def test_core_diagnoses_malformed_coordinate_types(coordinate):
    data = FightData(
        Fight(1, 999, "测试", 16, 20, 10000, False), {}, [], {0: [Sample(0, coordinate, 0, 0, 1, 1)]}
    )
    assert not Tracks(data).has(0)
    assert data.diagnostics


@pytest.mark.parametrize("blob", [[], None, {"version": local_index.INDEX_VERSION, "size": "wrong"}])
def test_wrong_index_cache_structure_is_rebuilt(tmp_path, blob):
    path = write_pull(tmp_path / "log.txt")
    cache = local_index._cache_file(path)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(blob), encoding="utf-8")
    assert index_log(path)[0].closed
    cache.write_text(json.dumps(blob), encoding="utf-8")
    assert local_index.marker_events(path) == []


def test_bad_cached_entry_field_is_rebuilt(tmp_path):
    path = write_pull(tmp_path / "log.txt")
    index_log(path)
    cache = local_index._cache_file(path)
    blob = json.loads(cache.read_text("utf-8"))
    blob["entries"][0]["start_ts"] = []
    cache.write_text(json.dumps(blob), encoding="utf-8")
    assert isinstance(index_log(path)[0].start_ts, str)


class Client:
    def __init__(self):
        self.calls = 0

    def query(self, query, variables):
        self.calls += 1
        return {"reportData": {"report": {"events": {"data": [{"timestamp": 1}], "nextPageTimestamp": None}}}}


def test_truncated_gzip_is_redownloaded(tmp_path):
    client = Client()
    fight = {"id": 1, "startTime": 0, "endTime": 10000}
    slices = (WclSlice("测试", "Casts"),)
    args = (client, "cn.warcraftlogs.com", "synthetic", fight, slices)
    expected = event_store.cached_events(*args)
    path = next((tmp_path / "cache").rglob("*.gz"))
    path.write_bytes(path.read_bytes()[:-8])
    assert event_store.cached_events(*args) == expected
    assert client.calls == 2
    with gzip.open(path, "rt", encoding="utf-8") as file:
        assert json.load(file)["events"] == expected


def test_cache_write_failure_preserves_download(monkeypatch):
    monkeypatch.setattr(event_store, "write_json", fail_write)
    client = Client()
    assert event_store.cached_events(
        client,
        "cn.warcraftlogs.com",
        "synthetic",
        {"id": 1, "startTime": 0, "endTime": 10},
        (WclSlice("测试", "Casts"),),
    )
    assert client.calls == 1


def test_index_write_failure_preserves_scan(tmp_path, monkeypatch):
    monkeypatch.setattr(local_index, "write_json", fail_write)
    assert index_log(write_pull(tmp_path / "log.txt"))[0].duration_ms == 10000


def test_unwritable_cache_directory_is_optional(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(blocker))
    assert index_log(write_pull(tmp_path / "log.txt"))[0].closed
    assert event_store.cached_events(
        Client(),
        "cn.warcraftlogs.com",
        "synthetic",
        {"id": 1, "startTime": 0, "endTime": 10},
        (WclSlice("测试", "Casts"),),
    )


def test_token_write_failure_keeps_memory_token(monkeypatch):
    monkeypatch.setattr(transport, "write_json", fail_write)
    client = WclClient("synthetic-id", "synthetic-secret")
    client.http.close()
    client.http = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"access_token": "synthetic-token", "expires_in": 3600})
        )
    )
    try:
        assert client.token() == "synthetic-token"
        assert client.token() == "synthetic-token"
    finally:
        client.close()


def test_partial_end_is_revisited_after_append_and_open_duration_updates(tmp_path):
    path = write_pull(tmp_path / "log.txt", end=False)
    with path.open("a", encoding="utf-8") as file:
        file.write(ts(10000) + '  ENCOUNTER_END,999,"测试",16,20,0,')
    entry = index_log(path)[0]
    assert not entry.closed and entry.duration_ms == 10000
    with path.open("a", encoding="utf-8") as file:
        file.write("10000\n")
    entry = index_log(path)[0]
    assert entry.closed and entry.duration_ms == 10000


def test_open_duration_grows_and_malformed_records_are_diagnosed(tmp_path, caplog):
    path = write_pull(tmp_path / "log.txt", ts(1000) + '  ENCOUNTER_START,nope,"坏记录",16,20,1', end=False)
    assert index_log(path)[0].duration_ms == 10000
    with path.open("a", encoding="utf-8") as file:
        file.write(ts(12000) + "  COMBATANT_INFO\n")
    entry = index_log(path)[0]
    assert not entry.closed and entry.duration_ms == 12000
    assert "字节" in caplog.text
    assert parse_encounter(path, entry).diagnostics["已忽略不完整的 COMBATANT_INFO"] == 1


def test_cached_hash_progress_still_detects_same_size_rewrite(tmp_path):
    path = write_pull(tmp_path / "log.txt")
    before = copy.deepcopy(index_log(path))
    progress = []
    assert index_log(path, progress.append)[0].content_digest == before[0].content_digest
    assert progress[-1] == 1
    path.write_bytes(path.read_bytes().replace(b"0.00,1.00", b"2.00,1.00"))
    assert index_log(path)[0].content_digest != before[0].content_digest
