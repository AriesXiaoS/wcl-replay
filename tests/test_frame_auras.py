# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Raid-frame debuffs are declared by the boss and follow aura apply/remove."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6.QtWidgets import QApplication

from wcl_replay.bosses.base import Analysis
from wcl_replay.bosses.coiled_altar.constants import (
    ENTOMBED,
    FIXATE,
    GREEN_CARRY,
    POSSESSED,
    PURPLE_CARRY,
)
from wcl_replay.bosses.coiled_altar.module import CoiledAltar, CoiledAltarAnalysis
from wcl_replay.core.models import Actor, ActorKind, Event, Fight, FightData, Sample
from wcl_replay.core.tracks import Tracks
from wcl_replay.ui.icon import aura_icon_path, aura_pixmap


def _fight(events: list[Event]) -> FightData:
    player = Actor(1, "P-1", "坦-格瑞姆巴托", ActorKind.PLAYER, class_name="PALADIN")
    return FightData(
        Fight(1, 3429, "盘卷祭坛", 16, 20, 8000, False),
        {1: player},
        events,
        {1: [Sample(0, 0.0, 0.0, 0.0, 100, 100)]},
    )


def test_coiled_altar_lists_the_four_raid_debuffs_and_their_icons():
    QApplication.instance() or QApplication([])
    auras = CoiledAltarAnalysis.frame_auras
    assert [aura.key for aura in auras] == ["entombed", "fixate", "caught", "carry"]
    assert [aura.label for aura in auras] == ["墓缚", "被魂盯", "被魂撞", "搬球"]
    spells = {spell_id for aura in auras for spell_id, _icon in aura.spells}
    assert spells == {ENTOMBED, FIXATE, POSSESSED, GREEN_CARRY, PURPLE_CARRY}
    for aura in auras:
        for _spell_id, stem in aura.spells:
            assert aura_icon_path(stem).is_file()
            pixmap = aura_pixmap(stem)
            assert pixmap is not None and pixmap.width() == 56


def test_tracked_debuffs_are_reported_only_while_they_are_up():
    events = [
        Event(1000, "SPELL_AURA_APPLIED", dst=1, spell_id=ENTOMBED),
        Event(5000, "SPELL_AURA_REMOVED", dst=1, spell_id=ENTOMBED),
        Event(2000, "SPELL_AURA_APPLIED", dst=1, spell_id=PURPLE_CARRY),
        Event(3000, "SPELL_AURA_REMOVED", dst=1, spell_id=PURPLE_CARRY),
        Event(4000, "SPELL_AURA_APPLIED", dst=1, spell_id=POSSESSED),
        Event(4500, "SPELL_AURA_REMOVED", dst=1, spell_id=POSSESSED),
    ]
    data = _fight(events)
    analysis = CoiledAltar().analyze(data, Tracks(data))
    assert analysis.active_frame_auras(1, 2500) == (
        ("entombed", "ability_demonhunter_shatteredsouls"),
        ("carry", "ability_creature_disease_03"),
    )
    assert analysis.active_frame_auras(1, 4200) == (
        ("entombed", "ability_demonhunter_shatteredsouls"),
        ("caught", "spell_nzinsanity_fearofdeath"),
    )
    assert analysis.active_frame_auras(1, 6000) == ()
    assert analysis.active_frame_auras(9, 2500) == ()


def test_a_generic_fight_offers_no_raid_debuffs():
    data = _fight([])
    analysis = Analysis(data, Tracks(data))
    assert analysis.frame_auras == ()
    assert analysis.active_frame_auras(1, 1000) == ()
