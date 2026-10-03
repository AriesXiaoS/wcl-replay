# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Replay state shared by all widgets: the loaded session, current time, playback and toggles."""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QElapsedTimer, QObject, QTimer, Signal

from ..bosses.base import Analysis
from ..core.models import FightData
from ..core.targets import Targets
from ..core.tracks import Tracks


@dataclass
class Session:
    data: FightData
    tracks: Tracks
    analysis: Analysis
    targets: Targets = field(init=False)

    def __post_init__(self) -> None:
        self.targets = Targets(self.data)

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
    selectionChanged = Signal()

    SPEEDS = (0.25, 0.5, 1.0, 2.0, 4.0)
    GHOST_SPEED_DEFAULT = 3.0
    GHOST_FACE_DEFAULT = 22.5
    UNIT_GROUPS = ("boss", "ghost", "player")  # back to front by default

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.session: Session | None = None
        self.t = 0.0
        self.speed = 1.0
        self.ghost_speed = self.GHOST_SPEED_DEFAULT
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

    def set_session(self, session: Session) -> None:
        self.pause()
        self.session = session
        self.layers = {lane.id: lane.default_on for lane in session.analysis.lanes}
        self.t = 0.0
        self._push_ghost_motion(session.analysis)
        had = self.selected is not None
        self.selected = None
        self.sessionChanged.emit()
        if had:
            self.selectionChanged.emit()
        self.timeChanged.emit(self.t)

    def clear_session(self) -> None:
        self.pause()
        self.session = None
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

    def set_ghost_speed(self, speed: float) -> None:
        """Preset used to simulate Manifestation of Dread paths. The log has no speed."""
        self.ghost_speed = max(0.0, float(speed))
        self._push_ghost_motion()
        self.ghostSpeedChanged.emit(self.ghost_speed)

    def set_ghost_face(self, face_deg: float) -> None:
        """Half-angle, in degrees, inside which a player staring at a ghost holds it still."""
        self.ghost_face_deg = max(0.0, float(face_deg))
        self._push_ghost_motion()
        self.ghostFaceChanged.emit(self.ghost_face_deg)

    def _push_ghost_motion(self, analysis: object | None = None) -> None:
        if analysis is None and self.session is not None:
            analysis = self.session.analysis
        if analysis is None or not hasattr(analysis, "apply_ghost_motion"):
            return
        analysis.apply_ghost_motion(self.ghost_speed, self.ghost_face_deg)
        if self.session is not None:
            self.timeChanged.emit(self.t)

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
