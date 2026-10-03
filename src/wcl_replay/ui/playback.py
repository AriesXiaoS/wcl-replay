# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Play / pause, speed selection and display toggles under the map."""

from __future__ import annotations

import math

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..bosses.base import Analysis, fmt_time
from .controller import ReplayController
from .theme import BORDER, PANEL, TEXT_DIM


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


class _Tunable:
    """A label and a text box. A valid number applies as it is typed; the wheel steps by ``step``."""

    def __init__(
        self,
        parent: QWidget,
        lay: QHBoxLayout,
        title: str,
        tip: str,
        *,
        maximum: float,
        suffix: str,
        edit_width: int,
        on_change,
        step: float = 0.1,
        mark: str = "",
        minimum: float = 0.0,
        decimals: int = 1,
    ):
        self.minimum = minimum
        self.maximum = maximum
        self.decimals = decimals
        self.step = step
        self._value = 0.0
        self.on_change = on_change
        label = QLabel(title)
        label.setToolTip(tip)
        lay.addWidget(label)
        if mark:
            lay.addWidget(QLabel(mark))
        self.edit = _NotchEdit(self._wheel, parent)
        self.edit.setFixedWidth(edit_width)
        self.edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.edit.setToolTip(tip)
        lay.addWidget(self.edit)
        lay.addWidget(QLabel(suffix))
        self.edit.textChanged.connect(self._on_text)
        self.edit.editingFinished.connect(self._normalize)

    def set_value(self, value: float) -> None:
        self._value = round(max(self.minimum, min(self.maximum, value)), self.decimals)
        self.edit.blockSignals(True)
        self.edit.setText(f"{self._value:.{self.decimals}f}")
        self.edit.blockSignals(False)

    def _parsed(self) -> float | None:
        raw = self.edit.text().strip().lstrip("±").rstrip("°").strip()
        if not raw:
            return None
        try:
            value = float(raw)
        except ValueError:
            return None
        return value if math.isfinite(value) else None

    def _on_text(self, _text: str) -> None:
        value = self._parsed()
        if value is None:
            return
        clamped = max(self.minimum, min(self.maximum, value))
        if clamped != value:
            self.set_value(clamped)
            self.on_change(self._value)
            return
        if clamped == self._value:
            return
        self._value = clamped
        self.on_change(self._value)

    def _normalize(self) -> None:
        value = self._parsed()
        if value is None:
            self.edit.blockSignals(True)
            self.edit.setText(f"{self._value:.{self.decimals}f}")
            self.edit.blockSignals(False)
            return
        rounded = round(max(self.minimum, min(self.maximum, value)), self.decimals)
        changed = rounded != self._value
        self.set_value(rounded)
        if changed:
            self.on_change(self._value)

    def _wheel(self, notches: int) -> None:
        base = self._parsed()
        if base is None:
            base = self._value
        self.set_value(base + notches * self.step)
        self.on_change(self._value)


class AnalysisParameterBar(QWidget):
    """Build controls only from the current boss's Qt-free parameter descriptions."""

    def __init__(self, ctl: ReplayController, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctl = ctl
        self.controls: dict[str, _Tunable | QComboBox] = {}
        self.boxes: dict[str, QWidget] = {}
        self._analysis: Analysis | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 0, 4, 4)
        outer.setSpacing(2)
        body = QWidget()
        self._row = QHBoxLayout(body)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(8)
        self._scroll = QScrollArea()
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setWidgetResizable(True)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._scroll.setFixedHeight(40)
        self._scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            f"QScrollBar:horizontal {{ background: {PANEL}; height: 6px; }}"
            f"QScrollBar::handle:horizontal {{ background: {BORDER}; border-radius: 3px; min-width: 30px; }}"
            "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }"
            "QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }"
        )
        self._scroll.setWidget(body)
        outer.addWidget(self._scroll)
        self.note = QLabel()
        self.note.setStyleSheet(f"color: {TEXT_DIM}; font-size: 8pt;")
        self.note.setWordWrap(True)
        self.note.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        outer.addWidget(self.note)
        ctl.sessionChanged.connect(self._rebuild)
        ctl.parametersChanged.connect(self._sync_values)
        self._rebuild()

    def _rebuild(self) -> None:
        analysis = self.ctl.session.analysis if self.ctl.session is not None else None
        self._analysis = analysis if isinstance(analysis, Analysis) else None
        while self._row.count():
            item = self._row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.controls.clear()
        self.boxes.clear()
        if self._analysis is None or not self._analysis.parameters:
            self.hide()
            return
        for parameter in self._analysis.parameters:
            box = QWidget(self)
            lay = QHBoxLayout(box)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(6)
            if parameter.choices:
                label = QLabel(parameter.label)
                label.setToolTip(parameter.tooltip)
                lay.addWidget(label)
                control = QComboBox(box)
                control.setObjectName(parameter.id)
                control.setStyleSheet("QComboBox { min-width: 80px; max-width: 160px; padding: 3px 6px; }")
                control.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
                control.setToolTip(parameter.tooltip)
                for value, label_text in parameter.choices:
                    control.addItem(label_text, value)
                control.currentIndexChanged.connect(
                    lambda _index, key=parameter.id, combo=control: self.ctl.set_parameter(
                        key, combo.currentData()
                    )
                )
                lay.addWidget(control)
            else:
                control = _Tunable(
                    box,
                    lay,
                    parameter.label,
                    parameter.tooltip,
                    minimum=parameter.minimum,
                    maximum=parameter.maximum,
                    suffix=parameter.suffix,
                    mark=parameter.prefix,
                    edit_width=52,
                    step=parameter.step,
                    decimals=parameter.decimals,
                    on_change=lambda value, key=parameter.id: self.ctl.set_parameter(key, value),
                )
                control.edit.setObjectName(parameter.id)
            self.controls[parameter.id] = control
            self.boxes[parameter.id] = box
            self._row.addWidget(box)
        self._row.addStretch(1)
        self.note.setText(self._analysis.parameter_note)
        self.note.setVisible(bool(self._analysis.parameter_note))
        self._sync_values()
        self.show()

    def _sync_values(self) -> None:
        if self._analysis is None:
            return
        values = self.ctl.parameter_values
        for parameter in self._analysis.parameters:
            control = self.controls.get(parameter.id)
            value = parameter.normalize(values.get(parameter.id, parameter.default))
            if isinstance(control, QComboBox):
                control.blockSignals(True)
                control.setCurrentIndex(control.findData(value))
                control.blockSignals(False)
            elif isinstance(control, _Tunable) and (control._value != value or not control.edit.text()):
                control.set_value(float(value))
            box = self.boxes.get(parameter.id)
            if box is not None:
                box.setVisible(parameter.is_visible(values))


class GhostSpeedBar(QWidget):
    """Separate from playback speed: how simulated ghosts walk. The log has neither rate nor facing."""

    _SPEED_MAX = 10.0
    _ACCEL_MAX = 20.0
    _PAUSE_MAX = 10.0

    def __init__(
        self, ctl: ReplayController, settings: QSettings | None = None, parent: QWidget | None = None
    ):
        super().__init__(parent)
        self.ctl = ctl
        self.settings = settings
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 0, 4, 4)
        outer.setSpacing(2)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        outer.addLayout(row)

        self.mode = QComboBox()
        self.mode.setObjectName("ghostMotion")
        self.mode.addItem("匀速模型", "constant")
        self.mode.addItem("加速模型", "accel")
        self.mode.setToolTip(
            "匀速模型：整段追人用同一个速度。面向从魂身上移开后，先停顿再走。"
            "加速模型：这次凝视一开始用起始速度，在加速时间内线性变到终止速度，之后保持终止速度。"
            "玩家看着魂时魂仍然停下，加速计时不因此暂停。"
        )
        row.addWidget(self.mode)

        speed_tip = "日志没有记录恐惧具象的移动速度。改数字即生效，滚轮每格 0.1 码/秒。"
        self.constant_box = QWidget()
        constant = QHBoxLayout(self.constant_box)
        constant.setContentsMargins(8, 0, 0, 0)
        constant.setSpacing(6)
        self.speed = _Tunable(
            self,
            constant,
            "魂速度",
            speed_tip,
            maximum=self._SPEED_MAX,
            suffix="码/秒",
            edit_width=52,
            on_change=self._apply_speed,
        )
        self.pause = _Tunable(
            self,
            constant,
            "停顿时间",
            "玩家把朝向从魂身上移开之后，再停这么多秒才开始走。看着魂时仍然马上停下。改数字即生效，滚轮每格 0.1 秒。",
            maximum=self._PAUSE_MAX,
            suffix="秒",
            edit_width=48,
            on_change=self._apply_pause,
        )
        self.speed_edit = self.speed.edit
        row.addWidget(self.constant_box)

        self.accel_box = QWidget()
        accel = QHBoxLayout(self.accel_box)
        accel.setContentsMargins(8, 0, 0, 0)
        accel.setSpacing(6)
        self.start = _Tunable(
            self,
            accel,
            "起始速度",
            "这次凝视刚开始时的速度。改数字即生效，滚轮每格 0.1 码/秒。",
            maximum=self._SPEED_MAX,
            suffix="码/秒",
            edit_width=48,
            on_change=self._apply_start,
        )
        self.end = _Tunable(
            self,
            accel,
            "终止速度",
            "加速时间走完之后保持的速度。改数字即生效，滚轮每格 0.1 码/秒。",
            maximum=self._SPEED_MAX,
            suffix="码/秒",
            edit_width=48,
            on_change=self._apply_end,
        )
        self.accel = _Tunable(
            self,
            accel,
            "加速时间",
            "从这次凝视开始，用这么多秒从起始速度线性变到终止速度。改数字即生效，滚轮每格 0.1 秒。",
            maximum=self._ACCEL_MAX,
            suffix="秒",
            edit_width=48,
            on_change=self._apply_accel,
        )
        row.addWidget(self.accel_box)

        row.addSpacing(12)
        self.face = _Tunable(
            self,
            row,
            "面对角度",
            "玩家朝向落在魂方向左右各这么多度以内时，这一拍魂不动。"
            "改数字即生效，精确到 0.1°；滚轮每格 1°。日志里没有这个角度。",
            maximum=90.0,
            suffix="°",
            edit_width=48,
            on_change=self._apply_face,
            step=1.0,
            mark="±",
        )
        self.face_edit = self.face.edit
        row.addStretch(1)

        self.note = QLabel("日志不记录魂相关数据。图中的魂由上面这些参数推算而来，不一定真实，仅供参考。")
        self.note.setStyleSheet(f"color: {TEXT_DIM}; font-size: 8pt;")
        self.note.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.note.setWordWrap(True)
        self.note.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        outer.addWidget(self.note)

        saved_mode = self._setting_text("ghost_motion", ctl.ghost_motion)
        if saved_mode not in ("constant", "accel"):
            saved_mode = ctl.ghost_motion
        self.speed.set_value(self._setting("ghost_speed", ctl.ghost_speed))
        self.pause.set_value(self._setting("ghost_pause_s", ctl.ghost_pause_s))
        self.start.set_value(self._setting("ghost_start_speed", ctl.ghost_start_speed))
        self.end.set_value(self._setting("ghost_end_speed", ctl.ghost_end_speed))
        self.accel.set_value(self._setting("ghost_accel_s", ctl.ghost_accel_s))
        self.face.set_value(self._setting("ghost_face_deg", ctl.ghost_face_deg))
        self.mode.blockSignals(True)
        self.mode.setCurrentIndex(max(0, self.mode.findData(saved_mode)))
        self.mode.blockSignals(False)
        ctl.set_ghost_motion(saved_mode)
        ctl.set_ghost_speed(self.speed._value)
        ctl.set_ghost_pause(self.pause._value)
        ctl.set_ghost_start_speed(self.start._value)
        ctl.set_ghost_end_speed(self.end._value)
        ctl.set_ghost_accel(self.accel._value)
        ctl.set_ghost_face(self.face._value)
        self._show_mode(saved_mode)
        self.mode.currentIndexChanged.connect(self._on_mode)
        ctl.sessionChanged.connect(self._sync_visible)
        self._sync_visible()

    def _show_mode(self, mode: str) -> None:
        self.constant_box.setVisible(mode != "accel")
        self.accel_box.setVisible(mode == "accel")

    def _on_mode(self, _index: int) -> None:
        mode = self.mode.currentData()
        if mode not in ("constant", "accel"):
            mode = "constant"
        self._show_mode(mode)
        self.ctl.set_ghost_motion(mode)
        if self.settings is not None:
            self.settings.setValue("ghost_motion", mode)

    def _setting(self, key: str, default: float) -> float:
        if self.settings is None:
            return default
        try:
            return float(self.settings.value(key, default))
        except (TypeError, ValueError):
            return default

    def _setting_text(self, key: str, default: str) -> str:
        if self.settings is None:
            return default
        value = self.settings.value(key, default)
        return default if value is None else str(value)

    def _apply_speed(self, speed: float) -> None:
        self.ctl.set_ghost_speed(speed)
        if self.settings is not None:
            self.settings.setValue("ghost_speed", speed)

    def _apply_pause(self, seconds: float) -> None:
        self.ctl.set_ghost_pause(seconds)
        if self.settings is not None:
            self.settings.setValue("ghost_pause_s", seconds)

    def _apply_start(self, speed: float) -> None:
        self.ctl.set_ghost_start_speed(speed)
        if self.settings is not None:
            self.settings.setValue("ghost_start_speed", speed)

    def _apply_end(self, speed: float) -> None:
        self.ctl.set_ghost_end_speed(speed)
        if self.settings is not None:
            self.settings.setValue("ghost_end_speed", speed)

    def _apply_accel(self, seconds: float) -> None:
        self.ctl.set_ghost_accel(seconds)
        if self.settings is not None:
            self.settings.setValue("ghost_accel_s", seconds)

    def _apply_face(self, degrees: float) -> None:
        self.ctl.set_ghost_face(degrees)
        if self.settings is not None:
            self.settings.setValue("ghost_face_deg", degrees)

    def _sync_visible(self) -> None:
        session = self.ctl.session
        self.setVisible(
            session is not None
            and isinstance(session.analysis, Analysis)
            and any(parameter.id == "ghost_motion" for parameter in session.analysis.parameters)
        )
