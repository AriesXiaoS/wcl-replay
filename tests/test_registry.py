# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Boss discovery when pkgutil cannot see compiled packages."""

from __future__ import annotations

import sys

import pytest

import wcl_replay.bosses.registry as registry
from wcl_replay.bosses._compiled_packages import NAMES
from wcl_replay.bosses.base import BossModule
from wcl_replay.bosses.coiled_altar.constants import ENCOUNTER_ID


def test_discover_uses_compiled_names_when_pkgutil_is_empty(monkeypatch):
    prefix = "wcl_replay.bosses.coiled_altar"
    saved_modules = {
        name: sys.modules[name]
        for name in list(sys.modules)
        if name == prefix or name.startswith(prefix + ".")
    }
    saved_registry = dict(registry._REGISTRY)
    saved_discovered = registry._discovered
    for name in saved_modules:
        del sys.modules[name]
    registry._REGISTRY.clear()
    registry._discovered = False
    monkeypatch.setattr(registry.pkgutil, "iter_modules", lambda _path: [])
    try:
        assert "coiled_altar" in NAMES
        registry.discover()
        boss = sys.modules[prefix]
        assert isinstance(registry.module_for(ENCOUNTER_ID), boss.CoiledAltar)
        assert ENCOUNTER_ID == 3429
    finally:
        sys.modules.update(saved_modules)
        registry._REGISTRY.clear()
        registry._REGISTRY.update(saved_registry)
        registry._discovered = saved_discovered


def test_register_rejects_a_different_class_for_the_same_encounter():
    class First(BossModule):
        encounter_ids = (999_001,)

    class Second(BossModule):
        encounter_ids = (999_001,)

    registry.register(First)
    try:
        with pytest.raises(ValueError):
            registry.register(Second)
        registry.register(First)
        assert registry._REGISTRY[999_001] is First
    finally:
        registry._REGISTRY.pop(999_001, None)
