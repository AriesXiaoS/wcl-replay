# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from wcl_replay.sources.local_log import EncounterEntry
from wcl_replay.ui.controller import ReplayController
from wcl_replay.ui.log_panel import LogPanel, PullBoard, pin_token, pins_from_settings, pull_key


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def _entry():
    return EncounterEntry(
        1,
        999,
        "Test",
        16,
        20,
        3004,
        100,
        200,
        "9/28/2026 23:22:00.000",
        1000,
        False,
        True,
        content_digest="same-bytes",
        analysis_digest="original-context",
    )


def test_changed_context_invalidates_board_cache_and_rejects_old_results(app):
    ctl, panel = ReplayController(), LogPanel()
    board = PullBoard(ctl, panel)
    path, original = "synthetic.txt", _entry()
    gen = board.prepare(path)
    board.show_entries(gen, path, [original])
    assert board.activate(0) == "start"
    old_key = board.keys[0]
    token = board.loads.run_id(old_key)
    board.loads.cache[old_key] = object()
    refreshed = replace(original, analysis_digest="changed-context")
    gen = board.prepare(path)
    board.show_entries(gen, path, [refreshed])
    assert board.keys[0] != old_key
    assert old_key not in board.loads.cache
    assert not board.finish(gen, path, old_key, object(), run_id=token)
    assert board.activate(0) == "start"
    assert ctl.session is None


def test_legacy_favorite_migrates_to_current_content_identity(app, tmp_path):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    path, entry = "synthetic.txt", _entry()
    old_key = ("local", path, entry.start_offset, entry.end_offset)
    settings.setValue("pinned_local_pulls", [pin_token(old_key)])
    board = PullBoard(ReplayController(), LogPanel(), settings=settings)
    gen = board.prepare(path)
    board.show_entries(gen, path, [entry])
    assert board.pinned == {pull_key(path, entry)}
    assert board.pinned == pins_from_settings(settings)
    assert json.loads(settings.value("pinned_local_pulls")[0])[-1] == entry.analysis_digest


def test_replacement_does_not_inherit_a_fingerprinted_favorite(app, tmp_path):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    path, entry = "synthetic.txt", _entry()
    settings.setValue("pinned_local_pulls", [pin_token(pull_key(path, entry))])
    board = PullBoard(ReplayController(), LogPanel(), settings=settings)
    gen = board.prepare(path)
    board.show_entries(
        gen, path, [replace(entry, content_digest="new-bytes", analysis_digest="new-analysis")]
    )
    assert not board.pinned
