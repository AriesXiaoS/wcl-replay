# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Which WCL event streams the Coiled Altar replay needs.

Player positions and facing come from damage taken, healing received, and the player's own
casts. Boss and add casts come from enemy casts. Floor orbs are not in the fight's enemy
list, so their spawn and coordinates are a name filter. Soul fixate is a debuff.
"""

from __future__ import annotations

from ..base import WclSlice
from . import constants as C

# Auras the phase models read. Casts themselves are in the enemy-cast stream.
_AURAS = (
    C.GREEN_CARRY,
    C.PURPLE_CARRY,
    C.RUPTURE,
    C.GUILLOTINE,
    C.GRIM_GUILLOTINE,
    C.SEVER_DEBUFF,
    C.POSSESSED,
    C.FIXATE,
    C.SOUL_SEVER_GHOST,
    C.GLOOMBOMB,
    C.RESONANCE,
    C.RESONANCE_MARK,
    C.WAIL,
    C.TONGUES,
    C.SOUL_SHIELD,
    C.ENTOMBED,
)

_ORB_NAMES = ("凝结的毒液追踪者", "烈毒变异体")


def _or(*parts: str) -> str:
    return " or ".join(parts)


def slices(report: dict, fight: dict) -> tuple[WclSlice, ...]:
    del report, fight
    auras = _or(*(f"ability.id = {spell_id}" for spell_id in _AURAS))
    orbs = _or(
        *(part for name in _ORB_NAMES for part in (f'source.name = "{name}"', f'target.name = "{name}"'))
    )
    return (
        WclSlice("敌方施法", "Casts", hostility="Enemies", resources=True),
        WclSlice("机制光环", "Debuffs", filter=auras),
        WclSlice("机制增益", "Buffs", filter=auras),
        WclSlice("死亡", "Deaths"),
        WclSlice("召唤", "Summons"),
        WclSlice("打断", "Interrupts"),
        WclSlice("毒液球", "All", resources=True, filter=orbs),
        WclSlice("玩家受伤", "DamageTaken", hostility="Friendlies", resources=True),
        WclSlice("玩家治疗", "Healing", hostility="Friendlies", resources=True),
        WclSlice("玩家施法", "Casts", hostility="Friendlies", resources=True),
    )
