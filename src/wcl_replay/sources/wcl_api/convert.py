# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Convert WCL API v2 report metadata + event JSON into the shared FightData model.

WCL differences from the raw combat log handled here:
- NPCs of one kind share an actor id and are told apart by ``sourceInstance`` / ``targetInstance``,
- positions are integers in hundredths of a yard, facing in hundredths of a radian,
- the API field ``y`` is north; ``x`` is east, so west is its negation. Facing is a
  standard math angle (0 = east), converted to the combat log's north-based facing,
- raid difficulty is 1/3/4/5 rather than the combat log's 17/14/15/16,
- ``resourceActor`` says whether the resource/position fields describe the source (1) or target (2).
"""

from __future__ import annotations

import math
from datetime import datetime

from ...core.models import Actor, ActorKind, Event, Fight, FightData, Sample
from ...core.specs import WCL_CLASS_TOKENS, class_of_spec, spec_id_for

COORD_SCALE = 100.0
# WCL raid difficulty -> ENCOUNTER_START difficulty used by the rest of the app.
_WCL_RAID_DIFFICULTY = {1: 17, 3: 14, 4: 15, 5: 16}

TYPE_MAP = {
    "cast": "SPELL_CAST_SUCCESS",
    "begincast": "SPELL_CAST_START",
    "damage": "SPELL_DAMAGE",
    "heal": "SPELL_HEAL",
    "absorbed": "SPELL_ABSORBED",
    "applybuff": "SPELL_AURA_APPLIED",
    "applydebuff": "SPELL_AURA_APPLIED",
    "removebuff": "SPELL_AURA_REMOVED",
    "removedebuff": "SPELL_AURA_REMOVED",
    "applybuffstack": "SPELL_AURA_APPLIED_DOSE",
    "applydebuffstack": "SPELL_AURA_APPLIED_DOSE",
    "removebuffstack": "SPELL_AURA_REMOVED_DOSE",
    "removedebuffstack": "SPELL_AURA_REMOVED_DOSE",
    "refreshbuff": "SPELL_AURA_REFRESH",
    "refreshdebuff": "SPELL_AURA_REFRESH",
    "summon": "SPELL_SUMMON",
    "death": "UNIT_DIED",
    "destroy": "UNIT_DESTROYED",
    "interrupt": "SPELL_INTERRUPT",
    "dispel": "SPELL_DISPEL",
    "energize": "SPELL_ENERGIZE",
    "resourcechange": "SPELL_ENERGIZE",
    "instakill": "SPELL_INSTAKILL",
}
# hitType values of damage events that did not land (dodge/parry/miss/immune/...).
MISS_HIT_TYPES = {0, 7, 8, 9, 10, 11, 12, 13, 14}


def log_difficulty(value: int) -> int:
    """Combat-log difficulty. Values the API already stores as 14/15/16/17 pass through."""
    return _WCL_RAID_DIFFICULTY.get(value, value)


def fight_from_meta(report: dict, f: dict, pull_number: int = 0) -> Fight:
    start = (report.get("startTime") or 0) + (f.get("startTime") or 0)
    d = datetime.fromtimestamp(start / 1000)
    label = f"{d.year}/{d.month}/{d.day} {d:%H:%M}"
    return Fight(
        id=int(f["id"]),
        encounter_id=int(f.get("encounterID") or 0),
        name=f.get("name") or "",
        difficulty=log_difficulty(int(f.get("difficulty") or 0)),
        group_size=int(f.get("size") or 0),
        duration_ms=int((f.get("endTime") or 0) - (f.get("startTime") or 0)),
        kill=bool(f.get("kill")),
        start_label=label,
        pull_number=pull_number,
    )


def pull_numbers(fights: list[dict]) -> dict[int, int]:
    counts: dict[tuple[int, int], int] = {}
    out = {}
    for f in sorted(fights, key=lambda f_: f_.get("startTime") or 0):
        if not f.get("encounterID"):
            continue
        key = (f["encounterID"], f.get("difficulty") or 0)
        counts[key] = counts.get(key, 0) + 1
        out[int(f["id"])] = counts[key]
    return out


class _Actors:
    def __init__(self, report: dict, fight: dict):
        md = report.get("masterData") or {}
        self.meta = {int(a["id"]): a for a in md.get("actors") or []}
        self.hostile = {int(n["id"]) for n in (fight.get("enemyNPCs") or []) + (fight.get("enemyPets") or [])}
        self.keys: dict[tuple[int, int], int] = {}
        self.actors: dict[int, Actor] = {}

    def get(self, wid: int | None, inst: int | None) -> int:
        if wid is None or wid < 0:
            return -1
        meta = self.meta.get(wid, {})
        kind = {"Player": ActorKind.PLAYER, "Pet": ActorKind.PET}.get(meta.get("type", ""), ActorKind.NPC)
        key = (wid, 0 if kind is ActorKind.PLAYER else (inst or 1))
        aid = self.keys.get(key)
        if aid is not None:
            return aid
        aid = len(self.keys)
        self.keys[key] = aid
        name = meta.get("name") or f"#{wid}"
        server = meta.get("server")
        if kind is ActorKind.PLAYER and server:
            name = f"{name}-{server}"
        a = Actor(id=aid, guid=f"wcl-{wid}-{key[1]}", name=name, kind=kind, hostile=wid in self.hostile)
        if kind is ActorKind.PLAYER:
            token = WCL_CLASS_TOKENS.get(meta.get("subType", ""))
            a.class_name = token
            icon = meta.get("icon") or ""
            if token and "-" in icon:
                a.spec_id = spec_id_for(token, icon.split("-", 1)[1])
        else:
            a.npc_id = int(meta["gameID"]) if meta.get("gameID") else None
        self.actors[aid] = a
        owner = meta.get("petOwner")
        if owner is not None and kind is not ActorKind.PLAYER:
            a.owner_id = self.get(int(owner), None)
        return aid


def convert(
    report: dict, fight: dict, raw_events: list[dict], pull_number: int = 0, source: str = ""
) -> FightData:
    f0 = fight.get("startTime") or 0
    actors = _Actors(report, fight)
    spells = {
        int(a["gameID"]): a.get("name") or "" for a in (report.get("masterData") or {}).get("abilities") or []
    }
    events: list[Event] = []
    samples: dict[int, list[Sample]] = {}
    diagnostics: dict[str, int] = {}

    def diagnose(message: str) -> None:
        diagnostics[message] = diagnostics.get(message, 0) + 1

    for ev in raw_events:
        typ = ev.get("type", "")
        try:
            t = int(ev.get("timestamp", f0) - f0)
        except (ValueError, TypeError, OverflowError):
            diagnose("已忽略无效事件时间")
            continue
        if not 0 <= t <= 2**63 - 1:
            diagnose("已忽略无效事件时间")
            continue
        src = actors.get(ev.get("sourceID"), ev.get("sourceInstance"))
        dst = actors.get(ev.get("targetID"), ev.get("targetInstance"))
        if typ == "combatantinfo":
            sid = ev.get("specID")
            if src >= 0 and sid:
                a = actors.actors[src]
                a.spec_id = int(sid)
                a.class_name = class_of_spec(a.spec_id) or a.class_name
            continue
        if typ in ("encounterstart", "encounterend", "zonechange", "mapchange"):
            continue
        spell = int(ev.get("abilityGameID") or 0)
        name = TYPE_MAP.get(typ, typ.upper())
        if typ in ("heal", "damage") and ev.get("tick"):
            name = "SPELL_PERIODIC_HEAL" if typ == "heal" else "SPELL_PERIODIC_DAMAGE"
        if typ == "damage":
            if spell == 1:
                name = "SWING_DAMAGE"
            elif ev.get("hitType") in MISS_HIT_TYPES and not ev.get("amount"):
                name = "SPELL_MISSED"
        e = Event(t=t, type=name, src=src, dst=dst, spell_id=spell, spell_name=spells.get(spell, ""))
        if typ in ("damage", "heal", "energize", "resourcechange"):
            e.amount = int(ev.get("amount") or ev.get("resourceChange") or 0)
        elif "stack" in typ:
            e.amount = int(ev.get("stack") or 0)
        if name.startswith("SPELL_AURA"):
            e.extra = "DEBUFF" if "debuff" in typ else "BUFF"
        elif typ in ("interrupt", "dispel"):
            e.extra = int(ev.get("extraAbilityGameID") or 0)
        elif typ == "summon" and src >= 0 and dst >= 0:
            a = actors.actors[dst]
            if a.owner_id is None:
                a.owner_id = src
                if actors.actors[src].kind is ActorKind.PLAYER:
                    a.kind = ActorKind.PET
        if typ == "death" and dst < 0:
            e.dst = src
        events.append(e)

        if "x" in ev and "y" in ev:
            who = ev.get("resourceActor", 2 if typ in ("damage", "heal") else 1)
            aid = src if who == 1 else dst if who == 2 else -1
            if aid >= 0:
                # API y is north. API x points east, so west is the negation. Facing is a math
                # angle (0 = east, hundredths of a radian); the combat log measures it from north.
                try:
                    sample = Sample(
                        t,
                        float(ev["y"]) / COORD_SCALE,
                        -float(ev["x"]) / COORD_SCALE,
                        -float(ev.get("facing") or 0) / COORD_SCALE - math.pi / 2,
                        int(ev.get("hitPoints") or 0),
                        int(ev.get("maxHitPoints") or 0),
                    )
                except (ValueError, TypeError, OverflowError):
                    diagnose("已忽略格式错误的坐标样本")
                    continue
                if not sample.valid():
                    diagnose("已忽略无效坐标或资源样本")
                    continue
                lst = samples.setdefault(aid, [])
                if not lst or lst[-1] != sample:
                    lst.append(sample)

    events.sort(key=lambda e_: e_.t)
    for lst in samples.values():
        lst.sort(key=lambda s: s.t)
    return FightData(
        fight=fight_from_meta(report, fight, pull_number),
        actors=actors.actors,
        events=events,
        samples=samples,
        source=source,
        diagnostics=diagnostics,
    )
