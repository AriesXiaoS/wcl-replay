# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Parse a single encounter of a WoWCombatLog (advanced logging) into FightData."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ...core.markers import clip_world_markers
from ...core.models import (
    FLAG_CONTROL_PLAYER,
    FLAG_REACTION_HOSTILE,
    Actor,
    ActorKind,
    Event,
    Fight,
    FightData,
    MapInfo,
    Sample,
)
from ...core.specs import class_of_spec
from .fields import split_fields
from .index import EncounterEntry, marker_events
from .timestamps import label_of, parse_ts_ms

NO_GUID = ("0000000000000000", "nil", "")

# Event suffixes that carry the advanced parameter block.
_ADV_SUFFIXES = (
    "_DAMAGE",
    "_DAMAGE_LANDED",
    "_HEAL",
    "_CAST_SUCCESS",
    "_ENERGIZE",
    "_DRAIN",
    "_LEECH",
    "_DAMAGE_SUPPORT",
    "_HEAL_SUPPORT",
    "_DAMAGE_LANDED_SUPPORT",
)
_AMOUNT_SUFFIXES = (
    "_DAMAGE",
    "_DAMAGE_LANDED",
    "_HEAL",
    "_DAMAGE_SUPPORT",
    "_HEAL_SUPPORT",
    "_DAMAGE_LANDED_SUPPORT",
    "_ENERGIZE",
)
# Offsets inside the advanced block (relative to infoGUID).
ADV_OWNER, ADV_HP, ADV_MAXHP, ADV_X, ADV_Y, ADV_MAP, ADV_FACING = 1, 2, 3, 14, 15, 16, 17
ADV_LEN = 19


def advanced_base(event: str) -> int | None:
    """Index of the advanced block's infoGUID for an event type, or None if it has none."""
    if event.startswith("SWING_") or event.startswith("ENVIRONMENTAL_"):
        base = 9
    elif event.startswith(("SPELL_", "RANGE_")) or event in ("DAMAGE_SPLIT", "DAMAGE_SHIELD"):
        base = 12
    else:
        return None
    if event in ("DAMAGE_SPLIT", "DAMAGE_SHIELD") or event.endswith(_ADV_SUFFIXES):
        return base
    return None


def parse_map_change(line: str) -> MapInfo | None:
    _ts, _, rest = line.partition("  ")
    f = split_fields(rest)
    if len(f) < 7 or f[0] != "MAP_CHANGE":
        return None
    try:
        x0, x1, y0, y1 = (float(v) for v in f[3:7])
        return MapInfo(int(f[1]), f[2], max(x0, x1), min(x0, x1), max(y0, y1), min(y0, y1))
    except ValueError:
        return None


def _flags(s: str) -> int:
    try:
        return int(s, 16)
    except ValueError:
        return 0


def _npc_id(guid: str) -> int | None:
    if guid.startswith(("Creature-", "Vehicle-", "Pet-")):
        parts = guid.split("-")
        if len(parts) > 5:
            try:
                return int(parts[5])
            except ValueError:
                return None
    return None


class _Builder:
    def __init__(self) -> None:
        self.by_guid: dict[str, int] = {}
        self.actors: dict[int, Actor] = {}
        self.samples: dict[int, list[Sample]] = {}

    def actor(self, guid: str, name: str = "", flags: int = 0) -> int:
        if guid in NO_GUID:
            return -1
        aid = self.by_guid.get(guid)
        if aid is None:
            aid = len(self.by_guid)
            self.by_guid[guid] = aid
            if guid.startswith("Player-"):
                kind = ActorKind.PLAYER
            elif guid.startswith("Pet-") or (flags & FLAG_CONTROL_PLAYER):
                kind = ActorKind.PET
            else:
                kind = ActorKind.NPC
            self.actors[aid] = Actor(
                id=aid,
                guid=guid,
                name=name if name != "nil" else "",
                kind=kind,
                npc_id=_npc_id(guid),
                hostile=bool(flags & FLAG_REACTION_HOSTILE),
            )
        else:
            a = self.actors[aid]
            if not a.name and name and name != "nil":
                a.name = name
            if flags & FLAG_REACTION_HOSTILE:
                a.hostile = True
        return aid

    def sample(self, f: list[str], base: int, t: int) -> None:
        if len(f) < base + ADV_LEN:
            return
        guid = f[base]
        if guid in NO_GUID:
            return
        try:
            x = float(f[base + ADV_X])
            y = float(f[base + ADV_Y])
            facing = float(f[base + ADV_FACING])
            hp = int(f[base + ADV_HP])
            max_hp = int(f[base + ADV_MAXHP])
        except ValueError:
            return
        aid = self.actor(guid)
        owner = f[base + ADV_OWNER]
        if owner not in NO_GUID:
            a = self.actors[aid]
            if a.owner_id is None:
                a.owner_id = self.actor(owner)
                if a.kind is ActorKind.NPC and owner.startswith("Player-"):
                    a.kind = ActorKind.PET
        lst = self.samples.setdefault(aid, [])
        if lst and lst[-1].t == t and lst[-1].x == x and lst[-1].y == y:
            return
        lst.append(Sample(t, x, y, facing, hp, max_hp))


def _int(s: str) -> int:
    try:
        return int(s)
    except ValueError:
        return 0


def parse_encounter(
    path: str | Path,
    entry: EncounterEntry,
    progress: Callable[[float], None] | None = None,
) -> FightData:
    with open(path, "rb") as fh:
        fh.seek(entry.start_offset)
        raw = fh.read(entry.end_offset - entry.start_offset)
    lines = raw.decode("utf-8", "replace").splitlines()
    del raw

    b = _Builder()
    events: list[Event] = []
    map_info = parse_map_change(entry.map_line) if entry.map_line else None
    start_ms: int | None = None
    last_t = 0
    n = len(lines)
    step = max(1, n // 100)

    for li, line in enumerate(lines):
        if progress and li % step == 0:
            progress(li / n)
        ts, sep, rest = line.partition("  ")
        if not sep:
            continue
        try:
            abs_ms = parse_ts_ms(ts)
        except ValueError:
            continue
        if start_ms is None:
            start_ms = abs_ms
        t = abs_ms - start_ms
        if t > last_t:
            last_t = t
        ev = rest[: rest.find(",")] if "," in rest else rest

        if ev == "COMBATANT_INFO":
            head = rest.split(",[", 1)[0].split(",")
            aid = b.actor(head[1])
            spec = _int(head[-1])
            if aid >= 0 and spec:
                a = b.actors[aid]
                a.spec_id = spec
                a.class_name = class_of_spec(spec)
            continue
        if ev == "MAP_CHANGE":
            mi = parse_map_change(line)
            if mi:
                map_info = mi
            continue
        if ev in ("ENCOUNTER_START", "ENCOUNTER_END", "ZONE_CHANGE", "COMBAT_LOG_VERSION", "EMOTE"):
            continue

        f = split_fields(rest)
        if len(f) < 9:
            continue
        src = b.actor(f[1], f[2], _flags(f[3]))
        dst = b.actor(f[5], f[6], _flags(f[7]))
        e = Event(t=t, type=ev, src=src, dst=dst)
        if len(f) > 11 and (ev.startswith(("SPELL_", "RANGE_")) or ev in ("DAMAGE_SPLIT", "DAMAGE_SHIELD")):
            e.spell_id = _int(f[9])
            e.spell_name = f[10]

        base = advanced_base(ev)
        if base is not None:
            b.sample(f, base, t)
            if ev.endswith(_AMOUNT_SUFFIXES) and len(f) > base + ADV_LEN:
                e.amount = _int(f[base + ADV_LEN])

        if ev.startswith("SPELL_AURA"):
            if len(f) > 12:
                e.extra = f[12]
            if ev.endswith("_DOSE") and len(f) > 13:
                e.amount = _int(f[13])
        elif ev in ("SPELL_INTERRUPT", "SPELL_DISPEL", "SPELL_STOLEN"):
            if len(f) > 12:
                e.extra = _int(f[12])
        elif ev.endswith("_MISSED") or ev == "SPELL_CAST_FAILED":
            if len(f) > 12:
                e.extra = f[12]
        elif ev == "SPELL_SUMMON" and dst >= 0 and src >= 0:
            a = b.actors[dst]
            if a.owner_id is None:
                a.owner_id = src
                if b.actors[src].kind is ActorKind.PLAYER:
                    a.kind = ActorKind.PET
        events.append(e)

    events.sort(key=lambda ev_: ev_.t)
    if progress:
        progress(1.0)
    fight = Fight(
        id=entry.seq,
        encounter_id=entry.encounter_id,
        name=entry.name,
        difficulty=entry.difficulty,
        group_size=entry.group_size,
        duration_ms=entry.duration_ms or last_t,
        kill=entry.kill,
        start_label=label_of(entry.start_ts),
        pull_number=entry.pull_number,
        instance_id=entry.instance_id,
    )
    markers = clip_world_markers(
        marker_events(path),
        parse_ts_ms(entry.start_ts),
        fight.duration_ms,
        entry.instance_id,
    )
    return FightData(
        fight=fight,
        actors=b.actors,
        events=events,
        samples=b.samples,
        source=f"local:{Path(path).name}",
        map_info=map_info,
        markers=markers,
    )
