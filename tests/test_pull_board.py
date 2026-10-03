# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Opening a cached pull shows it; a pull that finishes in the background only gets a mark."""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QProgressBar, QToolBar

from wcl_replay.sources.local_log import EncounterEntry
from wcl_replay.ui.controller import ReplayController
from wcl_replay.ui.log_panel import LogPanel, PullBoard
from wcl_replay.ui.main_window import MainWindow


def _entry(
    start: int, number: int, name: str, *, end: int | None = None, closed: bool = True
) -> EncounterEntry:
    return EncounterEntry(
        seq=number,
        encounter_id=1,
        name=name,
        difficulty=16,
        group_size=20,
        instance_id=1,
        start_offset=start,
        end_offset=start + 100 if end is None else end,
        start_ts="1/1/2026 12:00:00.000",
        duration_ms=90_000 if closed else 0,
        kill=closed,
        closed=closed,
        pull_number=number,
    )


def _session() -> SimpleNamespace:
    return SimpleNamespace(analysis=SimpleNamespace(lanes=[]))


def _board() -> tuple[QApplication, ReplayController, LogPanel, PullBoard]:
    app = QApplication.instance() or QApplication([])
    ctl = ReplayController()
    panel = LogPanel()
    board = PullBoard(ctl, panel)
    panel.activated.connect(board.activate)
    panel.resize(320, 640)
    panel.show()
    return app, ctl, panel, board


def test_cached_click_shows_and_a_background_finish_only_marks():
    app, ctl, panel, board = _board()
    path = r"D:\Logs\WoWCombatLog.txt"
    gen = board.prepare(path)
    board.show_entries(gen, path, [_entry(0, 1, "祖尔加"), _entry(200, 2, "玛拉卡斯")])
    assert "玛拉卡斯" in panel.rows[0].text.text()
    assert "祖尔加" in panel.rows[1].text.text()
    done = _session()
    running = _session()
    board.loads.cache[board.keys[0]] = done
    app.processEvents()

    QTest.mouseClick(panel.rows[1], Qt.MouseButton.LeftButton)
    assert board.loads.selected == board.keys[1]
    assert ctl.session is None
    assert not panel.rows[1].bar.isHidden()

    QTest.mouseClick(panel.rows[0], Qt.MouseButton.LeftButton)
    assert ctl.session is done
    assert board.loads.selected == board.keys[0]

    assert board.finish(gen, path, board.keys[1], running) is False
    assert ctl.session is done
    assert panel.rows[1].mark.text() == "✓"
    assert panel.rows[1].bar.isHidden()

    QTest.mouseClick(panel.rows[1], Qt.MouseButton.LeftButton)
    assert ctl.session is running


def test_refreshing_the_same_log_keeps_computed_pulls():
    _app, ctl, panel, board = _board()
    path = r"D:\Logs\WoWCombatLog.txt"
    gen = board.prepare(path)
    board.show_entries(gen, path, [_entry(0, 1, "祖尔加"), _entry(200, 2, "玛拉卡斯")])
    assert board.activate(0) == "start"
    key = board.keys[0]
    done = _session()
    assert board.finish(gen, path, key, done) is True
    ctl.t = 12_000.0

    gen2 = board.prepare(path, "refresh")
    assert key in board.loads.cache
    assert ctl.session is done
    assert ctl.t == 12_000.0
    board.show_entries(
        gen2,
        path,
        [_entry(0, 1, "祖尔加"), _entry(200, 2, "玛拉卡斯"), _entry(400, 3, "新首领")],
    )
    index = board.keys.index(key)
    assert panel.rows[index].mark.text() == "✓"
    assert panel.rows[0].mark.text() == ""
    assert board.loads.selected == key
    assert ctl.session is done
    assert ctl.t == 12_000.0
    assert board.activate(index) == "show"
    assert ctl.session is done


def test_refresh_drops_a_pull_whose_range_changed():
    _app, ctl, panel, board = _board()
    path = r"D:\Logs\WoWCombatLog.txt"
    gen = board.prepare(path)
    board.show_entries(gen, path, [_entry(0, 1, "祖尔加"), _entry(200, 2, "进行中", end=500, closed=False)])
    old = board.keys[0]
    assert board.activate(0) == "start"
    assert board.finish(gen, path, old, _session()) is True

    gen2 = board.prepare(path, "refresh")
    assert board.finish(gen, path, old, _session()) is False
    assert old in board.loads.cache
    board.show_entries(gen2, path, [_entry(0, 1, "祖尔加"), _entry(200, 2, "进行中", end=900, closed=True)])
    assert old not in board.loads.cache
    assert board.keys[0] not in board.loads.cache
    assert ctl.session is None
    assert panel.rows[0].mark.text() == ""
    assert board.finish(gen, path, old, _session()) is False
    assert old not in board.loads.cache
    assert board.activate(0) == "start"


def test_a_pull_started_before_refresh_still_lands():
    _app, ctl, panel, board = _board()
    path = r"D:\Logs\WoWCombatLog.txt"
    gen = board.prepare(path)
    board.show_entries(gen, path, [_entry(0, 1, "祖尔加")])
    assert board.activate(0) == "start"
    key = board.keys[0]
    gen2 = board.prepare(path, "refresh")
    assert ctl.session is None
    board.show_entries(gen2, path, [_entry(0, 1, "祖尔加")])
    assert not panel.rows[0].bar.isHidden()
    board.note_progress(gen, path, key, 0.4)
    assert panel.rows[0].bar.value() == 400
    done = _session()
    assert board.finish(gen, path, key, done) is True
    assert ctl.session is done
    assert panel.rows[0].mark.text() == "✓"


def test_clear_cache_drops_finished_pulls_and_keeps_one_that_is_running():
    _app, ctl, panel, board = _board()
    path = r"D:\Logs\WoWCombatLog.txt"
    gen = board.prepare(path)
    board.show_entries(gen, path, [_entry(0, 1, "祖尔加"), _entry(200, 2, "玛拉卡斯")])
    assert board.activate(1) == "start"
    done_key = board.keys[1]
    done = _session()
    assert board.finish(gen, path, done_key, done) is True
    assert board.activate(0) == "start"
    running_key = board.keys[0]
    assert panel.rows[1].mark.text() == "✓"
    assert ctl.session is done

    board.clear_cache()
    assert board.loads.cache == {}
    assert running_key in board.loads.running
    assert ctl.session is None
    assert panel.rows[1].mark.text() == ""
    assert not panel.rows[0].bar.isHidden()
    assert board.loads.selected == running_key

    again = _session()
    assert board.finish(gen, path, running_key, again) is True
    assert ctl.session is again
    assert panel.rows[0].mark.text() == "✓"


def test_main_window_clear_button_forgets_a_computed_pull():
    QApplication.instance() or QApplication([])
    win = MainWindow()
    assert not win.log_panel.clear_btn.isEnabled()
    path = r"D:\Logs\WoWCombatLog.txt"
    gen = win.board.prepare(path)
    win.board.show_entries(gen, path, [_entry(0, 1, "祖尔加")])
    key = win.board.keys[0]
    win.board.loads.click(key)
    done = _session()
    assert win.board.finish(gen, path, key, done) is True
    assert win.log_panel.clear_btn.isEnabled()
    assert win.log_panel.rows[0].mark.text() == "✓"
    win.log_panel.clearClicked.emit()
    assert key not in win.board.loads.cache
    assert win.ctl.session is None
    assert win.log_panel.rows[0].mark.text() == ""
    assert win.board.activate(0) == "start"


def test_starting_past_the_cap_releases_the_earliest_calculation():
    app = QApplication.instance() or QApplication([])
    ctl = ReplayController()
    panel = LogPanel()
    board = PullBoard(ctl, panel, limit=lambda: 2)
    path = r"D:\Logs\WoWCombatLog.txt"
    gen = board.prepare(path)
    board.show_entries(gen, path, [_entry(0, 1, "最早"), _entry(200, 2, "中间"), _entry(400, 3, "最新")])
    assert "最新" in panel.rows[0].text.text()
    assert "最早" in panel.rows[2].text.text()
    newest, oldest = board.keys[0], board.keys[2]
    first, second = _session(), _session()
    assert board.activate(2) == "start"
    assert board.finish(gen, path, oldest, first) is True
    assert board.activate(0) == "start"
    assert board.finish(gen, path, newest, second) is True
    assert board.activate(0) == "show"
    assert ctl.session is second

    assert board.activate(1) == "start"
    assert oldest not in board.loads.cache
    assert newest in board.loads.cache
    assert panel.rows[2].mark.text() == ""
    assert panel.rows[0].mark.text() == "✓"
    assert ctl.session is second
    assert board.loads.order[0] == newest
    app.processEvents()


def test_eviction_follows_click_order_when_results_finish_out_of_order():
    _app, _ctl, panel, board = _board()
    board._limit = lambda: 2
    path = r"D:\Logs\WoWCombatLog.txt"
    gen = board.prepare(path)
    board.show_entries(gen, path, [_entry(0, 1, "一"), _entry(200, 2, "二"), _entry(400, 3, "三")])
    for index in (0, 1, 2):
        assert board.activate(index) == "start"
    assert board.finish(gen, path, board.keys[1], _session()) is False
    assert board.finish(gen, path, board.keys[0], _session()) is False
    assert board.finish(gen, path, board.keys[2], _session()) is True
    assert board.keys[0] not in board.loads.cache
    assert board.keys[1] in board.loads.cache
    assert board.keys[2] in board.loads.cache
    assert panel.rows[0].mark.text() == ""
    assert panel.rows[1].mark.text() == "✓"


def test_releasing_a_pull_keeps_the_row_and_drops_memory():
    _app, ctl, panel, board = _board()
    path = r"D:\Logs\WoWCombatLog.txt"
    gen = board.prepare(path)
    board.show_entries(gen, path, [_entry(0, 1, "祖尔加"), _entry(200, 2, "玛拉卡斯")])
    done = _session()
    board.loads.cache[board.keys[0]] = done
    panel.mark_done(0)
    assert board.activate(0) == "show"
    assert ctl.session is done

    panel.rows[0].delete_btn.click()
    assert len(panel.rows) == 2
    assert "玛拉卡斯" in panel.rows[0].text.text()
    assert panel.rows[0].mark.text() == ""
    assert ctl.session is None
    assert board.keys[0] not in board.loads.cache
    assert board.activate(0) == "start"

    key = board.keys[0]
    panel.rows[0].delete_btn.click()
    assert len(panel.rows) == 2
    assert panel.rows[0].bar.isHidden()
    assert board.finish(gen, path, key, _session()) is False
    assert key not in board.loads.cache


def test_a_result_from_an_old_log_is_dropped():
    app, ctl, panel, board = _board()
    path = r"D:\Logs\WoWCombatLog.txt"
    gen = board.prepare(path)
    board.show_entries(gen, path, [_entry(0, 1, "祖尔加")])
    key = board.keys[0]
    board.prepare(r"D:\Logs\other.txt")
    assert board.finish(gen, path, key, _session()) is False
    assert ctl.session is None
    assert panel.rows == []
    # A window left shown here is destroyed inside a later event loop and aborts the process.
    panel.close()
    panel.deleteLater()
    app.processEvents()


def test_main_window_has_no_toolbar_or_global_progress():
    QApplication.instance() or QApplication([])
    win = MainWindow()
    assert win.findChild(QToolBar) is None
    assert win.findChildren(QProgressBar) == []
    assert win.log_panel.file_lbl.text() == "尚未选择日志"


def _sees_spinner(button) -> bool:
    image = button.grab().toImage()
    accent = 0
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            if color.red() > 140 and color.blue() > 220 and color.green() < 180:
                accent += 1
    return accent >= 8


def test_reading_covers_the_clicked_button_and_rejects_clicks():
    app, _, panel, board = _board()
    opened: list[int] = []
    refreshed: list[int] = []
    panel.openClicked.connect(lambda: opened.append(1))
    panel.refreshClicked.connect(lambda: refreshed.append(1))
    app.processEvents()

    board.prepare(r"D:\Logs\a.txt", "open")
    assert panel.open_btn.busy
    assert not panel.refresh_btn.busy
    assert not panel.open_btn.isEnabled()
    assert not panel.refresh_btn.isEnabled()
    assert panel.status_lbl.text() == "正在读取轮次…"
    app.processEvents()
    assert _sees_spinner(panel.open_btn)
    assert panel.open_btn._timer.isActive()
    angle = panel.open_btn._angle
    panel.open_btn._spin()
    assert panel.open_btn._angle != angle
    QTest.mouseClick(panel.open_btn, Qt.MouseButton.LeftButton)
    QTest.mouseClick(panel.refresh_btn, Qt.MouseButton.LeftButton)
    assert opened == []
    assert refreshed == []

    gen = board.gen
    board.prepare(r"D:\Logs\b.txt", "refresh")
    board.index_failed(gen, "读取失败：old")
    assert panel.refresh_btn.busy
    assert not panel.open_btn.busy
    assert panel.status_lbl.text() == "正在读取轮次…"
    app.processEvents()
    assert _sees_spinner(panel.refresh_btn)

    board.index_failed(board.gen, "读取失败：boom")
    assert not panel.open_btn.busy
    assert not panel.refresh_btn.busy
    assert panel.open_btn.isEnabled()
    assert panel.refresh_btn.isEnabled()
    assert panel.status_lbl.text() == "读取失败：boom"
    QTest.mouseClick(panel.open_btn, Qt.MouseButton.LeftButton)
    QTest.mouseClick(panel.refresh_btn, Qt.MouseButton.LeftButton)
    assert opened == [1]
    assert refreshed == [1]
