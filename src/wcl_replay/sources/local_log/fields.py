# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Comma separated field splitting for combat log lines (names are quoted and may contain commas)."""

from __future__ import annotations


def split_fields(s: str) -> list[str]:
    if '"' not in s:
        return s.split(",")
    segs = s.split('"')
    for i in range(1, len(segs), 2):
        if "," in segs[i]:
            return _split_slow(s)
    return "".join(segs).split(",")


def _split_slow(s: str) -> list[str]:
    out: list[str] = []
    cur: list[str] = []
    in_quote = False
    for ch in s:
        if ch == '"':
            in_quote = not in_quote
        elif ch == "," and not in_quote:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return out
