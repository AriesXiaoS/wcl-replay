# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Qt-free glue: FightData -> tracks -> boss analysis."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from .bosses import module_for
from .bosses.base import Analysis, LogEntry, ParameterValue, Seg
from .bosses.registry import discovery_errors
from .core.models import FightData
from .core.tracks import Tracks


def analyze(
    data: FightData,
    progress: Callable[[float, str], None] | None = None,
    *,
    parameters: Mapping[str, ParameterValue] | None = None,
) -> tuple[Tracks, Analysis]:
    if progress:
        progress(0.9, "构建坐标轨迹")
    tracks = Tracks(data)
    module = module_for(data.fight.encounter_id)
    if progress:
        progress(0.95, f"分析机制（{module.name}）")
    analysis = module.analyze(data, tracks)
    analysis.apply_parameters(parameters if parameters is not None else analysis.parameter_values)
    for name, error in discovery_errors().items():
        analysis.log.append(
            LogEntry(
                0,
                [
                    Seg("模块提示", "#e0a030", badge=True),
                    Seg(f" 首领模块 {name} 加载失败：{error}；未注册的遭遇战使用通用回放"),
                ],
            )
        )
    analysis.refresh_indexes()
    return tracks, analysis
