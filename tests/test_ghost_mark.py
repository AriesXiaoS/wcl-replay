# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""A ghost icon shows the first character of the player it is fixating."""

from __future__ import annotations

from wcl_replay.bosses.base import Interval
from wcl_replay.bosses.coiled_altar.p2 import Ghost
from wcl_replay.core.models import Actor, ActorKind, Fight, FightData
from wcl_replay.core.specs import CLASS_COLORS


def test_ghost_mark_uses_the_fixated_players_initial_and_class_color():
    priest = Actor(1, "Player-1", "潇-格瑞姆巴托", ActorKind.PLAYER, class_name="PRIEST")
    rogue = Actor(2, "Player-2", "影", ActorKind.PLAYER, class_name="ROGUE")
    data = FightData(Fight(1, 3429, "盘卷祭坛", 16, 20, 10000, False), {1: priest, 2: rogue}, [], {})
    ghost = Ghost(9, 0, 0.0, 0.0, 10000, fixates=[Interval(0, 4000, actor=1), Interval(4000, 8000, actor=2)])

    assert ghost.mark_at(data, 1000) == ("潇", CLASS_COLORS["PRIEST"])
    assert ghost.mark_at(data, 4000) == ("影", CLASS_COLORS["ROGUE"])
    assert ghost.mark_at(data, 8000) is None
