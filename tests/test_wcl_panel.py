# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""WCL rows stack newest-first and stay clickable. A finished row that is no longer selected is not shown."""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6.QtWidgets import QApplication

from wcl_replay.ui.controller import ReplayController
from wcl_replay.ui.wcl_panel import WclBoard, WclPanel, fight_label, quota_text


def _session() -> SimpleNamespace:
    return SimpleNamespace(analysis=SimpleNamespace(lanes=[]))


def test_fight_label_includes_the_start_clock():
    session = SimpleNamespace(
        data=SimpleNamespace(
            fight=SimpleNamespace(
                name="盘绕祭坛",
                difficulty=16,
                kill=True,
                pull_number=3,
                id=12,
                duration_ms=5 * 60 * 1000 + 30 * 1000,
                start_label="2026/9/28 23:22",
            )
        )
    )
    assert fight_label(session) == "盘绕祭坛 M · pull 3 · 23:22 · 5:30 · 击杀"


def test_quota_text_shows_spent_against_the_hourly_limit():
    assert quota_text(17, 3600, 125) == ("本小时 17 / 3600", "2 分钟后重置")
    assert quota_text(17.4, 3600, 40) == ("本小时 17.4 / 3600", "40 秒后重置")


def test_wcl_card_lays_out_quota_then_url_then_query():
    QApplication.instance() or QApplication([])
    panel = WclPanel()
    lay = panel.layout()
    row = lay.itemAt(0).layout()
    assert row.itemAt(0).widget() is panel.quota_lbl
    assert row.itemAt(1).widget() is panel.refresh_btn
    assert row.itemAt(2).widget() is panel.settings_btn
    assert lay.itemAt(1).widget() is panel.url_edit
    assert lay.itemAt(2).widget() is panel.query_btn
    assert lay.itemAt(lay.count() - 1).widget() is panel.clear_btn
    assert panel.quota_lbl.text() == "额度未刷新"


def test_wcl_rows_accumulate_and_a_background_result_stays_cached():
    QApplication.instance() or QApplication([])
    ctl = ReplayController()
    panel = WclPanel()
    board = WclBoard(ctl, panel)

    _index, first = board.begin("https://cn.warcraftlogs.com/reports/ya73XMW2TkvcnRQV?fight=3")
    assert first in board.loads.running
    assert panel.rows[0].download.host.isHidden() is False
    assert panel.rows[0].compute.host.isHidden() is False
    assert panel.rows[0].download.bar.value() == 0
    assert panel.rows[0].compute.bar.value() == 0

    _index, second = board.begin("https://cn.warcraftlogs.com/reports/ya73XMW2TkvcnRQV?fight=4")
    assert len(panel.rows) == 2
    assert board.loads.selected == second
    assert panel.rows[0].text.text().endswith("fight=4")
    assert panel.rows[1].text.text().endswith("fight=3")

    older = _session()
    assert board.finish(first, older, "旧的一场") is False
    assert ctl.session is None
    assert panel.rows[1].mark.text() == "✓"
    assert panel.rows[1].text.text() == "旧的一场"

    newer = _session()
    assert board.finish(second, newer, "新的一场") is True
    assert ctl.session is newer
    assert panel.rows[0].text.text() == "新的一场"

    assert board.activate(0) == "show"
    assert ctl.session is newer
    assert board.activate(1) == "show"
    assert ctl.session is older

    panel.rows[0].delete_btn.click()
    assert len(panel.rows) == 1
    assert ctl.session is older
    assert board.loads.cache[board.keys[0]] is older

    panel.clearClicked.connect(board.clear_cache)
    panel.clear_btn.click()
    assert board.loads.cache == {}
    assert ctl.session is None
    assert panel.rows[0].mark.text() == ""


def test_a_pinned_wcl_fight_stays_when_the_cache_is_cleared():
    QApplication.instance() or QApplication([])
    ctl = ReplayController()
    panel = WclPanel()
    board = WclBoard(ctl, panel)
    _index, key = board.begin("https://cn.warcraftlogs.com/reports/ya73XMW2TkvcnRQV?fight=3")
    kept = _session()
    assert board.finish(key, kept, "收藏的一场") is True
    panel.rows[0].star_btn.click()
    assert panel.rows[0].star_btn.text() == "★"

    board.clear_cache()
    assert board.loads.cache == {key: kept}
    assert ctl.session is kept
    assert panel.rows[0].mark.text() == "✓"
    assert panel.rows[0].star_btn.text() == "★"

    panel.rows[0].delete_btn.click()
    assert board.loads.cache == {}
    assert key not in board.pinned


def test_wcl_row_splits_download_and_compute_progress():
    QApplication.instance() or QApplication([])
    ctl = ReplayController()
    panel = WclPanel()
    board = WclBoard(ctl, panel)
    _index, key = board.begin("https://cn.warcraftlogs.com/reports/ya73XMW2TkvcnRQV?fight=3")
    row = panel.rows[0]

    board.note_progress(key, 0.4, "phase:download:读取报告…")
    assert row.download.bar.value() == 400
    assert row.download.percent.text() == "40%"
    assert row.download.bar.toolTip() == "读取报告…"
    assert row.compute.bar.value() == 0
    assert row.compute.percent.text() == "0%"

    board.note_progress(key, 1.0, "phase:download")
    board.note_progress(key, 0.35, "phase:compute:构建坐标轨迹")
    assert row.download.bar.value() == 1000
    assert row.download.percent.text() == "100%"
    assert row.compute.bar.value() == 350
    assert row.compute.percent.text() == "35%"
    assert row.compute.bar.toolTip() == "构建坐标轨迹"
    assert row.download.host.isHidden() is False
    assert row.compute.host.isHidden() is False

    assert board.finish(key, _session(), "一场") is True
    assert row.download.host.isHidden() is True
    assert row.compute.host.isHidden() is True
    assert row.mark.text() == "✓"
