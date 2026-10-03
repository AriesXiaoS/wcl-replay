# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Qt-free glue: FightData -> tracks -> boss analysis."""

from __future__ import annotations

from collections.abc import Callable

from .bosses import module_for
from .bosses.base import Analysis
from .core.models import FightData
from .core.tracks import Tracks


def analyze(data: FightData, progress: Callable[[float, str], None] | None = None) -> tuple[Tracks, Analysis]:
    if progress:
        progress(0.9, "构建坐标轨迹")
    tracks = Tracks(data)
    module = module_for(data.fight.encounter_id)
    if progress:
        progress(0.95, f"分析机制（{module.name}）")
    analysis = module.analyze(data, tracks)
    return tracks, analysis
