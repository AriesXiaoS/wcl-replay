# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from __future__ import annotations

import json
import mmap
import os
from pathlib import Path

import pytest
from fixture_log import TANK, TANK_GUID, cast, ts

from wcl_replay.core.cancellation import TaskCancelled, cancel_check
from wcl_replay.sources.local_log import index as local_index
from wcl_replay.sources.local_log import index_log, parse_encounter


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))


def write_pull(path: Path, *, closed: bool = True, repeats: int = 1) -> Path:
    lines = [ts(0) + '  ENCOUNTER_START,999,"测试",16,20,1']
    lines.extend(ts(10000) + "  " + cast(TANK, TANK_GUID, 1, 0, 1) for _ in range(repeats))
    if closed:
        lines.append(ts(10000) + '  ENCOUNTER_END,999,"测试",16,20,0,10000')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def forbid_hash(*args, **kwargs):
    raise AssertionError("unchanged metadata must not read the log for hashing")


def test_unchanged_metadata_returns_cache_without_hashing(tmp_path, monkeypatch):
    path = write_pull(tmp_path / "log.txt")
    entries = index_log(path)
    if local_index._file_metadata(path)[1] is None:
        pytest.skip("filesystem change time is unavailable")
    monkeypatch.setattr(local_index, "_prefix_digest", forbid_hash)
    seen = []
    assert index_log(path, seen.append) == entries
    assert seen == [1.0]


def test_metadata_only_change_verifies_once_then_refreshes_fast_cache_signature(tmp_path, monkeypatch):
    path = write_pull(tmp_path / "log.txt")
    entries = index_log(path)
    stat, change_time = local_index._file_metadata(path)
    if change_time is None:
        pytest.skip("filesystem change time is unavailable")
    cache_file = local_index._cache_file(path)
    before = json.loads(cache_file.read_text("utf-8"))
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    digest = local_index._prefix_digest
    sizes = []

    def traced_digest(path, size, progress=None):
        sizes.append(size)
        return digest(path, size, progress)

    monkeypatch.setattr(local_index, "_prefix_digest", traced_digest)
    assert index_log(path) == entries
    assert sizes == [stat.st_size]
    refreshed = json.loads(cache_file.read_text("utf-8"))
    assert refreshed["entries"] == before["entries"]
    assert refreshed["markers"] == before["markers"]
    assert refreshed["digest"] == before["digest"]
    assert refreshed["mtime_ns"] == path.stat().st_mtime_ns
    assert refreshed["change_time_ns"] == local_index._file_metadata(path)[1]
    monkeypatch.setattr(local_index, "_prefix_digest", forbid_hash)
    assert index_log(path) == entries


def test_metadata_refresh_write_failure_still_returns_verified_entries(tmp_path, monkeypatch, caplog):
    path = write_pull(tmp_path / "log.txt")
    entries = index_log(path)
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

    def fail_write(*args, **kwargs):
        raise PermissionError("synthetic unwritable cache")

    monkeypatch.setattr(local_index, "write_json", fail_write)
    assert index_log(path) == entries
    assert "缓存未保存" in caplog.text


@pytest.mark.parametrize("restore_mtime", [False, True])
def test_same_size_rewrite_invalidates_cache_even_when_mtime_is_restored(tmp_path, restore_mtime):
    path = write_pull(tmp_path / "log.txt")
    before = index_log(path)[0]
    original_stat, original_change = local_index._file_metadata(path)
    original_bytes = path.read_bytes()
    updated_bytes = original_bytes.replace(b"0.00,1.00", b"2.00,1.00")
    assert len(updated_bytes) == len(original_bytes) and updated_bytes != original_bytes
    path.write_bytes(updated_bytes)
    if restore_mtime:
        os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        assert path.stat().st_mtime_ns == original_stat.st_mtime_ns
        changed = local_index._file_metadata(path)[1]
        if original_change is not None and changed is not None:
            assert changed != original_change
    after = index_log(path)[0]
    assert after.content_digest != before.content_digest
    assert parse_encounter(path, after).samples[0][0].x == 2.0


def test_replacement_with_same_size_and_mtime_invalidates_cache(tmp_path):
    path = write_pull(tmp_path / "log.txt")
    before = index_log(path)[0]
    stat = path.stat()
    replacement = tmp_path / "replacement.txt"
    replacement.write_bytes(path.read_bytes().replace(b"0.00,1.00", b"2.00,1.00"))
    os.utime(replacement, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    os.replace(replacement, path)
    assert index_log(path)[0].content_digest != before.content_digest


@pytest.mark.parametrize("initially_unavailable", [False, True])
def test_unavailable_change_time_keeps_full_hash_validation(tmp_path, monkeypatch, initially_unavailable):
    path = write_pull(tmp_path / "log.txt")
    metadata = local_index._file_metadata
    if initially_unavailable:
        monkeypatch.setattr(local_index, "_file_metadata", lambda path: (metadata(path)[0], None))
    entries = index_log(path)
    monkeypatch.setattr(local_index, "_file_metadata", lambda path: (metadata(path)[0], None))
    digest = local_index._prefix_digest
    sizes = []

    def traced_digest(path, size, progress=None):
        sizes.append(size)
        return digest(path, size, progress)

    monkeypatch.setattr(local_index, "_prefix_digest", traced_digest)
    assert index_log(path) == entries
    assert index_log(path) == entries
    assert sizes == [path.stat().st_size, path.stat().st_size]


@pytest.mark.skipif(os.name != "nt", reason="Windows metadata API")
def test_windows_metadata_query_failure_uses_hash_fallback(tmp_path, monkeypatch):
    import ctypes

    def unavailable(*args, **kwargs):
        raise OSError("synthetic unsupported filesystem")

    path = write_pull(tmp_path / "log.txt")
    monkeypatch.setattr(ctypes, "WinDLL", unavailable)
    assert local_index._file_metadata(path)[1] is None
    assert index_log(path)[0].closed


def test_growth_still_hashes_previous_prefix_before_incremental_scan(tmp_path, monkeypatch):
    path = write_pull(tmp_path / "log.txt")
    first = index_log(path)[0]
    old_size = path.stat().st_size
    with path.open("a", encoding="utf-8") as file:
        file.write(ts(11000) + '  ENCOUNTER_START,888,"下一场",16,20,1\n')
        file.write(ts(12000) + '  ENCOUNTER_END,888,"下一场",16,20,0,1000\n')
    digest = local_index._prefix_digest
    sizes = []

    def traced_digest(path, size, progress=None):
        sizes.append(size)
        return digest(path, size, progress)

    monkeypatch.setattr(local_index, "_prefix_digest", traced_digest)
    entries = index_log(path)
    assert sizes == [old_size, path.stat().st_size]
    assert [entry.encounter_id for entry in entries] == [999, 888]
    assert entries[0].content_digest == first.content_digest


def test_growth_with_changed_prefix_rebuilds_old_entries(tmp_path):
    path = write_pull(tmp_path / "log.txt")
    before = index_log(path)[0]
    path.write_bytes(path.read_bytes().replace(b"0.00,1.00", b"2.00,1.00") + b"\n")
    assert index_log(path)[0].content_digest != before.content_digest


def test_cache_return_checks_cancellation_after_metadata(tmp_path, monkeypatch):
    path = write_pull(tmp_path / "log.txt")
    index_log(path)
    if local_index._file_metadata(path)[1] is None:
        pytest.skip("filesystem change time is unavailable")
    monkeypatch.setattr(local_index, "_prefix_digest", forbid_hash)
    calls = 0

    def check():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise TaskCancelled("cancelled before returning cached entries")

    token = cancel_check.set(check)
    try:
        with pytest.raises(TaskCancelled):
            index_log(path)
    finally:
        cancel_check.reset(token)
    assert calls == 2


def test_old_cache_schema_rebuilds_metadata_signature(tmp_path):
    path = write_pull(tmp_path / "log.txt")
    index_log(path)
    cache_file = local_index._cache_file(path)
    cached = json.loads(cache_file.read_text("utf-8"))
    cached["version"] = local_index.INDEX_VERSION - 1
    cached.pop("change_time_ns")
    cache_file.write_text(json.dumps(cached), encoding="utf-8")
    assert index_log(path)[0].closed
    rebuilt = json.loads(cache_file.read_text("utf-8"))
    assert rebuilt["version"] == local_index.INDEX_VERSION
    assert "change_time_ns" in rebuilt


def test_unfinished_pull_duration_is_scanned_once(tmp_path, monkeypatch):
    path = write_pull(tmp_path / "log.txt", closed=False, repeats=10)
    duration = local_index._fight_duration
    calls = 0

    def traced_duration(*args):
        nonlocal calls
        calls += 1
        return duration(*args)

    monkeypatch.setattr(local_index, "_fight_duration", traced_duration)
    entry = index_log(path)[0]
    assert calls == 1
    assert entry.duration_ms == parse_encounter(path, entry).fight.duration_ms == 10000


def test_unfinished_duration_cancellation_checks_are_bounded_and_timely(tmp_path):
    path = write_pull(tmp_path / "log.txt", closed=False, repeats=4096)
    entry = index_log(path)[0]
    calls = 0

    def check():
        nonlocal calls
        calls += 1

    with path.open("rb") as file, mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ) as mm:
        token = cancel_check.set(check)
        try:
            assert local_index._fight_duration(mm, entry) == 10000
        finally:
            cancel_check.reset(token)
        assert 2 < calls < 20
        calls = 0

        def cancel():
            nonlocal calls
            calls += 1
            if calls == 2:
                raise TaskCancelled("cancel during duration scan")

        token = cancel_check.set(cancel)
        try:
            with pytest.raises(TaskCancelled):
                local_index._fight_duration(mm, entry)
        finally:
            cancel_check.reset(token)
        assert calls == 2
