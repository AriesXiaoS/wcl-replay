# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Index of the encounters contained in a (possibly huge) WoWCombatLog file.

Only ENCOUNTER_START / ENCOUNTER_END lines, plus WORLD_MARKER place/remove lines, are located
(with mmap), so a 1.6 GB log is indexed in seconds. The result is cached next to the user's app
data and refreshed incrementally while the log keeps growing during a raid night.
"""

from __future__ import annotations

import hashlib
import json
import mmap
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from ...core.markers import MarkerEvent
from .fields import split_fields
from .timestamps import parse_ts_ms

INDEX_VERSION = 3

# Report progress through a long scan. One mmap.find over a multi-gigabyte log would not
# publish a fraction until it returned.
_SCAN_CHUNK = 8 * 1024 * 1024


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

    @property
    def difficulty_label(self) -> str:
        return DIFFICULTY_LABELS.get(self.difficulty, str(self.difficulty))


DIFFICULTY_LABELS = {14: "N", 15: "H", 16: "M", 17: "LFR", 1: "5N", 2: "5H", 23: "5M", 8: "M+"}


def cache_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(Path.home(), ".cache")
    d = Path(base) / "wcl_replay"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_file(path: Path) -> Path:
    key = hashlib.sha1(str(path.resolve()).lower().encode("utf-8")).hexdigest()[:16]
    d = cache_dir() / "index"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{key}.json"


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
) -> int:
    """Find ``needle`` at or after ``start``. ``progress`` receives absolute ``pos / len``."""
    size = len(mm)
    if start >= size:
        return -1
    overlap = max(0, len(needle) - 1)
    pos = start
    while pos < size:
        end = min(size, pos + chunk)
        found = mm.find(needle, pos, min(size, end + overlap))
        if found >= 0:
            return found
        pos = end
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
    open_entry: EncounterEntry | None = None
    while True:
        i = _find_yielding(mm, b"  ENCOUNTER_", pos, progress)
        if i < 0:
            break
        ls, le, line = _line_at(mm, i)
        pos = le
        if progress:
            progress(pos / size)
        ts, _, rest = line.partition("  ")
        f = split_fields(rest)
        if f[0] == "ENCOUNTER_START" and len(f) >= 6:
            if open_entry is not None:
                open_entry.end_offset = ls
            mi = _rfind_yielding(mm, b"  MAP_CHANGE,", 0, ls)
            map_line = _line_at(mm, mi)[2] if mi >= 0 else None
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
            return MarkerEvent(offset, abs_ms, True, int(f[2]), float(f[3]), float(f[4]), int(f[1]))
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
        hit = _marker_from_line(line, ls)
        if hit is not None:
            out.append(hit)
    return out


def marker_events(path: str | Path) -> list[MarkerEvent]:
    """World-marker lines for a log, from the index cache when it is current."""
    path = Path(path)
    cf = _cache_file(path)
    if cf.exists():
        try:
            cached = json.loads(cf.read_text("utf-8"))
            st = path.stat()
            if (
                cached.get("version") == INDEX_VERSION
                and cached.get("size") == st.st_size
                and cached.get("mtime") == st.st_mtime
                and "markers" in cached
            ):
                return [MarkerEvent(**m) for m in cached["markers"]]
        except (OSError, ValueError, KeyError, TypeError):
            pass
    if not path.exists() or path.stat().st_size == 0:
        return []
    with open(path, "rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
        return _scan_markers(mm, 0)


def _number_pulls(entries: list[EncounterEntry]) -> None:
    counter: dict[tuple[int, int], int] = {}
    for e in entries:
        key = (e.encounter_id, e.difficulty)
        counter[key] = counter.get(key, 0) + 1
        e.pull_number = counter[key]


def index_log(path: str | Path, progress: Callable[[float], None] | None = None) -> list[EncounterEntry]:
    path = Path(path)
    st = path.stat()
    cf = _cache_file(path)
    entries: list[EncounterEntry] = []
    markers: list[MarkerEvent] = []
    resume_from = 0
    if cf.exists():
        try:
            cached = json.loads(cf.read_text("utf-8"))
            if cached.get("version") == INDEX_VERSION and "markers" in cached:
                old = [EncounterEntry(**e) for e in cached["entries"]]
                if cached["size"] == st.st_size and cached["mtime"] == st.st_mtime:
                    return old
                if cached["size"] < st.st_size:
                    # Log grew: keep closed encounters, rescan from the first open one.
                    for e in old:
                        if not e.closed:
                            break
                        entries.append(e)
                    resume_from = entries[-1].end_offset if entries else 0
                    markers = [MarkerEvent(**m) for m in cached["markers"] if m["offset"] < resume_from]
        except (OSError, ValueError, KeyError, TypeError):
            entries, markers, resume_from = [], [], 0

    if st.st_size > 0:
        with open(path, "rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            if progress is None:
                scan_progress = marker_progress = None
            else:
                scan_progress = _scale_progress(progress, 0.0, 0.5, resume_from, st.st_size)
                marker_progress = _scale_progress(progress, 0.5, 1.0, resume_from, st.st_size)
            _scan(mm, resume_from, entries, scan_progress)
            markers.extend(_scan_markers(mm, resume_from, marker_progress))
    for i, e in enumerate(entries, 1):
        e.seq = i
    _number_pulls(entries)
    cf.write_text(
        json.dumps(
            {
                "version": INDEX_VERSION,
                "size": st.st_size,
                "mtime": st.st_mtime,
                "entries": [asdict(e) for e in entries],
                "markers": [asdict(m) for m in markers],
            },
            ensure_ascii=False,
        ),
        "utf-8",
    )
    return entries
