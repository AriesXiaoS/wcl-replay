# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Render the main window for a pull at given times to PNG files (works headless).

    uv run python tools/snapshot.py LOG --seq 31 --at 16 203 --out snapshots
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("--seq", type=int, required=True)
    ap.add_argument("--at", type=float, nargs="+", default=[16.0])
    ap.add_argument("--out", default="snapshots")
    ap.add_argument("--options", default="names,hp", help="comma separated: names,player_hp,hp,key")
    args = ap.parse_args()
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

    from PySide6.QtWidgets import QApplication

    from wcl_replay.pipeline import analyze
    from wcl_replay.sources.local_log import index_log, parse_encounter
    from wcl_replay.ui.controller import Session
    from wcl_replay.ui.main_window import MainWindow
    from wcl_replay.ui.theme import STYLE_SHEET

    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE_SHEET)
    entry = index_log(args.log)[args.seq - 1]
    data = parse_encounter(args.log, entry)
    tracks, analysis = analyze(data)
    win = MainWindow()
    win.resize(1500, 980)
    win.show()
    win.ctl.set_session(Session(data, tracks, analysis))
    for key in ("names", "player_hp", "hp", "key"):
        win.ctl.set_option(key, key in args.options.split(","))
    out = Path(args.out)
    out.mkdir(exist_ok=True)
    for sec in args.at:
        win.ctl.seek(sec * 1000)
        for _ in range(5):
            app.processEvents()
        win.findChild(type(win.centralWidget())).widget(1).widget(0)._render()
        app.processEvents()
        path = out / f"pull{entry.pull_number}_{int(sec):04d}.png"
        win.grab().save(str(path))
        print(path)


if __name__ == "__main__":
    main()
