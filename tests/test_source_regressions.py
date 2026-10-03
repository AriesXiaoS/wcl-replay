# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from __future__ import annotations

import copy

import pytest
from fixture_log import TANK_GUID, adv

from wcl_replay.bosses.base import WclSlice
from wcl_replay.bosses.coiled_altar import constants as C
from wcl_replay.bosses.coiled_altar.wcl import slices
from wcl_replay.core.targets import Targets
from wcl_replay.core.tracks import Tracks
from wcl_replay.sources.local_log import index_log, parse_encounter
from wcl_replay.sources.wcl_api import events as event_store
from wcl_replay.sources.wcl_api.client import WclClient, WclError
from wcl_replay.sources.wcl_api.convert import convert
from wcl_replay.sources.wcl_api.fetch import fetch_fight
from wcl_replay.sources.wcl_api.urls import normalize_host, parse_report_url, report_host
from wcl_replay.storage import cache_dir

CODE = "ABCDEFGHIJKLMNOP"
FIGHT = {"id": 1, "encounterID": 999, "startTime": 0, "endTime": 10000}
REPORT = {"startTime": 0, "fights": [FIGHT], "masterData": {"actors": [], "abilities": []}}


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))


@pytest.mark.parametrize(
    "host",
    [
        "evilwarcraftlogs.com",
        "warcraftlogs.com.evil.example",
        "evil.example@www.warcraftlogs.com",
        "www.warcraftlogs.com:80",
        "http://www.warcraftlogs.com",
        "www.warcraftlogs.com%2e.evil.example",
    ],
)
def test_invalid_hosts_are_rejected_before_transport_creation(host, monkeypatch):
    monkeypatch.setattr(
        "wcl_replay.sources.wcl_api.client.httpx.Client", lambda **kw: pytest.fail("transport created")
    )
    with pytest.raises(ValueError):
        WclClient("synthetic-id", "synthetic-secret", host=host)
    url = host + f"/reports/{CODE}?fight=1"
    with pytest.raises(ValueError):
        parse_report_url(url)
    with pytest.raises(ValueError):
        report_host(url)


def test_official_hosts_and_bare_codes_are_normalized():
    assert normalize_host("https://CN.WARCRAFTLOGS.COM:443/") == "cn.warcraftlogs.com"
    assert report_host(CODE) is None
    assert parse_report_url(f"https://de.warcraftlogs.com/reports/{CODE}#fight=3") == (CODE, 3)


class FakeClient:
    def __init__(self, *args, **kwargs):
        self.calls = []
        self.host = kwargs.get("host", "cn.warcraftlogs.com")
        self.closed = False

    def close(self):
        self.closed = True

    def query(self, query, variables):
        self.calls.append(variables)
        if "kind" not in variables:
            return {"reportData": {"report": copy.deepcopy(REPORT)}}
        return {"reportData": {"report": {"events": {"data": [], "nextPageTimestamp": None}}}}


def test_main_fetch_path_reuses_disk_events_after_client_restart(monkeypatch):
    clients = []

    def client_factory(*args, **kwargs):
        client = FakeClient()
        clients.append(client)
        return client

    monkeypatch.setattr("wcl_replay.sources.wcl_api.fetch.WclClient", client_factory)
    url = f"https://cn.warcraftlogs.com/reports/{CODE}?fight=1"
    fetch_fight("synthetic-id", "synthetic-secret", "cn.warcraftlogs.com", url)
    fetch_fight("synthetic-id", "synthetic-secret", "cn.warcraftlogs.com", url)
    assert len(clients[0].calls) == 6
    assert len(clients[1].calls) == 1
    assert all(client.closed for client in clients)


def test_event_cache_scope_version_range_and_corruption(monkeypatch):
    client = FakeClient()
    policy = (WclSlice("casts", "Casts"),)

    def fetch(host="cn.warcraftlogs.com", fight=None, slices_=policy):
        return event_store.cached_events(client, host, CODE, fight or FIGHT, slices_)

    fetch()
    fetch()
    assert len(client.calls) == 1
    fetch(host="www.warcraftlogs.com")
    fetch(fight=dict(FIGHT, endTime=11000))
    fetch(slices_=(WclSlice("casts", "Casts", filter="ability.id = 123"),))
    assert len(client.calls) == 4
    monkeypatch.setattr(event_store, "CACHE_VERSION", event_store.CACHE_VERSION + 1)
    fetch()
    assert len(client.calls) == 5
    for path in (cache_dir() / "wcl" / "events").glob("*.gz"):
        path.write_bytes(b"not gzip")
    fetch()
    assert len(client.calls) == 6


def test_empty_pages_continue_and_overlapping_slices_preserve_multiplicity():
    raw = {"timestamp": 1000, "type": "heal", "amount": 10}

    class Client:
        def query(self, q, variables):
            page = (
                {"data": [], "nextPageTimestamp": 1000}
                if variables["start"] == 0
                else {
                    "data": [raw, raw],
                    "nextPageTimestamp": None,
                }
            )
            return {"reportData": {"report": {"events": page}}}

    result = event_store.download(
        Client(), CODE, FIGHT, (WclSlice("all", "All"), WclSlice("heal", "Healing"))
    )
    assert result == [raw, raw]


def test_stalled_pagination_is_not_saved_as_a_complete_cache():
    class Client:
        def query(self, q, variables):
            return {
                "reportData": {
                    "report": {
                        "events": {
                            "data": [{"timestamp": 0}],
                            "nextPageTimestamp": variables["start"],
                        }
                    }
                }
            }

    with pytest.raises(WclError, match="分页未推进"):
        event_store.cached_events(Client(), "cn.warcraftlogs.com", CODE, FIGHT, (WclSlice("all", "All"),))
    assert not list((cache_dir() / "wcl" / "events").glob("*.gz"))


@pytest.mark.parametrize("has_next", [False, True])
def test_pagination_limit_allows_exactly_40_complete_pages(has_next):
    class Client:
        def query(self, q, variables):
            page = int(variables["start"])
            nxt = page + 1 if page < 39 or has_next else None
            return {
                "reportData": {
                    "report": {
                        "events": {
                            "data": [{"timestamp": page}],
                            "nextPageTimestamp": nxt,
                        }
                    }
                }
            }

    if has_next:
        with pytest.raises(WclError, match="超过 40 页"):
            event_store.download(Client(), CODE, FIGHT, (WclSlice("all", "All"),))
    else:
        result = event_store.download(Client(), CODE, FIGHT, (WclSlice("all", "All"),))
        assert len(result) == 40


def test_failed_fetch_still_closes_transport(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr("wcl_replay.sources.wcl_api.fetch.WclClient", lambda *a, **kw: client)
    with pytest.raises(WclError, match="没有 fight"):
        fetch_fight(
            "synthetic-id",
            "synthetic-secret",
            "cn.warcraftlogs.com",
            f"https://cn.warcraftlogs.com/reports/{CODE}?fight=999",
        )
    assert client.closed


def test_client_fight_data_uses_shared_event_cache(monkeypatch):
    client = WclClient("synthetic-id", "synthetic-secret")
    transport = FakeClient()
    monkeypatch.setattr(client, "query", transport.query)
    try:
        first = client.fight_data(CODE, 1)
        second = client.fight_data(CODE, 1)
        assert first.fight == second.fight
        assert len(transport.calls) == 3  # metadata plus friendlies and enemies, only once
    finally:
        client.close()


def test_both_sources_preserve_same_ms_health_and_facing(tmp_path):
    path = tmp_path / "same-time.txt"
    lines = [start_line()]
    for hp, facing in [(100, 0.0), (50, 1.0)]:
        lines.append(
            '9/28/2026 23:22:00.500  SPELL_HEAL,Player-1-1,"P",0x512,0x0,'
            f'{TANK_GUID},"T",0x512,0x0,123,"Heal",0x1,{adv(TANK_GUID, 1, 2, facing, hp)},10,0,0'
        )
    lines.append(end_line())
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    local = parse_encounter(path, index_log(path)[0])
    aid = local.events[0].dst
    assert Tracks(local).pose(aid, 500).hp == 50
    assert Tracks(local).pose(aid, 500).facing == 1.0
    report = {"masterData": {"actors": [{"id": 1, "type": "Player", "name": "P"}]}}
    raw = [
        {
            "timestamp": 500,
            "type": "cast",
            "sourceID": 1,
            "resourceActor": 1,
            "x": 100,
            "y": 200,
            "hitPoints": hp,
            "maxHitPoints": 100,
            "facing": facing,
        }
        for hp, facing in [(100, 0), (50, 100)]
    ]
    wcl = convert(report, FIGHT, raw)
    aid = wcl.events[0].src
    assert Tracks(wcl).pose(aid, 500).hp == 50
    assert len(wcl.samples[aid]) == 2


def start_line(encounter=999):
    return f'9/28/2026 23:22:00.000  ENCOUNTER_START,{encounter},"Test",16,20,1'


def end_line(encounter=999):
    return f'9/28/2026 23:22:01.000  ENCOUNTER_END,{encounter},"Test",16,20,1,1000'


def test_replacement_and_append_have_distinct_cache_behavior(tmp_path):
    path = tmp_path / "rotation.txt"
    path.write_text(start_line(111) + "\n" + end_line(111) + "\n", encoding="utf-8")
    old = index_log(path)[0]
    with path.open("a", encoding="utf-8") as file:
        file.write(start_line(222) + "\n" + end_line(222) + "\n")
    appended = index_log(path)
    assert [entry.encounter_id for entry in appended] == [111, 222]
    assert appended[0].content_digest == old.content_digest
    path.write_text(start_line(333) + "\n" + end_line(333) + "\n" + "\n" * 1000, encoding="utf-8")
    assert [entry.encounter_id for entry in index_log(path)] == [333]
    with pytest.raises(ValueError, match="日志内容已改变"):
        parse_encounter(path, old)


@pytest.mark.parametrize(
    "event,extra",
    [
        ("SPELL_DAMAGE", '123,"Damage",0x1,'),
        ("SPELL_HEAL", '123,"Heal",0x1,'),
        ("SWING_DAMAGE", ""),
        ("ENVIRONMENTAL_DAMAGE", "FALLING,"),
    ],
)
def test_non_advanced_amounts_are_available_without_positions(tmp_path, event, extra):
    path = tmp_path / "ordinary.txt"
    line = f'9/28/2026 23:22:00.500  {event},Creature-1,"B",0xa48,0x0,Player-1,"P",0x512,0x0,{extra}777,0,1,0,0,0,nil,nil,nil'
    path.write_text(start_line() + "\n" + line + "\n" + end_line() + "\n", encoding="utf-8")
    data = parse_encounter(path, index_log(path)[0])
    assert data.events[0].amount == 777
    assert data.samples == {}


def test_periodic_healing_does_not_retarget():
    report = {"masterData": {"actors": [{"id": i, "name": str(i), "type": "Player"} for i in (1, 2, 3)]}}
    raw = [
        {"timestamp": 1000, "type": "cast", "sourceID": 1, "targetID": 2},
        {"timestamp": 2000, "type": "heal", "sourceID": 1, "targetID": 3, "tick": True},
        {"timestamp": 3000, "type": "damage", "sourceID": 1, "targetID": 3, "tick": True},
    ]
    data = convert(report, FIGHT, raw)
    assert data.actors[Targets(data).at(data.events[0].src, 4000)].name == "2"
    assert [event.type for event in data.events[1:]] == ["SPELL_PERIODIC_HEAL", "SPELL_PERIODIC_DAMAGE"]


def test_orb_filters_use_report_locale_and_escape_names():
    name = 'Globule "Green"'
    report = {"masterData": {"actors": [{"gameID": C.NPC_GREEN, "name": name}]}}
    filter_ = next(slice_.filter for slice_ in slices(report, {}) if slice_.label == "毒液球")
    assert 'Globule \\"Green\\"' in filter_
    assert "凝结的毒液追踪者" not in filter_
    assert f"ability.id = {C.PURPLE_PLACE}" in filter_
