# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Raid health card at the top of the right column.

The combat log has no raid subgroup, so players are packed five to a column.
Twenty players fill four columns at full size. A larger raid keeps five per
column and narrows every frame so the card stays the same height.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QFrame, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ..core.models import Actor
from ..core.specs import Role, class_color, role_of_spec
from . import theme
from .controller import ReplayController

GROUP_SIZE = 5
BASE_COLUMNS = 4
_PAD = 2.0
_GAP = 4.0
_ROLE_ORDER = {Role.TANK: 0, Role.HEALER: 1, Role.DPS: 2}


def layout_columns(count: int) -> int:
    """Columns used to size each frame. Fewer than twenty players stay at the four-column size."""
    if count <= 0:
        return BASE_COLUMNS
    return max(BASE_COLUMNS, math.ceil(count / GROUP_SIZE))


def frame_rect(index: int, columns: int, width: float, height: float) -> tuple[float, float, float, float]:
    """Cell for ``index``, filled down a column and then into the next one."""
    gaps_x = _GAP * (columns - 1)
    gaps_y = _GAP * (GROUP_SIZE - 1)
    cell_w = (width - 2 * _PAD - gaps_x) / columns
    cell_h = (height - 2 * _PAD - gaps_y) / GROUP_SIZE
    col, row = divmod(index, GROUP_SIZE)
    x = _PAD + col * (cell_w + _GAP)
    y = _PAD + row * (cell_h + _GAP)
    return x, y, cell_w, cell_h


def order_players(players: list[Actor]) -> list[Actor]:
    """Tanks, then healers, then everyone else, each group by class and name."""
    return sorted(
        players,
        key=lambda a: (_ROLE_ORDER.get(role_of_spec(a.spec_id), 9), a.class_name or "", a.short_name, a.id),
    )


def unit_hp(session: object, actor_id: int, t: float) -> tuple[float | None, bool]:
    """Health fraction at ``t``. ``None`` means the log has no health yet. Dead units are 0."""
    tracks = session.tracks
    dead = bool(tracks.is_dead(actor_id, t))
    if dead:
        return 0.0, True
    pose = tracks.pose(actor_id, t)
    if pose is None or pose.max_hp <= 0:
        return None, False
    return max(0.0, min(1.0, float(pose.hp_frac))), False


def _name_pt(columns: int) -> float:
    return max(6.0, 9.0 * BASE_COLUMNS / columns)


def _bar_color(frac: float) -> str:
    if frac > 0.5:
        return "#4caf50"
    if frac > 0.25:
        return "#e0a030"
    return "#e04040"


class _RaidGrid(QWidget):
    def __init__(self, ctl: ReplayController, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctl = ctl
        self.setObjectName("raidGrid")
        self.setMouseTracking(False)
        ctl.sessionChanged.connect(self.update)
        ctl.timeChanged.connect(self.update)
        ctl.selectionChanged.connect(self.update)

    def _players(self) -> list[Actor]:
        session = self.ctl.session
        if session is None:
            return []
        return order_players(session.data.players())

    def _hit(self, pos: QPointF) -> int | None:
        players = self._players()
        if not players:
            return None
        columns = layout_columns(len(players))
        width, height = float(self.width()), float(self.height())
        for i, actor in enumerate(players):
            x, y, w, h = frame_rect(i, columns, width, height)
            if x <= pos.x() < x + w and y <= pos.y() < y + h:
                return actor.id
        return None

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.ctl.select(self._hit(event.position()))
        super().mousePressEvent(event)

    def paintEvent(self, _event) -> None:
        players = self._players()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not players:
            if self.ctl.session is not None:
                painter.setPen(QColor(theme.TEXT_DIM))
                painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "暂无玩家")
            painter.end()
            return
        session = self.ctl.session
        columns = layout_columns(len(players))
        width, height = float(self.width()), float(self.height())
        pt = _name_pt(columns)
        bar_h = max(4.0, 8.0 * BASE_COLUMNS / columns)
        selected = self.ctl.selected
        t = self.ctl.t
        for i, actor in enumerate(players):
            x, y, w, h = frame_rect(i, columns, width, height)
            self._draw_frame(painter, session, actor, x, y, w, h, pt, bar_h, t, actor.id == selected)
        painter.end()

    def _draw_frame(
        self,
        painter: QPainter,
        session: object,
        actor: Actor,
        x: float,
        y: float,
        w: float,
        h: float,
        pt: float,
        bar_h: float,
        t: float,
        selected: bool,
    ) -> None:
        rect = QRectF(x, y, w, h)
        painter.setPen(QPen(QColor(theme.ACCENT if selected else theme.BORDER), 1.5 if selected else 1))
        painter.setBrush(QColor("#3a3150" if selected else theme.PANEL_2))
        painter.drawRoundedRect(rect, 4, 4)

        pad = 4.0
        bar = QRectF(x + pad, y + h - pad - bar_h, max(1.0, w - 2 * pad), bar_h)
        name_rect = QRectF(x + pad, y + 1, max(1.0, w - 2 * pad), max(1.0, bar.top() - y - 2))
        frac, dead = unit_hp(session, actor.id, t)
        font = QFont(self.font().family())
        font.setPointSizeF(pt)
        font.setBold(True)
        painter.setFont(font)
        name = painter.fontMetrics().elidedText(
            actor.short_name, Qt.TextElideMode.ElideRight, int(name_rect.width())
        )
        painter.setPen(QColor(theme.TEXT_DIM if dead else class_color(actor.class_name)))
        painter.drawText(name_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#000000"))
        painter.drawRect(bar)
        if frac:
            painter.setBrush(QColor(_bar_color(frac)))
            painter.drawRect(QRectF(bar.x(), bar.y(), bar.width() * frac, bar.height()))


class RaidFrames(QFrame):
    """Name and health for every player. Clicking a frame selects that player."""

    def __init__(self, ctl: ReplayController, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("logCard")
        self.setMinimumHeight(180)
        self.setMaximumHeight(236)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 8)
        lay.setSpacing(4)
        title = QLabel("团队血量")
        title.setObjectName("stackTitle")
        lay.addWidget(title)
        self.grid = _RaidGrid(ctl)
        lay.addWidget(self.grid, 1)

    def sizeHint(self) -> QSize:
        return QSize(280, 220)
