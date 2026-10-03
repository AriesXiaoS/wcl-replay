# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Specialization ids -> class / role, and class colors."""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    TANK = "tank"
    HEALER = "healer"
    DPS = "dps"


CLASS_COLORS: dict[str, str] = {
    "DEATHKNIGHT": "#C41E3A",
    "DEMONHUNTER": "#A330C9",
    "DRUID": "#FF7C0A",
    "EVOKER": "#33937F",
    "HUNTER": "#AAD372",
    "MAGE": "#3FC7EB",
    "MONK": "#00FF98",
    "PALADIN": "#F48CBA",
    "PRIEST": "#FFFFFF",
    "ROGUE": "#FFF468",
    "SHAMAN": "#0070DD",
    "WARLOCK": "#8788EE",
    "WARRIOR": "#C69B6D",
}

# spec id -> (class, spec name, role)
SPECS: dict[int, tuple[str, str, Role]] = {
    250: ("DEATHKNIGHT", "Blood", Role.TANK),
    251: ("DEATHKNIGHT", "Frost", Role.DPS),
    252: ("DEATHKNIGHT", "Unholy", Role.DPS),
    577: ("DEMONHUNTER", "Havoc", Role.DPS),
    581: ("DEMONHUNTER", "Vengeance", Role.TANK),
    1480: ("DEMONHUNTER", "Devourer", Role.DPS),
    102: ("DRUID", "Balance", Role.DPS),
    103: ("DRUID", "Feral", Role.DPS),
    104: ("DRUID", "Guardian", Role.TANK),
    105: ("DRUID", "Restoration", Role.HEALER),
    1467: ("EVOKER", "Devastation", Role.DPS),
    1468: ("EVOKER", "Preservation", Role.HEALER),
    1473: ("EVOKER", "Augmentation", Role.DPS),
    253: ("HUNTER", "Beast Mastery", Role.DPS),
    254: ("HUNTER", "Marksmanship", Role.DPS),
    255: ("HUNTER", "Survival", Role.DPS),
    62: ("MAGE", "Arcane", Role.DPS),
    63: ("MAGE", "Fire", Role.DPS),
    64: ("MAGE", "Frost", Role.DPS),
    268: ("MONK", "Brewmaster", Role.TANK),
    269: ("MONK", "Windwalker", Role.DPS),
    270: ("MONK", "Mistweaver", Role.HEALER),
    65: ("PALADIN", "Holy", Role.HEALER),
    66: ("PALADIN", "Protection", Role.TANK),
    70: ("PALADIN", "Retribution", Role.DPS),
    256: ("PRIEST", "Discipline", Role.HEALER),
    257: ("PRIEST", "Holy", Role.HEALER),
    258: ("PRIEST", "Shadow", Role.DPS),
    259: ("ROGUE", "Assassination", Role.DPS),
    260: ("ROGUE", "Outlaw", Role.DPS),
    261: ("ROGUE", "Subtlety", Role.DPS),
    262: ("SHAMAN", "Elemental", Role.DPS),
    263: ("SHAMAN", "Enhancement", Role.DPS),
    264: ("SHAMAN", "Restoration", Role.HEALER),
    265: ("WARLOCK", "Affliction", Role.DPS),
    266: ("WARLOCK", "Demonology", Role.DPS),
    267: ("WARLOCK", "Destruction", Role.DPS),
    71: ("WARRIOR", "Arms", Role.DPS),
    72: ("WARRIOR", "Fury", Role.DPS),
    73: ("WARRIOR", "Protection", Role.TANK),
}

# WCL "type" strings (class names without spaces) -> combat log class tokens.
WCL_CLASS_TOKENS: dict[str, str] = {
    "DeathKnight": "DEATHKNIGHT",
    "DemonHunter": "DEMONHUNTER",
    "Druid": "DRUID",
    "Evoker": "EVOKER",
    "Hunter": "HUNTER",
    "Mage": "MAGE",
    "Monk": "MONK",
    "Paladin": "PALADIN",
    "Priest": "PRIEST",
    "Rogue": "ROGUE",
    "Shaman": "SHAMAN",
    "Warlock": "WARLOCK",
    "Warrior": "WARRIOR",
}


def class_of_spec(spec_id: int | None) -> str | None:
    spec = SPECS.get(spec_id or 0)
    return spec[0] if spec else None


def role_of_spec(spec_id: int | None) -> Role:
    spec = SPECS.get(spec_id or 0)
    return spec[2] if spec else Role.DPS


def spec_id_for(class_token: str, spec_name: str) -> int | None:
    for sid, (cls, name, _role) in SPECS.items():
        if cls == class_token and name.replace(" ", "").lower() == spec_name.replace(" ", "").lower():
            return sid
    return None


def class_color(class_name: str | None, default: str = "#9aa0a6") -> str:
    return CLASS_COLORS.get(class_name or "", default)
