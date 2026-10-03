# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Combat log timestamp parsing ("9/28/2026 23:22:44.2028")."""

from __future__ import annotations

from datetime import datetime
from functools import lru_cache


@lru_cache(maxsize=64)
def _day_ms(date_part: str) -> int:
    month, day, year = date_part.split("/")
    return int(datetime(int(year), int(month), int(day)).timestamp() * 1000)


def parse_ts_ms(ts: str) -> int:
    """Absolute milliseconds (local time epoch) of a combat log timestamp."""
    date_part, _, time_part = ts.partition(" ")
    hh, mm, rest = time_part.split(":")
    sec, _, frac = rest.partition(".")
    ms = int((frac + "000")[:3]) if frac else 0
    return _day_ms(date_part) + ((int(hh) * 60 + int(mm)) * 60 + int(sec)) * 1000 + ms


def label_of(ts: str) -> str:
    """'9/28/2026 23:22:44.2028' -> '2026/9/28 23:22'."""
    date_part, _, time_part = ts.partition(" ")
    month, day, year = date_part.split("/")
    return f"{year}/{month}/{day} {time_part[:5]}"
