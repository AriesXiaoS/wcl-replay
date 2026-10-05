# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Replay state shared by all widgets: the loaded session, current time, playback and toggles."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from PySide6.QtCore import QElapsedTimer, QObject, QSettings, QTimer, Signal

from ..bosses.base import Analysis, ParameterValue
from ..core.models import FightData
from ..core.targets import Targets
from ..core.tracks import Tracks


@dataclass(slots=True)
class Session:
    data: FightData
    tracks: Tracks
    analysis: Analysis
    targets: Targets = field(init=False)

    def __post_init__(self) -> None:
        # Analysis is normally built in a worker; reuse its compact target index.
        indexed = getattr(self.analysis, "targets", None)
        self.targets = (
            indexed
            if isinstance(indexed, Targets) and getattr(self.analysis, "data", None) is self.data
            else Targets(self.data)
        )

    @property
    def duration(self) -> int:
        return self.data.fight.duration_ms


class ReplayController(QObject):
    sessionChanged = Signal()
    timeChanged = Signal(float)
    playingChanged = Signal(bool)
    layersChanged = Signal()
    optionsChanged = Signal()
    unitOrderChanged = Signal()
    ghostSpeedChanged = Signal(float)
    ghostFaceChanged = Signal(float)
    parametersChanged = Signal()
    analysisChanged = Signal()
    selectionChanged = Signal()

    SPEEDS = (0.25, 0.5, 1.0, 2.0, 4.0)
    GHOST_SPEED_DEFAULT = 3.0
    GHOST_MOTION_DEFAULT = "accel"
    GHOST_START_SPEED_DEFAULT = 1.5
    GHOST_END_SPEED_DEFAULT = 3.0
    GHOST_ACCEL_DEFAULT = 0.5
    GHOST_PAUSE_DEFAULT = 0.5
    GHOST_FACE_DEFAULT = 22.5
    UNIT_GROUPS = ("boss", "ghost", "player")  # back to front by default
    _GHOST_PARAMETERS = frozenset(
        {
            "ghost_motion",
            "ghost_speed",
            "ghost_start_speed",
            "ghost_end_speed",
            "ghost_accel_s",
            "ghost_pause_s",
            "ghost_face_deg",
        }
    )

    def __init__(self, parent: QObject | None = None, *, settings: QSettings | None = None):
        super().__init__(parent)
        self.settings = settings
        self.parameter_values: dict[str, ParameterValue] = {}
        self._parameter_cache: dict[tuple[int, str], ParameterValue] = {}
        self._legacy_parameter_values: dict[str, ParameterValue] = {}
        self.session: Session | None = None
        self.session_origin: tuple[str, tuple] | None = None
        self.active_source = "local"
        self.t = 0.0
        self.speed = 1.0
        self.ghost_motion = self.GHOST_MOTION_DEFAULT
        self.ghost_speed = self.GHOST_SPEED_DEFAULT
        self.ghost_start_speed = self.GHOST_START_SPEED_DEFAULT
        self.ghost_end_speed = self.GHOST_END_SPEED_DEFAULT
        self.ghost_accel_s = self.GHOST_ACCEL_DEFAULT
        self.ghost_pause_s = self.GHOST_PAUSE_DEFAULT
        self.ghost_face_deg = self.GHOST_FACE_DEFAULT
        self.playing = False
        self.layers: dict[str, bool] = {}
        self.unit_order = list(self.UNIT_GROUPS)
        self.options = {"names": False, "player_hp": False, "hp": True, "key": False}
        self.selected: int | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self._clock = QElapsedTimer()

    # -- session ------------------------------------------------------------------------------

    def set_source(self, source: str) -> None:
        if source != self.active_source:
            self.active_source = source
            self.clear_session()

    def set_session(self, session: Session, *, origin: tuple[str, tuple] | None = None) -> None:
        self.pause()
        self.session = session
        self.session_origin = origin
        self.t = 0.0
        self._push_parameters(session.analysis)
        self.layers = {lane.id: lane.default_on for lane in session.analysis.lanes}
        had = self.selected is not None
        self.selected = None
        self.sessionChanged.emit()
        if had:
            self.selectionChanged.emit()
        self.timeChanged.emit(self.t)

    def show_session(self, session: Session, *, origin: tuple[str, tuple]) -> None:
        """Reselecting the same cached replay keeps its time, playback and unit selection."""
        if self.session is session:
            self.session_origin = origin
        else:
            self.set_session(session, origin=origin)

    def displayed_key(self, source: str, cache: Mapping[tuple, object]) -> tuple | None:
        """Record ownership survives a background replacement of its cached Session."""
        if self.session is None:
            return None
        if self.session_origin is not None:
            owner_source, key = self.session_origin
            return key if owner_source == source else None
        # Standalone consumers may still install a cached replay without an origin.
        return next((key for key, cached in cache.items() if self.session is cached), None)

    def clear_session(self) -> None:
        self.pause()
        self.session = None
        self.session_origin = None
        self.parameter_values = {}
        self.layers = {}
        self.t = 0.0
        had = self.selected is not None
        self.selected = None
        self.sessionChanged.emit()
        if had:
            self.selectionChanged.emit()
        self.timeChanged.emit(self.t)

    def select(self, actor_id: int | None) -> None:
        """Select a unit for the plates under the map. The same unit again clears it."""
        nxt = None if actor_id is None or actor_id == self.selected else actor_id
        if nxt == self.selected:
            return
        self.selected = nxt
        self.selectionChanged.emit()

    def layer_on(self, layer: str) -> bool:
        return not layer or self.layers.get(layer, True)

    def toggle_layer(self, layer: str) -> None:
        self.layers[layer] = not self.layers.get(layer, True)
        self.layersChanged.emit()

    def set_option(self, key: str, value: bool) -> None:
        self.options[key] = value
        self.optionsChanged.emit()

    def set_unit_order(self, order: list[str]) -> None:
        """Back-to-front draw order. The last id is painted on top. Missing kinds are not reinserted."""
        cleaned: list[str] = []
        for group in order:
            if group and group not in cleaned:
                cleaned.append(group)
        if cleaned == self.unit_order:
            return
        self.unit_order = cleaned
        self.unitOrderChanged.emit()

    def nudge_unit_group(self, group: str, toward_front: bool) -> None:
        if group not in self.unit_order:
            return
        order = list(self.unit_order)
        i = order.index(group)
        j = i + (1 if toward_front else -1)
        if j < 0 or j >= len(order):
            return
        order[i], order[j] = order[j], order[i]
        self.set_unit_order(order)

    # -- time ---------------------------------------------------------------------------------

    def seek(self, t: float) -> None:
        if self.session is None:
            return
        self.t = max(0.0, min(float(self.session.duration), t))
        self.timeChanged.emit(self.t)

    def step(self, delta_ms: float) -> None:
        self.seek(self.t + delta_ms)

    def play(self) -> None:
        if self.session is None or self.playing:
            return
        if self.t >= self.session.duration:
            self.t = 0.0
        self.playing = True
        self._clock.start()
        self._timer.start()
        self.playingChanged.emit(True)

    def pause(self) -> None:
        if not self.playing:
            return
        self.playing = False
        self._timer.stop()
        self.playingChanged.emit(False)

    def toggle_play(self) -> None:
        self.pause() if self.playing else self.play()

    def set_speed(self, speed: float) -> None:
        self.speed = speed

    def set_ghost_motion(self, mode: str) -> None:
        """``constant`` keeps one speed and pauses after a look-away. ``accel`` ramps, with no pause."""
        mode = "accel" if mode == "accel" else "constant"
        self._set_legacy_parameter("ghost_motion", mode)

    def set_ghost_speed(self, speed: float) -> None:
        """Constant-mode rate, in yards per second. The log has no speed."""
        self._set_legacy_parameter("ghost_speed", max(0.0, float(speed)))

    def set_ghost_start_speed(self, speed: float) -> None:
        self._set_legacy_parameter("ghost_start_speed", max(0.0, float(speed)))

    def set_ghost_end_speed(self, speed: float) -> None:
        self._set_legacy_parameter("ghost_end_speed", max(0.0, float(speed)))

    def set_ghost_accel(self, seconds: float) -> None:
        self._set_legacy_parameter("ghost_accel_s", max(0.0, float(seconds)))

    def set_ghost_pause(self, seconds: float) -> None:
        """Constant-mode delay, in seconds, after the player looks away from a ghost."""
        self._set_legacy_parameter("ghost_pause_s", max(0.0, float(seconds)))

    def set_ghost_face(self, face_deg: float) -> None:
        """Half-angle, in degrees, inside which a player staring at a ghost holds it still."""
        self._set_legacy_parameter("ghost_face_deg", max(0.0, float(face_deg)))

    # -- boss-defined parameters -------------------------------------------------------------

    def set_parameter(self, parameter_id: str, value: ParameterValue) -> None:
        self.set_parameters({parameter_id: value})

    def set_parameters(self, values: Mapping[str, ParameterValue]) -> None:
        """Update declared controls together, then rebuild and redraw the analysis once."""
        analysis = self.session.analysis if self.session is not None else None
        if not isinstance(analysis, Analysis):
            return
        changed = {}
        for parameter in analysis.parameters:
            if parameter.id not in values:
                continue
            value = parameter.normalize(values[parameter.id])
            if self.parameter_values.get(parameter.id) != value:
                changed[parameter.id] = value
        if not changed:
            return
        analysis.apply_parameters(changed)
        analysis.refresh_indexes()
        self.session.targets = analysis.targets
        self.layers = {lane.id: self.layers.get(lane.id, lane.default_on) for lane in analysis.lanes}
        self.parameter_values = dict(analysis.parameter_values)
        encounter_id = analysis.data.fight.encounter_id
        for parameter in analysis.parameters:
            if parameter.id not in changed:
                continue
            value = self.parameter_values[parameter.id]
            self._parameter_cache[encounter_id, parameter.id] = value
            if self.settings is not None:
                self.settings.setValue(parameter.storage_key(encounter_id), value)
            self._sync_legacy_parameter(parameter.id, value)
        self.parametersChanged.emit()
        self.analysisChanged.emit()
        self.layersChanged.emit()
        self.timeChanged.emit(self.t)

    def _push_parameters(self, analysis: Analysis | None = None) -> None:
        if analysis is None and self.session is not None:
            analysis = self.session.analysis
        # Older consumers may provide a minimal result with no parameter capability.
        if not isinstance(analysis, Analysis) or not analysis.parameters:
            self.parameter_values = {}
            return
        values = {}
        encounter_id = analysis.data.fight.encounter_id
        for parameter in analysis.parameters:
            key = (encounter_id, parameter.id)
            fallback = self._legacy_parameter_values.get(
                parameter.id, analysis.parameter_values.get(parameter.id, parameter.default)
            )
            value = self._parameter_cache.get(key, fallback)
            if key not in self._parameter_cache and self.settings is not None:
                value = self.settings.value(parameter.storage_key(encounter_id), value)
            value = parameter.normalize(value)
            if not parameter.choices:
                value = parameter.normalize(round(float(value), parameter.decimals))
            values[parameter.id] = value
        analysis.apply_parameters(values)
        analysis.refresh_indexes()
        if self.session is not None and self.session.analysis is analysis:
            self.session.targets = analysis.targets
        self.parameter_values = dict(analysis.parameter_values)
        for parameter_id, value in self.parameter_values.items():
            self._parameter_cache[encounter_id, parameter_id] = value
            self._sync_legacy_parameter(parameter_id, value)
        self.parametersChanged.emit()

    def _set_legacy_parameter(self, parameter_id: str, value: ParameterValue) -> None:
        analysis = self.session.analysis if self.session is not None else None
        if isinstance(analysis, Analysis) and any(
            parameter.id == parameter_id for parameter in analysis.parameters
        ):
            self.set_parameter(parameter_id, value)
        else:
            self._legacy_parameter_values[parameter_id] = value
            self._sync_legacy_parameter(parameter_id, value)

    def _sync_legacy_parameter(self, parameter_id: str, value: ParameterValue) -> None:
        if parameter_id not in self._GHOST_PARAMETERS:
            return
        setattr(self, parameter_id, value)
        if parameter_id == "ghost_speed":
            self.ghostSpeedChanged.emit(float(value))
        elif parameter_id == "ghost_face_deg":
            self.ghostFaceChanged.emit(float(value))

    def _tick(self) -> None:
        dt = self._clock.restart()
        if self.session is None:
            return
        t = self.t + dt * self.speed
        if t >= self.session.duration:
            self.seek(self.session.duration)
            self.pause()
            return
        self.seek(t)
