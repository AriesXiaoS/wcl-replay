# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Frozen entry point. Compiling app.py directly breaks src-layout relative imports."""

from __future__ import annotations

from wcl_replay.app import main

if __name__ == "__main__":
    main()
