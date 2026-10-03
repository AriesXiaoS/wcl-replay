# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Raid health frames: twenty players keep full size, thirty only get narrower."""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QSplitter

from wcl_replay.core.models import Actor, ActorKind
from wcl_replay.ui.controller import ReplayController
from wcl_replay.ui.main_window import MainWindow
from wcl_replay.ui.map_view import MapView
from wcl_replay.ui.raid_frames import _RaidGrid, frame_rect, layout_columns, order_players, unit_hp


def _click(widget, x: float, y: float) -> None:
    local = QPoint(int(x), int(y))
    press = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        local,
        widget.mapToGlobal(local),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(widget, press)


def test_twenty_players_set_the_frame_size_and_thirty_only_narrow_it():
    wide = frame_rect(0, layout_columns(20), 400, 180)
    small = frame_rect(0, layout_columns(8), 400, 180)
    narrow = frame_rect(0, layout_columns(30), 400, 180)
    assert layout_columns(0) == 4
    assert layout_columns(20) == 4
    assert layout_columns(25) == 5
    assert layout_columns(30) == 6
    assert small == wide
    assert narrow[2] < wide[2]
    assert narrow[3] == wide[3]


def test_a_column_fills_downward_before_the_next_column_starts():
    columns = layout_columns(30)
    top = frame_rect(0, columns, 400, 180)
    below = frame_rect(1, columns, 400, 180)
    next_group = frame_rect(5, columns, 400, 180)
    assert below[0] == top[0]
    assert below[1] > top[1]
    assert next_group[1] == top[1]
    assert next_group[0] > top[0]


def test_players_are_ordered_tank_then_healer_then_dps():
    players = [
        Actor(3, "p", "术-格瑞姆巴托", ActorKind.PLAYER, spec_id=265, class_name="WARLOCK"),
        Actor(1, "p", "坦-格瑞姆巴托", ActorKind.PLAYER, spec_id=66, class_name="PALADIN"),
        Actor(2, "p", "奶-格瑞姆巴托", ActorKind.PLAYER, spec_id=105, class_name="DRUID"),
    ]
    assert [actor.id for actor in order_players(players)] == [1, 2, 3]


def test_dead_units_report_empty_health():
    dead = SimpleNamespace(tracks=SimpleNamespace(is_dead=lambda _a, _t: True, pose=lambda _a, _t: None))
    assert unit_hp(dead, 1, 0) == (0.0, True)
    missing = SimpleNamespace(
        tracks=SimpleNamespace(
            is_dead=lambda _a, _t: False,
            pose=lambda _a, _t: SimpleNamespace(hp=0, max_hp=0, hp_frac=0.0),
        )
    )
    assert unit_hp(missing, 1, 0) == (None, False)


def test_clicking_a_frame_selects_that_player_and_clicking_again_clears_it():
    QApplication.instance() or QApplication([])
    ctl = ReplayController()
    tank = Actor(1, "P-1", "坦-格瑞姆巴托", ActorKind.PLAYER, spec_id=66, class_name="PALADIN")
    mage = Actor(2, "P-2", "法-格瑞姆巴托", ActorKind.PLAYER, spec_id=63, class_name="MAGE")

    class Data:
        actors = {1: tank, 2: mage}

        def players(self):
            return [tank, mage]

    ctl.session = SimpleNamespace(
        data=Data(),
        tracks=SimpleNamespace(
            is_dead=lambda _a, _t: False,
            pose=lambda _a, _t: SimpleNamespace(hp=80, max_hp=100, hp_frac=0.8),
        ),
    )
    grid = _RaidGrid(ctl)
    grid.resize(360, 180)
    rect = frame_rect(0, layout_columns(2), grid.width(), grid.height())
    _click(grid, rect[0] + rect[2] / 2, rect[1] + rect[3] / 2)
    assert ctl.selected == tank.id
    _click(grid, rect[0] + rect[2] / 2, rect[1] + rect[3] / 2)
    assert ctl.selected is None


def test_clicking_a_map_unit_uses_the_same_selection():
    QApplication.instance() or QApplication([])
    ctl = ReplayController()
    view = MapView(ctl)
    view._hits = {7: (QPointF(12, 12), 8)}
    view._pick(QPointF(12, 12))
    assert ctl.selected == 7
    view._pick(QPointF(12, 12))
    assert ctl.selected is None
    view._pick(QPointF(80, 80))
    assert ctl.selected is None


def test_a_new_fight_clears_the_selected_unit():
    QApplication.instance() or QApplication([])
    ctl = ReplayController()
    ctl.select(3)
    ctl.set_session(SimpleNamespace(analysis=SimpleNamespace(lanes=[])))
    assert ctl.selected is None


def test_raid_frames_sit_at_the_top_of_the_right_column():
    QApplication.instance() or QApplication([])
    window = MainWindow()
    splitter = window.raid_frames.parentWidget()
    assert isinstance(splitter, QSplitter)
    assert splitter.orientation() == Qt.Orientation.Vertical
    assert splitter.widget(0) is window.raid_frames
    window.close()
