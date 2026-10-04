# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Right-hand panels: RIGHT NOW (status at the current time) and WHAT HAPPENED (clickable event log)."""

from __future__ import annotations

import bisect
import html

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor, QTextFormat, QTextTable
from PySide6.QtWidgets import QTextBrowser, QTextEdit, QWidget

from ..bosses.base import Cell, Seg, fmt_time
from . import theme
from .controller import ReplayController


def _mix(color: str, other: str, f: float) -> str:
    def rgb(c: str) -> tuple[int, int, int]:
        c = c.lstrip("#")
        if len(c) == 3:
            c = "".join(ch * 2 for ch in c)
        if len(c) < 6:
            return (154, 160, 166)
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)

    a, b = rgb(color), rgb(other)
    r, g, bch = (round(x * (1 - f) + y * f) for x, y in zip(a, b, strict=True))
    return f"#{r:02x}{g:02x}{bch:02x}"


def seg_html(seg: Seg) -> str:
    text = html.escape(seg.text).replace("\n", "<br>")
    color = seg.color or theme.TEXT
    if seg.badge:
        return (
            f'<span style="background-color:{_mix(color, theme.PANEL, 0.72)}; color:{color};">'
            f"&nbsp;{text}&nbsp;</span>"
        )
    weight = "bold" if seg.bold else "normal"
    return f'<span style="color:{color}; font-weight:{weight};">{text}</span>'


def cell_html(c: Cell) -> str:
    if c.bar is not None:
        w = max(1, min(100, int(c.bar * 100)))
        color = c.color or theme.ACCENT
        return (
            f'<td width="60" valign="middle"><table width="100%" cellspacing="0" cellpadding="0" height="5">'
            f'<tr><td width="{w}%" bgcolor="{color}" height="5"></td>'
            f'<td width="{100 - w}%" bgcolor="{theme.PANEL_2}" height="5"></td></tr></table></td>'
        )
    return f'<td align="{c.align}">{seg_html(Seg(c.text, c.color, c.badge, c.bold))}</td>'


class StatusPanel(QTextBrowser):
    def __init__(self, ctl: ReplayController, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctl = ctl
        self.setOpenLinks(False)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._render)
        ctl.timeChanged.connect(lambda _t: self._timer.start() if not self._timer.isActive() else None)
        ctl.sessionChanged.connect(self._render)
        ctl.analysisChanged.connect(self._render)
        ctl.layersChanged.connect(self._render)

    def _render(self) -> None:
        s = self.ctl.session
        parts = [f'<p style="color:{theme.TEXT_DIM}; font-weight:bold;">RIGHT NOW</p>']
        if s is None:
            self.setHtml("".join(parts))
            return
        for sec in s.analysis.status_at(self.ctl.t):
            parts.append(
                f'<p style="color:{sec.color}; font-weight:bold; margin-top:8px;">{html.escape(sec.title)}</p>'
            )
            if sec.rows:
                parts.append('<table width="100%" cellspacing="0" cellpadding="2">')
                for row in sec.rows:
                    parts.append("<tr>" + "".join(cell_html(c) for c in row) + "</tr>")
                parts.append("</table>")
            if sec.note:
                parts.append(f'<p style="color:{theme.TEXT_DIM};">{html.escape(sec.note)}</p>')
        sb = self.verticalScrollBar().value()
        self.setHtml("".join(parts))
        self.verticalScrollBar().setValue(sb)


class EventLogPanel(QTextBrowser):
    HIGHLIGHT_MS = 2500

    def __init__(self, ctl: ReplayController, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctl = ctl
        self.setOpenLinks(False)
        self.anchorClicked.connect(self._on_anchor)
        self.document().setDefaultStyleSheet("a { text-decoration: none; }")
        self.document().setUndoRedoEnabled(False)
        self._key: tuple[int, int] | None = None
        self._times: list[int] = []
        self._visible_indices: list[int] = []
        self._table: QTextTable | None = None
        self._future_marks: list[QTextEdit.ExtraSelection] = []
        self._inline_backgrounds: list[list[QTextEdit.ExtraSelection]] = []
        ctl.sessionChanged.connect(self._on_session)
        ctl.analysisChanged.connect(self._on_session)
        ctl.timeChanged.connect(lambda _t: self._render())
        ctl.layersChanged.connect(lambda: self._render(force=True))

    def _on_session(self) -> None:
        s = self.ctl.session
        self._times = [e.t for e in s.analysis.log] if s else []
        self._render(force=True)

    def _on_anchor(self, url: QUrl) -> None:
        text = url.toString()
        if text.startswith("t:"):
            self.ctl.seek(int(text[2:]))

    def _render(self, force: bool = False) -> None:
        s = self.ctl.session
        if s is None:
            self._key = None
            self._visible_indices = []
            self._table = None
            self._future_marks = []
            self._inline_backgrounds = []
            self.setExtraSelections([])
            if force or not self.document().isEmpty():
                self.setHtml("")
            return
        t = self.ctl.t
        cur = bisect.bisect_right(self._times, t)
        lo = bisect.bisect_left(self._times, t - self.HIGHLIGHT_MS)
        key = (lo, cur)
        if key == self._key and not force:
            return
        if force or self._key is None:
            self._build_log()
        self._update_marks(lo, cur)
        self._key = key
        if self._visible_indices:
            visible_cur = bisect.bisect_left(self._visible_indices, cur)
            anchor_at = max(0, visible_cur - 3)
            self.scrollToAnchor(f"event-{self._visible_indices[anchor_at]}")

    def _build_log(self) -> None:
        """Build rich text only when its content or lane filtering changes."""
        s = self.ctl.session
        parts = [
            f'<p style="color:{theme.TEXT_DIM}; font-weight:bold;">WHAT HAPPENED · 点击跳转</p>',
            '<table width="100%" cellspacing="0" cellpadding="3">',
        ]
        visible = [(i, e) for i, e in enumerate(s.analysis.log) if not e.lane or self.ctl.layer_on(e.lane)]
        self._visible_indices = [i for i, _e in visible]
        for i, e in visible:
            name = f'<a name="event-{i}"></a>'
            body = "".join(seg_html(sg) for sg in e.segments)
            if e.sub:
                body += "<br>" + "".join(seg_html(sg) for sg in e.sub)
            parts.append(
                f'<tr><td width="38" valign="top">{name}<a href="t:{e.t}" style="color:{theme.TEXT_DIM};">'
                f'{fmt_time(e.t)}</a></td><td><a href="t:{e.t}">{body}</a></td></tr>'
            )
        parts.append("</table>")
        self.setHtml("".join(parts))
        self._table = next(
            (frame for frame in self.document().rootFrame().childFrames() if isinstance(frame, QTextTable)),
            None,
        )
        future = QTextCharFormat()
        future.setForeground(QColor(_mix(theme.TEXT_DIM, theme.PANEL, 0.4)))
        self._future_marks = [self._cell_mark(row, 0, future) for row in range(len(visible))]
        self._inline_backgrounds = [self._background_marks(row) for row in range(len(visible))]

    def _update_marks(self, lo: int, cur: int) -> None:
        """Paint time-dependent styles without changing or laying out the rich-text table."""
        if self._table is None:
            return
        indices = self._visible_indices
        start, stop = bisect.bisect_left(indices, lo), bisect.bisect_left(indices, cur)
        highlighted = QTextCharFormat()
        highlighted.setBackground(QColor(theme.PANEL_2))
        highlighted.setProperty(QTextFormat.Property.FullWidthSelection, True)
        marks = self._future_marks[stop:]
        marks.extend(
            self._cell_mark(row, column, highlighted) for row in range(start, stop) for column in range(2)
        )
        # The row tint must stay behind badge backgrounds already present in the rich text.
        marks.extend(mark for row in range(start, stop) for mark in self._inline_backgrounds[row])
        self.setExtraSelections(marks)

    def _cell_mark(self, row: int, column: int, fmt: QTextCharFormat) -> QTextEdit.ExtraSelection:
        cell = self._table.cellAt(row, column)
        mark = QTextEdit.ExtraSelection()
        mark.cursor = cell.firstCursorPosition()
        mark.cursor.setPosition(cell.lastCursorPosition().position(), QTextCursor.MoveMode.KeepAnchor)
        mark.format = fmt
        return mark

    def _background_marks(self, row: int) -> list[QTextEdit.ExtraSelection]:
        cell = self._table.cellAt(row, 1)
        start, end = cell.firstCursorPosition().position(), cell.lastCursorPosition().position()
        block = self.document().findBlock(start)
        marks = []
        while block.isValid() and block.position() < end:
            fragments = block.begin()
            while not fragments.atEnd():
                fragment = fragments.fragment()
                fmt = fragment.charFormat()
                if fmt.hasProperty(QTextFormat.Property.BackgroundBrush):
                    mark = QTextEdit.ExtraSelection()
                    mark.cursor = QTextCursor(self.document())
                    mark.cursor.setPosition(max(start, fragment.position()))
                    mark.cursor.setPosition(
                        min(end, fragment.position() + fragment.length()), QTextCursor.MoveMode.KeepAnchor
                    )
                    mark.format = QTextCharFormat()
                    mark.format.setBackground(fmt.background())
                    marks.append(mark)
                fragments += 1
            block = block.next()
        return marks
