# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Room map: renders units from the position tracks plus the analysis' primitives at the current time."""

from __future__ import annotations

import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QWheelEvent
from PySide6.QtWidgets import QWidget

from ..bosses.base import (
    Circle,
    Cone,
    Dot,
    Label,
    Line,
    Path,
    Prim,
    UnitFlash,
    UnitRing,
    UnitStyle,
    UnitTag,
    fmt_time,
)
from ..core.markers import style_of
from ..core.specs import Role, class_color, role_of_spec
from . import theme
from .controller import ReplayController
from .stack_order import boss_ids_of, merge_back_to_front, present_stack_groups, stack_group

# A beam sitting in another room must not set the camera. Markers within this many
# yards of where players actually stood are the ones around the pull.
_MARKER_PAD = 80.0
# A press that moves less than this is a click, not a pan.
_CLICK_PX = 5.0
_PLATE_W = 172.0
_PLATE_H = 38.0
_PLATE_GAP = 12.0
# Carry and ghost routes: small dots, packed tight, so they do not read as the cone dashes.
_PATH_DOT_PX = 2.2
_PATH_DOT_PITCH_PX = 3.4
_PATH_POINTS = 4096
_PATH_DOTS = 2048
_GRID_LINES = 128


def frame_bounds(
    player_box: tuple[float, float, float, float],
    markers: list[tuple[float, float]],
    pad: float = _MARKER_PAD,
) -> tuple[float, float, float, float]:
    """Square view around the players, expanded by nearby world markers only."""
    x0, x1, y0, y1 = player_box
    if not all(math.isfinite(v) for v in player_box):
        return (-50.0, 50.0, -50.0, 50.0)
    original = player_box
    for x, y in markers:
        if original[0] - pad <= x <= original[1] + pad and original[2] - pad <= y <= original[3] + pad:
            x0, x1 = min(x0, x), max(x1, x)
            y0, y1 = min(y0, y), max(y1, y)
    cx, cy = x0 / 2 + x1 / 2, y0 / 2 + y1 / 2
    half = max(x1 - x0, y1 - y0, 1.0) / 2 + 6
    if not all(math.isfinite(v) for v in (cx - half, cx + half, cy - half, cy + half)):
        return (-50.0, 50.0, -50.0, 50.0)
    return (cx - half, cx + half, cy - half, cy + half)


def _prim_inside(an, pr: Prim) -> bool:
    if isinstance(pr, Line):
        return an.in_arena(pr.x1, pr.y1) and an.in_arena(pr.x2, pr.y2)
    if isinstance(pr, Path):
        return any(an.in_arena(x, y) for x, y in pr.points)
    if isinstance(pr, (Circle, Cone, Dot, Label)):
        return an.in_arena(pr.x, pr.y)
    return True


def qcolor(c: str, alpha: float = 1.0) -> QColor:
    q = QColor(c)
    q.setAlphaF(max(0.0, min(1.0, alpha)))
    return q


def _top_round_rect(x: float, y: float, w: float, h: float, radius: float) -> QPainterPath:
    """Rect flush with its bottom edge; only the top corners are rounded."""
    r = min(radius, w / 2, h / 2)
    path = QPainterPath()
    path.moveTo(x, y + h)
    path.lineTo(x, y + r)
    path.arcTo(x, y, 2 * r, 2 * r, 180, -90)
    path.lineTo(x + w - r, y)
    path.arcTo(x + w - 2 * r, y, 2 * r, 2 * r, 90, -90)
    path.lineTo(x + w, y + h)
    path.closeSubpath()
    return path


def _dots_along(points: list[QPointF], pitch: float, budget: int = _PATH_DOTS) -> list[QPointF]:
    """Evenly spaced points along a screen polyline, including the tip."""
    if not points or not math.isfinite(pitch) or pitch <= 0 or budget < 2:
        return []
    points = points[:_PATH_POINTS]
    if any(not math.isfinite(v) for pt in points for v in (pt.x(), pt.y())):
        return []
    lengths = [math.hypot(b.x() - a.x(), b.y() - a.y()) for a, b in zip(points, points[1:], strict=False)]
    total = sum(lengths)
    if not math.isfinite(total):
        return []
    pitch = max(pitch, total / (budget - 1))
    out = [points[0]]
    carry = 0.0
    prev = points[0]
    for cur in points[1:]:
        dx = cur.x() - prev.x()
        dy = cur.y() - prev.y()
        seg = math.hypot(dx, dy)
        if seg < 1e-4:
            prev = cur
            continue
        walked = 0.0
        while carry + seg - walked >= pitch and len(out) < budget - 1:
            walked += pitch - carry
            t = walked / seg
            out.append(QPointF(prev.x() + dx * t, prev.y() + dy * t))
            carry = 0.0
        carry += seg - walked
        prev = cur
        if len(out) >= budget - 1:
            break
    tip = points[-1]
    last = out[-1]
    if math.hypot(tip.x() - last.x(), tip.y() - last.y()) > pitch * 0.45:
        out.append(tip)
    return out


class MapView(QWidget):
    def __init__(self, ctl: ReplayController, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctl = ctl
        self.setMinimumSize(420, 420)
        self.setMouseTracking(True)
        self._zoom = 1.0
        self._pan = QPointF(0, 0)
        self._press: QPointF | None = None
        self._drag: QPointF | None = None
        self._hits: dict[int, tuple[QPointF, float]] = {}
        self._bounds = (-40.0, 40.0, -40.0, 40.0)
        ctl.sessionChanged.connect(self._on_session)
        ctl.analysisChanged.connect(self._on_analysis)
        ctl.selectionChanged.connect(self.update)
        ctl.timeChanged.connect(lambda _t: self.update())
        ctl.layersChanged.connect(self.update)
        ctl.optionsChanged.connect(self.update)
        ctl.unitOrderChanged.connect(self.update)
        self._blink = QTimer(self)
        self._blink.setInterval(40)
        self._blink.timeout.connect(self.update)

    # -- geometry -----------------------------------------------------------------------------

    def _on_session(self) -> None:
        self._zoom = 1.0
        self._pan = QPointF(0, 0)
        self._on_analysis()

    def _on_analysis(self) -> None:
        self._hits = {}
        s = self.ctl.session
        if s is None:
            return
        if s.analysis.arena and all(math.isfinite(v) for v in s.analysis.arena):
            self._bounds = s.analysis.arena
        else:
            box = s.tracks.bounds([p.id for p in s.data.players()], 1, 99)
            self._bounds = frame_bounds(box, [(m.x, m.y) for m in s.data.markers])
        self.update()

    def _scale(self) -> float:
        x0, x1, y0, y1 = self._bounds
        return min(self.width() / max(1.0, y1 - y0), self.height() / max(1.0, x1 - x0)) * 0.94 * self._zoom

    def w2s(self, x: float, y: float) -> QPointF:
        x0, x1, y0, y1 = self._bounds
        s = self._scale()
        u = ((y0 + y1) / 2 - y) * s
        v = ((x0 + x1) / 2 - x) * s
        return QPointF(self.width() / 2 + u + self._pan.x(), self.height() / 2 + v + self._pan.y())

    def yd(self, r: float) -> float:
        return r * self._scale()

    @staticmethod
    def qt_angle(facing: float) -> float:
        """WoW facing -> Qt arc angle in degrees (0 = 3 o'clock, counter-clockwise)."""
        return math.degrees(math.atan2(math.cos(facing), -math.sin(facing)))

    # -- input --------------------------------------------------------------------------------

    def wheelEvent(self, e: QWheelEvent) -> None:
        factor = 1.15 ** (e.angleDelta().y() / 120)
        new_zoom = max(0.4, min(8.0, self._zoom * factor))
        f = new_zoom / self._zoom
        c = QPointF(self.width() / 2, self.height() / 2) + self._pan
        p = e.position()
        self._pan = self._pan + (p - c) * (1 - f)
        self._zoom = new_zoom
        self.update()

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._press = e.position()
            self._drag = None

    def mouseMoveEvent(self, e) -> None:
        if self._press is None:
            return
        if self._drag is None:
            delta = e.position() - self._press
            if delta.x() * delta.x() + delta.y() * delta.y() < _CLICK_PX * _CLICK_PX:
                return
            self._drag = self._press
        self._pan = self._pan + (e.position() - self._drag)
        self._drag = e.position()
        self.update()

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self._press is not None and self._drag is None:
            self._pick(self._press)
        self._press = None
        self._drag = None

    def _pick(self, pos: QPointF) -> None:
        hit: int | None = None
        best = 0.0
        for aid, (center, radius) in self._hits.items():
            dx = pos.x() - center.x()
            dy = pos.y() - center.y()
            dist = dx * dx + dy * dy
            if dist <= (radius + 3) ** 2 and (hit is None or dist < best):
                hit = aid
                best = dist
        self.ctl.select(hit)

    def mouseDoubleClickEvent(self, e) -> None:
        self._zoom = 1.0
        self._pan = QPointF(0, 0)
        self.update()

    # -- painting -----------------------------------------------------------------------------

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(theme.BG))
        s = self.ctl.session
        if s is None:
            p.setPen(QColor(theme.TEXT_DIM))
            p.setFont(QFont(self.font().family(), 13))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "打开本地战斗日志，或者使用 WCL API")
            return
        t = self.ctl.t
        an = s.analysis
        self._draw_arena(p)

        prims = [pr for pr in an.overlays_at(t) if self.ctl.layer_on(pr.layer) and _prim_inside(an, pr)]
        paths = [pr for pr in prims if isinstance(pr, Path)]
        under = [pr for pr in prims if isinstance(pr, (Circle, Cone))]
        lines = [pr for pr in prims if isinstance(pr, Line)]
        dots = [pr for pr in prims if isinstance(pr, Dot)]
        over = [pr for pr in prims if isinstance(pr, (Label, UnitRing, UnitTag, UnitFlash))]
        for pr in paths:
            self._draw_path(p, pr)
        for pr in under:
            self._draw_area(p, pr)
        for pr in lines:
            self._draw_line(p, pr)
        self._draw_markers(p, t)
        loose = [pr for pr in dots if not pr.stack]
        stacked: dict[str, list[Dot]] = {}
        for pr in dots:
            if pr.stack:
                stacked.setdefault(pr.stack, []).append(pr)
        for pr in loose:
            self._draw_dot(p, pr)

        unit_pos: dict[int, tuple[QPointF, float]] = {}
        buckets: dict[str, list[int]] = {}
        for aid in an.units_at(t):
            buckets.setdefault(stack_group(an, aid), []).append(aid)
        for actor in s.data.players():
            buckets.setdefault("player", []).append(actor.id)
        present = {group for group, _label in present_stack_groups(an)}
        present.update(buckets)
        present.update(stacked)
        for key in merge_back_to_front(self.ctl.unit_order, present):
            for pr in stacked.get(key, []):
                self._draw_dot(p, pr)
            draw = self._draw_player if key == "player" else self._draw_npc
            for aid in buckets.get(key, []):
                draw(p, aid, t, unit_pos)
        for pr in over:
            self._draw_over(p, pr, unit_pos)
        self._hits = {
            aid: spot
            for aid, spot in unit_pos.items()
            if (actor := s.data.actors.get(aid)) is not None and (actor.is_player or actor.hostile)
        }
        blinking = any(isinstance(pr, UnitFlash) for pr in over)
        if blinking and not self._blink.isActive():
            self._blink.start()
        elif not blinking and self._blink.isActive():
            self._blink.stop()

        self._draw_hud(p, t)
        if self.ctl.options.get("hp", True):
            self._draw_bars(p, t)
        if self.ctl.options.get("key"):
            self._draw_key(p, _PLATE_H if self.ctl.selected is not None else 0.0)
        self._draw_target(p, s, t)
        p.end()

    def _draw_arena(self, p: QPainter) -> None:
        x0, x1, y0, y1 = self._bounds
        tl = self.w2s(x1, y1)
        br = self.w2s(x0, y0)
        rect = QRectF(tl, br)
        p.setPen(QPen(QColor(theme.BORDER), 1.5))
        p.setBrush(QColor(theme.ARENA))
        p.drawRoundedRect(rect, 10, 10)
        p.setPen(QPen(QColor(theme.GRID), 1))
        span = max(x1 - x0, y1 - y0)
        if not math.isfinite(span) or span <= 0:
            return
        step = max(10.0, span / (_GRID_LINES - 1), 48.0 / max(self._scale(), 1e-300))
        gx = math.ceil(x0 / step) * step
        for i in range(_GRID_LINES):
            value = gx + i * step
            if value >= x1:
                break
            a, b = self.w2s(value, y1), self.w2s(value, y0)
            p.drawLine(a, b)
        gy = math.ceil(y0 / step) * step
        for i in range(_GRID_LINES):
            value = gy + i * step
            if value >= y1:
                break
            a, b = self.w2s(x1, value), self.w2s(x0, value)
            p.drawLine(a, b)

    def _pen(self, color: str, alpha: float, width: float, dashed: bool) -> QPen:
        pen = QPen(qcolor(color, alpha), width)
        if dashed:
            pen.setStyle(Qt.PenStyle.DashLine)
        return pen

    def _draw_area(self, p: QPainter, pr: Circle | Cone) -> None:
        c = self.w2s(pr.x, pr.y)
        r = self.yd(pr.r if isinstance(pr, Circle) else pr.radius)
        p.setPen(self._pen(pr.color, pr.line_alpha, getattr(pr, "width", 1.5), pr.dashed))
        p.setBrush(qcolor(pr.color, pr.fill_alpha))
        if isinstance(pr, Circle):
            p.drawEllipse(c, r, r)
            label_at = QPointF(c.x(), c.y() + r + 10)
        else:
            path = QPainterPath(c)
            rect = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)
            qa = self.qt_angle(pr.direction)
            path.arcTo(rect, qa - pr.angle_deg / 2, pr.angle_deg)
            path.closeSubpath()
            p.drawPath(path)
            dx, dy = -math.sin(pr.direction), -math.cos(pr.direction)
            label_at = QPointF(c.x() + dx * r * 0.6, c.y() + dy * r * 0.6)
        if pr.label:
            self._text(p, label_at, pr.label, pr.color, 8, boxed=False)

    def _draw_line(self, p: QPainter, pr: Line) -> None:
        p.setPen(self._pen(pr.color, pr.alpha, pr.width, pr.dashed))
        p.drawLine(self.w2s(pr.x1, pr.y1), self.w2s(pr.x2, pr.y2))

    def _draw_path(self, p: QPainter, pr: Path) -> None:
        an = self.ctl.session.analysis
        run: list[tuple[float, float]] = []
        dots_left = _PATH_DOTS

        def flush() -> None:
            nonlocal dots_left
            if len(run) < 2:
                run.clear()
                return
            screen = [self.w2s(x, y) for x, y in run]
            if pr.dashed:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(qcolor(pr.color, pr.alpha))
                radius = _PATH_DOT_PX / 2
                centers = _dots_along(screen, _PATH_DOT_PITCH_PX, dots_left)
                dots_left -= len(centers)
                for center in centers:
                    p.drawEllipse(center, radius, radius)
            else:
                path = QPainterPath()
                path.moveTo(screen[0])
                for point in screen[1:]:
                    path.lineTo(point)
                pen = self._pen(pr.color, pr.alpha, pr.width, False)
                pen.setCapStyle(Qt.PenCapStyle.FlatCap)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(path)
            run.clear()

        stride = max(1, math.ceil(len(pr.points) / _PATH_POINTS))
        for x, y in pr.points[::stride]:
            if an.in_arena(x, y):
                run.append((x, y))
            else:
                flush()
        flush()

    def _draw_markers(self, p: QPainter, t: float) -> None:
        s = self.ctl.session
        if s is None:
            return
        for m in s.data.markers_at(t):
            if not s.analysis.in_arena(m.x, m.y):
                continue
            st = style_of(m.index)
            self._draw_marker_icon(p, self.w2s(m.x, m.y), 8.0, st.shape, st.color)

    def _draw_marker_icon(self, p: QPainter, c: QPointF, r: float, shape: str, color: str) -> None:
        alpha = 0.45
        p.setPen(QPen(qcolor("#141418", alpha), 1.2))
        p.setBrush(qcolor(color, alpha))
        if shape == "square":
            p.drawRoundedRect(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r), 1.5, 1.5)
        elif shape == "circle":
            p.drawEllipse(c, r, r)
        elif shape == "cross":
            p.setPen(
                QPen(qcolor(color, alpha), max(2.2, r * 0.42), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            )
            d = r * 0.85
            p.drawLine(QPointF(c.x() - d, c.y() - d), QPointF(c.x() + d, c.y() + d))
            p.drawLine(QPointF(c.x() - d, c.y() + d), QPointF(c.x() + d, c.y() - d))
        elif shape == "skull":
            p.drawEllipse(c, r, r * 0.95)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(qcolor("#1a1a1a", alpha))
            eye = r * 0.22
            p.drawEllipse(QPointF(c.x() - r * 0.32, c.y() - r * 0.12), eye, eye)
            p.drawEllipse(QPointF(c.x() + r * 0.32, c.y() - r * 0.12), eye, eye)
            p.drawRoundedRect(QRectF(c.x() - r * 0.16, c.y() + r * 0.28, r * 0.32, r * 0.22), 1, 1)
        else:
            p.drawPath(self._marker_path(shape, c, r))

    def _marker_path(self, shape: str, c: QPointF, r: float) -> QPainterPath:
        path = QPainterPath()
        if shape == "triangle":
            path.moveTo(c.x(), c.y() - r)
            path.lineTo(c.x() + r * 0.92, c.y() + r * 0.72)
            path.lineTo(c.x() - r * 0.92, c.y() + r * 0.72)
        elif shape == "diamond":
            path.moveTo(c.x(), c.y() - r)
            path.lineTo(c.x() + r * 0.78, c.y())
            path.lineTo(c.x(), c.y() + r)
            path.lineTo(c.x() - r * 0.78, c.y())
        elif shape == "star":
            for i in range(10):
                ang = -math.pi / 2 + i * math.pi / 5
                rad = r if i % 2 == 0 else r * 0.42
                pt = QPointF(c.x() + math.cos(ang) * rad, c.y() + math.sin(ang) * rad)
                if i == 0:
                    path.moveTo(pt)
                else:
                    path.lineTo(pt)
        elif shape == "moon":
            path.addEllipse(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r))
            cut = QPainterPath()
            cut.addEllipse(QRectF(c.x() - r * 0.15, c.y() - r * 0.85, r * 1.7, r * 1.7))
            path = path.subtracted(cut)
        path.closeSubpath()
        return path

    def _draw_dot(self, p: QPainter, pr: Dot) -> None:
        c = self.w2s(pr.x, pr.y)
        r = pr.size_px / 2 * max(0.8, min(1.6, self._zoom**0.5))
        if pr.glow:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(qcolor(pr.color, 0.25))
            p.drawEllipse(c, r * 1.6, r * 1.6)
        if pr.dashed_ring:
            p.setPen(self._pen(pr.color, 0.9, 1.3, True))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(c, r * 1.45, r * 1.45)
        p.setPen(QPen(QColor(pr.outline), 1.5))
        grad_c = QColor(pr.color)
        p.setBrush(QBrush(grad_c))
        p.drawEllipse(c, r, r)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qcolor("#ffffff", 0.35))
        p.drawEllipse(QPointF(c.x() - r * 0.3, c.y() - r * 0.3), r * 0.35, r * 0.35)
        if pr.label:
            self._text(p, QPointF(c.x(), c.y() + r + 9), pr.label, pr.color, 8)

    def _unit_radius(self, base: float) -> float:
        return base * max(0.75, min(1.7, self._zoom**0.5))

    def _draw_npc(self, p: QPainter, aid: int, t: float, out: dict) -> None:
        s = self.ctl.session
        pose = s.tracks.pose(aid, t)
        if pose is None or not s.analysis.in_arena(pose.x, pose.y):
            return
        actor = s.data.actors[aid]
        style = s.analysis.unit_styles.get(aid)
        if style is None:
            is_boss = aid in boss_ids_of(s.analysis)
            style = UnitStyle(
                "#c0392b" if not is_boss else "#7a5c2e",
                17 if is_boss else 9,
                actor.name if is_boss else "",
                "#f0c75e" if is_boss else "#1a1a1a",
                show_facing=is_boss,
                hp_bar=not is_boss,
            )
        c = self.w2s(pose.x, pose.y)
        r = self._unit_radius(style.radius_px)
        out[aid] = (c, r)
        if style.show_facing:
            dx, dy = -math.sin(pose.facing), -math.cos(pose.facing)
            p.setPen(QPen(qcolor(style.border, 0.9), 3))
            p.drawLine(c, QPointF(c.x() + dx * (r + 7), c.y() + dy * (r + 7)))
        p.setPen(QPen(QColor(style.border), 2.5 if style.show_facing else 1.5))
        p.setBrush(qcolor(style.color, style.fill_alpha))
        p.drawEllipse(c, r, r)
        mark = s.analysis.unit_glyph_at(aid, t)
        if mark:
            self._draw_mark(p, c, r, mark[0], mark[1])
        if style.label and (self.ctl.options.get("names") or style.show_facing):
            self._text(p, QPointF(c.x(), c.y() + r + 10), style.label, "#e8e8e8", 8)
        if style.hp_bar and self.ctl.options.get("hp", True) and pose.max_hp > 0:
            self._hp_bar(p, c, r, pose.hp_frac)

    def _draw_mark(self, p: QPainter, c: QPointF, r: float, text: str, color: str) -> None:
        p.setPen(QColor(color))
        f = QFont(self.font().family(), max(7, int(r * 0.9)))
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r), Qt.AlignmentFlag.AlignCenter, text)

    def _draw_player(self, p: QPainter, aid: int, t: float, out: dict) -> None:
        s = self.ctl.session
        pose = s.tracks.pose(aid, t)
        if pose is None or not s.analysis.in_arena(pose.x, pose.y):
            return
        actor = s.data.actors[aid]
        dead = s.tracks.is_dead(aid, t)
        c = self.w2s(pose.x, pose.y)
        r = self._unit_radius(10)
        out[aid] = (c, r)
        color = class_color(actor.class_name)
        if dead:
            self._draw_death_x(p, c, r)
            if self.ctl.options.get("names"):
                self._text(p, QPointF(c.x(), c.y() + r + 9), actor.short_name, color, 8)
            return
        role = role_of_spec(actor.spec_id)
        border = {Role.TANK: "#9fb4ff", Role.HEALER: "#7dffa8"}.get(role, "#0d0d0d")
        p.setPen(QPen(QColor(border), 2 if role is not Role.DPS else 1.5))
        p.setBrush(qcolor(color))
        p.drawEllipse(c, r, r)
        p.setPen(QColor("#111111"))
        f = QFont(self.font().family(), max(7, int(r * 0.8)))
        f.setBold(True)
        p.setFont(f)
        p.drawText(
            QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r),
            Qt.AlignmentFlag.AlignCenter,
            actor.short_name[:1],
        )
        self._draw_facing(p, c, r, pose.facing)
        if self.ctl.options.get("names"):
            self._text(p, QPointF(c.x(), c.y() + r + 9), actor.short_name, color, 8)
        if self.ctl.options.get("player_hp") and pose.max_hp > 0:
            self._hp_bar(p, c, r, pose.hp_frac, offset=r + (16 if self.ctl.options.get("names") else 4))

    def _draw_death_x(self, p: QPainter, c: QPointF, r: float) -> None:
        d = r * 0.9
        p.setBrush(Qt.BrushStyle.NoBrush)
        a = QPointF(c.x() - d, c.y() - d)
        b = QPointF(c.x() + d, c.y() + d)
        left = QPointF(c.x() - d, c.y() + d)
        right = QPointF(c.x() + d, c.y() - d)
        for width, color in ((4.2, "#14161b"), (2.4, "#e04040")):
            p.setPen(QPen(QColor(color), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(a, b)
            p.drawLine(left, right)

    def _draw_facing(self, p: QPainter, c: QPointF, r: float, facing: float) -> None:
        """Small triangle sitting on the circle rim. Screen axes: u = -sin, v = -cos."""
        dx, dy = -math.sin(facing), -math.cos(facing)
        px, py = -dy, dx
        base = r + 0.6
        half = 3.0
        tip = QPointF(c.x() + dx * (r + 6.5), c.y() + dy * (r + 6.5))
        left = QPointF(c.x() + dx * base + px * half, c.y() + dy * base + py * half)
        right = QPointF(c.x() + dx * base - px * half, c.y() + dy * base - py * half)
        head = QPainterPath()
        head.moveTo(tip)
        head.lineTo(left)
        head.lineTo(right)
        head.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#f4f4f4"))
        p.drawPath(head)

    def _hp_bar(self, p: QPainter, c: QPointF, r: float, frac: float, offset: float | None = None) -> None:
        w = max(16.0, r * 2.2)
        y = c.y() + (offset if offset is not None else r + 3)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#000000"))
        p.drawRect(QRectF(c.x() - w / 2, y, w, 3.5))
        col = "#4caf50" if frac > 0.5 else "#e0a030" if frac > 0.25 else "#e04040"
        p.setBrush(QColor(col))
        p.drawRect(QRectF(c.x() - w / 2, y, w * max(0.0, min(1.0, frac)), 3.5))

    def _draw_target(self, p: QPainter, s, t: float) -> None:
        if self.ctl.selected is None:
            return
        actor = s.data.actors.get(self.ctl.selected)
        if actor is None:
            return
        dst = s.targets.at(self.ctl.selected, t)
        target = s.data.actors.get(dst) if dst is not None else None
        total = _PLATE_W * 2 + _PLATE_GAP
        x = max(0.0, (self.width() - total) / 2)
        y = self.height() - _PLATE_H
        self._draw_plate(p, s, t, actor, x, y)
        self._draw_plate(p, s, t, target, x + _PLATE_W + _PLATE_GAP, y)

    def _draw_plate(self, p: QPainter, s, t: float, actor, x: float, y: float) -> None:
        w, h = _PLATE_W, _PLATE_H
        if actor is None:
            color = theme.TEXT_DIM
            name = "无目标"
            frac = 0.0
            dead = True
            label = "—"
        else:
            color = self._plate_color(s, actor)
            name = actor.short_name
            dead = s.tracks.is_dead(actor.id, t)
            pose = s.tracks.pose(actor.id, t)
            frac = 0.0 if dead or pose is None or pose.max_hp <= 0 else pose.hp_frac
            label = "—" if pose is None or pose.max_hp <= 0 else f"{frac * 100:.0f}%"
        path = _top_round_rect(x, y, w, h, 6)
        p.setPen(QPen(QColor(color), 1.5))
        p.setBrush(QColor(theme.PANEL))
        p.drawPath(path)

        name_font = QFont(self.font().family(), 9)
        name_font.setBold(True)
        p.setFont(name_font)
        p.setPen(QColor(color))
        p.drawText(
            QRectF(x + 8, y + 3, w - 48, 16),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            name,
        )
        p.setFont(QFont(self.font().family(), 8))
        p.setPen(QColor(theme.TEXT_DIM))
        p.drawText(
            QRectF(x + w - 44, y + 3, 36, 16),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
            label,
        )

        bar = QRectF(x + 8, y + 22, w - 16, 8)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#000000"))
        p.drawRect(bar)
        if dead:
            fill = "#555555"
        elif frac > 0.5:
            fill = "#4caf50"
        elif frac > 0.25:
            fill = "#e0a030"
        else:
            fill = "#e04040"
        p.setBrush(QColor(fill))
        p.drawRect(QRectF(bar.x(), bar.y(), bar.width() * max(0.0, min(1.0, frac)), bar.height()))

    def _plate_color(self, s, actor) -> str:
        if actor.is_player:
            return class_color(actor.class_name)
        style = s.analysis.unit_styles.get(actor.id)
        if style is not None:
            return style.color
        return "#c0392b"

    def _draw_over(self, p: QPainter, pr: Prim, units: dict) -> None:
        if isinstance(pr, Label):
            c = self.w2s(pr.x, pr.y)
            self._text(p, QPointF(c.x(), c.y() + pr.dy_px), pr.text, pr.color, pr.size_px, boxed=pr.boxed)
        elif isinstance(pr, UnitRing):
            if pr.actor_id not in units:
                return
            c, r = units[pr.actor_id]
            rr = r + (6 if pr.solid else 4)
            p.setBrush(Qt.BrushStyle.NoBrush)
            if pr.solid:
                p.setPen(QPen(QColor(pr.color), pr.width))
                p.drawEllipse(c, rr, rr)
                return
            p.setPen(QPen(qcolor(pr.color, 0.25), 3))
            p.drawEllipse(c, rr, rr)
            p.setPen(QPen(QColor(pr.color), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(
                QRectF(c.x() - rr, c.y() - rr, 2 * rr, 2 * rr),
                90 * 16,
                int(-360 * 16 * max(0.0, min(1.0, pr.progress))),
            )
        elif isinstance(pr, UnitFlash):
            self._draw_flash(p, pr, units)
        elif isinstance(pr, UnitTag):
            if pr.actor_id not in units:
                return
            c, r = units[pr.actor_id]
            self._text(p, QPointF(c.x(), c.y() - r - 9), pr.text, pr.color, 7.5, boxed=True)

    def _draw_flash(self, p: QPainter, pr: UnitFlash, units: dict) -> None:
        if pr.actor_id not in units:
            return
        c, r = units[pr.actor_id]
        pulse = 0.5 + 0.5 * math.sin(time.monotonic() * 10.0)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(qcolor(pr.color, 0.25 + 0.75 * pulse), 2.2 + 1.8 * pulse))
        p.drawEllipse(c, r, r)

    def _text(
        self, p: QPainter, at: QPointF, text: str, color: str, size: float, boxed: bool = False
    ) -> None:
        f = QFont(self.font().family())
        f.setPointSizeF(size)
        p.setFont(f)
        fm = p.fontMetrics()
        w = fm.horizontalAdvance(text) + 6
        h = fm.height()
        rect = QRectF(at.x() - w / 2, at.y() - h / 2, w, h)
        if boxed:
            p.setPen(QPen(qcolor(color, 0.7), 1))
            p.setBrush(qcolor("#0e0f13", 0.85))
            p.drawRoundedRect(rect, 3, 3)
        else:
            p.setPen(qcolor("#000000", 0.7))
            p.drawText(rect.translated(1, 1), Qt.AlignmentFlag.AlignCenter, text)
        p.setPen(QColor(color))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def _draw_hud(self, p: QPainter, t: float) -> None:
        an = self.ctl.session.analysis
        x, y = 14, 12
        f = QFont(self.font().family(), 20)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#ffffff"))
        p.drawText(QPointF(x, y + 24), fmt_time(t))
        y += 34
        f = QFont(self.font().family(), 9)
        f.setBold(True)
        p.setFont(f)
        for i, line in enumerate(an.hud_at(t)):
            if i > 0:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(line.color))
                p.drawRoundedRect(QRectF(x, y + 4, 8, 8), 2, 2)
                p.setPen(QColor("#e8e8e8"))
                p.drawText(QPointF(x + 14, y + 12), line.text)
            else:
                p.setPen(QColor(line.color))
                p.drawText(QPointF(x, y + 12), line.text)
            y += 18

    def _draw_bars(self, p: QPainter, t: float) -> None:
        bars = self.ctl.session.analysis.bars_at(t)
        if not bars:
            return
        w = 200
        x = self.width() - w - 12
        y = 12
        h = 14 + len(bars) * 30
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.setBrush(qcolor("#0e0f13", 0.85))
        p.drawRoundedRect(QRectF(x - 8, y - 6, w + 16, h), 8, 8)
        f = QFont(self.font().family(), 8)
        f.setBold(True)
        p.setFont(f)
        for b in bars:
            alpha = 0.45 if b.dim else 1.0
            p.setPen(qcolor("#e8e8e8", alpha))
            p.drawText(
                QRectF(x, y, w - 40, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, b.label
            )
            p.drawText(
                QRectF(x + w - 60, y, 60, 14),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                b.sub,
            )
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(qcolor("#2c2f36", alpha))
            p.drawRoundedRect(QRectF(x, y + 16, w, 6), 3, 3)
            p.setBrush(qcolor(b.color, alpha))
            p.drawRoundedRect(QRectF(x, y + 16, w * max(0.0, min(1.0, b.frac)), 6), 3, 3)
            y += 30

    def _draw_key(self, p: QPainter, lift: float = 0.0) -> None:
        lanes = self.ctl.session.analysis.lanes
        f = QFont(self.font().family(), 8)
        p.setFont(f)
        x, y = 14, self.height() - 14 - 16 * len(lanes) - lift
        for lane in lanes:
            on = self.ctl.layer_on(lane.id)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(qcolor(lane.color, 1.0 if on else 0.3))
            p.drawEllipse(QPointF(x + 5, y + 6), 5, 5)
            p.setPen(qcolor("#e8e8e8", 1.0 if on else 0.4))
            p.drawText(QPointF(x + 16, y + 10), f"{lane.name} — {lane.help}" if lane.help else lane.name)
            y += 16
