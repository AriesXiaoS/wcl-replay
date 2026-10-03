# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""A tiny hand-built Coiled Altar pull in combat log (advanced logging) format.

Scenario (yards; Zul'jan at the origin, the tank north-west of it at y=+10):
- 1.0 s  wave: green G1 at (5, 10) in front of the boss, green G2 at (0, -5) behind it, purple P1 near the DPS
- 2.0 s  the DPS picks up a purple (carry debuff), 7.0 s it ends and 7.05 s a new purple P2 is dropped at (-2, -10)
- 10/13 s Sever on the tank: everyone gains 1 Rupture stack -> G1 pops
- 20 s   Zul'jan dies -> phase 2, the remaining floor (G2, P2) pops: 2 Rupture stacks
- 24.5 s P3 wave: G3 in front of the tank, P3 off to the side
- 29 s   凋零撕裂: +1 Rupture pops G3, P3 stays
"""

from __future__ import annotations

from pathlib import Path

NONE = "0000000000000000,nil,0x80000000,0x80000000"
NO_GUID = "0000000000000000"

TANK_GUID = "Player-1-0001"
DPS_GUID = "Player-1-0002"
BOSS_GUID = "Creature-0-1-2950-1-257911-0000000001"
MAL_GUID = "Creature-0-1-2950-1-259854-0000000002"
G1_GUID = "Creature-0-1-2950-1-268042-0000000010"
G2_GUID = "Creature-0-1-2950-1-268042-0000000011"
P1_GUID = "Creature-0-1-2950-1-271982-0000000012"
P2_GUID = "Creature-0-1-2950-1-271982-0000000013"
G3_GUID = "Creature-0-1-2950-1-268042-0000000014"
P3_GUID = "Creature-0-1-2950-1-271982-0000000015"

TANK = f'{TANK_GUID},"Tank-Realm",0x512,0x0'
DPS = f'{DPS_GUID},"Dps-Realm",0x512,0x0'
BOSS = f'{BOSS_GUID},"祖尔加",0xa48,0x0'
MAL = f'{MAL_GUID},"玛拉卡斯",0xa48,0x0'
G1 = f'{G1_GUID},"绿色毒液球",0xa48,0x0'
G2 = f'{G2_GUID},"绿色毒液球",0xa48,0x0'
P1 = f'{P1_GUID},"紫色毒液球",0xa48,0x0'
P2 = f'{P2_GUID},"紫色毒液球",0xa48,0x0'
G3 = f'{G3_GUID},"绿色毒液球",0xa48,0x0'
P3 = f'{P3_GUID},"紫色毒液球",0xa48,0x0'


def ts(ms: int) -> str:
    return f"9/28/2026 23:22:{ms // 1000:02d}.{ms % 1000:03d}0"


def adv(guid: str, x: float, y: float, facing: float = 0.0, hp: int = 100, max_hp: int = 100) -> str:
    return ",".join(
        [
            guid,
            NO_GUID,
            str(hp),
            str(max_hp),
            *["0"] * 10,
            f"{x:.2f}",
            f"{y:.2f}",
            "2500",
            f"{facing:.4f}",
            "80",
        ]
    )


def cast(src: str, src_guid: str, spell: int, x: float, y: float, dst: str = NONE) -> str:
    return f'SPELL_CAST_SUCCESS,{src},{dst},{spell},"spell",0x1,{adv(src_guid, x, y)}'


def damage(src: str, dst: str, dst_guid: str, spell: int, x: float, y: float, amount: int = 1000) -> str:
    return f'SPELL_DAMAGE,{src},{dst},{spell},"spell",0x1,{adv(dst_guid, x, y)},{amount},0,-1,1,0,0,0,nil,nil,nil,ST'


def aura(kind: str, src: str, dst: str, spell: int, stacks: int | None = None) -> str:
    s = f'{kind},{src},{dst},{spell},"spell",0x1,DEBUFF'
    return s + (f",{stacks}" if stacks is not None else "")


def lines() -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = [
        (0, 'MAP_CHANGE,2500,"盘卷祭坛",100.0,-100.0,200.0,0.0'),
        (0, 'ENCOUNTER_START,3429,"盘卷祭坛",16,20,2950'),
        (0, f"COMBATANT_INFO,{TANK_GUID},0,1,2,3,250,[(1,2)],(3),[]"),
        (0, f"COMBATANT_INFO,{DPS_GUID},0,1,2,3,62,[(1,2)],(3),[]"),
        (500, cast(BOSS, BOSS_GUID, 1282287, 0.0, 0.0)),
        (500, damage(BOSS, TANK, TANK_GUID, 1, 0.0, 10.0)),
        (500, damage(BOSS, DPS, DPS_GUID, 1282287, 0.0, -10.0)),
        (1000, f'SPELL_CAST_SUCCESS,{BOSS},{NONE},1299960,"剧毒洪流",0x8,{adv(BOSS_GUID, 0.0, 0.0)}'),
    ]
    for t, g, guid, x, y, summon in (
        (1000, G1, G1_GUID, 5.0, 10.0, 1299781),
        (1000, G2, G2_GUID, 0.0, -5.0, 1299781),
        (1000, P1, P1_GUID, -1.0, -9.0, 1310544),
    ):
        out.append((t, f'SPELL_SUMMON,{BOSS},{g},{summon},"spell",0x8'))
        place = 1282403 if summon == 1299781 else 1309742
        out.append((t + 10, cast(g, guid, place, x, y)))
    out += [
        (2000, aura("SPELL_AURA_APPLIED", P1, DPS, 1310498)),
        (7000, aura("SPELL_AURA_REMOVED", P1, DPS, 1310498)),
        (7050, cast(P2, P2_GUID, 1309742, -2.0, -10.0)),
        (8000, damage(BOSS, DPS, DPS_GUID, 1282287, -2.0, -10.0)),
        (10000, f'SPELL_CAST_START,{BOSS},{NONE},1299684,"撕裂",0x1'),
        (13000, cast(BOSS, BOSS_GUID, 1299684, 0.0, 0.0, TANK)),
        (13000, aura("SPELL_AURA_APPLIED", BOSS, TANK, 1301690)),
        (13050, aura("SPELL_AURA_APPLIED", BOSS, TANK, 1299838)),
        (13050, aura("SPELL_AURA_APPLIED", BOSS, DPS, 1299838)),
        (19000, damage(DPS, BOSS, BOSS_GUID, 1, 0.0, 0.0, 5000)),
        (20000, f"UNIT_DIED,{NONE},{BOSS},0"),
        (20100, cast(MAL, MAL_GUID, 1, 0.0, 0.0)),
        (21000, f'SPELL_CAST_START,{MAL},{NONE},1286620,"灵魂撕裂",0x1'),
        (22000, cast(MAL, MAL_GUID, 1286620, 0.0, 0.0, TANK)),
        (22000, damage(MAL, TANK, TANK_GUID, 1286620, 0.0, 10.0)),
        (20100, aura("SPELL_AURA_APPLIED_DOSE", BOSS, TANK, 1299838, 2)),
        (20100, aura("SPELL_AURA_APPLIED_DOSE", BOSS, TANK, 1299838, 3)),
        (20100, aura("SPELL_AURA_APPLIED_DOSE", BOSS, DPS, 1299838, 2)),
        (20100, aura("SPELL_AURA_APPLIED_DOSE", BOSS, DPS, 1299838, 3)),
        (24000, f'SPELL_CAST_SUCCESS,{BOSS},{NONE},1298381,"盘卷祭坛亵渎",0x1,{adv(BOSS_GUID, 0.0, 0.0)}'),
        (24500, f'SPELL_SUMMON,{BOSS},{G3},1299781,"spell",0x8'),
        (24510, cast(G3, G3_GUID, 1282403, 2.0, -18.0)),
        (24500, f'SPELL_SUMMON,{BOSS},{P3},1310544,"spell",0x8'),
        (24510, cast(P3, P3_GUID, 1309742, -8.0, 8.0)),
        (25500, aura("SPELL_AURA_APPLIED", BOSS, DPS, 1299266)),
        (27000, f'SPELL_CAST_START,{BOSS},{NONE},1307292,"凋零撕裂",0x1'),
        (29000, cast(BOSS, BOSS_GUID, 1307292, 0.0, 0.0, TANK)),
        (29050, aura("SPELL_AURA_APPLIED_DOSE", BOSS, TANK, 1299838, 4)),
        (28000, aura("SPELL_AURA_REMOVED", BOSS, DPS, 1299266)),
        (28050, damage(BOSS, DPS, DPS_GUID, 1299301, 0.0, -10.0)),
        (28050, damage(BOSS, TANK, TANK_GUID, 1299301, 1.0, -9.0)),
        (30000, 'ENCOUNTER_END,3429,"盘卷祭坛",16,20,1,30000'),
    ]
    return out


def write_log(path: Path) -> Path:
    body = "\n".join(f"{ts(t)}  {line}" for t, line in sorted(lines(), key=lambda it: it[0]))
    path.write_text(body + "\n", encoding="utf-8")
    return path
