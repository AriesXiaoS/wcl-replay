# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Boss-defined controls work without adding boss-specific logic to the public UI."""

from __future__ import annotations

import math
import os
from collections.abc import Mapping

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

import pytest
from PySide6.QtCore import QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QComboBox

from wcl_replay.bosses.base import (
    Analysis,
    AnalysisParameter,
    BossModule,
    Circle,
    Cone,
    ParameterValue,
)
from wcl_replay.bosses.coiled_altar import constants as C
from wcl_replay.bosses.coiled_altar.module import CoiledAltarAnalysis
from wcl_replay.core.models import Actor, ActorKind, Event, Fight, FightData, Sample
from wcl_replay.core.tracks import Tracks
from wcl_replay.ui.controller import ReplayController, Session
from wcl_replay.ui.main_window import MainWindow
from wcl_replay.ui.playback import AnalysisParameterBar, GhostSpeedBar


class _RadiusAnalysis(Analysis):
    parameters = (
        AnalysisParameter(
            "prediction_mode",
            "预测形状",
            "circle",
            choices=(("circle", "圆形"), ("cone", "扇形")),
            tooltip="选择预测范围形状。",
        ),
        AnalysisParameter(
            "radius",
            "危险半径",
            8.0,
            minimum=1.0,
            maximum=40.0,
            suffix="码",
            tooltip="预测危险半径。",
            visible_when=(("prediction_mode", "circle"),),
        ),
        AnalysisParameter(
            "offset",
            "中心偏移",
            0.0,
            minimum=-10.0,
            maximum=10.0,
            suffix="码",
            decimals=2,
            step=0.25,
            visible_when=(("prediction_mode", "cone"),),
        ),
    )

    def __init__(self, data, tracks):
        super().__init__(data, tracks)
        self.applications = 0

    def apply_parameters(self, values: Mapping[str, ParameterValue]) -> None:
        super().apply_parameters(values)
        self.applications += 1

    def overlays_at(self, _t):
        radius = float(self.parameter_values["radius"])
        if self.parameter_values["prediction_mode"] == "circle":
            return [Circle(0.0, 0.0, radius, "#ff0000")]
        return [Cone(float(self.parameter_values["offset"]), 0.0, 0.0, 45.0, radius, "#ff0000")]


class _RadiusBoss(BossModule):
    def analyze(self, data, tracks):
        return _RadiusAnalysis(data, tracks)


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(tmp_path):
    return QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)


def _data(encounter_id: int = 9001) -> FightData:
    return FightData(Fight(1, encounter_id, "测试首领", 16, 20, 90_000, False), {}, [], {})


def _radius_session(encounter_id: int = 9001) -> Session:
    data = _data(encounter_id)
    tracks = Tracks(data)
    return Session(data, tracks, _RadiusBoss().analyze(data, tracks))


def _ghost_session() -> Session:
    actors = {
        0: Actor(0, "Creature-Zul", "祖尔加", ActorKind.NPC, npc_id=C.NPC_ZULJAN, hostile=True),
        1: Actor(1, "Player-1", "玩家", ActorKind.PLAYER),
        2: Actor(2, "Creature-Ghost", "恐惧具象", ActorKind.NPC, npc_id=C.NPC_GHOST, hostile=True),
        3: Actor(3, "Creature-Mal", "玛拉卡斯", ActorKind.NPC, npc_id=C.NPC_MALACRASS, hostile=True),
    }
    events = [
        Event(0, "UNIT_DIED", dst=0),
        Event(0, "SPELL_SUMMON", src=3, dst=2),
        Event(0, "SPELL_AURA_APPLIED", src=2, dst=1, spell_id=C.FIXATE),
        Event(4000, "SPELL_AURA_REMOVED", src=2, dst=1, spell_id=C.FIXATE),
    ]
    samples = {
        0: [Sample(0, 0.0, 0.0, 0.0, 0, 100)],
        1: [Sample(0, 0.0, 0.0, 0.0, 100, 100), Sample(4000, 0.0, 0.0, 0.0, 100, 100)],
        2: [Sample(0, 0.0, 30.0, 0.0, 1, 1)],
        3: [Sample(0, 10.0, 0.0, 0.0, 100, 100)],
    }
    data = FightData(Fight(1, C.ENCOUNTER_ID, "盘卷祭坛", 16, 20, 4000, False), actors, events, samples)
    tracks = Tracks(data)
    return Session(data, tracks, CoiledAltarAnalysis(data, tracks))


def _wheel(edit, notches: int) -> None:
    edit.wheelEvent(
        QWheelEvent(
            QPointF(0, 0),
            QPointF(0, 0),
            QPoint(0, 0),
            QPoint(0, 120 * notches),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
    )


def test_new_boss_parameters_drive_the_main_window_and_real_overlays(app, settings):
    win = MainWindow(settings=settings)
    session = _radius_session()
    try:
        win.ctl.set_session(session)
        bar = win.parameter_bar
        assert isinstance(bar, AnalysisParameterBar)
        assert not bar.isHidden()
        assert set(bar.controls) == {"prediction_mode", "radius", "offset"}
        assert not bar.boxes["radius"].isHidden()
        assert bar.boxes["offset"].isHidden()
        assert bar.controls["offset"].edit.text() == "0.00"
        assert bar.note.isHidden()
        win.ctl.seek(1234)
        bar.controls["radius"].edit.setText("12.4")
        assert session.analysis.overlays_at(1234)[0].r == 12.4
        assert win.ctl.t == 1234
        mode = bar.controls["prediction_mode"]
        assert isinstance(mode, QComboBox)
        mode.setCurrentIndex(mode.findData("cone"))
        assert bar.boxes["radius"].isHidden()
        assert not bar.boxes["offset"].isHidden()
        _wheel(bar.controls["offset"].edit, -1)
        assert bar.controls["offset"].edit.text() == "-0.25"
        assert session.analysis.overlays_at(1234)[0].x == -0.25
        assert settings.value("analysis_parameters/9001/radius", type=float) == 12.4
        assert settings.value("analysis_parameters/9001/offset", type=float) == -0.25
    finally:
        win.tasks.shutdown()
        win.close()
        win.deleteLater()
        app.processEvents()


def test_no_parameter_analysis_hides_the_bar_and_removes_previous_controls(app):
    ctl = ReplayController()
    bar = AnalysisParameterBar(ctl)
    assert bar.isHidden()
    ctl.set_session(_radius_session())
    assert not bar.isHidden()
    data = _data()
    tracks = Tracks(data)
    ctl.set_session(Session(data, tracks, Analysis(data, tracks)))
    assert bar.isHidden()
    assert not bar.controls
    assert not ctl.parameter_values
    ctl.clear_session()
    assert bar.isHidden()
    bar.deleteLater()
    app.processEvents()


def test_parameters_are_restored_and_scoped_by_encounter(app, settings):
    ctl = ReplayController(settings=settings)
    first = _radius_session(9001)
    ctl.set_session(first)
    ctl.set_parameters({"radius": 15.0, "prediction_mode": "cone", "offset": -1.25})
    assert first.analysis.applications == 2  # one initial application, one combined update
    second = _radius_session(9002)
    ctl.set_session(second)
    assert ctl.parameter_values == {"prediction_mode": "circle", "radius": 8.0, "offset": 0.0}
    ctl.set_session(first)
    assert ctl.parameter_values["radius"] == 15.0
    restored = ReplayController(settings=settings)
    restored.set_session(_radius_session(9001))
    assert restored.parameter_values == {"prediction_mode": "cone", "radius": 15.0, "offset": -1.25}


def test_descriptor_defaults_are_authoritative_and_explicit_legacy_presets_still_work(app):
    class DifferentGhostDefaults(Analysis):
        parameters = (AnalysisParameter("ghost_speed", "移动速度", 6.0),)

    data = _data()
    tracks = Tracks(data)
    ctl = ReplayController()
    ctl.set_session(Session(data, tracks, DifferentGhostDefaults(data, tracks)))
    assert ctl.parameter_values["ghost_speed"] == 6.0
    preset = ReplayController()
    preset.set_ghost_speed(4.5)
    preset.set_session(Session(data, tracks, DifferentGhostDefaults(data, tracks)))
    assert preset.parameter_values["ghost_speed"] == 4.5


def test_invalid_saved_values_and_edits_use_descriptor_defaults_and_bounds(app, settings):
    settings.setValue("analysis_parameters/9001/prediction_mode", "unsupported")
    settings.setValue("analysis_parameters/9001/radius", "nan")
    settings.setValue("analysis_parameters/9001/offset", "invalid")
    ctl = ReplayController(settings=settings)
    bar = AnalysisParameterBar(ctl)
    ctl.set_session(_radius_session())
    assert ctl.parameter_values == {"prediction_mode": "circle", "radius": 8.0, "offset": 0.0}
    bar.controls["radius"].edit.setText("100")
    assert ctl.parameter_values["radius"] == 40.0
    bar.controls["radius"].edit.setText("NaN")
    bar.controls["radius"].edit.editingFinished.emit()
    assert ctl.parameter_values["radius"] == 40.0
    assert bar.controls["radius"].edit.text() == "40.0"
    ctl.set_parameter("radius", -100.0)
    assert ctl.parameter_values["radius"] == 1.0
    assert math.isfinite(ctl.parameter_values["radius"])
    bar.deleteLater()
    app.processEvents()


def test_coiled_defaults_mode_visibility_and_legacy_setting_keys_are_preserved(app, settings):
    ctl = ReplayController(settings=settings)
    bar = AnalysisParameterBar(ctl)
    session = _ghost_session()
    ctl.set_session(session)
    assert len(session.analysis.parameters) == 7
    assert ctl.parameter_values == {
        "ghost_motion": "accel",
        "ghost_speed": 3.0,
        "ghost_pause_s": 0.5,
        "ghost_start_speed": 1.5,
        "ghost_end_speed": 3.0,
        "ghost_accel_s": 0.5,
        "ghost_face_deg": 22.5,
    }
    assert "仅供参考" in bar.note.text()
    assert bar.boxes["ghost_speed"].isHidden()
    assert not bar.boxes["ghost_start_speed"].isHidden()
    mode = bar.controls["ghost_motion"]
    mode.setCurrentIndex(mode.findData("constant"))
    assert not bar.boxes["ghost_speed"].isHidden()
    assert not bar.boxes["ghost_pause_s"].isHidden()
    assert bar.boxes["ghost_start_speed"].isHidden()
    bar.controls["ghost_speed"].edit.setText("4.1")
    bar.controls["ghost_face_deg"].edit.setText("±15.4°")
    assert ctl.ghost_speed == 4.1
    assert ctl.ghost_face_deg == 15.4
    _wheel(bar.controls["ghost_face_deg"].edit, 1)
    assert ctl.ghost_face_deg == 16.4
    assert settings.value("ghost_motion") == "constant"
    assert settings.value("ghost_speed", type=float) == 4.1
    assert settings.value("ghost_face_deg", type=float) == 16.4
    assert not any(key.startswith("analysis_parameters/") for key in settings.allKeys())
    bar.deleteLater()
    app.processEvents()


def test_legacy_coiled_values_restore_before_the_first_model_application(app, settings):
    settings.setValue("ghost_motion", "constant")
    settings.setValue("ghost_speed", "4.5")
    settings.setValue("ghost_pause_s", "0.8")
    settings.setValue("ghost_start_speed", "2.0")
    settings.setValue("ghost_end_speed", "3.5")
    settings.setValue("ghost_accel_s", "1.25")
    settings.setValue("ghost_face_deg", "15.4")
    ctl = ReplayController(settings=settings)
    session = _ghost_session()
    ctl.set_session(session)
    p2 = session.analysis.p2
    assert p2.motion_mode == "constant"
    assert (p2.speed, p2.pause_s, p2.start_speed, p2.end_speed, p2.accel_s, p2.face_deg) == (
        4.5,
        0.8,
        2.0,
        3.5,
        1.2,
        15.4,
    )
    assert ctl.ghost_speed == 4.5


def test_legacy_ghost_bar_records_saved_presets_before_a_session_is_loaded(app, settings):
    settings.setValue("ghost_motion", "constant")
    settings.setValue("ghost_speed", "4.5")
    settings.setValue("ghost_pause_s", "0.8")
    settings.setValue("ghost_face_deg", "15.4")
    ctl = ReplayController()
    bar = GhostSpeedBar(ctl, settings)
    session = _ghost_session()
    ctl.set_session(session)
    p2 = session.analysis.p2
    assert (p2.motion_mode, p2.speed, p2.pause_s, p2.face_deg) == ("constant", 4.5, 0.8, 15.4)
    assert not bar.isHidden()
    bar.deleteLater()
    app.processEvents()


def test_coiled_parameter_changes_rebuild_paths_and_keep_observed_tracks(app):
    ctl = ReplayController()
    bar = AnalysisParameterBar(ctl)
    session = _ghost_session()
    ctl.set_session(session)
    observed = session.tracks.observed_track(2)
    ctl.set_parameters({"ghost_motion": "constant", "ghost_speed": 1.0, "ghost_pause_s": 0.0})
    slow = session.tracks.position(2, 1000)
    ctl.set_ghost_speed(4.0)
    fast = session.tracks.position(2, 1000)
    assert slow is not None and fast is not None
    assert slow[1] - fast[1] > 2.5
    assert session.tracks.observed_track(2) is observed
    assert list(observed.y) == [30.0]
    assert bar.controls["ghost_speed"].edit.text() == "4.0"
    assert session.analysis.parameter_values["ghost_speed"] == 4.0
    ctl.set_ghost_motion("accel")
    ctl.set_ghost_start_speed(1.0)
    ctl.set_ghost_end_speed(3.0)
    ctl.set_ghost_accel(2.0)
    ctl.set_ghost_face(12.5)
    p2 = session.analysis.p2
    assert (p2.motion_mode, p2.start_speed, p2.end_speed, p2.accel_s, p2.face_deg) == (
        "accel",
        1.0,
        3.0,
        2.0,
        12.5,
    )
    bar.deleteLater()
    app.processEvents()
