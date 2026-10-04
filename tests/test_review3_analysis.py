from __future__ import annotations

import pytest

from wcl_replay import pipeline
from wcl_replay.bosses.base import Analysis, AnalysisParameter, BossModule
from wcl_replay.bosses.coiled_altar import constants as C
from wcl_replay.bosses.coiled_altar.module import CoiledAltarAnalysis
from wcl_replay.bosses.coiled_altar.p2 import P2Model
from wcl_replay.core.models import Actor, ActorKind, Event, Fight, FightData, Sample
from wcl_replay.core.tracks import Tracks


def ghost_data():
    actors = {
        0: Actor(0, "p", "玩家", ActorKind.PLAYER),
        1: Actor(1, "zul", "祖尔加", ActorKind.NPC, npc_id=C.NPC_ZULJAN, hostile=True),
        2: Actor(2, "mal", "玛拉卡斯", ActorKind.NPC, npc_id=C.NPC_MALACRASS, hostile=True),
        3: Actor(3, "ghost", "魂", ActorKind.NPC, npc_id=C.NPC_GHOST, hostile=True),
    }
    events = [
        Event(6000, "UNIT_DIED", dst=1),
        Event(6100, "SPELL_SUMMON", src=2, dst=3),
        Event(6100, "SPELL_AURA_APPLIED", src=3, dst=0, spell_id=C.FIXATE),
        Event(8100, "SPELL_AURA_REMOVED", src=3, dst=0, spell_id=C.FIXATE),
    ]
    samples = {
        0: [Sample(6000, 1158.6, 0, 0, 100, 100)],
        1: [Sample(0, 1158.6, 0, 0, 100, 100)],
        2: [Sample(6000, 1158.6, 0, 0, 100, 100)],
        3: [Sample(6100, 1158.6, 30, 0, 1, 1)],
    }
    return FightData(Fight(1, C.ENCOUNTER_ID, "测试", 16, 20, 10000, False), actors, events, samples)


@pytest.mark.parametrize(
    "parameters,expected_mode,expected_y",
    [(None, "accel", 27.3), ({"ghost_motion": "constant", "ghost_speed": 4}, "constant", 26)],
)
def test_pipeline_builds_only_final_ghost_path(monkeypatch, parameters, expected_mode, expected_y):
    calls = []
    simulate = P2Model._simulate_ghosts

    def counted(model):
        calls.append(model.motion_mode)
        return simulate(model)

    monkeypatch.setattr(P2Model, "_simulate_ghosts", counted)
    data = ghost_data()
    tracks, analysis = pipeline.analyze(data, parameters=parameters)
    assert calls == [expected_mode]
    assert analysis.parameter_values["ghost_motion"] == analysis.p2.motion_mode == expected_mode
    assert tracks.position(3, 7100)[1] == pytest.approx(expected_y)
    assert tracks.observed_track(3).y[0] == 30
    assert tracks.present(3, 8000) and not tracks.present(3, 8200)
    assert analysis.p2.route_overlays(7100)


def test_direct_analysis_applies_defaults_once_and_reuses_unchanged_motion(monkeypatch):
    calls = []
    simulate = P2Model._simulate_ghosts

    def counted(model):
        calls.append(model.motion_mode)
        return simulate(model)

    monkeypatch.setattr(P2Model, "_simulate_ghosts", counted)
    data = ghost_data()
    tracks = Tracks(data)
    analysis = CoiledAltarAnalysis(data, tracks)
    assert calls == ["accel"]
    assert tracks.position(3, 7100)[1] == pytest.approx(27.3)
    analysis.apply_parameters(analysis.parameter_values)
    assert calls == ["accel"]
    analysis.apply_parameters({"ghost_motion": "constant", "ghost_speed": 4})
    assert calls == ["accel", "constant"]
    assert tracks.position(3, 7100)[1] == pytest.approx(26)


def test_deferred_constant_model_still_builds_from_observed_samples():
    data = ghost_data()
    tracks = Tracks(data)
    observed = tracks.track(3)
    model = P2Model(data, tracks, [e.t for e in data.events], 6000, defer_motion=True)
    assert tracks.track(3) is observed
    model.set_motion(mode="constant", speed=C.GHOST_SPEED)
    assert tracks.track(3) is not observed
    assert tracks.position(3, 7100)[1] == pytest.approx(27)
    assert model.route_overlays(7100)


def test_parameter_aware_factory_supports_existing_module_hook(monkeypatch):
    class ExistingAnalysis(Analysis):
        parameters = (AnalysisParameter("radius", "半径", 8, minimum=1, maximum=40),)

        def apply_parameters(self, values):
            super().apply_parameters(values)
            self.arena = (-self.parameter_values["radius"], self.parameter_values["radius"], -10, 10)

    class ExistingModule(BossModule):
        def analyze(self, data, tracks):
            return ExistingAnalysis(data, tracks)

    monkeypatch.setattr(pipeline, "module_for", lambda _eid: ExistingModule())
    _, analysis = pipeline.analyze(ghost_data(), parameters={"radius": 12})
    assert analysis.parameter_values == {"radius": 12}
    assert analysis.arena == (-12, 12, -10, 10)
