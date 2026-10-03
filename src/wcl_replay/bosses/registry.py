# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Discovers boss modules (sub-packages of wcl_replay.bosses) and dispatches by encounter id."""

from __future__ import annotations

import importlib
import logging
import pkgutil
import sys

from .base import BossModule

_REGISTRY: dict[int, type[BossModule]] = {}
_discovered = False
_ERRORS: dict[str, str] = {}


def register(cls: type[BossModule]) -> type[BossModule]:
    if any(not isinstance(eid, int) or eid <= 0 for eid in cls.encounter_ids):
        raise ValueError("encounter_ids 必须为正整数")
    for eid in cls.encounter_ids:
        prev = _REGISTRY.get(eid)
        if prev is not None and prev is not cls:
            raise ValueError(f"encounter {eid} 已由 {prev.__name__} 注册，不能再交给 {cls.__name__}")
    _REGISTRY.update(dict.fromkeys(cls.encounter_ids, cls))
    return cls


def discover() -> None:
    global _discovered
    if _discovered:
        return
    from . import __path__ as pkg_path

    names = [mod.name for mod in pkgutil.iter_modules(pkg_path) if mod.ispkg]
    # Nuitka standalone compiles packages off the filesystem, so pkgutil often
    # sees nothing. tools/build_windows.py rewrites _compiled_packages.py first.
    if not names:
        from . import _compiled_packages

        names = list(_compiled_packages.NAMES)
    for name in names:
        package = f"{__package__}.{name}"
        before = dict(_REGISTRY)
        existing = set(sys.modules)
        try:
            importlib.import_module(package)
        except Exception as exc:
            _REGISTRY.clear()
            _REGISTRY.update(before)
            for loaded in set(sys.modules) - existing:
                if loaded == package or loaded.startswith(package + "."):
                    sys.modules.pop(loaded, None)
            _ERRORS[name] = f"{type(exc).__name__}: {exc}"
            logging.getLogger(__name__).warning("首领模块 %s 加载失败：%s", name, _ERRORS[name])
    _discovered = True


def module_for(encounter_id: int) -> BossModule:
    discover()
    cls = _REGISTRY.get(encounter_id, BossModule)
    return cls()


def registered() -> dict[int, type[BossModule]]:
    discover()
    return dict(_REGISTRY)


def discovery_errors() -> dict[str, str]:
    discover()
    return dict(_ERRORS)
