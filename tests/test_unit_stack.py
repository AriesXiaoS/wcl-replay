# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Occlusion cards: who draws on top, including orbs and other hostile NPCs."""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from wcl_replay.bosses.base import Dot, UnitStyle
from wcl_replay.core.models import Actor, ActorKind
from wcl_replay.ui.controller import ReplayController
from wcl_replay.ui.main_window import MainWindow
from wcl_replay.ui.map_view import MapView
from wcl_replay.ui.stack_order import merge_back_to_front, present_stack_groups, splice_visible, stack_group
from wcl_replay.ui.stack_panel import UnitStackPanel


def _actors() -> dict[int, Actor]:
    return {
        1: Actor(1, "Player-1", "潇-格瑞姆巴托", ActorKind.PLAYER),
        2: Actor(2, "Creature-1", "祖尔加", ActorKind.NPC, npc_id=1, hostile=True),
        3: Actor(3, "Creature-2", "恐惧具象", ActorKind.NPC, npc_id=261218, hostile=True),
        4: Actor(4, "Creature-3", "怨毒盘魂者", ActorKind.NPC, npc_id=9, hostile=True),
        5: Actor(5, "Creature-4", "凝结的毒液", ActorKind.NPC, npc_id=11, hostile=True),
        6: Actor(6, "Creature-5", "友方", ActorKind.NPC, npc_id=12, hostile=False),
    }


def _analysis():
    actors = _actors()

    class Data:
        def players(self):
            return [actor for actor in actors.values() if actor.is_player]

        def markers_at(self, _t):
            return []

    data = Data()
    data.actors = actors

    class Analysis:
        lanes: list = []
        arena = (-40.0, 40.0, -40.0, 40.0)
        _bosses = [2]
        unit_styles = {3: UnitStyle("#fff", group="ghost")}
        hidden_npc_ids = frozenset({11})
        stack_extras = (("orb", "球"),)

        def in_arena(self, x, y):
            x0, x1, y0, y1 = self.arena
            return x0 <= x <= x1 and y0 <= y <= y1

        def units_at(self, _t):
            return [4, 2]

        def overlays_at(self, _t):
            return [Dot(0, 0, "#ffffff", stack=""), Dot(1, 1, "#22cc55", stack="orb")]

        def hud_at(self, _t):
            return []

        def bars_at(self, _t):
            return []

        def unit_glyph_at(self, _aid, _t):
            return None

    analysis = Analysis()
    analysis.data = data
    return analysis


def _session():
    analysis = _analysis()
    tracks = SimpleNamespace(pose=lambda _aid, _t: None, is_dead=lambda _aid, _t: False)
    return SimpleNamespace(data=analysis.data, analysis=analysis, tracks=tracks)


def test_stack_group_splits_player_boss_ghost_and_each_npc():
    analysis = _analysis()
    assert stack_group(analysis, 1) == "player"
    assert stack_group(analysis, 2) == "boss"
    assert stack_group(analysis, 3) == "ghost"
    assert stack_group(analysis, 4) == "npc:9"


def test_present_groups_skip_hidden_orbs_friendlies_and_markers():
    labels = dict(present_stack_groups(_analysis()))
    assert labels == {"player": "玩家", "boss": "Boss", "ghost": "魂", "npc:9": "怨毒盘魂者", "orb": "球"}
    assert "光柱" not in labels.values()


def test_default_order_keeps_a_saved_arrangement_and_inserts_new_kinds():
    present = {"player", "ghost", "boss", "orb", "npc:9"}
    assert merge_back_to_front([], present) == ["orb", "npc:9", "boss", "ghost", "player"]
    assert merge_back_to_front(["boss", "ghost", "player"], present) == [
        "orb",
        "npc:9",
        "boss",
        "ghost",
        "player",
    ]
    # A fight with no orbs leaves the saved orb slot alone when the user reorders the rest.
    saved = splice_visible(["orb", "boss", "ghost", "player"], ["boss", "player", "ghost"])
    assert saved[0] == "orb"
    assert merge_back_to_front(saved, {"boss", "player", "ghost"})[-1] == "ghost"


def test_nudge_reorders_back_to_front_and_stops_at_the_ends():
    QApplication.instance() or QApplication([])
    ctl = ReplayController()
    assert ctl.unit_order == ["boss", "ghost", "player"]

    ctl.nudge_unit_group("player", toward_front=False)
    assert ctl.unit_order == ["boss", "player", "ghost"]

    ctl.nudge_unit_group("boss", toward_front=False)
    assert ctl.unit_order == ["boss", "player", "ghost"]

    ctl.nudge_unit_group("ghost", toward_front=True)
    assert ctl.unit_order == ["boss", "player", "ghost"]

    ctl.set_unit_order(["player", "orb"])
    assert ctl.unit_order == ["player", "orb"]


def test_pick_up_slides_the_others_and_settles_on_release():
    app = QApplication.instance() or QApplication([])
    ctl = ReplayController()
    panel = UnitStackPanel(ctl, None)
    ctl.set_session(_session())
    panel.show()
    app.processEvents()
    assert panel.findChild(QLabel, "stackTitle").text() == "层级关系"

    top = panel.cards[0]
    home = top.y()
    panel.begin_drag(top, QPoint(8, 4))
    assert top.property("lifted") is True
    panel.shift_held_to(len(panel.cards) - 1)
    assert panel.cards[0].label == "魂"
    assert panel.cards[-1] is top
    assert top.property("lifted") is True
    assert panel.sliding
    assert top.y() == home

    panel.end_drag()
    QTest.qWait(400)
    assert top.property("lifted") is False
    assert top.y() == panel._slot_y(panel.cards.index(top))
    panel.close()


def test_dragging_the_top_card_to_the_bottom_puts_someone_else_in_front():
    QApplication.instance() or QApplication([])
    ctl = ReplayController()
    panel = UnitStackPanel(ctl, None)
    ctl.set_session(_session())
    assert [card.label for card in panel.cards] == ["玩家", "魂", "Boss", "怨毒盘魂者", "球"]
    panel.move_card(0, len(panel.cards) - 1)
    assert panel.cards[0].label == "魂"
    assert ctl.unit_order[-1] == "ghost"


class _MemorySettings:
    def __init__(self):
        self.store: dict[str, str] = {}

    def value(self, key, default=""):
        return self.store.get(key, default)

    def setValue(self, key, value):
        self.store[key] = value


def test_dragged_order_is_restored_for_the_next_panel():
    QApplication.instance() or QApplication([])
    settings = _MemorySettings()
    ctl = ReplayController()
    panel = UnitStackPanel(ctl, settings)
    ctl.set_session(_session())
    panel.move_card(0, len(panel.cards) - 1)
    labels = [card.label for card in panel.cards]

    ctl2 = ReplayController()
    panel2 = UnitStackPanel(ctl2, settings)
    ctl2.set_session(_session())
    assert [card.label for card in panel2.cards] == labels


class _RecordingMap(MapView):
    def paintEvent(self, event) -> None:
        self.drawn: list = []
        super().paintEvent(event)

    def _draw_markers(self, painter, t) -> None:
        self.drawn.append("markers")
        super()._draw_markers(painter, t)

    def _draw_dot(self, painter, prim) -> None:
        self.drawn.append(("dot", prim.stack))
        super()._draw_dot(painter, prim)

    def _draw_npc(self, painter, aid, t, out) -> None:
        self.drawn.append(("unit", stack_group(self.ctl.session.analysis, aid)))
        super()._draw_npc(painter, aid, t, out)

    def _draw_player(self, painter, aid, t, out) -> None:
        self.drawn.append(("unit", "player"))
        super()._draw_player(painter, aid, t, out)


def test_markers_and_loose_dots_stay_under_the_unit_stack():
    app = QApplication.instance() or QApplication([])
    ctl = ReplayController()
    view = _RecordingMap(ctl)
    panel = UnitStackPanel(ctl, None, view)
    view.resize(900, 700)
    view.show()
    ctl.set_session(_session())
    app.processEvents()

    assert view.drawn == [
        "markers",
        ("dot", ""),
        ("dot", "orb"),
        ("unit", "npc:9"),
        ("unit", "boss"),
        ("unit", "player"),
    ]
    assert panel.x() > view.width() / 2
    assert panel.y() > view.height() / 2
    center = panel.mapTo(view, QPoint(panel.width() // 2, panel.height() // 2))
    hit = view.childAt(center)
    assert hit is not None and (hit is panel or panel.isAncestorOf(hit))
    local = hit.mapFrom(view, center)
    press = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        local,
        hit.mapToGlobal(local),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(hit, press)
    assert view._drag is None
    view.close()


def test_stack_panel_floats_on_the_map_instead_of_sitting_under_it():
    QApplication.instance() or QApplication([])
    window = MainWindow()
    assert window.stack_panel.parent() is window.map_view
    column = window.map_view.parentWidget().layout()
    widgets = [column.itemAt(i).widget() for i in range(column.count())]
    assert window.stack_panel not in widgets
    window.close()
