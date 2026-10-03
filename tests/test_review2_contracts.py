from __future__ import annotations

import math
import os
import sys
from itertools import permutations
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, QPropertyAnimation
from PySide6.QtWidgets import QApplication, QWidget

from wcl_replay import pipeline
from wcl_replay.bosses import registry
from wcl_replay.bosses.base import Analysis, AnalysisParameter, BossModule, Lane, LogEntry, Seg, UnitStyle
from wcl_replay.bosses.coiled_altar import constants as C
from wcl_replay.bosses.coiled_altar.module import CoiledAltarAnalysis
from wcl_replay.bosses.coiled_altar.p1 import Globule, P1Model, SeverRec
from wcl_replay.bosses.coiled_altar.p2 import P2Model
from wcl_replay.core.models import Actor, ActorKind, Event, Fight, FightData, Sample
from wcl_replay.core.tracks import Track, Tracks
from wcl_replay.ui.controller import ReplayController, Session
from wcl_replay.ui.map_view import MapView, _dots_along, frame_bounds
from wcl_replay.ui.panels import EventLogPanel
from wcl_replay.ui.raid_frames import AuraFilter
from wcl_replay.ui.stack_order import present_stack_groups, stack_group
from wcl_replay.ui.stack_panel import _CardColumn
from wcl_replay.ui.timeline import HEADER_H, ROW_H, TimelineWidget


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def data():
    return FightData(
        Fight(1, 999, "测试", 16, 20, 10000, False),
        {0: Actor(0, "p", "玩家", ActorKind.PLAYER)},
        [],
        {0: [Sample(0, 0, 0, 0, 100, 100)]},
    )


def ghosts():
    d = data()
    d.fight.encounter_id = C.ENCOUNTER_ID
    d.actors[1] = Actor(1, "b", "首领", ActorKind.NPC, npc_id=C.NPC_MALACRASS, hostile=True)
    d.samples[1] = [Sample(6000, 1158.6, 0, 0, 100, 100)]
    for aid, start, end in ((2, 6100, 7100), (3, 6200, 8100)):
        d.actors[aid] = Actor(aid, f"g{aid}", "魂", ActorKind.NPC, npc_id=C.NPC_GHOST, hostile=True)
        d.samples[aid] = [Sample(6000, 1158.6, 10, 0, 100, 100)]
        d.events.extend(
            [
                Event(start, "SPELL_AURA_APPLIED", src=aid, dst=0, spell_id=C.FIXATE),
                Event(end, "SPELL_AURA_REMOVED", src=aid, dst=0, spell_id=C.FIXATE),
            ]
        )
    d.events.sort(key=lambda e: e.t)
    return d


def test_independent_fixate_frame_icon_matches_mechanic():
    d = ghosts()
    analysis = CoiledAltarAnalysis(d, Tracks(d))
    assert any(g.target_at(7500) == 0 for g in analysis.p2.ghosts)
    assert ("fixate", "ability_fixated_state_purple") in analysis.active_frame_auras(0, 7500)
    assert analysis.active_frame_auras(0, 8500) == ()


def test_pipeline_defaults_and_explicit_parameters_match_model(app):
    d = ghosts()
    tracks, analysis = pipeline.analyze(d)
    assert analysis.parameter_values["ghost_motion"] == analysis.p2.motion_mode == "accel"
    position = tracks.position(2, 6600)
    ctl = ReplayController()
    ctl.set_session(Session(d, tracks, analysis))
    assert tracks.position(2, 6600) == position
    _, configured = pipeline.analyze(ghosts(), parameters={"ghost_motion": "constant", "ghost_speed": 4})
    assert configured.p2.motion_mode == "constant" and configured.p2.speed == 4
    fresh = ReplayController()
    fresh.set_session(Session(configured.data, configured.tracks, configured))
    assert configured.p2.motion_mode == "constant" and configured.p2.speed == 4


def test_player_prediction_uses_its_own_indices_and_observed_health():
    d = data()
    d.samples[0].append(Sample(1000, 1, 1, 0, 100, 100))
    d.events = [Event(1000, "UNIT_DIED", dst=0)]
    tracks = Tracks(d)
    tracks.set_derived(0, Track([Sample(0, 50, 20, 0, 0, 1)]))
    assert tracks.pose(0, 1500).x == 50
    assert tracks.pose(0, 1500).hp == 100
    assert tracks.is_dead(0, 1500)


def test_player_prediction_does_not_interpolate_across_resurrection():
    d = data()
    d.samples[0] += [Sample(1500, 0, 0, 0, 1, 100), Sample(3000, 100, 0, 0, 80, 100)]
    d.events = [Event(1000, "UNIT_DIED", dst=0)]
    tracks = Tracks(d)
    tracks.set_derived(
        0,
        Track(
            [Sample(t, x, 0, 0, 0, 1) for t, x in ((0, 0), (800, 8), (2000, 20), (3000, 100), (4000, 110))]
        ),
    )
    assert tracks.pose(0, 2000).x == 8
    assert tracks.is_dead(0, 2000)
    assert tracks.pose(0, 3500).x == 105
    assert tracks.pose(0, 3500).hp == 80
    assert not tracks.is_dead(0, 3500)


def test_register_is_atomic(monkeypatch):
    monkeypatch.setattr(registry, "_REGISTRY", {})

    class First(BossModule):
        encounter_ids = (90001,)

    class Second(BossModule):
        encounter_ids = (90002, 90001)

    registry.register(First)
    with pytest.raises(ValueError):
        registry.register(Second)
    assert registry._REGISTRY == {90001: First}


def test_failed_discovery_rolls_back_and_generic_replay_reports_warning(monkeypatch):
    monkeypatch.setattr(registry, "_REGISTRY", {})
    monkeypatch.setattr(registry, "_ERRORS", {})
    monkeypatch.setattr(registry, "_discovered", False)
    monkeypatch.setattr(
        registry.pkgutil, "iter_modules", lambda path: [SimpleNamespace(name="broken", ispkg=True)]
    )

    def broken_import(name):
        class Broken(BossModule):
            encounter_ids = (90003,)

        registry.register(Broken)
        sys.modules[name + ".partial"] = SimpleNamespace()
        raise ImportError("synthetic import failure")

    monkeypatch.setattr(registry.importlib, "import_module", broken_import)
    assert type(registry.module_for(999)) is BossModule
    assert not registry._REGISTRY
    assert "wcl_replay.bosses.broken.partial" not in sys.modules
    _, analysis = pipeline.analyze(data())
    assert any("加载失败" in s.text for entry in analysis.log for s in entry.segments)


class ChangingAnalysis(Analysis):
    parameters = (AnalysisParameter("count", "数量", 1, minimum=1, maximum=4),)

    def apply_parameters(self, values):
        super().apply_parameters(values)
        count = int(self.parameter_values["count"])
        self.log = [LogEntry(count * 1000, [Seg(f"事件{count}")])]
        self.lanes = [Lane(f"lane{i}", f"图层{i}", "#ffffff", [], default_on=i == 0) for i in range(count)]
        self.arena = (-count * 10, count * 10, -20, 20)


def test_parameter_change_refreshes_all_result_consumers_preserving_user_state(app):
    d = data()
    tracks = Tracks(d)
    analysis = ChangingAnalysis(d, tracks)
    ctl = ReplayController()
    panel, timeline, view = EventLogPanel(ctl), TimelineWidget(ctl), MapView(ctl)
    ctl.set_session(Session(d, tracks, analysis))
    ctl.seek(2500)
    ctl.select(0)
    ctl.toggle_layer("lane0")
    view._zoom = 2
    ctl.set_parameter("count", 4)
    assert panel._times == [4000] and "事件4" in panel.toPlainText() and "事件1" not in panel.toPlainText()
    assert timeline.height() == HEADER_H + 4 * ROW_H + 8
    assert not ctl.layer_on("lane0") and not ctl.layer_on("lane3")
    assert ctl.t == 2500 and ctl.selected == 0 and view._zoom == 2
    assert view._bounds == (-40, 40, -20, 20)
    ctl.set_parameter("count", 1)
    assert set(ctl.layers) == {"lane0"}


def test_camera_marker_filter_is_order_independent():
    results = {
        frame_bounds((0, 1, 0, 1), list(markers)) for markers in permutations([(70, 0), (140, 0), (210, 0)])
    }
    assert len(results) == 1
    assert next(iter(results))[1] == 76


@pytest.mark.parametrize("end", [1e8, 1e100])
def test_grid_draw_count_is_bounded_for_extreme_coordinates(app, end):
    view = MapView(ReplayController())
    view._bounds = frame_bounds((0, end, 0, 1), [])

    class Painter:
        lines = 0

        def setPen(self, *args):
            pass

        def setBrush(self, *args):
            pass

        def drawRoundedRect(self, *args):
            pass

        def drawLine(self, *args):
            self.lines += 1

    painter = Painter()
    view._draw_arena(painter)
    assert 0 < painter.lines <= 256


@pytest.mark.parametrize("end", [1e8, 1e308, float("inf"), float("nan")])
def test_path_dot_count_and_values_are_bounded(end):
    dots = _dots_along([QPointF(0, 0), QPointF(end, 0)], 3.4)
    assert len(dots) <= 2048
    assert all(math.isfinite(p.x()) and math.isfinite(p.y()) for p in dots)


def test_finished_and_replaced_slide_animations_are_deleted(app):
    column, card = _CardColumn(), QWidget()
    for i in range(12):
        column.slide(card, QPoint(i + 1, 0), 10)
        animation = next(iter(column._anims.values()))
        animation.setCurrentTime(10)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not column._anims
    assert not column.findChildren(QPropertyAnimation)
    column.slide(card, QPoint(100, 0), 100)
    column.slide(card, QPoint(200, 0), 100)
    column.stop_all()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not column.findChildren(QPropertyAnimation)


def test_boss_owned_unit_groups_need_no_ui_change():
    d = data()
    d.actors[2] = Actor(2, "npc", "幻象", ActorKind.NPC, npc_id=123, hostile=True)
    analysis = Analysis(d, Tracks(d))
    analysis.unit_styles[2] = UnitStyle("#ffffff", group="illusion", group_label="幻象")
    assert stack_group(analysis, 2) == "illusion"
    assert ("illusion", "幻象") in present_stack_groups(analysis)


def test_unknown_fixate_end_is_not_counted_as_reaching_player():
    d = ghosts()
    model = P2Model(d, Tracks(d), [e.t for e in d.events], 6000)
    assert {e.reason for e in model.fixate_ends} == {"unknown"}
    assert any("原因未知" in s.text for entry in model.log() for s in entry.segments)


def test_orb_fallback_retains_inference_count():
    model = object.__new__(P1Model)
    record = SeverRec(0, 0, 1000, 0, 0, 0, 0, stacks=1)
    orb = Globule(1, "green", -5, 0, 0)
    assert model._pick_popped(record, [orb]) == [orb]
    assert record.inferred_pops == 1


def test_bomb_aura_end_is_explicitly_marked_as_inference():
    d = data()
    d.events = [
        Event(1000, "SPELL_AURA_APPLIED", dst=0, spell_id=C.GLOOMBOMB),
        Event(5000, "SPELL_AURA_REMOVED", dst=0, spell_id=C.GLOOMBOMB),
    ]
    model = P2Model(d, Tracks(d), [e.t for e in d.events], 0)
    assert model.bombs[0].evidence == "aura_end"
    assert any("推定" in s.text for entry in model.log() for s in entry.segments)


def test_parameter_refresh_preserves_aura_choices_without_saved_settings(app):
    d = ghosts()
    tracks = Tracks(d)
    ctl = ReplayController()
    filter_ = AuraFilter(ctl)
    ctl.set_session(Session(d, tracks, CoiledAltarAnalysis(d, tracks)))
    box = next(box for box in filter_._boxes if box.property("auraKey") == "fixate")
    box.setChecked(False)
    ctl.set_parameter("ghost_speed", 4)
    assert "fixate" not in filter_.selected_keys()
