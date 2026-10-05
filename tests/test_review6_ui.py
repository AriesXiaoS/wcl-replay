# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from __future__ import annotations

import gc
import os
import weakref
from dataclasses import replace
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

import pytest
from PySide6.QtWidgets import QApplication

from wcl_replay.bosses.base import Analysis
from wcl_replay.core.models import Fight, FightData
from wcl_replay.core.tracks import Tracks
from wcl_replay.sources.local_log import EncounterEntry
from wcl_replay.ui.controller import ReplayController, Session
from wcl_replay.ui.log_panel import LogPanel, PullBoard
from wcl_replay.ui.wcl_panel import WclBoard, WclPanel


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def _session(name):
    data = FightData(Fight(1, 999, name, 16, 20, 90_000, False), {}, [], {})
    tracks = Tracks(data)
    return Session(data, tracks, Analysis(data, tracks))


def _entry(number, name):
    return EncounterEntry(
        number,
        999,
        name,
        16,
        20,
        1,
        number * 100,
        (number + 1) * 100,
        "1/1/2026 12:00:00.000",
        90_000,
        False,
        True,
        number,
        content_digest=f"bytes-{number}",
        analysis_digest=f"context-{number}",
    )


@pytest.fixture(params=("local", "wcl"))
def case(request, app):
    source = request.param
    ctl = ReplayController()
    ctl.set_source(source)
    panel = LogPanel() if source == "local" else WclPanel()

    def active():
        return ctl.active_source == source

    board = (
        PullBoard(ctl, panel, active=active, limit=lambda: 100)
        if source == "local"
        else WclBoard(ctl, panel, active=active)
    )
    current = SimpleNamespace(source=source, ctl=ctl, panel=panel, board=board, path="synthetic.txt")
    if source == "local":
        current.entries = [_entry(number, name) for number, name in enumerate(("A", "B", "C"), 1)]
        current.gen = board.prepare(current.path)
        board.show_entries(current.gen, current.path, current.entries)
    yield current
    ctl.clear_session()
    panel.close()
    panel.deleteLater()
    app.processEvents()


def _start(case, name):
    board = case.board
    if case.source == "wcl":
        _, key = board.begin(name)
        return key
    index = next(i for i, entry in enumerate(board.entries) if entry.name == name)
    assert board.activate(index) == "start"
    return board.keys[index]


def _finish(case, key, name):
    result = _session(name)
    token = case.board.loads.run_id(key)
    if case.source == "wcl":
        shown = case.board.finish(key, result, name, run_id=token)
    else:
        shown = case.board.finish(case.gen, case.path, key, result, run_id=token)
    return shown, result


def _pin(case, key):
    case.board.toggle_pin(case.board.keys.index(key))


def _playing(ctl):
    ctl.seek(12_000)
    ctl.select(123)
    ctl.play()


def test_clear_cache_keeps_displayed_favorite_while_another_record_is_loading(case):
    a = _start(case, "A")
    shown, visible = _finish(case, a, "A")
    assert shown
    _pin(case, a)
    _playing(case.ctl)
    b = _start(case, "B")

    case.board.clear_cache()

    assert case.board.loads.cache == {a: visible}
    assert case.board.loads.selected == b and b in case.board.loads.running
    assert case.ctl.session is visible and case.ctl.session_origin == (case.source, a)
    assert case.ctl.playing and case.ctl.t == 12_000 and case.ctl.selected == 123
    assert case.panel.rows[case.board.keys.index(a)].mark.text() == "✓"
    shown, replacement = _finish(case, b, "B")
    assert shown and case.ctl.session is replacement
    assert case.ctl.session_origin == (case.source, b)
    assert a in case.board.loads.cache


def test_clear_cache_drops_visible_unpinned_record_even_when_a_different_favorite_survives(case):
    b = _start(case, "B")
    shown, favorite = _finish(case, b, "B")
    assert shown
    _pin(case, b)
    a = _start(case, "A")
    assert _finish(case, a, "A")[0]
    _playing(case.ctl)
    c = _start(case, "C")

    case.board.clear_cache()

    assert case.board.loads.cache == {b: favorite}
    assert case.board.loads.selected == c and c in case.board.loads.running
    assert case.ctl.session is None and case.ctl.session_origin is None
    assert not case.ctl.playing and case.ctl.t == 0
    assert case.panel.rows[case.board.keys.index(a)].mark.text() == ""


def test_legacy_unowned_cached_replay_can_be_kept_reselected_and_released(case):
    a = _start(case, "A")
    shown, favorite = _finish(case, a, "A")
    assert shown
    _pin(case, a)
    case.ctl.set_session(favorite)
    assert case.ctl.session_origin is None
    _playing(case.ctl)
    _start(case, "B")
    case.board.clear_cache()
    assert case.ctl.session is favorite and case.ctl.playing and case.ctl.t == 12_000

    assert case.board.activate(case.board.keys.index(a)) == "show"
    assert case.ctl.session_origin == (case.source, a)
    assert case.ctl.playing and case.ctl.t == 12_000 and case.ctl.selected == 123
    _pin(case, a)
    case.board.clear_cache()
    assert case.ctl.session is None and case.ctl.session_origin is None


def test_hidden_board_cleanup_preserves_the_other_source_replay(case):
    a = _start(case, "A")
    assert _finish(case, a, "A")[0]
    other = "wcl" if case.source == "local" else "local"
    case.ctl.set_source(other)
    foreign = _session("其他来源")
    origin = (other, (other, "foreign"))
    case.ctl.set_session(foreign, origin=origin)
    _playing(case.ctl)
    case.board.clear_cache()
    if case.source == "wcl":
        case.board.remove(case.board.keys.index(a))
    else:
        case.board.release(case.board.keys.index(a))
    assert case.ctl.session is foreign and case.ctl.session_origin == origin
    assert case.ctl.playing and case.ctl.t == 12_000


def test_controller_origin_is_ready_for_session_callbacks_and_clears_with_direct_install_or_source(app):
    ctl = ReplayController()
    seen = []
    ctl.sessionChanged.connect(lambda: seen.append((ctl.session, ctl.session_origin)))
    result = _session("A")
    origin = ("local", ("local", "synthetic", 1))
    ctl.set_session(result, origin=origin)
    assert seen[-1] == (result, origin)
    ctl.set_session(result)
    assert seen[-1] == (result, None)
    ctl.set_session(result, origin=origin)
    ctl.clear_session()
    assert seen[-1] == (None, None)
    ctl.set_session(result, origin=origin)
    ctl.set_source("wcl")
    assert seen[-1] == (None, None)


def _refreshed_in_background(app, *, limit=0):
    ctl, panel = ReplayController(), WclPanel()
    ctl.set_source("wcl")
    board = WclBoard(ctl, panel, limit=lambda: limit)
    _, a = board.begin("A")
    old = _session("旧 A")
    assert board.finish(a, old, "A", run_id=board.loads.run_id(a))
    _playing(ctl)
    assert board.reload(board.keys.index(a)) == a
    token = board.loads.run_id(a)
    _, b = board.begin("B")
    fresh = _session("新 A")
    assert not board.finish(a, fresh, "A 更新", run_id=token)
    assert ctl.session is old and board.loads.cache[a] is fresh
    return ctl, panel, board, a, b, old, fresh


def test_deleting_refreshed_wcl_record_releases_the_older_displayed_version(app):
    ctl, panel, board, a, b, old, fresh = _refreshed_in_background(app)
    previous_analysis = weakref.ref(old.analysis)
    del old
    board.remove(board.keys.index(a))
    gc.collect()
    assert previous_analysis() is None
    assert ctl.session is None and ctl.session_origin is None and not ctl.playing
    assert a not in board.loads.cache and b in board.loads.running
    board.fail(b, "离线", run_id=board.loads.run_id(b))
    board.remove(board.keys.index(b))
    assert not panel.rows and not board.loads.cache and ctl.session is None
    panel.deleteLater()


def test_wcl_cap_protects_displayed_record_after_its_cached_version_is_replaced(app):
    ctl, panel, board, a, b, old, fresh = _refreshed_in_background(app, limit=1)
    _, c = board.begin("C")
    assert board.activate(board.keys.index(b)) == "wait"
    assert not board.finish(c, _session("C"), "C", run_id=board.loads.run_id(c))
    assert board.loads.cache == {a: fresh}
    assert ctl.session is old and ctl.session_origin == ("wcl", a)
    assert ctl.playing and ctl.t == 12_000
    assert panel.rows[board.keys.index(a)].mark.text() == "✓"
    assert panel.rows[board.keys.index(c)].mark.text() == ""
    newest = _session("B")
    assert board.finish(b, newest, "B", run_id=board.loads.run_id(b))
    assert board.loads.cache == {b: newest}
    assert ctl.session is newest and ctl.session_origin == ("wcl", b)
    ctl.clear_session()
    panel.deleteLater()


@pytest.mark.parametrize("pinned", (False, True))
def test_clear_cache_uses_displayed_wcl_record_even_when_its_cache_has_a_newer_version(app, pinned):
    ctl, panel, board, a, b, old, fresh = _refreshed_in_background(app)
    if pinned:
        board.toggle_pin(board.keys.index(a))
    board.clear_cache()
    if pinned:
        assert board.loads.cache == {a: fresh}
        assert ctl.session is old and ctl.session_origin == ("wcl", a)
        assert ctl.playing and ctl.t == 12_000
    else:
        assert not board.loads.cache
        assert ctl.session is None and ctl.session_origin is None and not ctl.playing
    assert board.loads.selected == b and b in board.loads.running
    ctl.clear_session()
    panel.deleteLater()


def test_source_switch_restores_latest_cached_wcl_version_with_its_record_origin(app):
    ctl, panel, board, a, _b, old, fresh = _refreshed_in_background(app)
    board.loads.selected = a
    ctl.set_source("local")
    assert ctl.session is None and ctl.session_origin is None
    ctl.set_source("wcl")
    board.show_selected()
    assert ctl.session is fresh and ctl.session is not old
    assert ctl.session_origin == ("wcl", a)
    ctl.clear_session()
    panel.deleteLater()


@pytest.mark.parametrize("pinned", (False, True))
def test_clear_cache_during_wcl_refresh_keeps_its_job_and_does_not_reactivate_an_old_replay(app, pinned):
    ctl, panel = ReplayController(), WclPanel()
    board = WclBoard(ctl, panel)
    _, a = board.begin("A")
    old = _session("旧 A")
    assert board.finish(a, old, "A", run_id=board.loads.run_id(a))
    if pinned:
        board.toggle_pin(0)
    _playing(ctl)
    board.reload(0)
    token = board.loads.run_id(a)
    _, b = board.begin("B")
    board.clear_cache()
    assert board.loads.is_current(a, token) and board.loads.selected == b
    if pinned:
        assert ctl.session is old and ctl.session_origin == ("wcl", a) and ctl.playing
    else:
        assert ctl.session is None and ctl.session_origin is None and not ctl.playing
    fresh = _session("新 A")
    assert not board.finish(a, fresh, "A 更新", run_id=token)
    assert board.loads.cache[a] is fresh
    assert ctl.session is (old if pinned else None)
    board.remove(board.keys.index(a))
    assert ctl.session is None and ctl.session_origin is None
    panel.deleteLater()


@pytest.mark.parametrize("changed_record", (None, "A", "B"))
def test_local_index_refresh_uses_displayed_record_when_another_record_is_pending(app, changed_record):
    ctl, panel = ReplayController(), LogPanel()
    board = PullBoard(ctl, panel)
    path, a_entry, b_entry = "synthetic.txt", _entry(1, "A"), _entry(2, "B")
    gen = board.prepare(path)
    board.show_entries(gen, path, [a_entry, b_entry])
    assert board.activate(1) == "start"
    a = board.keys[1]
    visible = _session("A")
    assert board.finish(gen, path, a, visible, run_id=board.loads.run_id(a))
    _playing(ctl)
    assert board.activate(0) == "start"
    b = board.keys[0]
    gen = board.prepare(path)
    assert ctl.session is visible and ctl.playing and ctl.t == 12_000
    refreshed = replace(a_entry, analysis_digest="changed-context") if changed_record == "A" else a_entry
    board.show_entries(gen, path, [refreshed] if changed_record == "B" else [refreshed, b_entry])
    if changed_record == "B":
        assert board.loads.selected is None and b not in board.loads.running
    else:
        assert board.loads.selected == b and b in board.loads.running
    if changed_record != "A":
        assert ctl.session is visible and ctl.session_origin == ("local", a)
        assert ctl.playing and ctl.t == 12_000
    else:
        assert a not in board.loads.cache
        assert ctl.session is None and ctl.session_origin is None and not ctl.playing
    ctl.clear_session()
    panel.deleteLater()
