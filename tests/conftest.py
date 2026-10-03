# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from __future__ import annotations

import pytest
from fixture_log import write_log

from wcl_replay.sources.local_log import index_log, parse_encounter


@pytest.fixture
def log_path(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    return write_log(tmp_path / "WoWCombatLog-test.txt")


@pytest.fixture
def fight_data(log_path):
    (entry,) = index_log(log_path)
    return parse_encounter(log_path, entry)
