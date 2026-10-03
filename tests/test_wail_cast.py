# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Wail of Terror's bar follows Curse of Tongues the way the client cast bar does."""

from __future__ import annotations

from wcl_replay.bosses.coiled_altar.constants import WAIL_CAST_MS, WAIL_CAST_MS_DEFAULT
from wcl_replay.bosses.coiled_altar.p2 import wail_remaining_ms

MYTHIC = WAIL_CAST_MS[16]


def test_difficulty_cast_times():
    assert WAIL_CAST_MS[16] == 10_000
    assert WAIL_CAST_MS[15] == 12_000
    assert WAIL_CAST_MS_DEFAULT == 15_000


def test_uncursed_bar_counts_down_the_base_cast():
    assert wail_remaining_ms(0, 0, MYTHIC, []) == 10_000
    assert wail_remaining_ms(0, 4_000, MYTHIC, []) == 6_000


def test_curse_from_the_start_stretches_the_whole_bar():
    cursed = [(0, 60_000)]
    assert wail_remaining_ms(0, 0, MYTHIC, cursed) == 13_000
    assert wail_remaining_ms(0, 6_500, MYTHIC, cursed) == 6_500


def test_applying_curse_mid_cast_lengthens_only_what_is_left():
    cursed = [(5_000, 60_000)]
    assert wail_remaining_ms(0, 4_999, MYTHIC, cursed) == 5_001
    assert wail_remaining_ms(0, 5_000, MYTHIC, cursed) == 6_500


def test_dispelling_curse_mid_cast_shortens_only_what_is_left():
    cursed = [(0, 6_500)]
    just_before = wail_remaining_ms(0, 6_499, MYTHIC, cursed)
    assert just_before == 6_501
    assert wail_remaining_ms(0, 6_500, MYTHIC, cursed) == 5_000
