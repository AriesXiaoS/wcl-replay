# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run_tool(tool: str, *args: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "tools" / tool), *map(str, args)],
        cwd=ROOT,
        capture_output=True,
        encoding="utf-8",
        timeout=45,
    )


@pytest.mark.parametrize("seq", [0, -1, 2])
def test_dump_pull_rejects_out_of_range_encounter(log_path, seq):
    result = run_tool("dump_pull.py", log_path, "--seq", seq)
    assert result.returncode == 2
    assert "seq must be between 1 and 1" in result.stderr
    assert "Traceback" not in result.stderr
    assert "parsed " not in result.stdout


def test_dump_pull_accepts_a_valid_encounter(log_path):
    result = run_tool("dump_pull.py", log_path, "--seq", 1)
    assert result.returncode == 0, result.stderr
    assert "parsed " in result.stdout


def test_snapshot_keeps_fractional_and_repeated_times_in_distinct_files(log_path, tmp_path):
    out = tmp_path / "shots"
    result = run_tool(
        "snapshot.py",
        log_path,
        "--seq",
        1,
        "--at",
        16.1,
        16.9,
        16,
        16.1,
        "--out",
        out,
        "--settings",
        tmp_path / "isolated.ini",
    )
    assert result.returncode == 0, result.stderr
    paths = [Path(line) for line in result.stdout.splitlines()]
    assert len(paths) == len(set(paths)) == 4
    assert [path.name for path in paths] == [
        "pull1_0016_100.png",
        "pull1_0016_900.png",
        "pull1_0016.png",
        "pull1_0016_100_2.png",
    ]
    assert set(paths) == set(out.glob("*.png"))
    assert all(path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n") for path in paths)


@pytest.mark.parametrize("time", ["nan", "inf", "-1"])
def test_snapshot_rejects_invalid_time_before_rendering(log_path, tmp_path, time):
    out = tmp_path / "shots"
    result = run_tool("snapshot.py", log_path, "--seq", 1, "--at", time, "--out", out)
    assert result.returncode == 2
    assert "finite, non-negative times" in result.stderr
    assert not out.exists()
