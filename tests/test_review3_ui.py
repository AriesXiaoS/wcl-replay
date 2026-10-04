# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Selection follows visible units, and filtered logs keep following replay time."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication

from wcl_replay.bosses.base import Analysis, Lane, LogEntry, Seg, UnitFlash
from wcl_replay.core.models import Actor, ActorKind, Fight, FightData, Sample
from wcl_replay.core.tracks import Tracks
from wcl_replay.ui.controller import ReplayController, Session
from wcl_replay.ui.map_view import MapView
from wcl_replay.ui.panels import EventLogPanel


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


class _FlashingAnalysis(Analysis):
    def overlays_at(self, t):
        return [UnitFlash(1, "#ff0000")]


def _session(*, npc_y: float = 0.0, flashing: bool = False) -> Session:
    data = FightData(
        Fight(1, 1, "测试首领", 16, 2, 10000, False),
        {
            1: Actor(1, "Player-1", "玩家", ActorKind.PLAYER),
            2: Actor(2, "Creature-1", "首领", ActorKind.NPC, npc_id=1, hostile=True),
        },
        [],
        {
            aid: [Sample(0, 0, y, 0, 100, 100), Sample(10000, 0, y, 0, 100, 100)]
            for aid, y in ((1, 0.0), (2, npc_y))
        },
    )
    tracks = Tracks(data)
    analysis = (_FlashingAnalysis if flashing else Analysis)(data, tracks)
    analysis.arena = (-40.0, 40.0, -40.0, 40.0)
    return Session(data, tracks, analysis)


@pytest.mark.parametrize("npc_y", [0.0, 0.9])
@pytest.mark.parametrize("front", [1, 2])
def test_overlapping_units_select_the_visible_front_even_nearer_the_back_center(app, npc_y, front):
    ctl = ReplayController()
    view = MapView(ctl)
    try:
        view.resize(600, 600)
        view.show()
        ctl.set_session(_session(npc_y=npc_y))
        ctl.set_unit_order(["boss", "player"] if front == 1 else ["player", "boss"])
        app.processEvents()
        view.grab()
        back = 2 if front == 1 else 1
        assert list(view._hits)[-1] == front
        view._pick(view._hits[back][0])
        assert ctl.selected == front
    finally:
        view.close()
        view.deleteLater()
        app.processEvents()


def test_a_front_units_click_margin_does_not_take_a_visible_back_unit(app):
    ctl = ReplayController()
    view = MapView(ctl)
    try:
        # At x=12, the back unit is visible: this lies outside the front unit's radius of 10.
        view._hits = {1: (QPointF(12, 0), 10), 2: (QPointF(0, 0), 10)}
        view._pick(QPointF(12, 0))
        assert ctl.selected == 1
    finally:
        view.deleteLater()
        app.processEvents()


def test_click_margin_still_selects_the_nearest_unit_and_empty_space_clears_selection(app):
    ctl = ReplayController()
    view = MapView(ctl)
    try:
        view._hits = {1: (QPointF(0, 0), 10), 2: (QPointF(24, 0), 10)}
        view._pick(QPointF(11.5, 0))
        assert ctl.selected == 1
        view._pick(QPointF(100, 100))
        assert ctl.selected is None
    finally:
        view.deleteLater()
        app.processEvents()


def test_clearing_a_flashing_session_stops_the_map_timer(app):
    ctl = ReplayController()
    view = MapView(ctl)
    try:
        view.show()
        ctl.set_session(_session(flashing=True))
        app.processEvents()
        view.grab()
        assert view._blink.isActive()
        ctl.clear_session()
        assert not view._blink.isActive()
        assert not view._hits
        app.processEvents()
        assert not view._blink.isActive()
    finally:
        ctl.clear_session()
        view.close()
        view.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("hidden", [{73}, set(range(70, 76))])
def test_event_log_follows_visible_events_when_the_previous_anchor_is_filtered(app, hidden):
    ctl = ReplayController()
    panel = EventLogPanel(ctl)
    session = _session()
    session.analysis.log = [
        LogEntry(i * 100, [Seg(f"事件 {i}")], "hidden" if i in hidden else "visible") for i in range(100)
    ]
    session.analysis.lanes = [Lane("visible", "显示", "#ffffff"), Lane("hidden", "隐藏", "#ffffff")]
    try:
        panel.resize(300, 150)
        panel.show()
        ctl.set_session(session)
        ctl.seek(7500)
        app.processEvents()
        assert panel.verticalScrollBar().value() > 0
        ctl.toggle_layer("hidden")
        app.processEvents()
        assert panel.verticalScrollBar().value() > panel.verticalScrollBar().maximum() / 2
        assert "事件 73" not in panel.toPlainText()
        assert "事件 76" in panel.toPlainText()
    finally:
        panel.close()
        panel.deleteLater()
        app.processEvents()


def test_event_log_handles_no_visible_events_and_future_only_visible_events(app):
    ctl = ReplayController()
    panel = EventLogPanel(ctl)
    session = _session()
    session.analysis.log = [LogEntry(8000, [Seg("未来事件")], "events")]
    session.analysis.lanes = [Lane("events", "事件", "#ffffff")]
    try:
        ctl.set_session(session)
        ctl.seek(7500)
        assert "未来事件" in panel.toPlainText()
        ctl.toggle_layer("events")
        assert "未来事件" not in panel.toPlainText()
        assert panel.verticalScrollBar().value() == 0
        ctl.toggle_layer("events")
        assert "未来事件" in panel.toPlainText()
    finally:
        panel.deleteLater()
        app.processEvents()
