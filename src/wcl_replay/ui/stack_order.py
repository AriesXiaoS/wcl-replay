# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Which kinds of unit draw above which. Order is back-to-front: the last entry is on top."""

from __future__ import annotations

from ..core.models import ActorKind

# Lower sorts further back. Players sit above bosses, orbs sit on the floor.
_RANK = {"orb": 0, "boss": 2, "ghost": 3, "player": 4}


def boss_ids_of(analysis) -> list[int]:
    """Boss actor ids. A stand-in analysis may still only set ``_bosses``."""
    ids = getattr(analysis, "boss_ids", None)
    if ids is None:
        ids = getattr(analysis, "_bosses", ())
    return list(ids)


def stack_group(analysis, aid: int) -> str:
    """Occlusion bucket for one actor. Markers are not actors and never come through here."""
    actor = analysis.data.actors.get(aid)
    if actor is not None and actor.is_player:
        return "player"
    if aid in boss_ids_of(analysis):
        return "boss"
    style = analysis.unit_styles.get(aid)
    if style is not None and style.group:
        return style.group
    npc_id = actor.npc_id if actor is not None else None
    return f"npc:{npc_id}" if npc_id is not None else "npc:?"


def present_stack_groups(analysis) -> list[tuple[str, str]]:
    """Categories that exist in this fight, as ``(id, label)``. Absent kinds are omitted."""
    found: dict[str, str] = {}
    actors = list(analysis.data.actors.values())
    if any(a.is_player for a in actors):
        found["player"] = "玩家"
    bosses = set(boss_ids_of(analysis))
    if bosses:
        found["boss"] = "Boss"
    hidden = set(getattr(analysis, "hidden_npc_ids", ()))
    for style in analysis.unit_styles.values():
        if style.group:
            found.setdefault(
                style.group, "魂" if style.group == "ghost" else style.group_label or style.group
            )
    for actor in actors:
        if actor.kind is not ActorKind.NPC or not actor.hostile or actor.npc_id is None:
            continue
        if actor.id in bosses or actor.npc_id in hidden:
            continue
        style = analysis.unit_styles.get(actor.id)
        if style is not None and style.group:
            continue
        found.setdefault(f"npc:{actor.npc_id}", actor.name)
    for group, label in getattr(analysis, "stack_extras", ()):
        found.setdefault(group, label)
    return list(found.items())


def _rank(group: str) -> tuple:
    if group in _RANK:
        return (_RANK[group], "")
    return (1, group)


def merge_back_to_front(saved: list[str], present: set[str]) -> list[str]:
    """Back-to-front order of the groups in this fight.

    Relative order already saved by the user is kept. A kind that shows up for the first time
    is inserted at its default depth (orbs at the back, players at the front).
    """
    order = [group for group in saved if group in present]
    missing = [group for group in present if group not in order]
    for group in sorted(missing, key=_rank):
        rank = _rank(group)
        at = next((i for i, other in enumerate(order) if _rank(other) > rank), len(order))
        order.insert(at, group)
    return order


def splice_visible(saved: list[str], visible_back_to_front: list[str]) -> list[str]:
    """Write a new visible order back into the saved list, leaving hidden kinds where they were."""
    visible = set(visible_back_to_front)
    merged: list[str] = []
    index = 0
    placed: set[str] = set()
    for group in saved:
        if group in visible:
            merged.append(visible_back_to_front[index])
            placed.add(visible_back_to_front[index])
            index += 1
        elif group not in placed:
            merged.append(group)
            placed.add(group)
    merged.extend(group for group in visible_back_to_front if group not in placed)
    return merged
