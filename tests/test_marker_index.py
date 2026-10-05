# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from __future__ import annotations

import random

import pytest

from wcl_replay.core.cancellation import TaskCancelled, cancel_check
from wcl_replay.core.markers import MarkerEvent, clip_world_markers, clip_world_markers_many
from wcl_replay.core.models import WorldMarker


def test_batch_markers_keep_instance_context_and_exact_fight_boundaries():
    events = [
        MarkerEvent(1, 100, True, 0, 1, 2, 10),
        MarkerEvent(2, 200, True, 0, 3, 4, 20),
        MarkerEvent(3, 300, False, 0, zone_unload=True),
        MarkerEvent(4, 600, False, 0),
        MarkerEvent(5, 600, True, 1, 5, 6, 10),
        MarkerEvent(6, 700, True, 1, 7, 8, 10),
        MarkerEvent(7, 800, False, 1),
    ]
    fights = [(400, 200, 10), (400, 200, 20), (600, 200, 10), (800, 200, 10), (400, 200, 30)]
    assert clip_world_markers_many(events, fights) == [
        [WorldMarker(0, 1, 2, 0, 200)],
        [WorldMarker(0, 3, 4, 0, 200)],
        [WorldMarker(1, 5, 6, 0, 100), WorldMarker(1, 7, 8, 100, 200)],
        [],
        [],
    ]


@pytest.mark.parametrize("removed_last", [False, True])
def test_batch_marker_ties_follow_supplied_order_without_offsets(removed_last):
    placed = MarkerEvent(0, 1000, True, 0, 10, 20, 5)
    removed = MarkerEvent(0, 1000, False, 0)
    events = [placed, removed] if removed_last else [removed, placed]
    result = [] if removed_last else [WorldMarker(0, 10, 20, 0, 1001)]
    assert clip_world_markers_many(events, [(2000, 1000, 5), (2000, 1000, 0)]) == [result, []]


def test_batch_clipping_matches_independent_windows_for_mixed_instances_and_overlapping_pulls():
    rng = random.Random(3429)
    events = [
        MarkerEvent(
            offset,
            rng.randrange(30),
            placed=rng.choice([True, False]),
            index=rng.randrange(8),
            x=rng.random(),
            y=rng.random(),
            instance_id=rng.choice([0, 10, 20]),
            zone_unload=rng.choice([True, False]),
        )
        for offset in range(200)
    ]
    # Deliberately neither chronological nor ordered by instance/window.
    rng.shuffle(events)
    fights = [(rng.randrange(40), rng.randrange(20), rng.choice([0, 10, 20, 30])) for _ in range(100)]
    expected = [clip_world_markers(events, *fight) for fight in fights]
    assert clip_world_markers_many(events, fights) == expected
    assert clip_world_markers_many([], fights) == [[] for _ in fights]
    assert clip_world_markers_many(events, []) == []


def test_batch_marker_history_can_be_cancelled():
    events = [MarkerEvent(offset, offset, True, 0, 10, 20, 5) for offset in range(2000)]
    calls = 0

    def cancel():
        nonlocal calls
        calls += 1
        if calls == 3:
            raise TaskCancelled("cancel marker timeline")

    token = cancel_check.set(cancel)
    try:
        with pytest.raises(TaskCancelled, match="marker timeline"):
            clip_world_markers_many(events, [(0, 3000, 5)])
    finally:
        cancel_check.reset(token)
