# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Source-independent data model shared by the local log parser, the WCL API client and boss modules.

Coordinates are WoW world coordinates in yards, in the order the combat log writes them
(``x`` grows to the north, ``y`` grows to the west). Times are milliseconds since the fight start.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ActorKind(StrEnum):
    PLAYER = "player"
    NPC = "npc"
    PET = "pet"


# Unit flag bits written by the combat log (COMBATLOG_OBJECT_*).
FLAG_REACTION_HOSTILE = 0x00000040
FLAG_CONTROL_PLAYER = 0x00000100


@dataclass(slots=True)
class Actor:
    id: int
    guid: str
    name: str
    kind: ActorKind
    npc_id: int | None = None
    spec_id: int | None = None
    class_name: str | None = None
    owner_id: int | None = None
    hostile: bool = False

    @property
    def short_name(self) -> str:
        if self.kind is ActorKind.PLAYER and "-" in self.name:
            return self.name.split("-", 1)[0]
        return self.name

    @property
    def is_player(self) -> bool:
        return self.kind is ActorKind.PLAYER


@dataclass(slots=True)
class Event:
    t: int
    type: str
    src: int = -1
    dst: int = -1
    spell_id: int = 0
    spell_name: str = ""
    # Damage/heal amount, or the stack count for *_DOSE aura events.
    amount: int = 0
    # Event specific: aura type ("BUFF"/"DEBUFF"), interrupted spell id, miss type, ...
    extra: str | int | None = None


@dataclass(slots=True)
class Sample:
    """One positional/resource snapshot of a unit."""

    t: int
    x: float
    y: float
    facing: float
    hp: int
    max_hp: int


@dataclass(slots=True)
class Fight:
    id: int
    encounter_id: int
    name: str
    difficulty: int
    group_size: int
    duration_ms: int
    kill: bool
    start_label: str = ""
    pull_number: int = 0
    instance_id: int = 0


@dataclass(slots=True)
class MapInfo:
    """uiMap bounds from MAP_CHANGE: x spans [x_min, x_max], y spans [y_min, y_max]."""

    map_id: int
    name: str
    x_max: float
    x_min: float
    y_max: float
    y_min: float


@dataclass(slots=True)
class WorldMarker:
    """One raid world-marker beam (光柱) while it is up.

    ``start`` is inclusive and ``end`` is exclusive, both milliseconds from the fight start.
    ``index`` is the combat-log marker id, 0–7, matching ``/wm`` 1–8.
    """

    index: int
    x: float
    y: float
    start: int
    end: int


@dataclass(slots=True)
class FightData:
    fight: Fight
    actors: dict[int, Actor]
    events: list[Event]
    samples: dict[int, list[Sample]]
    source: str = ""
    map_info: MapInfo | None = None
    # Phase starts supplied by the data source (WCL), as (t, name).
    phases: list[tuple[int, str]] = field(default_factory=list)
    markers: list[WorldMarker] = field(default_factory=list)

    def players(self) -> list[Actor]:
        return [a for a in self.actors.values() if a.kind is ActorKind.PLAYER]

    def actors_by_npc(self, *npc_ids: int) -> list[Actor]:
        ids = set(npc_ids)
        return [a for a in self.actors.values() if a.npc_id in ids]

    def markers_at(self, t: float) -> list[WorldMarker]:
        return [m for m in self.markers if m.start <= t < m.end]

    def events_of(self, *types: str, spell_ids: set[int] | None = None) -> list[Event]:
        ts = set(types)
        return [
            e
            for e in self.events
            if (not ts or e.type in ts) and (spell_ids is None or e.spell_id in spell_ids)
        ]
