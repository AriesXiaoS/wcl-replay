# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""The map camera follows the pull, not a world marker left in another room."""

from __future__ import annotations

from wcl_replay.ui.map_view import frame_bounds


def test_a_far_marker_does_not_stretch_the_view():
    players = (1117.0, 1196.0, -31.0, 33.0)
    near = [(1119.0, 34.0), (1196.0, -33.0)]
    far = (1582.0, 11.0)
    x0, x1, y0, y1 = frame_bounds(players, [*near, far])
    assert x1 - x0 < 120
    assert y1 - y0 < 120
    assert x0 < 1119 and x1 > 1196
    assert far[0] > x1
