# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Left-hand column: pick a local combat log, then compute one pull at a time."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QRectF, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..bosses.base import fmt_time
from ..sources.local_log import EncounterEntry
from .controller import ReplayController
from .theme import ACCENT, BG, TEXT_DIM


def pull_label(entry: EncounterEntry) -> str:
    result = "击杀" if entry.kill else ("灭团" if entry.closed else "进行中")
    clock = entry.start_ts.split(" ")[1][:5] if " " in entry.start_ts else entry.start_ts
    return (
        f"#{entry.pull_number:<3d} {entry.name} {entry.difficulty_label} · {clock} · "
        f"{fmt_time(entry.duration_ms)} · {result}"
    )


def pull_key(path: str, entry: EncounterEntry) -> tuple:
    key = ("local", path, entry.start_offset, entry.end_offset)
    digest = entry.analysis_digest or entry.content_digest
    return (*key, digest) if digest else key


def pin_token(key: tuple) -> str | None:
    """Stable text for a local pull. WCL rows live only until the process exits."""
    if len(key) not in (4, 5) or key[0] != "local":
        return None
    return json.dumps(list(key[1:]), ensure_ascii=False)


def pins_from_settings(settings: QSettings | None) -> set[tuple]:
    if settings is None:
        return set()
    raw = settings.value("pinned_local_pulls", []) or []
    if isinstance(raw, str):
        raw = [raw]
    pins: set[tuple] = set()
    for item in raw:
        try:
            path, start, end, *digest = json.loads(str(item))
            if len(digest) > 1:
                continue
            key = ("local", str(path), int(start), int(end))
            pins.add((*key, str(digest[0])) if digest else key)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    return pins


def short_error(tb: str) -> str:
    lines = [line for line in tb.strip().splitlines() if line.strip()]
    text = lines[-1] if lines else "计算失败"
    return text if len(text) <= 120 else text[:117] + "…"


class PullLoads:
    """Which pull is selected, which are cached, and which are still computing.

    ``order`` is the sequence of calculations, oldest first. Viewing a cached pull does not
    move it. The board drops from the front of this list, not from the bottom of the log.
    """

    def __init__(self) -> None:
        self.selected: tuple | None = None
        self.cache: dict[tuple, object] = {}
        self.running: set[tuple] = set()
        self.order: list[tuple] = []
        self._run_ids: dict[tuple, int] = {}
        self._seq = 0
        self._handles: dict[tuple, object] = {}

    def bind(self, key: tuple, handle: object) -> None:
        if handle is None:
            return
        if key in self.running:
            self._handles[key] = handle
        else:
            handle.cancel()

    def reset(self) -> None:
        for handle in list(self._handles.values()):
            handle.cancel()
        self._handles.clear()
        self.selected = None
        self.cache.clear()
        self.running.clear()
        self.order.clear()
        self._run_ids.clear()

    def click(self, key: tuple) -> str:
        """``show`` if cached, ``wait`` if already running, ``start`` if this click begins work."""
        self.selected = key
        if key in self.cache:
            return "show"
        if key in self.running:
            return "wait"
        self.running.add(key)
        self._seq += 1
        self._run_ids[key] = self._seq
        self.remember(key)
        return "start"

    def complete(self, key: tuple, session: object) -> bool:
        """Store the result. True only when this pull is still the one the user has selected."""
        self.running.discard(key)
        self._handles.pop(key, None)
        self._run_ids.pop(key, None)
        self.cache[key] = session
        self.remember(key)
        return self.selected == key

    def abandon(self, key: tuple) -> None:
        self.running.discard(key)
        self._run_ids.pop(key, None)
        self.forget_order(key)
        handle = self._handles.pop(key, None)
        if handle is not None:
            handle.cancel()

    def run_id(self, key: tuple) -> int:
        return self._run_ids[key]

    def is_current(self, key: tuple, run_id: int | None) -> bool:
        # Asynchronous callers supply the token captured when the job was started.
        return key in self.running and (run_id is None or self._run_ids.get(key) == run_id)

    def remember(self, key: tuple) -> None:
        """Record a calculation the first time it starts. A later view does not move it."""
        if key not in self.order:
            self.order.append(key)

    def forget_order(self, key: tuple) -> None:
        try:
            self.order.remove(key)
        except ValueError:
            pass


class _PullRow(QFrame):
    clicked = Signal()
    deleteClicked = Signal()
    starClicked = Signal()

    def __init__(
        self,
        label: str,
        parent: QWidget | None = None,
        *,
        action_tip: str = "释放这场的内存",
        star_tip: str = "收藏。缓存满了或清除缓存时都会留下这场。",
    ):
        super().__init__(parent)
        self.setObjectName("pullRow")
        self._star_tip = star_tip
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(4)
        top = QHBoxLayout()
        self.mark = QLabel("")
        self.mark.setFixedWidth(18)
        self.mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text = QLabel(label)
        self.text.setWordWrap(True)
        self.text.setToolTip(label)
        self.star_btn = QPushButton("☆")
        self.star_btn.setObjectName("rowStar")
        self.star_btn.setFixedSize(18, 18)
        self.star_btn.setToolTip(star_tip)
        self.star_btn.clicked.connect(self.starClicked.emit)
        self.delete_btn = QPushButton("×")
        self.delete_btn.setObjectName("rowDelete")
        self.delete_btn.setFixedSize(18, 18)
        self.delete_btn.setToolTip(action_tip)
        self.delete_btn.clicked.connect(self.deleteClicked.emit)
        top.addWidget(self.mark)
        top.addWidget(self.text, 1)
        top.addWidget(self.star_btn)
        top.addWidget(self.delete_btn)
        lay.addLayout(top)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        self.bar.hide()
        lay.addWidget(self.bar)
        self.err = QLabel("")
        self.err.setWordWrap(True)
        self.err.setStyleSheet("color: #e07070;")
        self.err.hide()
        lay.addWidget(self.err)
        for child in (self.mark, self.text, self.bar, self.err):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def set_starred(self, on: bool) -> None:
        self.star_btn.setText("★" if on else "☆")
        self.star_btn.setProperty("starred", on)
        self.star_btn.setToolTip("已收藏，不会被自动清掉。" if on else self._star_tip)
        self.star_btn.style().unpolish(self.star_btn)
        self.star_btn.style().polish(self.star_btn)

    def set_selected(self, on: bool) -> None:
        self.setProperty("selected", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def show_progress(self, frac: float) -> None:
        self.err.hide()
        self.mark.setText("")
        self.bar.show()
        self.bar.setValue(int(max(0.0, min(1.0, frac)) * 1000))

    def mark_done(self) -> None:
        self.bar.hide()
        self.err.hide()
        self.mark.setText("✓")
        self.mark.setToolTip("计算完成")

    def clear_done(self) -> None:
        if self.mark.text() != "✓":
            return
        self.mark.setText("")
        self.mark.setToolTip("")

    def release_result(self) -> None:
        """Clear the checkmark, progress, and error. The row itself stays."""
        self.bar.hide()
        self.bar.setValue(0)
        self.err.hide()
        self.err.setText("")
        self.mark.setText("")
        self.mark.setToolTip("")

    def mark_error(self, message: str) -> None:
        self.bar.hide()
        self.mark.setText("!")
        self.mark.setToolTip(message)
        self.err.setText(message)
        self.err.show()


class _SpinnerButton(QPushButton):
    """Button with a loading veil. Clicks are refused while ``busy``."""

    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setObjectName("logAction")
        self._busy = False
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._spin)

    @property
    def busy(self) -> bool:
        return self._busy

    def set_busy(self, busy: bool) -> None:
        if busy == self._busy:
            return
        self._busy = busy
        self.setProperty("busy", busy)
        self.style().unpolish(self)
        self.style().polish(self)
        if busy:
            self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def _spin(self) -> None:
        self._angle = (self._angle - 24) % 360
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self._busy:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        veil = QColor(BG)
        veil.setAlpha(235)
        painter.fillRect(self.rect().adjusted(1, 1, -1, -1), veil)
        side = max(10.0, min(14.0, self.height() - 8.0))
        rect = QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)
        track_color = QColor(TEXT_DIM)
        track_color.setAlpha(80)
        track = QPen(track_color)
        track.setWidthF(2.0)
        track.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(track)
        painter.drawEllipse(rect)
        arc = QPen(QColor(ACCENT))
        arc.setWidthF(2.0)
        arc.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(arc)
        painter.drawArc(rect, self._angle * 16, 110 * 16)
        painter.end()


class LogPanel(QFrame):
    """Open a combat log and list its pulls. Each row owns its own progress bar."""

    openClicked = Signal()
    refreshClicked = Signal()
    clearClicked = Signal()
    activated = Signal(int)
    releaseRequested = Signal(int)
    starToggled = Signal(int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("logCard")
        self.setMinimumWidth(340)
        self._rows: list[_PullRow] = []
        self._selected: int | None = None
        self._has_file = False
        self._reading: str | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        buttons = QHBoxLayout()
        self.open_btn = _SpinnerButton("打开本地日志…")
        self.open_btn.clicked.connect(self.openClicked.emit)
        self.refresh_btn = _SpinnerButton("刷新")
        self.refresh_btn.setToolTip("重新扫描当前日志。已算完且范围没变的轮次会保留")
        self.refresh_btn.setEnabled(False)
        self.refresh_btn.clicked.connect(self.refreshClicked.emit)
        buttons.addWidget(self.open_btn)
        buttons.addWidget(self.refresh_btn)
        lay.addLayout(buttons)
        self.file_lbl = QLabel("尚未选择日志")
        self.file_lbl.setWordWrap(True)
        lay.addWidget(self.file_lbl)
        self.status_lbl = QLabel("")
        self.status_lbl.setWordWrap(True)
        self.status_lbl.setStyleSheet("color: #8b93a1;")
        lay.addWidget(self.status_lbl)

        self._scroll = QScrollArea()
        self._scroll.setObjectName("logList")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.viewport().setObjectName("logViewport")
        self._body = QWidget()
        self._body.setObjectName("logListBody")
        self._list = QVBoxLayout(self._body)
        self._list.setContentsMargins(0, 0, 0, 0)
        self._list.setSpacing(2)
        self._list.addStretch(1)
        self._scroll.setWidget(self._body)
        lay.addWidget(self._scroll, 1)
        self.clear_btn = QPushButton("清除缓存")
        self.clear_btn.setObjectName("logAction")
        self.clear_btn.setToolTip("丢掉已算完的轮次，下次打开会重新计算")
        self.clear_btn.setEnabled(False)
        self.clear_btn.clicked.connect(self.clearClicked.emit)
        lay.addWidget(self.clear_btn)

    def set_file(self, name: str, full_path: str) -> None:
        self.file_lbl.setText(name)
        self.file_lbl.setToolTip(full_path)
        self._has_file = True
        self.clear_btn.setEnabled(True)
        if self._reading is None:
            self.refresh_btn.setEnabled(True)

    def set_reading(self, source: str | None) -> None:
        """``open`` / ``refresh`` covers that button with a spinner and refuses both clicks."""
        self._reading = source
        self.open_btn.set_busy(source == "open")
        self.refresh_btn.set_busy(source == "refresh")
        reading = source is not None
        self.open_btn.setEnabled(not reading)
        self.refresh_btn.setEnabled(self._has_file and not reading)

    def set_status(self, text: str) -> None:
        self.status_lbl.setText(text)

    def set_pulls(self, labels: list[str]) -> None:
        while self._list.count():
            item = self._list.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._rows = []
        self._selected = None
        for label in labels:
            row = _PullRow(label, self._body)
            row.clicked.connect(lambda r=row: self.activated.emit(self._rows.index(r)))
            row.deleteClicked.connect(lambda r=row: self.releaseRequested.emit(self._rows.index(r)))
            row.starClicked.connect(lambda r=row: self.starToggled.emit(self._rows.index(r)))
            self._rows.append(row)
            self._list.addWidget(row)
        self._list.addStretch(1)

    @property
    def rows(self) -> list[_PullRow]:
        return self._rows

    def set_selected(self, index: int | None) -> None:
        self._selected = index
        for i, row in enumerate(self._rows):
            row.set_selected(i == index)

    def show_progress(self, index: int, frac: float) -> None:
        self._rows[index].show_progress(frac)

    def mark_done(self, index: int) -> None:
        self._rows[index].mark_done()

    def mark_error(self, index: int, message: str) -> None:
        self._rows[index].mark_error(message)

    def clear_done(self, index: int) -> None:
        self._rows[index].clear_done()


class PullBoard:
    """Ties the pull list to the replay session. A finished job is shown only if it is still selected.

    Refreshing the same log keeps results whose bytes and map/marker context did not change.
    A pull whose content or analysis context changed gets a new key and is computed again.
    """

    def __init__(
        self,
        ctl: ReplayController,
        panel: LogPanel,
        limit: Callable[[], int] | None = None,
        settings: QSettings | None = None,
        active: Callable[[], bool] | None = None,
    ):
        self.ctl = ctl
        self.panel = panel
        self.settings = settings
        self.active = active or (lambda: True)
        self._limit = limit or (lambda: 3)
        self.loads = PullLoads()
        self.pinned = pins_from_settings(settings)
        self.panel.releaseRequested.connect(self.release)
        self.panel.starToggled.connect(self.toggle_pin)
        self.gen = 0
        self.path = ""
        self.entries: list[EncounterEntry] = []
        self.keys: list[tuple] = []
        self._index: dict[tuple, int] = {}
        self._listed = False

    def prepare(self, path: str, source: str | None = None) -> int:
        same_log = path == self.path
        self.gen += 1
        self.path = path
        self.entries = []
        self.keys = []
        self._index = {}
        self._listed = False
        if not same_log:
            self.loads.reset()
        self.panel.set_file(Path(path).name, path)
        self.panel.set_status("正在读取轮次…")
        self.panel.set_pulls([])
        if source is not None:
            self.panel.set_reading(source)
        if self.active() and (not same_log or self.loads.selected not in self.loads.cache):
            self.ctl.clear_session()
        return self.gen

    def show_entries(self, gen: int, path: str, entries: list[EncounterEntry]) -> None:
        if gen != self.gen or path != self.path:
            return
        self.entries = list(reversed(entries))
        self.keys = [pull_key(path, entry) for entry in self.entries]
        self._index = {key: i for i, key in enumerate(self.keys)}
        self._listed = True
        self._migrate_legacy_pins()
        self._drop_stale(path)
        self.panel.set_status(f"{len(entries)} 次遭遇战")
        self.panel.set_pulls([pull_label(entry) for entry in self.entries])
        self.panel.set_reading(None)
        for key, index in self._index.items():
            if key in self.pinned:
                self.panel.rows[index].set_starred(True)
            if key in self.loads.cache:
                self.panel.mark_done(index)
            elif key in self.loads.running:
                self.panel.show_progress(index, 0.0)
        self._restore_selection()

    def toggle_pin(self, index: int) -> None:
        if not 0 <= index < len(self.keys):
            return
        key = self.keys[index]
        if key in self.pinned:
            self.pinned.discard(key)
        else:
            self.pinned.add(key)
        self.panel.rows[index].set_starred(key in self.pinned)
        self._save_pins()

    def index_failed(self, gen: int, message: str) -> None:
        if gen != self.gen:
            return
        self.panel.set_status(message)
        self.panel.set_reading(None)

    def activate(self, index: int) -> str:
        key = self.keys[index]
        action = self.loads.click(key)
        self.panel.set_selected(index)
        if action == "show":
            if self.active():
                self.ctl.set_session(self.loads.cache[key])
        elif action == "start":
            self.panel.show_progress(index, 0.0)
            self._make_room()
        return action

    def note_progress(
        self, _gen: int, path: str, key: tuple, frac: float, *, run_id: int | None = None
    ) -> None:
        if path != self.path or key not in self._index or not self.loads.is_current(key, run_id):
            return
        self.panel.show_progress(self._index[key], frac)

    def finish(self, _gen: int, path: str, key: tuple, session: object, *, run_id: int | None = None) -> bool:
        """Keep a result for this log even when a refresh bumped the index generation.

        The range and analysis digest are part of ``key``, so changed input is a different key.
        """
        if path != self.path:
            return False
        if not self.loads.is_current(key, run_id):
            return False
        if self._listed and key not in self._index:
            self.loads.abandon(key)
            return False
        show = self.loads.complete(key, session)
        show = show and self.active()
        if key not in self._index:
            return False
        self.panel.mark_done(self._index[key])
        if show:
            self.ctl.set_session(session)
        self.trim_to_limit(keep=key)
        return show

    def release(self, index: int) -> None:
        """Drop one pull's computed result. The list row stays, so it can be computed again."""
        if not 0 <= index < len(self.keys):
            return
        key = self.keys[index]
        self.loads.abandon(key)
        session = self.loads.cache.pop(key, None)
        if session is not None and self.ctl.session is session:
            self.ctl.clear_session()
        if self.loads.selected == key:
            self.loads.selected = None
            self.panel.set_selected(None)
        self.panel.rows[index].release_result()

    def trim_to_limit(self, keep: tuple | None = None) -> None:
        """Forget finished pulls past the cap, oldest calculation first."""
        cap = self._cap()
        while len(self.loads.cache) > cap:
            if not self._evict_one(keep):
                return

    def _make_room(self) -> None:
        """A pull that just started will become one more finished result."""
        cap = self._cap()
        while len(self.loads.cache) + len(self.loads.running) > cap:
            if not self._evict_one():
                return

    def _cap(self) -> int:
        try:
            return max(1, int(self._limit()))
        except (TypeError, ValueError):
            return 3

    def _evict_one(self, keep: tuple | None = None) -> bool:
        for key in list(self.loads.order):
            if key == keep or key in self.loads.running or key not in self.loads.cache or key in self.pinned:
                continue
            self._forget(key, self._index.get(key))
            return True
        return False

    def _forget(self, key: tuple, index: int | None) -> None:
        session = self.loads.cache.pop(key, None)
        self.loads.forget_order(key)
        if session is not None and self.ctl.session is session:
            self.ctl.clear_session()
        if self.loads.selected == key and key not in self.loads.running:
            self.loads.selected = None
            self.panel.set_selected(None)
        if index is not None:
            self.panel.clear_done(index)

    def clear_cache(self) -> None:
        """Drop finished results. A pinned pull, and one that is still computing, stay."""
        self.loads.cache = {key: session for key, session in self.loads.cache.items() if key in self.pinned}
        self.loads.order = [
            key for key in self.loads.order if key in self.loads.running or key in self.loads.cache
        ]
        if self.loads.selected not in self.loads.cache:
            if self.loads.selected not in self.loads.running:
                self.loads.selected = None
                self.panel.set_selected(None)
            if self.active():
                self.ctl.clear_session()
        for index, key in enumerate(self.keys):
            if key not in self.loads.running and key not in self.loads.cache:
                self.panel.clear_done(index)

    def fail(self, _gen: int, path: str, key: tuple, message: str, *, run_id: int | None = None) -> None:
        if path != self.path or not self.loads.is_current(key, run_id):
            return
        self.loads.abandon(key)
        if key not in self._index:
            return
        self.panel.mark_error(self._index[key], message)

    def _drop_stale(self, path: str) -> None:
        """Drop results for this log whose identity is no longer a listed pull."""

        def gone(key: tuple) -> bool:
            return len(key) >= 2 and key[0] == "local" and key[1] == path and key not in self._index

        for key in [key for key in self.loads.cache if gone(key)]:
            del self.loads.cache[key]
        for key in [key for key in self.loads.running if gone(key)]:
            self.loads.abandon(key)
        self.loads.order = [key for key in self.loads.order if not gone(key)]
        if any(gone(key) for key in self.pinned):
            self.pinned = {key for key in self.pinned if not gone(key)}
            self._save_pins()

    def _migrate_legacy_pins(self) -> None:
        """Keep old range-only favorites, attaching the current fingerprint on first refresh."""
        changed = False
        for key in self.keys:
            legacy = key[:4]
            if len(key) == 5 and legacy in self.pinned:
                self.pinned.remove(legacy)
                self.pinned.add(key)
                changed = True
        if changed:
            self._save_pins()

    def _save_pins(self) -> None:
        if self.settings is None:
            return
        tokens = [token for key in self.pinned if (token := pin_token(key))]
        self.settings.setValue("pinned_local_pulls", tokens)

    def show_selected(self) -> None:
        """Show this card's cached pull again after the source switch comes back to local logs."""
        if self.loads.selected in self._index and self.loads.selected in self.loads.cache:
            self._restore_selection()

    def _restore_selection(self) -> None:
        selected = self.loads.selected
        if selected not in self._index:
            if selected is not None or self.ctl.session is not None:
                self.loads.selected = None
                if self.active():
                    self.ctl.clear_session()
            return
        self.panel.set_selected(self._index[selected])
        cached = self.loads.cache.get(selected)
        if self.active() and cached is not None and self.ctl.session is not cached:
            self.ctl.set_session(cached)
