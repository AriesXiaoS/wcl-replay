# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Play / pause, speed selection and display toggles under the map."""

from __future__ import annotations

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSlider, QWidget

from ..bosses.base import fmt_time
from .controller import ReplayController


class PlaybackBar(QWidget):
    OPTIONS = (("names", "Names"), ("player_hp", "Player HP"), ("hp", "HP"), ("key", "Key"))

    def __init__(self, ctl: ReplayController, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctl = ctl
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self.play_btn = QPushButton("▶")
        self.play_btn.setFixedWidth(42)
        self.play_btn.clicked.connect(ctl.toggle_play)
        lay.addWidget(self.play_btn)
        self.time_lbl = QLabel("0:00 / 0:00")
        self.time_lbl.setMinimumWidth(90)
        lay.addWidget(self.time_lbl)
        lay.addSpacing(8)

        self.speed_group = QButtonGroup(self)
        for sp in ctl.SPEEDS:
            b = QPushButton(f"{sp:g}×")
            b.setCheckable(True)
            b.setChecked(sp == ctl.speed)
            b.clicked.connect(lambda _c=False, v=sp: ctl.set_speed(v))
            self.speed_group.addButton(b)
            lay.addWidget(b)
        lay.addSpacing(8)
        for key, label in self.OPTIONS:
            b = QPushButton(label)
            b.setCheckable(True)
            b.setChecked(ctl.options.get(key, False))
            b.toggled.connect(lambda on, k=key: ctl.set_option(k, on))
            lay.addWidget(b)
        lay.addStretch(1)

        ctl.timeChanged.connect(self._on_time)
        ctl.sessionChanged.connect(lambda: self._on_time(ctl.t))
        ctl.playingChanged.connect(lambda on: self.play_btn.setText("❚❚" if on else "▶"))

    def _on_time(self, t: float) -> None:
        s = self.ctl.session
        dur = s.duration if s else 0
        self.time_lbl.setText(f"{fmt_time(t)} / {fmt_time(dur)}")


class _NotchCounter:
    """Turn wheel angle into notches of 120, ignoring the system scroll-line count."""

    def __init__(self) -> None:
        self.left = 0

    def take(self, event: QWheelEvent) -> int | None:
        delta = event.angleDelta().y() or event.angleDelta().x()
        if delta == 0:
            return None
        if event.inverted():
            delta = -delta
        self.left += delta
        notches = int(self.left / 120)
        if notches:
            self.left -= notches * 120
        return notches


class _NotchSlider(QSlider):
    """One mouse-wheel notch moves the value by ``singleStep``."""

    def __init__(self, orientation: Qt.Orientation, parent: QWidget | None = None):
        super().__init__(orientation, parent)
        self._wheel = _NotchCounter()

    def wheelEvent(self, event: QWheelEvent) -> None:
        notches = self._wheel.take(event)
        if notches is None:
            super().wheelEvent(event)
            return
        if notches:
            self.setValue(self.value() + notches * self.singleStep())
        event.accept()


class _NotchEdit(QLineEdit):
    """One mouse-wheel notch calls ``on_notches`` with the notch count."""

    def __init__(self, on_notches, parent: QWidget | None = None):
        super().__init__(parent)
        self._on_notches = on_notches
        self._wheel = _NotchCounter()

    def wheelEvent(self, event: QWheelEvent) -> None:
        notches = self._wheel.take(event)
        if notches is None:
            super().wheelEvent(event)
            return
        if notches:
            self._on_notches(notches)
        event.accept()


class GhostSpeedBar(QWidget):
    """Separate from playback speed: how fast simulated ghosts walk, in yards per second."""

    _SCALE = 10  # slider units per yard/second
    _FACE_SCALE = 10  # slider units per degree; one wheel notch is one degree

    def __init__(
        self, ctl: ReplayController, settings: QSettings | None = None, parent: QWidget | None = None
    ):
        super().__init__(parent)
        self.ctl = ctl
        self.settings = settings
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 0, 4, 4)
        title = QLabel("魂速度")
        title.setToolTip(
            "日志没有记录恐惧具象的移动速度。拖动滑块、在框里输入后回车，或在滑块和输入框上滚动滚轮（每格 0.1 码/秒）。"
        )
        lay.addWidget(title)
        self.slider = _NotchSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 10 * self._SCALE)
        self.slider.setSingleStep(1)
        self.slider.setPageStep(5)
        self.slider.setFixedWidth(180)
        self.slider.setToolTip(title.toolTip())
        lay.addWidget(self.slider)
        self.speed_edit = self._number_box(52, title.toolTip(), self._wheel_speed)
        lay.addWidget(self.speed_edit)
        lay.addWidget(QLabel("码/秒"))
        lay.addSpacing(12)
        face_title = QLabel("面对角度")
        face_title.setToolTip(
            "玩家朝向落在魂方向左右各这么多度以内时，这一拍魂不动。"
            "滑块和输入框精确到 0.1°；滚轮每格仍是 1°。日志里没有这个角度。"
        )
        lay.addWidget(face_title)
        self.face_slider = _NotchSlider(Qt.Orientation.Horizontal)
        self.face_slider.setRange(0, 90 * self._FACE_SCALE)
        self.face_slider.setSingleStep(self._FACE_SCALE)
        self.face_slider.setPageStep(5 * self._FACE_SCALE)
        self.face_slider.setFixedWidth(140)
        self.face_slider.setToolTip(face_title.toolTip())
        lay.addWidget(self.face_slider)
        lay.addWidget(QLabel("±"))
        self.face_edit = self._number_box(48, face_title.toolTip(), self._wheel_face)
        lay.addWidget(self.face_edit)
        lay.addWidget(QLabel("°"))
        lay.addStretch(1)

        saved = self._setting("ghost_speed", ctl.ghost_speed)
        face = self._setting("ghost_face_deg", ctl.ghost_face_deg)
        self.slider.blockSignals(True)
        self.slider.setValue(self._to_slider(saved))
        self.slider.blockSignals(False)
        self.face_slider.blockSignals(True)
        self.face_slider.setValue(self._to_face(face))
        self.face_slider.blockSignals(False)
        self._show(self._from_slider(self.slider.value()))
        self._show_face(self._from_face(self.face_slider.value()))
        ctl.ghost_speed = self._from_slider(self.slider.value())
        ctl.ghost_face_deg = self._from_face(self.face_slider.value())
        self.slider.valueChanged.connect(self._on_slide)
        self.face_slider.valueChanged.connect(self._on_face)
        self.speed_edit.editingFinished.connect(self._commit_speed)
        self.face_edit.editingFinished.connect(self._commit_face)
        ctl.sessionChanged.connect(self._sync_visible)
        self._sync_visible()

    def _to_slider(self, speed: float) -> int:
        return int(round(max(0.0, min(10.0, speed)) * self._SCALE))

    def _from_slider(self, value: int) -> float:
        return value / self._SCALE

    def _to_face(self, degrees: float) -> int:
        return int(round(max(0.0, min(90.0, degrees)) * self._FACE_SCALE))

    def _from_face(self, value: int) -> float:
        return value / self._FACE_SCALE

    def _setting(self, key: str, default: float) -> float:
        if self.settings is None:
            return default
        try:
            return float(self.settings.value(key, default))
        except (TypeError, ValueError):
            return default

    def _number_box(self, width: int, tip: str, on_notches) -> _NotchEdit:
        box = _NotchEdit(on_notches, self)
        box.setFixedWidth(width)
        box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.setToolTip(tip)
        return box

    def _wheel_speed(self, notches: int) -> None:
        try:
            speed = float(self.speed_edit.text().strip())
        except ValueError:
            speed = self._from_slider(self.slider.value())
        target = self._to_slider(speed + notches / self._SCALE)
        if self.slider.value() != target:
            self.slider.setValue(target)
        else:
            self._show(self._from_slider(target))
            self._apply_speed(self._from_slider(target))

    def _wheel_face(self, notches: int) -> None:
        raw = self.face_edit.text().strip().lstrip("±").rstrip("°")
        try:
            degrees = float(raw)
        except ValueError:
            degrees = self._from_face(self.face_slider.value())
        degrees = round(max(0.0, min(90.0, degrees + notches)), 1)
        target = self._to_face(degrees)
        if self.face_slider.value() != target:
            self.face_slider.setValue(target)
        else:
            self._show_face(self._from_face(target))
            self._apply_face(self._from_face(target))

    def _show(self, speed: float) -> None:
        self.speed_edit.setText(f"{speed:.1f}")

    def _show_face(self, degrees: float) -> None:
        self.face_edit.setText(f"{degrees:.1f}")

    def _on_slide(self, value: int) -> None:
        speed = self._from_slider(value)
        self._show(speed)
        self._apply_speed(speed)

    def _on_face(self, value: int) -> None:
        degrees = self._from_face(value)
        self._show_face(degrees)
        self._apply_face(degrees)

    def _commit_speed(self) -> None:
        try:
            speed = float(self.speed_edit.text().strip())
        except ValueError:
            self._show(self.ctl.ghost_speed)
            return
        speed = round(max(0.0, min(10.0, speed)), 1)
        self.slider.blockSignals(True)
        self.slider.setValue(self._to_slider(speed))
        self.slider.blockSignals(False)
        self._show(speed)
        self._apply_speed(speed)

    def _commit_face(self) -> None:
        raw = self.face_edit.text().strip().lstrip("±").rstrip("°")
        try:
            degrees = float(raw)
        except ValueError:
            self._show_face(self.ctl.ghost_face_deg)
            return
        degrees = round(max(0.0, min(90.0, degrees)), 1)
        self.face_slider.blockSignals(True)
        self.face_slider.setValue(self._to_face(degrees))
        self.face_slider.blockSignals(False)
        self._show_face(degrees)
        self._apply_face(degrees)

    def _apply_speed(self, speed: float) -> None:
        self.ctl.set_ghost_speed(speed)
        if self.settings is not None:
            self.settings.setValue("ghost_speed", speed)

    def _apply_face(self, degrees: float) -> None:
        self.ctl.set_ghost_face(degrees)
        if self.settings is not None:
            self.settings.setValue("ghost_face_deg", degrees)

    def _sync_visible(self) -> None:
        session = self.ctl.session
        self.setVisible(session is not None and hasattr(session.analysis, "apply_ghost_motion"))
