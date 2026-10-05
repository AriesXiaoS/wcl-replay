# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from __future__ import annotations

import builtins
import copy
import gzip
import hashlib
import json
from dataclasses import replace

import pytest

from wcl_replay.core.cancellation import TaskCancelled, cancel_check
from wcl_replay.sources.local_log import index_log, parse_encounter
from wcl_replay.sources.local_log import parser as local_parser
from wcl_replay.sources.wcl_api import fetch as wcl_fetch
from wcl_replay.sources.wcl_api.client import WclClient, WclError
from wcl_replay.storage import cache_dir

CODE = "ABCDEFGHIJKLMNOP"
HOST = "cn.warcraftlogs.com"
URL = f"https://{HOST}/reports/{CODE}?fight=1"
FIGHT = {"id": 1, "encounterID": 999, "startTime": 0, "endTime": 10000}


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))


class _Server:
    def __init__(self):
        self.event = {"timestamp": 500, "type": "damage", "sourceID": 1, "amount": 10}
        self.calls = []

    def query(self, query, variables):
        self.calls.append(variables.copy())
        if "kind" not in variables:
            return {
                "reportData": {
                    "report": {
                        "startTime": 1_800_000_000_000,
                        "revision": 1,
                        "fights": [FIGHT.copy()],
                        "masterData": {
                            "actors": [{"id": 1, "name": "Boss", "type": "NPC"}],
                            "abilities": [],
                        },
                    }
                }
            }
        events = [copy.deepcopy(self.event)] if variables["kind"] in ("All", "DamageTaken") else []
        return {"reportData": {"report": {"events": {"data": events, "nextPageTimestamp": None}}}}


@pytest.fixture(params=["fetch", "client"])
def wcl_reader(request, monkeypatch):
    server = _Server()
    client = WclClient("synthetic-id", "synthetic-secret", host=HOST)
    monkeypatch.setattr(client, "query", server.query)
    if request.param == "fetch":
        monkeypatch.setattr(wcl_fetch, "WclClient", lambda *args, **kwargs: client)

        def read(*, force_refresh=False):
            return wcl_fetch.fetch_fight(
                "synthetic-id", "synthetic-secret", HOST, URL, force_refresh=force_refresh
            )

    else:

        def read(*, force_refresh=False):
            return client.fight_data(CODE, 1, force_refresh=force_refresh)

    try:
        yield server, read, request.param
    finally:
        client.close()


def _event_cache():
    return next((cache_dir() / "wcl" / "events").glob("*.gz"))


def test_bad_download_is_not_cached_and_normal_retry_recovers(wcl_reader):
    server, read, _ = wcl_reader
    server.event["sourceID"] = "broken"
    with pytest.raises(WclError, match="事件数据格式错误"):
        read()
    assert not list((cache_dir() / "wcl" / "events").glob("*.gz"))
    server.event["sourceID"] = 1
    server.event["amount"] = 99
    assert read().events[0].amount == 99
    calls = len(server.calls)
    assert read().events[0].amount == 99
    assert len(server.calls) - calls <= 1  # Fetch reads metadata; the client has a report-memory hit.


def test_structurally_valid_bad_cache_is_automatically_replaced(wcl_reader):
    server, read, _ = wcl_reader
    assert read().events[0].amount == 10
    path = _event_cache()
    with gzip.open(path, "rt", encoding="utf-8") as file:
        blob = json.load(file)
    blob["events"][0]["sourceID"] = "broken"
    with gzip.open(path, "wt", encoding="utf-8") as file:
        json.dump(blob, file)
    server.event["amount"] = 99
    assert read().events[0].amount == 99
    with gzip.open(path, "rt", encoding="utf-8") as file:
        repaired = json.load(file)
    assert repaired["events"][0]["sourceID"] == 1
    assert repaired["events"][0]["amount"] == 99


def test_failed_force_conversion_preserves_a_usable_previous_cache(wcl_reader):
    server, read, _ = wcl_reader
    assert read().events[0].amount == 10
    path = _event_cache()
    previous = path.read_bytes()
    server.event["sourceID"] = "broken"
    with pytest.raises(WclError, match="事件数据格式错误"):
        read(force_refresh=True)
    assert path.read_bytes() == previous
    assert read().events[0].amount == 10


def test_cancellation_after_force_conversion_does_not_replace_the_cache(wcl_reader, monkeypatch):
    server, read, entry_point = wcl_reader
    assert read().events[0].amount == 10
    path = _event_cache()
    previous = path.read_bytes()
    server.event["amount"] = 99
    module = wcl_fetch if entry_point == "fetch" else __import__(WclClient.__module__, fromlist=["convert"])
    original = module.convert
    cancelled = False

    def convert_then_cancel(*args, **kwargs):
        nonlocal cancelled
        result = original(*args, **kwargs)
        cancelled = True
        return result

    def check():
        if cancelled:
            raise TaskCancelled("synthetic cancellation after conversion")

    monkeypatch.setattr(module, "convert", convert_then_cancel)
    token = cancel_check.set(check)
    try:
        with pytest.raises(TaskCancelled):
            read(force_refresh=True)
    finally:
        cancel_check.reset(token)
    assert path.read_bytes() == previous


@pytest.mark.parametrize("chunk", [3, 17, 128])
def test_streaming_parse_preserves_unicode_and_pull_boundaries(log_path, monkeypatch, chunk):
    entry = index_log(log_path)[0]
    expected = parse_encounter(log_path, entry)
    with log_path.open("ab") as file:
        file.write(b"\nthis later record must not be parsed\n")
    monkeypatch.setattr(local_parser, "_READ_CHUNK", chunk)
    sizes = []

    class Reader:
        def __init__(self, *args):
            self.file = builtins.open(*args)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.file.__exit__(*args)

        def seek(self, offset):
            return self.file.seek(offset)

        def read(self, size):
            sizes.append(size)
            return self.file.read(size)

    monkeypatch.setattr(local_parser, "open", Reader, raising=False)
    progress = []
    assert parse_encounter(log_path, entry, progress.append) == expected
    assert len(sizes) > 1 and max(sizes) <= chunk
    assert sum(sizes) == entry.end_offset - entry.start_offset
    assert progress == sorted(progress)
    assert progress[0] == 0 and progress[-1] == 1


def test_streaming_open_pull_drops_only_the_incomplete_tail(tmp_path, monkeypatch):
    path = tmp_path / "partial.txt"
    start = b'9/28/2026 23:22:00.000  ENCOUNTER_START,999,"Test",16,20,3004\r\n'
    complete = '9/28/2026 23:22:00.500  SPELL_CAST_START,Player-1,"中文姓名",0x512,0,nil,nil,0,0,123,"法术",1\r\n'.encode()
    partial = '9/28/2026 23:22:00.900  SPELL_CAST_START,Player-1,"中文'.encode()[:-1]
    path.write_bytes(start + complete + partial)
    entry = index_log(path)[0]
    monkeypatch.setattr(local_parser, "_READ_CHUNK", 3)
    data = parse_encounter(path, entry)
    assert len(data.events) == 1
    assert data.events[0].spell_name == "法术"
    assert data.actors[data.events[0].src].name == "中文姓名"
    assert data.diagnostics == {"末尾记录尚未写完，等待刷新补齐": 1}

    # Growing the file must not make the old entry consume bytes beyond its indexed range.
    with path.open("ab") as file:
        file.write('文姓名",0x512,0,nil,nil,0,0,123,"法术",1\r\n'.encode()[2:])
    assert parse_encounter(path, entry) == data
    refreshed = parse_encounter(path, index_log(path)[0])
    assert len(refreshed.events) == 2
    assert refreshed.events[-1].spell_name == "法术"
    assert refreshed.actors[refreshed.events[-1].src].name == "中文姓名"
    assert refreshed.diagnostics == {}


def _large_ignored_log(tmp_path, count=5000):
    path = tmp_path / "streamed.txt"
    first = '9/28/2026 23:22:00.000  ENCOUNTER_START,999,"Test",16,20,3004\n'
    row = '9/28/2026 23:22:00.500  EMOTE,"中文记录"\n'
    end = '9/28/2026 23:22:01.000  ENCOUNTER_END,999,"Test",16,20,1,1000\n'
    path.write_text(first + row * count + end, encoding="utf-8", newline="")
    return path, index_log(path)[0]


def test_log_changed_during_streaming_never_reports_success(tmp_path, monkeypatch):
    path, entry = _large_ignored_log(tmp_path)
    monkeypatch.setattr(local_parser, "_READ_CHUNK", 1024)
    progress = []
    changed = False

    def update(fraction):
        nonlocal changed
        progress.append(fraction)
        if fraction > 0 and not changed:
            with path.open("r+b") as file:
                file.seek(entry.end_offset - 10)
                file.write(b"9")
            changed = True

    with pytest.raises(ValueError, match="日志内容已改变"):
        parse_encounter(path, entry, update)
    assert changed
    assert progress == sorted(progress)
    assert 1.0 not in progress


def test_truncated_pull_never_reports_success(log_path):
    entry = index_log(log_path)[0]
    log_path.write_bytes(log_path.read_bytes()[:-100])
    progress = []
    with pytest.raises(ValueError, match="日志内容已改变"):
        parse_encounter(log_path, entry, progress.append)
    assert 1.0 not in progress


def test_periodic_cancellation_works_without_progress_even_inside_one_chunk(tmp_path, monkeypatch):
    path, entry = _large_ignored_log(tmp_path)
    monkeypatch.setattr(local_parser, "_READ_CHUNK", path.stat().st_size * 2)
    timestamps = []
    original = local_parser.parse_ts_ms

    def counted_timestamp(value):
        timestamps.append(value)
        return original(value)

    monkeypatch.setattr(local_parser, "parse_ts_ms", counted_timestamp)
    checks = 0

    def check():
        nonlocal checks
        checks += 1
        if checks == 3:
            raise TaskCancelled("synthetic cancellation inside one chunk")

    token = cancel_check.set(check)
    try:
        with pytest.raises(TaskCancelled):
            parse_encounter(path, entry)
    finally:
        cancel_check.reset(token)
    assert 0 < len(timestamps) < 2000


def test_empty_indexed_range_retains_digest_protection(log_path):
    entry = index_log(log_path)[0]
    empty = replace(
        entry, end_offset=entry.start_offset, content_digest=hashlib.blake2b(b"", digest_size=16).hexdigest()
    )
    data = parse_encounter(log_path, empty)
    assert data.events == [] and data.actors == {}
    with pytest.raises(ValueError, match="日志内容已改变"):
        parse_encounter(log_path, replace(empty, content_digest="wrong"))
