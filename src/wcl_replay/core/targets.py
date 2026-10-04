# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Who each unit was acting on, inferred from the combat log.

The log has no current-target field. A cast, a swing, or a direct heal that names
another unit is treated as a retarget, and that choice sticks until the next one.
"""

from __future__ import annotations

import bisect

from .models import FightData

# Periodic damage and heals keep ticking whoever was dotted; they are not a new target.
_TARGET_EVENTS = frozenset(
    {
        "SPELL_CAST_START",
        "SPELL_CAST_SUCCESS",
        "SWING_DAMAGE",
        "SWING_MISSED",
        "RANGE_DAMAGE",
        "RANGE_MISSED",
        "SPELL_HEAL",
    }
)


class Targets:
    """Per-actor target changes, in time order."""

    def __init__(self, data: FightData) -> None:
        times: dict[int, list[int]] = {}
        dests: dict[int, list[int]] = {}
        actors = data.actors
        for event in data.events:
            if event.type not in _TARGET_EVENTS:
                continue
            if event.src < 0 or event.dst < 0 or event.dst == event.src or event.dst not in actors:
                continue
            times.setdefault(event.src, []).append(event.t)
            dests.setdefault(event.src, []).append(event.dst)
        self._times = times
        self._dests = dests

    def at(self, actor_id: int, t: float) -> int | None:
        """The unit this actor was acting on at ``t``, or None before their first such action."""
        stamps = self._times.get(actor_id)
        if not stamps:
            return None
        index = bisect.bisect_right(stamps, t) - 1
        if index < 0:
            return None
        return self._dests[actor_id][index]
