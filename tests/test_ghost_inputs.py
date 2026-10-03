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


def test_typing_speed_and_angle_applies_immediately():
    _app, ctl, bar = _bar()
    assert bar.speed_edit.text() == "3.0"
    assert bar.face_edit.text() == "22.5"

    bar.speed_edit.setText("3.2")
    assert ctl.ghost_speed == 3.2
    assert bar.speed_edit.text() == "3.2"

    bar.face_edit.setText("±15.4°")
    assert ctl.ghost_face_deg == 15.4
    bar.face_edit.editingFinished.emit()
    assert bar.face_edit.text() == "15.4"

    bar.speed_edit.setText("abc")
    bar.speed_edit.editingFinished.emit()
    assert ctl.ghost_speed == 3.2
    assert bar.speed_edit.text() == "3.2"

    bar.face_edit.setText("120")
    assert ctl.ghost_face_deg == 90
    assert bar.face_edit.text() == "90.0"

    bar.speed_edit.setText("1.8")
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


def test_mouse_wheel_steps_speed_and_angle():
    _app, ctl, bar = _bar()

    _wheel(bar.speed_edit, 1)
    assert bar.speed_edit.text() == "3.1"
    assert ctl.ghost_speed == 3.1

    _wheel(bar.face_edit, 1)
    assert bar.face_edit.text() == "23.5"
    assert ctl.ghost_face_deg == 23.5

    _wheel(bar.speed_edit, -1)
    _wheel(bar.face_edit, -1)
    assert bar.speed_edit.text() == "3.0"
    assert ctl.ghost_speed == 3.0
    assert bar.face_edit.text() == "22.5"
    assert ctl.ghost_face_deg == 22.5


def test_accel_mode_shows_its_three_parameters_and_the_note_sits_below():
    _app, ctl, bar = _bar()
    assert bar.mode.currentData() == "accel"
    assert ctl.ghost_motion == "accel"
    assert bar.accel_box.isVisibleTo(bar)
    assert not bar.constant_box.isVisibleTo(bar)
    assert bar.layout().itemAt(0).layout().itemAt(0).widget() is bar.mode
    assert bar.layout().itemAt(1).widget() is bar.note
    assert "仅供参考" in bar.note.text()
    assert bar.start.edit.text() == "1.5"
    assert bar.end.edit.text() == "3.0"
    assert bar.accel.edit.text() == "0.5"

    bar.start.edit.setText("1.4")
    assert ctl.ghost_start_speed == 1.4
    bar.end.edit.setText("4")
    assert ctl.ghost_end_speed == 4.0
    bar.accel.edit.setText("2.5")
    assert ctl.ghost_accel_s == 2.5

    bar.mode.setCurrentIndex(bar.mode.findData("constant"))
    assert ctl.ghost_motion == "constant"
    assert bar.constant_box.isVisibleTo(bar)
    assert bar.pause.edit.isVisibleTo(bar)
    assert ctl.ghost_speed == 3.0
    assert bar.pause.edit.text() == "0.5"
    assert ctl.ghost_pause_s == 0.5
    bar.pause.edit.setText("0.8")
    assert ctl.ghost_pause_s == 0.8


def test_mouse_wheel_on_edits_steps_by_tenth_and_one_degree():
    _app, ctl, bar = _bar()

    _wheel(bar.speed_edit, 1)
    assert bar.speed_edit.text() == "3.1"
    assert ctl.ghost_speed == 3.1

    _wheel(bar.face_edit, -1)
    assert bar.face_edit.text() == "21.5"
    assert ctl.ghost_face_deg == 21.5

    bar.speed_edit.setText("2")
    _wheel(bar.speed_edit, 1)
    assert ctl.ghost_speed == 2.1
    assert bar.speed_edit.text() == "2.1"

    bar.face_edit.setText("20.5")
    _wheel(bar.face_edit, 1)
    assert ctl.ghost_face_deg == 21.5
    assert bar.face_edit.text() == "21.5"
