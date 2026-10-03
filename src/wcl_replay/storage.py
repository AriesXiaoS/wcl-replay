# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Shared per-user cache storage. Writes become visible only after they are complete."""

from __future__ import annotations

import gzip
import json
import logging
import os
import tempfile
import time
from pathlib import Path


def cache_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".cache")
    directory = base / "wcl_replay"
    return directory


def cache_warning(exc: OSError) -> None:
    logging.getLogger(__name__).warning("缓存未保存，本次结果仍可使用：%s", exc)


def prune_cache(
    directory: Path, *, keep: Path, max_bytes: int = 512 * 1024 * 1024, max_age: float = 30 * 86400
) -> None:
    """Bound disposable event/index caches only; never remove tokens or follow symlinks."""
    if directory.name not in {"events", "index"}:
        return
    files = []
    for path in directory.iterdir():
        if path.is_symlink() or not path.is_file() or not path.name.endswith((".json", ".json.gz")):
            continue
        stat = path.stat()
        files.append((stat.st_mtime, stat.st_size, path))
    total = sum(size for _mtime, size, _path in files)
    cutoff = time.time() - max_age
    for mtime, size, path in sorted(files):
        if path == keep:
            continue
        if mtime < cutoff or total > max_bytes:
            path.unlink(missing_ok=True)
            total -= size


def write_json(path: Path, value: object, *, compressed: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as file:
        temporary = Path(file.name)
    try:
        opener = gzip.open if compressed else open
        with opener(temporary, "wt", encoding="utf-8") as file:
            json.dump(value, file, ensure_ascii=False)
        os.replace(temporary, path)
        try:
            prune_cache(path.parent, keep=path)
        except OSError as exc:
            logging.getLogger(__name__).warning("缓存清理未完成：%s", exc)
    finally:
        temporary.unlink(missing_ok=True)
