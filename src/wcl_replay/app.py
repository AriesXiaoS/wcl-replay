# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Application entry point: `uv run wcl-replay [path/to/WoWCombatLog.txt]`."""

from __future__ import annotations

import multiprocessing
import sys

from PySide6.QtWidgets import QApplication

from .ui.icon import configure_windows_taskbar, load_app_icon
from .ui.main_window import MainWindow
from .ui.theme import STYLE_SHEET


def main() -> None:
    multiprocessing.freeze_support()
    configure_windows_taskbar()
    app = QApplication(sys.argv)
    app.setApplicationName("WCL Replay")
    app.setStyleSheet(STYLE_SHEET)
    icon = load_app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)
    win = MainWindow()
    win.show()
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if args and not args[0].startswith("http"):
        win.open_log(args[0])
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
