# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Index of the encounters contained in a (possibly huge) WoWCombatLog file.

Only ENCOUNTER_START / ENCOUNTER_END lines, plus WORLD_MARKER place/remove lines, are located
(with mmap), so a 1.6 GB log is indexed in seconds. The result is cached next to the user's app
data and refreshed incrementally while the log keeps growing during a raid night.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import logging
import math
import mmap
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import BinaryIO

from ...core.cancellation import check_cancelled
from ...core.difficulty import DIFFICULTY_LABELS
from ...core.markers import MarkerEvent, clip_world_markers_many
from ...core.models import WorldMarker
from ...storage import cache_dir as cache_dir
from ...storage import cache_warning, write_json
from .fields import split_fields
from .timestamps import parse_ts_ms

INDEX_VERSION = 10

# Report progress through a long scan. One mmap.find over a multi-gigabyte log would not
# publish a fraction until it returned.
_SCAN_CHUNK = 8 * 1024 * 1024
_CANCEL_LINES = 1024
_CANCEL_BYTES = 256 * 1024
_INDEX_ATTEMPTS = 2


class _SnapshotChanged(ValueError):
    """The file stopped describing the snapshot an indexing attempt was reading."""


@dataclass(slots=True)
class EncounterEntry:
    seq: int
    encounter_id: int
    name: str
    difficulty: int
    group_size: int
    instance_id: int
    start_offset: int
    end_offset: int
    start_ts: str
    duration_ms: int
    kill: bool
    closed: bool
    pull_number: int = 0
    map_line: str | None = None
    content_digest: str = ""
    analysis_digest: str = ""
    # None is only for manually constructed/legacy entries; an indexed empty snapshot is ().
    marker_snapshot: tuple[WorldMarker, ...] | None = None

    @property
    def difficulty_label(self) -> str:
        return DIFFICULTY_LABELS.get(self.difficulty, str(self.difficulty))


def _prefix_digest(path: Path, size: int, progress: Callable[[float], None] | None = None) -> str:
    digest = hashlib.blake2b(digest_size=16)
    with path.open("rb") as file:
        remaining = size
        while remaining:
            check_cancelled()
            block = file.read(min(_SCAN_CHUNK, remaining))
            if not block:
                raise ValueError("日志在读取时被截断，请刷新")
            digest.update(block)
            remaining -= len(block)
            if progress:
                progress((size - remaining) / max(1, size))
    return digest.hexdigest()


def _windows_change_time_ns(file: BinaryIO) -> int | None:
    """Read Windows metadata change time; ``st_ctime`` is creation time there."""
    try:
        import ctypes
        import msvcrt
        from ctypes import wintypes

        class FileBasicInfo(ctypes.Structure):
            _fields_ = [
                ("creation_time", ctypes.c_longlong),
                ("last_access_time", ctypes.c_longlong),
                ("last_write_time", ctypes.c_longlong),
                ("change_time", ctypes.c_longlong),
                ("attributes", wintypes.DWORD),
            ]

        query = ctypes.WinDLL("kernel32", use_last_error=True).GetFileInformationByHandleEx
        query.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        query.restype = wintypes.BOOL
        info = FileBasicInfo()
        # Borrow the existing handle. The Python file owns and closes it.
        handle = msvcrt.get_osfhandle(file.fileno())
        if query(handle, 0, ctypes.byref(info), ctypes.sizeof(info)) and info.change_time > 0:
            return info.change_time * 100
    except (AttributeError, ImportError, OSError, ValueError):
        pass
    return None


def _file_metadata(path: Path) -> tuple[os.stat_result, int | None]:
    """Read identity and change time from the same file, without reading its contents."""
    with path.open("rb") as file:
        stat = os.fstat(file.fileno())
        if os.name == "nt":
            change_time = _windows_change_time_ns(file)
        else:
            change_time = getattr(stat, "st_ctime_ns", None)
        return stat, change_time if isinstance(change_time, int) and change_time > 0 else None


def _metadata_matches(cached: dict, stat: os.stat_result, change_time: int | None) -> bool:
    return (
        change_time is not None
        and cached.get("change_time_ns") == change_time
        and cached.get("identity") == [stat.st_dev, stat.st_ino]
        and cached.get("size") == stat.st_size
        and cached.get("mtime_ns") == stat.st_mtime_ns
    )


def _ensure_snapshot(
    path: Path,
    stat: os.stat_result,
    change_time: int | None,
    *,
    digest: str | None = None,
    progress: Callable[[float], None] | None = None,
    verify_prefix: bool = False,
) -> None:
    """Accept live suffix appends only after verifying the scanned prefix stayed unchanged."""
    try:
        current, current_change = _file_metadata(path)
    except FileNotFoundError as exc:
        raise _SnapshotChanged from exc
    identity = (stat.st_dev, stat.st_ino)
    same_identity = (current.st_dev, current.st_ino) == identity
    unchanged = (
        same_identity
        and current.st_size == stat.st_size
        and current.st_mtime_ns == stat.st_mtime_ns
        and current_change == change_time
    )
    if unchanged and not verify_prefix:
        return
    if same_identity and (current.st_size > stat.st_size or unchanged) and digest is not None:
        if _prefix_digest(path, stat.st_size, progress) == digest:
            after, _after_change = _file_metadata(path)
            if (after.st_dev, after.st_ino) == identity and after.st_size >= current.st_size:
                return
    raise _SnapshotChanged


def _cache_file(path: Path) -> Path:
    key = hashlib.sha1(str(path.resolve()).lower().encode("utf-8")).hexdigest()[:16]
    d = cache_dir() / "index"
    return d / f"{key}.json"


def _entry_from_cache(raw: dict) -> EncounterEntry:
    snapshot = raw["marker_snapshot"]
    if not isinstance(snapshot, list):
        raise ValueError("索引缓存光柱快照错误")
    return EncounterEntry(**(raw | {"marker_snapshot": tuple(WorldMarker(**marker) for marker in snapshot)}))


def _read_cache(path: Path) -> dict:
    cached = json.loads(path.read_text("utf-8"))
    if not isinstance(cached, dict):
        raise ValueError("缓存根对象不是字典")
    if cached.get("version") != INDEX_VERSION:
        return {}
    if not isinstance(cached.get("size"), int) or cached["size"] < 0:
        raise ValueError("索引缓存大小错误")
    if cached.get("change_time_ns") is not None and (
        type(cached["change_time_ns"]) is not int or cached["change_time_ns"] <= 0
    ):
        raise ValueError("索引缓存变更时间错误")
    if not isinstance(cached.get("entries"), list) or not isinstance(cached.get("markers"), list):
        raise ValueError("索引缓存列表错误")
    for raw in cached["entries"]:
        entry = _entry_from_cache(raw)
        if (
            any(
                type(getattr(entry, key)) is not int
                for key in (
                    "seq",
                    "encounter_id",
                    "difficulty",
                    "group_size",
                    "instance_id",
                    "start_offset",
                    "end_offset",
                    "duration_ms",
                    "pull_number",
                )
            )
            or not 0 <= entry.start_offset <= entry.end_offset <= cached["size"]
            or entry.duration_ms < 0
            or not isinstance(entry.closed, bool)
            or not isinstance(entry.kill, bool)
            or any(
                not isinstance(getattr(entry, key), str)
                for key in ("name", "start_ts", "content_digest", "analysis_digest")
            )
            or (entry.map_line is not None and not isinstance(entry.map_line, str))
        ):
            raise ValueError("索引缓存字段错误")
        parse_ts_ms(entry.start_ts)
        for marker in entry.marker_snapshot or ():
            if (
                any(type(getattr(marker, key)) is not int for key in ("index", "start", "end"))
                or not 0 <= marker.start < marker.end <= entry.duration_ms + 1
                or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (marker.x, marker.y))
            ):
                raise ValueError("索引缓存光柱快照字段错误")
        if entry.analysis_digest != _analysis_digest(entry):
            raise ValueError("索引缓存分析上下文错误")
    for raw in cached["markers"]:
        marker = MarkerEvent(**raw)
        if (
            any(type(getattr(marker, key)) is not int for key in ("offset", "abs_ms", "index", "instance_id"))
            or not isinstance(marker.placed, bool)
            or not isinstance(marker.zone_unload, bool)
            or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (marker.x, marker.y))
        ):
            raise ValueError("标记缓存字段错误")
    return cached


def _line_at(mm: mmap.mmap, i: int) -> tuple[int, int, str]:
    ls = mm.rfind(b"\n", 0, i) + 1
    le = mm.find(b"\n", i)
    if le < 0:
        le = len(mm)
    return ls, le, mm[ls:le].decode("utf-8", "replace").rstrip("\r")


def _find_yielding(
    mm: mmap.mmap,
    needle: bytes,
    start: int,
    progress: Callable[[float], None] | None,
    *,
    chunk: int = _SCAN_CHUNK,
    end: int | None = None,
) -> int:
    """Find ``needle`` in ``[start, end)``. Progress receives absolute ``pos / len``."""
    size = len(mm)
    stop = size if end is None else min(size, end)
    if start >= stop:
        return -1
    overlap = max(0, len(needle) - 1)
    pos = start
    while pos < stop:
        check_cancelled()
        chunk_end = min(stop, pos + chunk)
        found = mm.find(needle, pos, min(stop, chunk_end + overlap))
        if found >= 0:
            return found
        pos = chunk_end
        if progress is not None and size:
            progress(pos / size)
    return -1


def _rfind_yielding(
    mm: mmap.mmap,
    needle: bytes,
    start: int,
    end: int,
    *,
    chunk: int = _SCAN_CHUNK,
) -> int:
    overlap = max(0, len(needle) - 1)
    pos = end
    while pos > start:
        check_cancelled()
        chunk_start = max(start, pos - chunk)
        found = mm.rfind(needle, max(start, chunk_start - overlap), pos)
        if found >= 0:
            return found
        if chunk_start == start:
            break
        pos = chunk_start
    return -1


def _scale_progress(
    callback: Callable[[float], None],
    begin: float,
    end: float,
    origin: int,
    size: int,
) -> Callable[[float], None]:
    """Map an absolute ``pos / size`` fraction onto ``[begin, end]`` within ``[origin, size]``."""
    span = max(1, size - origin)

    def inner(frac: float) -> None:
        pos = frac * size
        local = min(1.0, max(0.0, (pos - origin) / span))
        callback(begin + (end - begin) * local)

    return inner


def _scan(
    mm: mmap.mmap,
    start_pos: int,
    entries: list[EncounterEntry],
    progress: Callable[[float], None] | None,
) -> None:
    size = len(mm)
    pos = start_pos
    # Recover the map before an incremental scan once. Later starts only inspect new bytes.
    map_index = _rfind_yielding(mm, b"  MAP_CHANGE,", 0, start_pos) if start_pos else -1
    map_line = _line_at(mm, map_index)[2] if map_index >= 0 else None
    map_pos = start_pos
    open_entry: EncounterEntry | None = None
    while True:
        i = _find_yielding(mm, b"  ENCOUNTER_", pos, progress)
        if i < 0:
            break
        ls, le, line = _line_at(mm, i)
        pos = le
        # A writer may have stopped halfway through a field. Revisit it on refresh.
        if le == size:
            break
        if progress:
            progress(pos / size)
        ts, _, rest = line.partition("  ")
        f = split_fields(rest)
        try:
            parse_ts_ms(ts)
            if f[0] == "ENCOUNTER_START":
                if len(f) < 6:
                    raise ValueError("开始记录字段不足")
                for field in (1, 3, 4, 5):
                    int(f[field])
            elif f[0] == "ENCOUNTER_END":
                if len(f) < 7:
                    raise ValueError("结束记录字段不足")
                int(f[1])
                if int(f[6]) < 0:
                    raise ValueError("战斗时长不能为负数")
        except ValueError as exc:
            logging.getLogger(__name__).warning("已跳过字节 %d 的无效遭遇战记录：%s", ls, exc)
            continue
        if f[0] == "ENCOUNTER_START" and len(f) >= 6:
            if open_entry is not None:
                open_entry.end_offset = ls
            while (mi := _find_yielding(mm, b"  MAP_CHANGE,", map_pos, None, end=ls)) >= 0:
                _map_start, map_pos, map_line = _line_at(mm, mi)
            map_pos = ls
            open_entry = EncounterEntry(
                seq=len(entries) + 1,
                encounter_id=int(f[1]),
                name=f[2],
                difficulty=int(f[3]),
                group_size=int(f[4]),
                instance_id=int(f[5]),
                start_offset=ls,
                end_offset=size,
                start_ts=ts,
                duration_ms=0,
                kill=False,
                closed=False,
                map_line=map_line,
            )
            entries.append(open_entry)
        elif f[0] == "ENCOUNTER_END" and len(f) >= 7 and open_entry is not None:
            if int(f[1]) == open_entry.encounter_id:
                open_entry.end_offset = le
                open_entry.kill = f[5] == "1"
                open_entry.duration_ms = int(f[6])
                open_entry.closed = True
                open_entry = None


def _marker_from_line(line: str, offset: int) -> MarkerEvent | None:
    ts, _, rest = line.partition("  ")
    f = split_fields(rest)
    if not f:
        return None
    try:
        abs_ms = parse_ts_ms(ts)
        if f[0] == "WORLD_MARKER_PLACED" and len(f) >= 5:
            x, y = float(f[3]), float(f[4])
            if not all(math.isfinite(v) for v in (x, y)):
                return None
            return MarkerEvent(offset, abs_ms, True, int(f[2]), x, y, int(f[1]))
        if f[0] == "WORLD_MARKER_REMOVED" and len(f) >= 2:
            # The live log writes just the index. Some references also include an instance id.
            index = int(f[1]) if len(f) == 2 else int(f[2])
            instance = 0 if len(f) == 2 else int(f[1])
            return MarkerEvent(offset, abs_ms, False, index, instance_id=instance)
    except ValueError:
        return None
    return None


def _scan_markers(
    mm: mmap.mmap,
    start_pos: int,
    progress: Callable[[float], None] | None = None,
) -> list[MarkerEvent]:
    pos = start_pos
    out: list[MarkerEvent] = []
    while True:
        i = _find_yielding(mm, b"  WORLD_MARKER_", pos, progress)
        if i < 0:
            break
        ls, le, line = _line_at(mm, i)
        pos = le
        if le == len(mm):
            break
        hit = _marker_from_line(line, ls)
        if hit is not None:
            out.append(hit)
    return out


def _mark_zone_unloads(mm: mmap.mmap, markers: list[MarkerEvent]) -> None:
    """Reclassify old removals too: an appended zone change can complete their context."""
    if not any(not event.placed for event in markers):
        return
    zones: list[int] = []
    pos = 0
    while True:
        i = _find_yielding(mm, b"  ZONE_CHANGE,", pos, None)
        if i < 0:
            break
        _ls, le, line = _line_at(mm, i)
        pos = le
        try:
            zones.append(parse_ts_ms(line.partition("  ")[0]))
        except ValueError:
            continue
    zones.sort()
    for event in markers:
        if event.placed:
            continue
        index = bisect.bisect_left(zones, event.abs_ms - 1000)
        event.zone_unload = index < len(zones) and zones[index] <= event.abs_ms + 1000


def marker_events(path: str | Path) -> list[MarkerEvent]:
    """World-marker lines for a log, from the index cache when it is current."""
    path = Path(path)
    cf = _cache_file(path)
    try:
        if cf.exists():
            cached = _read_cache(cf)
            st, change_time = _file_metadata(path)
            if (
                cached.get("version") == INDEX_VERSION
                and _metadata_matches(cached, st, change_time)
                and "markers" in cached
            ):
                return [MarkerEvent(**m) for m in cached["markers"]]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    if not path.exists() or path.stat().st_size == 0:
        return []
    with open(path, "rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
        markers = _scan_markers(mm, 0)
        _mark_zone_unloads(mm, markers)
        return markers


def _fight_duration(mm: mmap.mmap, entry: EncounterEntry) -> int:
    if entry.closed:
        return entry.duration_ms
    # An unfinished pull has no duration field. Match the parser's timestamp fallback.
    start_ms = parse_ts_ms(entry.start_ts)
    duration = 0
    pos = entry.start_offset
    checked_pos = pos
    lines_since_check = 0
    check_cancelled()
    while pos < entry.end_offset:
        if lines_since_check >= _CANCEL_LINES or pos - checked_pos >= _CANCEL_BYTES:
            check_cancelled()
            checked_pos = pos
            lines_since_check = 0
        end = mm.find(b"\n", pos, entry.end_offset)
        if end < 0:
            end = entry.end_offset
        separator = mm.find(b"  ", pos, end)
        if separator >= 0:
            try:
                duration = max(duration, parse_ts_ms(mm[pos:separator].decode("utf-8")) - start_ms)
            except (UnicodeDecodeError, ValueError):
                pass
        pos = end + 1
        lines_since_check += 1
    check_cancelled()
    return duration


def _analysis_digest(entry: EncounterEntry) -> str:
    """Identity of the pull bytes and the external map/marker context actually used by analysis."""
    payload = [
        entry.content_digest,
        entry.map_line,
        [asdict(marker) for marker in entry.marker_snapshot or ()],
    ]
    return hashlib.blake2b(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"), digest_size=16
    ).hexdigest()


def _number_pulls(entries: list[EncounterEntry]) -> None:
    counter: dict[tuple[int, int], int] = {}
    for e in entries:
        key = (e.encounter_id, e.difficulty)
        counter[key] = counter.get(key, 0) + 1
        e.pull_number = counter[key]


def index_log(path: str | Path, progress: Callable[[float], None] | None = None) -> list[EncounterEntry]:
    path = Path(path)
    last_progress = 0.0

    def publish(fraction: float) -> None:
        nonlocal last_progress
        # A retry restarts the work, but never sends the UI backwards or completes early.
        last_progress = max(last_progress, min(0.99, fraction))
        if progress:
            progress(last_progress)

    for _attempt in range(_INDEX_ATTEMPTS):
        check_cancelled()
        st, change_time = _file_metadata(path)
        try:
            entries = _index_snapshot(path, st, change_time, publish if progress else None)
        except _SnapshotChanged:
            continue
        except (OSError, ValueError):
            # A concurrent replacement/truncation can fail a read before the final check.
            # Keep ordinary I/O/format errors visible when the snapshot itself is unchanged.
            try:
                _ensure_snapshot(path, st, change_time)
            except _SnapshotChanged:
                continue
            raise
        check_cancelled()
        if progress:
            progress(1.0)
        return entries
    check_cancelled()
    raise ValueError("日志在建立索引时发生变化，请稍后刷新")


def _index_snapshot(
    path: Path,
    st: os.stat_result,
    change_time: int | None,
    progress: Callable[[float], None] | None,
) -> list[EncounterEntry]:
    cf = _cache_file(path)
    entries: list[EncounterEntry] = []
    markers: list[MarkerEvent] = []
    resume_from = 0
    try:
        if cf.exists():
            cached = _read_cache(cf)
            if cached.get("version") == INDEX_VERSION and "markers" in cached:
                old = [_entry_from_cache(e) for e in cached["entries"]]
                if _metadata_matches(cached, st, change_time):
                    _ensure_snapshot(
                        path,
                        st,
                        change_time,
                        digest=cached["digest"],
                        progress=(lambda frac: progress(frac * 0.99)) if progress else None,
                    )
                    check_cancelled()
                    return old
                unchanged = (
                    cached.get("identity") == [st.st_dev, st.st_ino]
                    and 0 <= cached["size"] <= st.st_size
                    and cached.get("digest")
                    == _prefix_digest(
                        path, cached["size"], (lambda frac: progress(frac * 0.2)) if progress else None
                    )
                )
                if unchanged and cached["size"] == st.st_size:
                    _ensure_snapshot(
                        path,
                        st,
                        change_time,
                        digest=cached["digest"],
                        progress=(lambda frac: progress(0.2 + frac * 0.79)) if progress else None,
                    )
                    check_cancelled()
                    if (
                        cached.get("mtime_ns") != st.st_mtime_ns
                        or cached.get("change_time_ns") != change_time
                    ):
                        # A touch or attribute change needs only one content verification.
                        cached["mtime_ns"] = st.st_mtime_ns
                        cached["change_time_ns"] = change_time
                        try:
                            write_json(cf, cached)
                        except OSError as exc:
                            cache_warning(exc)
                    return old
                if unchanged and cached["size"] < st.st_size:
                    # Log grew: keep closed encounters, rescan from the first open one.
                    for e in old:
                        if not e.closed:
                            break
                        entries.append(e)
                    resume_from = entries[-1].end_offset if entries else 0
                    markers = [MarkerEvent(**m) for m in cached["markers"] if m["offset"] < resume_from]
    except _SnapshotChanged:
        raise
    except (OSError, ValueError, KeyError, TypeError):
        entries, markers, resume_from = [], [], 0

    # Hash before scanning, so live growth can be distinguished from a prefix rewrite.
    # Stable files retain one full-prefix hash when change time is available. A second
    # read is needed for growth during this attempt or unsupported metadata queries.
    digest = _prefix_digest(path, st.st_size, (lambda frac: progress(0.2 + frac * 0.2)) if progress else None)
    if st.st_size > 0:
        with open(path, "rb") as fh, mmap.mmap(fh.fileno(), st.st_size, access=mmap.ACCESS_READ) as mm:
            if progress is None:
                scan_progress = marker_progress = None
            else:
                scan_progress = _scale_progress(progress, 0.4, 0.6, resume_from, st.st_size)
                marker_progress = _scale_progress(progress, 0.6, 0.8, resume_from, st.st_size)
            _scan(mm, resume_from, entries, scan_progress)
            markers.extend(_scan_markers(mm, resume_from, marker_progress))
            _mark_zone_unloads(mm, markers)
            view = memoryview(mm)
            try:
                for entry in entries:
                    check_cancelled()
                    if not entry.closed:
                        entry.duration_ms = _fight_duration(mm, entry)
                    if not entry.content_digest:
                        entry.content_digest = hashlib.blake2b(
                            view[entry.start_offset : entry.end_offset], digest_size=16
                        ).hexdigest()
                snapshots = clip_world_markers_many(
                    markers,
                    [
                        (parse_ts_ms(entry.start_ts), entry.duration_ms, entry.instance_id)
                        for entry in entries
                    ],
                )
                for entry, snapshot in zip(entries, snapshots, strict=True):
                    entry.marker_snapshot = tuple(snapshot)
                    entry.analysis_digest = _analysis_digest(entry)
            finally:
                view.release()
    for i, e in enumerate(entries, 1):
        e.seq = i
    _number_pulls(entries)
    payload = {
        "version": INDEX_VERSION,
        "size": st.st_size,
        "mtime_ns": st.st_mtime_ns,
        "change_time_ns": change_time,
        "identity": [st.st_dev, st.st_ino],
        "digest": digest,
        "entries": [asdict(e) for e in entries],
        "markers": [asdict(m) for m in markers],
    }
    _ensure_snapshot(
        path,
        st,
        change_time,
        digest=digest,
        progress=(lambda frac: progress(0.8 + frac * 0.2)) if progress else None,
        verify_prefix=change_time is None,
    )
    check_cancelled()
    try:
        write_json(cf, payload)
    except OSError as exc:
        cache_warning(exc)
    return entries
