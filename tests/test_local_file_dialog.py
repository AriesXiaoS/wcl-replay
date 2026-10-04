# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication, QFileDialog, QLineEdit

from wcl_replay.ui import main_window
from wcl_replay.ui.main_window import MainWindow
from wcl_replay.workers import index_log_job


class ManualRunner:
    def __init__(self):
        self.jobs = []

    def run(self, fn, args, on_done, on_fail, on_progress=None):
        self.jobs.append(SimpleNamespace(fn=fn, args=args, done=on_done, fail=on_fail, progress=on_progress))


def wait(app, ready):
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        app.processEvents()
        if ready():
            return True
        time.sleep(0.005)
    return False


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("last_dir", str(tmp_path))
    runner = ManualRunner()
    monkeypatch.setattr(main_window, "TaskRunner", lambda _parent: runner)
    win = MainWindow(settings)
    win.show()
    app.processEvents()
    yield app, win, runner
    if win._log_dialog is not None:
        win._log_dialog.reject()
    win.close()
    win.deleteLater()
    app.processEvents()


def test_file_picker_returns_immediately_and_keeps_qt_timers_running(window):
    app, win, runner = window
    win.open_log_dialog()
    dialog = win._log_dialog
    assert dialog is not None and dialog.isVisible()
    assert dialog.testOption(QFileDialog.Option.DontUseNativeDialog)
    assert dialog.fileMode() == QFileDialog.FileMode.ExistingFile
    ticks = []
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start()
    try:
        assert wait(app, lambda: len(ticks) >= 5)
        assert dialog.isVisible() and not runner.jobs
        win.open_log_dialog()
        assert win._log_dialog is dialog
    finally:
        timer.stop()


def test_accept_closes_picker_before_loading_and_reuses_it_on_next_open(window, tmp_path, monkeypatch):
    app, win, runner = window
    states = []
    original = win.board.prepare

    def prepare(path, source):
        states.append(win._log_dialog.isVisible())
        return original(path, source)

    monkeypatch.setattr(win.board, "prepare", prepare)
    dialog = None
    for index in range(2):
        path = tmp_path / f"WoWCombatLog-{index}.txt"
        path.write_text("", encoding="utf-8")
        win.open_log_dialog()
        if dialog is None:
            dialog = win._log_dialog
        assert win._log_dialog is dialog

        dialog.findChild(QLineEdit, "fileNameEdit").setText(str(path))
        assert Path(dialog.selectedFiles()[0]) == path
        dialog.accept()
        assert not dialog.isVisible()
        assert len(runner.jobs) == index
        assert wait(app, lambda expected=index + 1: len(runner.jobs) == expected)
        job = runner.jobs[-1]
        assert job.fn is index_log_job and len(job.args) == 1 and Path(job.args[0]) == path
        assert Path(win.board.path) == path
        assert win.log_panel.open_btn.busy
        job.done([])
        assert not win.log_panel.open_btn.busy
    assert states == [False, False]


def test_cancel_leaves_current_log_and_loading_state_unchanged(window, tmp_path):
    app, win, runner = window
    path = tmp_path / "WoWCombatLog-current.txt"
    win.open_log(str(path))
    runner.jobs[-1].done([])
    win.open_log_dialog()
    win._log_dialog.reject()
    app.processEvents()
    assert len(runner.jobs) == 1
    assert win.board.path == str(path)
    assert not win.log_panel.open_btn.busy
    assert win.log_panel.status_lbl.text() == "0 次遭遇战"
