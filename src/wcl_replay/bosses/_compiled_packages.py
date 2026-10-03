# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Boss packages included when pkgutil cannot see compiled modules.

Written by tools/build_windows.py. Development still discovers packages with
pkgutil; a Nuitka standalone build falls back to this tuple.
"""

from __future__ import annotations

NAMES: tuple[str, ...] = ("coiled_altar",)
