# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Raid health card at the top of the right column.

The combat log has no raid subgroup, so players are packed five to a column.
Twenty players fill four columns at full size. A larger raid keeps five per
column and narrows every frame so the card stays the same height.

Each frame is one health bar: the name sits in the upper half, and selected
debuff icons sit in one row across the lower half. Icons start at about half
the frame and shrink together when they would overflow. Which debuffs exist
comes from the boss; nothing is drawn until the filter below the card is checked.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from PySide6.QtCore import QDateTime, QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core.models import Actor
from ..core.specs import Role, class_color, role_of_spec
from . import theme
from .controller import ReplayController
from .icon import aura_pixmap

GROUP_SIZE = 5
BASE_COLUMNS = 4
_PAD = 2.0
_GAP = 4.0
_ROLE_ORDER = {Role.TANK: 0, Role.HEALER: 1, Role.DPS: 2}
_MISSING_HP = "#101218"


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


def aura_metrics(width: float, height: float, count: int) -> tuple[float, float, float]:
    """``(pad, gap, size)`` for ``count`` icons inside one health frame.

    The default edge is about half the frame, inset so the health color stays visible
    beside and between the icons. The row shrinks instead of wrapping.
    """
    if count <= 0 or width <= 0 or height <= 0:
        return 0.0, 0.0, 0.0
    pad = max(3.0, height * 0.08)
    gap = max(2.0, height * 0.05)
    size = max(1.0, height * 0.5 - pad)
    inner = max(1.0, width - 2 * pad)
    span = count * size + max(0, count - 1) * gap
    if span > inner:
        size = max(1.0, (inner - max(0, count - 1) * gap) / count)
    return pad, gap, size


def _bar_color(frac: float) -> str:
    if frac > 0.5:
        return "#4caf50"
    if frac > 0.25:
        return "#e0a030"
    return "#e04040"


def _name_pt(cell_h: float) -> float:
    return max(7.0, min(11.0, cell_h * 0.28))


class _RaidGrid(QWidget):
    def __init__(
        self,
        ctl: ReplayController,
        chosen: Callable[[], set[str]] | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.ctl = ctl
        self._chosen = chosen or (lambda: set())
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
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if not players:
            if self.ctl.session is not None:
                painter.setPen(QColor(theme.TEXT_DIM))
                painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "暂无玩家")
            painter.end()
            return
        session = self.ctl.session
        columns = layout_columns(len(players))
        width, height = float(self.width()), float(self.height())
        selected = self.ctl.selected
        chosen = self._chosen()
        t = self.ctl.t
        for i, actor in enumerate(players):
            x, y, w, h = frame_rect(i, columns, width, height)
            icons = self._icons(session, actor.id, t, chosen)
            self._draw_frame(painter, session, actor, x, y, w, h, t, actor.id == selected, icons)
        painter.end()

    def _icons(self, session: object, actor_id: int, t: float, chosen: set[str]) -> list[QPixmap]:
        if not chosen:
            return []
        icons: list[QPixmap] = []
        for key, stem in session.analysis.active_frame_auras(actor_id, t):
            if key not in chosen:
                continue
            pixmap = aura_pixmap(stem)
            if pixmap is not None:
                icons.append(pixmap)
        return icons

    def _draw_frame(
        self,
        painter: QPainter,
        session: object,
        actor: Actor,
        x: float,
        y: float,
        w: float,
        h: float,
        t: float,
        selected: bool,
        icons: list[QPixmap],
    ) -> None:
        pen_w = 3.0 if selected else 1.0
        body = QRectF(x, y, w, h).adjusted(1.5, 1.5, -1.5, -1.5)
        path = QPainterPath()
        path.addRoundedRect(body, 4, 4)
        painter.setClipPath(path)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(_MISSING_HP))
        painter.drawRect(body)
        frac, dead = unit_hp(session, actor.id, t)
        if frac:
            painter.setBrush(QColor(_bar_color(frac)))
            painter.drawRect(QRectF(body.x(), body.y(), body.width() * frac, body.height()))
        self._draw_name(painter, actor, body, dead)
        self._draw_icons(painter, body, icons)
        painter.setClipping(False)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(theme.ACCENT if selected else theme.BORDER), pen_w))
        painter.drawRoundedRect(body, 4, 4)

    def _draw_name(self, painter: QPainter, actor: Actor, body: QRectF, dead: bool) -> None:
        pad, _gap, _size = aura_metrics(body.width(), body.height(), 1)
        upper = QRectF(body.x() + pad, body.y(), body.width() - 2 * pad, body.height() * 0.5)
        font = QFont(self.font().family())
        font.setPointSizeF(_name_pt(body.height()))
        font.setBold(True)
        painter.setFont(font)
        name = painter.fontMetrics().elidedText(
            actor.short_name, Qt.TextElideMode.ElideRight, int(upper.width())
        )
        align = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
        painter.setPen(QColor(0, 0, 0, 180))
        painter.drawText(upper.translated(1, 1), align, name)
        painter.setPen(QColor(theme.TEXT_DIM if dead else class_color(actor.class_name)))
        painter.drawText(upper, align, name)

    def _draw_icons(self, painter: QPainter, body: QRectF, icons: list[QPixmap]) -> None:
        if not icons:
            return
        pad, gap, size = aura_metrics(body.width(), body.height(), len(icons))
        edge = max(1, int(size))
        icon_y = body.y() + body.height() * 0.5 + (body.height() * 0.5 - edge) / 2
        x = body.x() + pad
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for pixmap in icons:
            rect = QRect(int(x), int(icon_y), edge, edge)
            painter.drawPixmap(rect, pixmap)
            painter.setPen(QPen(QColor("#101010"), 1))
            painter.drawRect(rect.adjusted(0, 0, -1, -1))
            x += edge + gap


class RaidFrames(QFrame):
    """Health for every player. Clicking a frame selects that player."""

    def __init__(
        self,
        ctl: ReplayController,
        chosen: Callable[[], set[str]] | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("logCard")
        self.setMinimumHeight(248)
        self.setMaximumHeight(380)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 8)
        lay.setSpacing(4)
        title = QLabel("团队血量")
        title.setObjectName("stackTitle")
        lay.addWidget(title)
        self.grid = _RaidGrid(ctl, chosen)
        lay.addWidget(self.grid, 1)

    def sizeHint(self) -> QSize:
        return QSize(280, 300)


class _AuraPopup(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("auraPopup")
        self.hidden_at = 0
        self._was_shown = False

    def showEvent(self, event) -> None:
        self._was_shown = True
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        if self._was_shown:
            self.hidden_at = QDateTime.currentMSecsSinceEpoch()
        self._was_shown = False
        super().hideEvent(event)


class AuraFilter(QFrame):
    """Dropdown under the raid card. Checks are per encounter and start empty."""

    changed = Signal()

    def __init__(self, ctl: ReplayController, settings=None, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctl = ctl
        self.settings = settings
        self.setObjectName("logCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._boxes: list[QCheckBox] = []
        self._encounter: int | None = None

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        title = QLabel("减益")
        title.setObjectName("stackTitle")
        lay.addWidget(title)
        self.button = QPushButton()
        self.button.setToolTip("勾选后显示在血条下半行。默认都不显示。")
        self.button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.button.clicked.connect(self._open)
        inner = QHBoxLayout(self.button)
        inner.setContentsMargins(10, 0, 10, 0)
        self.summary = QLabel("不显示")
        arrow = QLabel("▾")
        arrow.setObjectName("stackTitle")
        for label in (self.summary, arrow):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        inner.addWidget(self.summary, 1)
        inner.addWidget(arrow)
        lay.addWidget(self.button, 1)

        self.popup = _AuraPopup(self)
        self._popup_lay = QVBoxLayout(self.popup)
        self._popup_lay.setContentsMargins(8, 6, 8, 6)
        self._popup_lay.setSpacing(2)

        ctl.sessionChanged.connect(self._rebuild)
        self._rebuild()

    def sizeHint(self) -> QSize:
        return QSize(280, 40)

    def selected_keys(self) -> set[str]:
        return {str(box.property("auraKey")) for box in self._boxes if box.isChecked()}

    def _open(self) -> None:
        # The button click that dismisses the popup arrives after hideEvent.
        if QDateTime.currentMSecsSinceEpoch() - self.popup.hidden_at < 200:
            return
        self.popup.adjustSize()
        width = max(self.button.width(), self.popup.sizeHint().width(), 180)
        self.popup.setMinimumWidth(width)
        self.popup.adjustSize()
        origin = self.button.mapToGlobal(QPoint(0, self.button.height()))
        screen = self.button.screen()
        if screen is not None and origin.y() + self.popup.height() > screen.availableGeometry().bottom():
            origin = self.button.mapToGlobal(QPoint(0, -self.popup.height()))
        self.popup.move(origin)
        self.popup.show()

    def _rebuild(self) -> None:
        for box in self._boxes:
            box.blockSignals(True)
        self.popup.hide()
        while self._popup_lay.count():
            item = self._popup_lay.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._boxes = []
        session = self.ctl.session
        auras = () if session is None else tuple(session.analysis.frame_auras)
        self._encounter = None if session is None else int(session.data.fight.encounter_id)
        if not auras:
            note = QLabel("当前首领没有可筛选的减益")
            self._popup_lay.addWidget(note)
            self._set_summary()
            return
        saved = self._load()
        for aura in auras:
            box = QCheckBox(aura.label)
            box.setProperty("auraKey", aura.key)
            if aura.tip:
                box.setToolTip(aura.tip)
            pixmap = aura_pixmap(aura.spells[0][1]) if aura.spells else None
            if pixmap is not None:
                box.setIcon(QIcon(pixmap))
                box.setIconSize(QSize(18, 18))
            box.blockSignals(True)
            box.setChecked(aura.key in saved)
            box.blockSignals(False)
            box.toggled.connect(self._on_toggled)
            self._popup_lay.addWidget(box)
            self._boxes.append(box)
        self._set_summary()

    def _on_toggled(self, _checked: bool) -> None:
        self._set_summary()
        self._save()
        self.changed.emit()

    def _set_summary(self) -> None:
        labels = [box.text() for box in self._boxes if box.isChecked()]
        self.summary.setText("、".join(labels) if labels else "不显示")

    def _settings_key(self) -> str | None:
        if self._encounter is None:
            return None
        return f"frame_auras/{self._encounter}"

    def _load(self) -> set[str]:
        key = self._settings_key()
        if key is None or self.settings is None:
            return set()
        raw = str(self.settings.value(key, "") or "")
        return {part for part in raw.split(",") if part}

    def _save(self) -> None:
        key = self._settings_key()
        if key is None or self.settings is None:
            return
        self.settings.setValue(key, ",".join(sorted(self.selected_keys())))
