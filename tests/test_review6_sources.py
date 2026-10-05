# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from wcl_replay.core.cancellation import TaskCancelled, cancel_check
from wcl_replay.sources.local_log import index as local_index
from wcl_replay.sources.local_log import index_log, parse_encounter, timestamps


@pytest.fixture(autouse=True)
def isolated_cache_and_days(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))
    timestamps._day_ms.cache_clear()
    yield
    timestamps._day_ms.cache_clear()


def _pull(*, encounter=999, group_size=20, kill=1, second=0):
    return (
        f'9/28/2026 23:22:{second:02d}.000  ENCOUNTER_START,{encounter},"Boss",16,{group_size},3004\n'
        f'9/28/2026 23:22:{second + 1:02d}.000  ENCOUNTER_END,{encounter},"Boss",16,{group_size},{kill},1000\n'
    ).encode()


def _rewrite(path, data):
    assert len(data) == path.stat().st_size
    # Avoid truncating a file while its original bytes are mapped by the indexing attempt.
    with path.open("r+b") as file:
        file.write(data)


@pytest.mark.parametrize("restore_mtime", [False, True])
def test_same_length_edit_after_pull_hash_retries_without_poisoning_cache(
    tmp_path, monkeypatch, restore_mtime
):
    path = tmp_path / "rewrite.txt"
    path.write_bytes(_pull())
    stat, change = local_index._file_metadata(path)
    if restore_mtime and change is None:
        pytest.skip("filesystem change time is unavailable")
    clip = local_index.clip_world_markers_many
    scans = 0

    def rewrite_after_hash(*args):
        nonlocal scans
        result = clip(*args)
        scans += 1
        if scans == 1:
            _rewrite(path, _pull(kill=0))
            if restore_mtime:
                os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        return result

    monkeypatch.setattr(local_index, "clip_world_markers_many", rewrite_after_hash)
    progress = []
    entry = index_log(path, progress.append)[0]
    assert scans == 2
    assert not entry.kill and not parse_encounter(path, entry).fight.kill
    assert progress == sorted(progress) and progress[-1] == 1.0
    cached = json.loads(local_index._cache_file(path).read_text("utf-8"))
    assert cached["digest"] == hashlib.blake2b(path.read_bytes(), digest_size=16).hexdigest()
    assert index_log(path)[0] == entry


def test_replacement_after_initial_hash_retries_with_the_new_file_identity(tmp_path, monkeypatch):
    path = tmp_path / "replace.txt"
    path.write_bytes(_pull())
    digest = local_index._prefix_digest
    replaced = False

    def replace_after_hash(path_, size, progress=None):
        nonlocal replaced
        result = digest(path_, size, progress)
        if not replaced:
            replacement = tmp_path / "next.txt"
            replacement.write_bytes(_pull(kill=0))
            os.replace(replacement, path)
            replaced = True
        return result

    monkeypatch.setattr(local_index, "_prefix_digest", replace_after_hash)
    entry = index_log(path)[0]
    assert replaced and not entry.kill
    assert parse_encounter(path, entry).fight.kill is False
    cached = json.loads(local_index._cache_file(path).read_text("utf-8"))
    stat = path.stat()
    assert cached["identity"] == [stat.st_dev, stat.st_ino]


def test_unavailable_change_time_verifies_the_scan_even_if_mtime_is_restored(tmp_path, monkeypatch):
    path = tmp_path / "no-change-time.txt"
    path.write_bytes(_pull())
    metadata = local_index._file_metadata
    monkeypatch.setattr(local_index, "_file_metadata", lambda path_: (metadata(path_)[0], None))
    stat = path.stat()
    clip = local_index.clip_world_markers_many
    scans = 0

    def rewrite_and_restore_mtime(*args):
        nonlocal scans
        result = clip(*args)
        scans += 1
        if scans == 1:
            _rewrite(path, _pull(kill=0))
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        return result

    monkeypatch.setattr(local_index, "clip_world_markers_many", rewrite_and_restore_mtime)
    entry = index_log(path)[0]
    assert scans == 2 and not entry.kill
    assert parse_encounter(path, entry).fight.kill is False


def test_edit_during_metadata_only_cache_verification_is_retried(tmp_path, monkeypatch):
    path = tmp_path / "verify.txt"
    path.write_bytes(_pull())
    original = index_log(path)[0]
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    digest = local_index._prefix_digest
    edited = False

    def edit_after_verification(path_, size, progress=None):
        nonlocal edited
        result = digest(path_, size, progress)
        if not edited:
            _rewrite(path, _pull(kill=0))
            edited = True
        return result

    monkeypatch.setattr(local_index, "_prefix_digest", edit_after_verification)
    entry = index_log(path)[0]
    assert edited and not entry.kill and entry.content_digest != original.content_digest
    assert parse_encounter(path, entry).fight.kill is False


def test_continuous_rewrites_stop_after_a_bound_and_preserve_the_previous_cache(tmp_path, monkeypatch):
    path = tmp_path / "busy.txt"
    path.write_bytes(_pull())
    index_log(path)
    cache = local_index._cache_file(path)
    previous = cache.read_bytes()
    _rewrite(path, _pull(group_size=21))
    clip = local_index.clip_world_markers_many
    scans = 0

    def rewrite_every_scan(*args):
        nonlocal scans
        result = clip(*args)
        scans += 1
        _rewrite(path, _pull(group_size=21 + scans))
        return result

    monkeypatch.setattr(local_index, "clip_world_markers_many", rewrite_every_scan)
    progress = []
    with pytest.raises(ValueError, match="日志在建立索引时发生变化，请稍后刷新"):
        index_log(path, progress.append)
    assert scans == 2
    assert cache.read_bytes() == previous
    assert progress == sorted(progress) and 1.0 not in progress
    monkeypatch.setattr(local_index, "clip_world_markers_many", clip)
    entry = index_log(path)[0]
    assert entry.group_size == 23 and parse_encounter(path, entry).fight.group_size == 23


@pytest.mark.parametrize("rewrite_prefix", [False, True])
def test_append_during_scan_returns_a_consistent_snapshot_and_refreshes_later(
    tmp_path, monkeypatch, rewrite_prefix
):
    path = tmp_path / "live.txt"
    original = _pull()
    path.write_bytes(original)
    appended = _pull(encounter=888, second=2)
    clip = local_index.clip_world_markers_many
    scans = 0

    def append_after_scan(*args):
        nonlocal scans
        result = clip(*args)
        scans += 1
        if scans == 1:
            if rewrite_prefix:
                _rewrite(path, _pull(kill=0))
            with path.open("ab") as file:
                file.write(appended)
        return result

    monkeypatch.setattr(local_index, "clip_world_markers_many", append_after_scan)
    progress = []
    entries = index_log(path, progress.append)
    assert progress == sorted(progress) and progress[-1] == 1.0
    if rewrite_prefix:
        assert scans == 2 and [entry.encounter_id for entry in entries] == [999, 888]
        assert not entries[0].kill
    else:
        assert scans == 1 and [entry.encounter_id for entry in entries] == [999]
        cached = json.loads(local_index._cache_file(path).read_text("utf-8"))
        assert cached["size"] == len(original)
        assert cached["digest"] == hashlib.blake2b(original, digest_size=16).hexdigest()
        assert [entry.encounter_id for entry in index_log(path)] == [999, 888]
    assert parse_encounter(path, entries[0]).fight.kill == entries[0].kill


def test_continuous_suffix_appends_do_not_force_retry_or_fail_live_indexing(tmp_path, monkeypatch):
    path = tmp_path / "continuous.txt"
    original = _pull()
    path.write_bytes(original)
    digest = local_index._prefix_digest
    reads = 0

    def append_after_every_read(path_, size, progress=None):
        nonlocal reads
        result = digest(path_, size, progress)
        reads += 1
        with path.open("ab") as file:
            file.write(b'9/28/2026 23:22:02.000  EMOTE,"live"\n')
        return result

    monkeypatch.setattr(local_index, "_prefix_digest", append_after_every_read)
    entries = index_log(path)
    assert len(entries) == 1 and reads == 2  # Initial hash and one growing-prefix verification.
    assert entries[0].end_offset < len(original)
    assert parse_encounter(path, entries[0]).fight.kill
    cached = json.loads(local_index._cache_file(path).read_text("utf-8"))
    assert cached["size"] == len(original)


def test_hot_cache_remains_usable_if_a_suffix_arrives_during_the_cache_read(tmp_path, monkeypatch):
    path = tmp_path / "hot-live.txt"
    path.write_bytes(_pull())
    original = index_log(path)
    read_cache = local_index._read_cache
    appended = False

    def append_during_cache_read(cache):
        nonlocal appended
        result = read_cache(cache)
        if not appended:
            with path.open("ab") as file:
                file.write(_pull(encounter=888, second=2))
            appended = True
        return result

    monkeypatch.setattr(local_index, "_read_cache", append_during_cache_read)
    assert index_log(path) == original
    assert [entry.encounter_id for entry in index_log(path)] == [999, 888]


def test_retry_honors_cancellation_without_saving_a_partial_snapshot(tmp_path, monkeypatch):
    path = tmp_path / "cancel.txt"
    path.write_bytes(_pull())
    clip = local_index.clip_world_markers_many
    changed = False

    def edit_then_cancel(*args):
        nonlocal changed
        result = clip(*args)
        _rewrite(path, _pull(kill=0))
        changed = True
        return result

    def check():
        if changed:
            raise TaskCancelled("cancelled before snapshot retry")

    monkeypatch.setattr(local_index, "clip_world_markers_many", edit_then_cancel)
    token = cancel_check.set(check)
    try:
        with pytest.raises(TaskCancelled, match="snapshot retry"):
            index_log(path)
    finally:
        cancel_check.reset(token)
    assert not local_index._cache_file(path).exists()


@pytest.mark.parametrize(
    "start,end,offsets",
    [
        ("11/1/2026 23:59:59.000", "11/2/2026 00:00:00.000", (-240, -300)),
        ("3/8/2026 23:59:59.000", "3/9/2026 00:00:00.000", (-300, -240)),
    ],
)
def test_dst_change_day_midnight_is_a_one_second_interval(start, end, offsets, monkeypatch):
    # Simulate local-midnight datetime.timestamp behavior on an Eastern host, without
    # changing the OS time zone. The wall-clock parser must not depend on this method.
    first_day = int(start.split("/")[1])

    def local_datetime(year, month, day):
        offset = offsets[0] if day == first_day else offsets[1]
        return datetime(year, month, day, tzinfo=timezone(timedelta(minutes=offset)))

    monkeypatch.setattr(timestamps, "datetime", local_datetime, raising=False)
    assert timestamps.parse_ts_ms(end) - timestamps.parse_ts_ms(start) == 1000


def test_wall_clock_epoch_and_leap_day_preserve_fraction_truncation():
    assert timestamps.parse_ts_ms("1/1/1970 00:00:00.0000") == 0
    assert timestamps.parse_ts_ms("2/29/2024 23:59:59.9999") + 1 == timestamps.parse_ts_ms(
        "3/1/2024 00:00:00.0001"
    )
    assert (
        timestamps.parse_ts_ms("1/1/2027 00:00:00.5009") - timestamps.parse_ts_ms("12/31/2026 23:59:59.0001")
        == 1500
    )


@pytest.mark.parametrize("closed", [False, True])
def test_dst_midnight_fight_events_and_markers_stay_within_the_declared_duration(
    tmp_path, monkeypatch, closed
):
    def eastern_datetime(year, month, day):
        return datetime(year, month, day, tzinfo=timezone(timedelta(hours=-4 if day == 1 else -5)))

    monkeypatch.setattr(timestamps, "datetime", eastern_datetime, raising=False)
    path = tmp_path / "dst.txt"
    text = (
        "11/1/2026 23:59:58.000  WORLD_MARKER_PLACED,3004,0,10,20\n"
        '11/1/2026 23:59:59.000  ENCOUNTER_START,999,"Boss",16,20,3004\n'
        '11/2/2026 00:00:00.0001  SPELL_CAST_START,Player-1,"姓名",0x512,0,nil,nil,0,0,123,"法术",1\n'
    )
    if closed:
        text += '11/2/2026 00:00:00.500  ENCOUNTER_END,999,"Boss",16,20,1,1500\n'
    path.write_text(text, encoding="utf-8")
    data = parse_encounter(path, index_log(path)[0])
    duration = 1500 if closed else 1000
    assert data.fight.duration_ms == duration and data.events[0].t == 1000
    assert (data.markers[0].start, data.markers[0].end) == (0, duration + 1)


def test_pre_wall_clock_index_schema_rebuilds_cached_marker_times(tmp_path, monkeypatch):
    path = tmp_path / "old-cache.txt"
    path.write_bytes(b"9/28/2026 23:21:59.000  WORLD_MARKER_PLACED,3004,0,10,20\n" + _pull())
    original = index_log(path)
    cache = local_index._cache_file(path)
    cached = json.loads(cache.read_text("utf-8"))
    cached["version"] = 9
    cached["markers"][0]["abs_ms"] += 3600_000
    cached["entries"][0]["marker_snapshot"] = []
    cache.write_text(json.dumps(cached), encoding="utf-8")
    scan = local_index._scan
    scanned = False

    def observe_scan(*args):
        nonlocal scanned
        scanned = True
        return scan(*args)

    monkeypatch.setattr(local_index, "_scan", observe_scan)
    assert index_log(path) == original and scanned
    assert json.loads(cache.read_text("utf-8"))["version"] > 9
    assert parse_encounter(path, original[0]).markers[0].x == 10
