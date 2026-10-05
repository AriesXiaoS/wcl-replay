# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from __future__ import annotations

import copy
import gzip
import json

import pytest

from wcl_replay.bosses.base import WclSlice
from wcl_replay.core.cancellation import TaskCancelled, cancel_check
from wcl_replay.sources.wcl_api import events as event_store
from wcl_replay.sources.wcl_api.client import WclClient, WclError
from wcl_replay.sources.wcl_api.fetch import fetch_fight
from wcl_replay.storage import cache_dir
from wcl_replay.workers import fetch_wcl_job

CODE = "ABCDEFGHIJKLMNOP"
HOST = "cn.warcraftlogs.com"
URL = f"https://{HOST}/reports/{CODE}?fight=1"
FIGHT = {"id": 1, "encounterID": 999, "startTime": 0, "endTime": 10000}
POLICY = (WclSlice("玩家受伤", "DamageTaken"),)


class _Server:
    def __init__(self):
        self.revision = 1
        self.amount = 10
        self.calls = []
        self.clients = []

    def open(self, *args, **kwargs):
        client = _Client(self, kwargs.get("host", HOST))
        self.clients.append(client)
        return client

    def query(self, query, variables):
        self.calls.append(variables.copy())
        if "kind" not in variables:
            report = {
                "startTime": 1_800_000_000_000,
                "fights": [copy.deepcopy(FIGHT)],
                "masterData": {
                    "actors": [{"id": 1, "name": "Boss", "type": "NPC"}],
                    "abilities": [],
                },
            }
            # Omit fields the real GraphQL request did not ask for.
            if "revision" in query:
                report["revision"] = self.revision
            return {"reportData": {"report": report}}
        events = (
            [{"timestamp": 500, "type": "damage", "sourceID": 1, "amount": self.amount}]
            if variables["kind"] in ("All", "DamageTaken")
            else []
        )
        return {"reportData": {"report": {"events": {"data": events, "nextPageTimestamp": None}}}}


class _Client:
    def __init__(self, server, host):
        self.server = server
        self.host = host
        self.closed = False

    def close(self):
        self.closed = True

    def query(self, query, variables):
        return self.server.query(query, variables)


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))


@pytest.fixture
def server(monkeypatch):
    server = _Server()
    monkeypatch.setattr("wcl_replay.sources.wcl_api.fetch.WclClient", server.open)
    return server


def _fetch(*, force_refresh=False):
    return fetch_fight("synthetic-id", "synthetic-secret", HOST, URL, force_refresh=force_refresh)


def test_report_reexport_redownloads_same_range_events_and_reuses_new_revision(server):
    first = _fetch()
    assert first.events[0].amount == 10
    assert len(server.calls) == 6
    server.revision = 2
    server.amount = 99
    updated = _fetch()
    assert updated.fight == first.fight
    assert updated.events[0].amount == 99
    assert len(server.calls) == 12
    assert _fetch().events[0].amount == 99
    assert len(server.calls) == 13
    assert all(client.closed for client in server.clients)


def test_force_fetch_redownloads_a_fresh_cache_with_unchanged_revision(server):
    assert _fetch().events[0].amount == 10
    server.amount = 99
    assert _fetch().events[0].amount == 10
    assert len(server.calls) == 7
    assert _fetch(force_refresh=True).events[0].amount == 99
    assert len(server.calls) == 13
    assert _fetch().events[0].amount == 99
    assert len(server.calls) == 14


def test_client_force_refresh_bypasses_report_memory_and_event_disk_cache(server, monkeypatch):
    client = WclClient("synthetic-id", "synthetic-secret", host=HOST)
    monkeypatch.setattr(client, "query", server.query)
    try:
        assert client.fight_data(CODE, 1).events[0].amount == 10
        server.revision = 2
        server.amount = 99
        assert client.fight_data(CODE, 1).events[0].amount == 10
        assert len(server.calls) == 3
        assert client.fight_data(CODE, 1, force_refresh=True).events[0].amount == 99
        assert client.report(CODE)["revision"] == 2
        assert len(server.calls) == 6
        assert client.fight_data(CODE, 1).events[0].amount == 99
        assert len(server.calls) == 6
    finally:
        client.close()


def test_client_updated_report_revision_invalidates_events_without_force(server, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(event_store.time, "time", lambda: clock[0])
    client = WclClient("synthetic-id", "synthetic-secret", host=HOST)
    monkeypatch.setattr(client, "query", server.query)
    try:
        assert client.fight_data(CODE, 1).events[0].amount == 10
        server.revision = 2
        server.amount = 99
        clock[0] += 301
        assert client.fight_data(CODE, 1).events[0].amount == 99
        assert len(server.calls) == 6
    finally:
        client.close()


@pytest.mark.parametrize("revision", [None, 1])
def test_event_cache_expires_at_ttl_even_with_an_unchanged_or_missing_revision(server, monkeypatch, revision):
    clock = [1000.0]
    monkeypatch.setattr(event_store.time, "time", lambda: clock[0])
    client = server.open()
    args = (client, HOST, CODE, FIGHT, POLICY)
    assert event_store.cached_events(*args, revision=revision)[0]["amount"] == 10
    server.amount = 99
    clock[0] += event_store.CACHE_TTL_SECONDS - 1
    assert event_store.cached_events(*args, revision=revision)[0]["amount"] == 10
    assert len(server.calls) == 1
    clock[0] += 1
    assert event_store.cached_events(*args, revision=revision)[0]["amount"] == 99
    assert len(server.calls) == 2
    path = next((cache_dir() / "wcl" / "events").glob("*.gz"))
    with gzip.open(path, "rt", encoding="utf-8") as file:
        blob = json.load(file)
    assert blob["fetched_at"] == clock[0]
    assert blob["revision"] == revision


@pytest.mark.parametrize("fetched_at", [None, "wrong", True, float("nan"), float("inf"), 0, 1001])
def test_invalid_or_future_fetch_time_redownloads_the_cache(server, monkeypatch, fetched_at):
    monkeypatch.setattr(event_store.time, "time", lambda: 1000.0)
    client = server.open()
    args = (client, HOST, CODE, FIGHT, POLICY)
    event_store.cached_events(*args, revision=1)
    path = next((cache_dir() / "wcl" / "events").glob("*.gz"))
    with gzip.open(path, "rt", encoding="utf-8") as file:
        blob = json.load(file)
    blob["fetched_at"] = fetched_at
    with gzip.open(path, "wt", encoding="utf-8") as file:
        json.dump(blob, file)
    server.amount = 99
    assert event_store.cached_events(*args, revision=1)[0]["amount"] == 99
    assert len(server.calls) == 2


@pytest.mark.parametrize("failure", ["network", "cancel"])
def test_failed_or_cancelled_force_refresh_preserves_the_previous_cache(server, monkeypatch, failure):
    client = server.open()
    args = (client, HOST, CODE, FIGHT, POLICY)
    assert event_store.cached_events(*args, revision=1)[0]["amount"] == 10
    path = next((cache_dir() / "wcl" / "events").glob("*.gz"))
    previous = path.read_bytes()
    query = client.query
    cancelled = False

    def failing_query(query_text, variables):
        nonlocal cancelled
        if failure == "network":
            raise WclError("synthetic offline")
        result = query(query_text, variables)
        cancelled = True
        return result

    def check():
        if cancelled:
            raise TaskCancelled("synthetic cancellation after the last page")

    monkeypatch.setattr(client, "query", failing_query)
    token = cancel_check.set(check)
    try:
        with pytest.raises(TaskCancelled if failure == "cancel" else WclError):
            event_store.cached_events(*args, revision=1, force_refresh=True)
    finally:
        cancel_check.reset(token)
    assert path.read_bytes() == previous
    assert event_store.cached_events(*args, revision=1)[0]["amount"] == 10


def test_worker_forwards_force_refresh_and_keeps_download_compute_progress(monkeypatch):
    options, notes = [], []

    def fake_fetch(client_id, client_secret, host, url, progress=None, *, force_refresh=False):
        options.append(force_refresh)
        progress(0.84, "下载完成")
        return "data"

    def fake_analyze(data, progress):
        progress(0.9, "构建坐标轨迹")
        return "tracks", "analysis"

    monkeypatch.setattr("wcl_replay.sources.wcl_api.fetch.fetch_fight", fake_fetch)
    monkeypatch.setattr("wcl_replay.workers.analyze", fake_analyze)
    result = fetch_wcl_job(
        URL,
        "synthetic-id",
        "synthetic-secret",
        HOST,
        lambda frac, msg="": notes.append((frac, msg)),
        force_refresh=True,
    )
    assert result == ("data", "tracks", "analysis")
    assert options == [True]
    assert notes == [
        (1.0, "phase:download:下载完成"),
        (1.0, "phase:download"),
        (0.35, "phase:compute:构建坐标轨迹"),
        (1.0, "phase:compute"),
    ]
