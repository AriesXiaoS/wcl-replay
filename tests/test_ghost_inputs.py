# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""The ghost speed and facing readouts are editable."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

from wcl_replay.ui.controller import ReplayController
from wcl_replay.ui.playback import GhostSpeedBar


def _bar() -> tuple[QApplication, ReplayController, GhostSpeedBar]:
    app = QApplication.instance() or QApplication([])
    ctl = ReplayController()
    return app, ctl, GhostSpeedBar(ctl, None)


def test_typing_speed_and_angle_updates_the_sliders():
    _app, ctl, bar = _bar()
    assert bar.speed_edit.text() == "3.0"
    assert bar.face_edit.text() == "22.5"

    bar.speed_edit.setText("3.2")
    bar.speed_edit.editingFinished.emit()
    assert ctl.ghost_speed == 3.2
    assert bar.slider.value() == 32
    assert bar.speed_edit.text() == "3.2"

    bar.face_edit.setText("±15.4°")
    bar.face_edit.editingFinished.emit()
    assert ctl.ghost_face_deg == 15.4
    assert bar.face_slider.value() == 154
    assert bar.face_edit.text() == "15.4"

    bar.speed_edit.setText("abc")
    bar.speed_edit.editingFinished.emit()
    assert ctl.ghost_speed == 3.2
    assert bar.speed_edit.text() == "3.2"

    bar.face_edit.setText("120")
    bar.face_edit.editingFinished.emit()
    assert ctl.ghost_face_deg == 90
    assert bar.face_edit.text() == "90.0"

    bar.slider.setValue(18)
    assert bar.speed_edit.text() == "1.8"
    assert ctl.ghost_speed == 1.8


def _wheel(slider, notches: int) -> None:
    slider.wheelEvent(
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


def test_mouse_wheel_steps_speed_and_angle_by_one():
    _app, ctl, bar = _bar()
    speed_before = bar.slider.value()
    face_before = bar.face_slider.value()

    _wheel(bar.slider, 1)
    assert bar.slider.value() == speed_before + 1
    assert ctl.ghost_speed == (speed_before + 1) / 10

    _wheel(bar.face_slider, 1)
    assert bar.face_slider.value() == face_before + bar.face_slider.singleStep()
    assert ctl.ghost_face_deg == (face_before / 10) + 1

    _wheel(bar.slider, -1)
    _wheel(bar.face_slider, -1)
    assert bar.slider.value() == speed_before
    assert bar.face_slider.value() == face_before


def test_mouse_wheel_on_edits_steps_by_tenth_and_one_degree():
    _app, ctl, bar = _bar()

    _wheel(bar.speed_edit, 1)
    assert bar.speed_edit.text() == "3.1"
    assert bar.slider.value() == 31
    assert ctl.ghost_speed == 3.1

    _wheel(bar.face_edit, -1)
    assert bar.face_edit.text() == "21.5"
    assert bar.face_slider.value() == 215
    assert ctl.ghost_face_deg == 21.5

    bar.speed_edit.setText("2")
    _wheel(bar.speed_edit, 1)
    assert ctl.ghost_speed == 2.1
    assert bar.speed_edit.text() == "2.1"

    bar.face_edit.setText("20.5")
    _wheel(bar.face_edit, 1)
    assert ctl.ghost_face_deg == 21.5
    assert bar.face_edit.text() == "21.5"
