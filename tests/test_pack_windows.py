# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""The Windows release zip carries the version and drops Nuitka's .dist folder name."""

from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "pack_windows", Path(__file__).parents[1] / "tools/pack_windows.py"
)
pack = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(pack)


def test_archive_names_include_the_version():
    assert pack.archive_name("0.1.0") == "wcl-replay-0.1.0-windows.zip"
    assert pack.archive_root("0.1.0") == "wcl-replay-0.1.0"


def test_pack_dist_renames_the_root_and_keeps_files(tmp_path: Path):
    dist = tmp_path / "wcl_replay_entry.dist"
    (dist / "PySide6").mkdir(parents=True)
    (dist / "wcl-replay.exe").write_bytes(b"exe")
    (dist / "PySide6" / "qt.dll").write_bytes(b"dll")
    dest = tmp_path / "out" / "wcl-replay-0.1.0-windows.zip"

    packed = pack.pack_dist(dist, dest, "0.1.0")

    assert packed == dest
    with zipfile.ZipFile(dest) as archive:
        assert sorted(archive.namelist()) == [
            "wcl-replay-0.1.0/PySide6/qt.dll",
            "wcl-replay-0.1.0/wcl-replay.exe",
        ]


def test_pack_dist_requires_the_executable(tmp_path: Path):
    dist = tmp_path / "wcl_replay_entry.dist"
    dist.mkdir()
    with pytest.raises(SystemExit, match="missing executable"):
        pack.pack_dist(dist, tmp_path / "wcl-replay-0.1.0-windows.zip", "0.1.0")
