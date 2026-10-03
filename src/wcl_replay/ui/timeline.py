# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Bottom timeline: phase header, one lane per mechanic (click the label to toggle it on the map)."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from ..bosses.base import fmt_time
from . import theme
from .controller import ReplayController
from .map_view import qcolor

LABEL_W = 140
HEADER_H = 20
ROW_H = 22


class TimelineWidget(QWidget):
    def __init__(self, ctl: ReplayController, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctl = ctl
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._scrubbing = False
        ctl.sessionChanged.connect(self._on_session)
        ctl.analysisChanged.connect(self._on_session)
        ctl.timeChanged.connect(lambda _t: self.update())
        ctl.layersChanged.connect(self.update)
        self._on_session()

    def _lanes(self):
        s = self.ctl.session
        return s.analysis.lanes if s else []

    def _on_session(self) -> None:
        self.setFixedHeight(HEADER_H + max(1, len(self._lanes())) * ROW_H + 8)
        self.update()

    def _plot_rect(self) -> QRectF:
        return QRectF(LABEL_W + 8, 0, max(10, self.width() - LABEL_W - 16), self.height())

    def _x_of(self, t: float) -> float:
        s = self.ctl.session
        r = self._plot_rect()
        dur = max(1, s.duration) if s else 1
        return r.left() + r.width() * (t / dur)

    def _t_of(self, x: float) -> float:
        s = self.ctl.session
        r = self._plot_rect()
        return max(0.0, min(1.0, (x - r.left()) / r.width())) * (s.duration if s else 0)

    # -- input --------------------------------------------------------------------------------

    def mousePressEvent(self, e) -> None:
        if self.ctl.session is None:
            return
        pos = e.position()
        if pos.x() < LABEL_W:
            row = int((pos.y() - HEADER_H) // ROW_H)
            lanes = self._lanes()
            if 0 <= row < len(lanes):
                self.ctl.toggle_layer(lanes[row].id)
            return
        self._scrubbing = True
        self.ctl.seek(self._t_of(pos.x()))

    def mouseMoveEvent(self, e) -> None:
        pos = e.position()
        if self._scrubbing:
            self.ctl.seek(self._t_of(pos.x()))
            return
        lanes = self._lanes()
        row = int((pos.y() - HEADER_H) // ROW_H)
        if pos.x() < LABEL_W and 0 <= row < len(lanes) and lanes[row].help:
            QToolTip.showText(e.globalPosition().toPoint(), lanes[row].help, self)
        elif pos.x() >= LABEL_W and 0 <= row < len(lanes):
            t = self._t_of(pos.x())
            best = None
            for it in lanes[row].items:
                d = abs(self._x_of(it.t) - pos.x())
                if it.label and d < 6 and (best is None or d < best[0]):
                    best = (d, it)
            if best:
                QToolTip.showText(
                    e.globalPosition().toPoint(), f"{fmt_time(best[1].t)}  {best[1].label}", self
                )
            else:
                QToolTip.showText(e.globalPosition().toPoint(), fmt_time(t), self)

    def mouseReleaseEvent(self, e) -> None:
        self._scrubbing = False

    # -- painting -----------------------------------------------------------------------------

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(theme.PANEL))
        s = self.ctl.session
        if s is None:
            return
        plot = self._plot_rect()
        f = QFont(self.font().family(), 8)
        p.setFont(f)

        for ph in s.analysis.phases:
            x = self._x_of(ph.t)
            p.setPen(QPen(qcolor("#b58cff", 0.6), 1, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(x, HEADER_H - 2), QPointF(x, self.height()))
            p.setPen(QColor("#b58cff"))
            p.drawText(QPointF(x + 3, 13), f"{ph.short or ph.name} {fmt_time(ph.t)}")

        for i, lane in enumerate(self._lanes()):
            y = HEADER_H + i * ROW_H
            on = self.ctl.layer_on(lane.id)
            row = QRectF(plot.left(), y + 2, plot.width(), ROW_H - 4)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(qcolor(lane.color, 0.07 if on else 0.03))
            p.drawRoundedRect(row, 3, 3)
            lab = QRectF(4, y + 2, LABEL_W - 4, ROW_H - 4)
            p.setPen(QPen(qcolor(lane.color, 0.8 if on else 0.3), 1))
            p.setBrush(qcolor(theme.PANEL_2, 1))
            p.drawRoundedRect(lab, 5, 5)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(qcolor(lane.color, 1.0 if on else 0.3))
            p.drawEllipse(QPointF(lab.left() + 10, lab.center().y()), 4.5, 4.5)
            p.setPen(qcolor("#e8e8e8", 1.0 if on else 0.45))
            p.drawText(lab.adjusted(20, 0, -18, 0), Qt.AlignmentFlag.AlignVCenter, lane.name)
            p.drawText(
                lab.adjusted(0, 0, -6, 0),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                "✓" if on else "",
            )
            self._draw_lane(p, lane, row, on)

        x = self._x_of(self.ctl.t)
        p.setPen(QPen(QColor("#ffffff"), 1.5))
        p.drawLine(QPointF(x, 0), QPointF(x, self.height()))

    def _draw_lane(self, p: QPainter, lane, row: QRectF, on: bool) -> None:
        alpha = 0.95 if on else 0.35
        if lane.series:
            vmax = max((v for _t, v in lane.series), default=0) or 1
            path = QPainterPath(QPointF(self._x_of(lane.series[0][0]), row.bottom()))
            prev_v = 0.0
            for t, v in lane.series:
                x = self._x_of(t)
                path.lineTo(QPointF(x, row.bottom() - row.height() * prev_v / vmax))
                path.lineTo(QPointF(x, row.bottom() - row.height() * v / vmax))
                prev_v = v
            path.lineTo(QPointF(row.right(), row.bottom() - row.height() * prev_v / vmax))
            path.lineTo(QPointF(row.right(), row.bottom()))
            path.closeSubpath()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(qcolor(lane.color, 0.35 * alpha))
            p.drawPath(path)
        cy = row.center().y()
        for it in lane.items:
            color = qcolor(it.color or lane.color, alpha)
            x = self._x_of(it.t)
            if it.shape == "span" and it.t_end is not None:
                x2 = max(x + 2, self._x_of(it.t_end))
                p.setPen(Qt.PenStyle.NoPen)
                c2 = QColor(color)
                c2.setAlphaF(0.55 * alpha)
                p.setBrush(c2)
                p.drawRoundedRect(QRectF(x, row.top() + 3, x2 - x, row.height() - 6), 2, 2)
            elif it.shape == "triangle":
                p.setPen(QPen(color, 1.3))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPolygon(QPolygonF([QPointF(x, cy - 5), QPointF(x - 5, cy + 4), QPointF(x + 5, cy + 4)]))
            elif it.shape == "diamond":
                p.setPen(QPen(color, 1.3))
                p.setBrush(qcolor("#ffffff", 0.0))
                p.drawPolygon(
                    QPolygonF(
                        [QPointF(x, cy - 5), QPointF(x + 5, cy), QPointF(x, cy + 5), QPointF(x - 5, cy)]
                    )
                )
            elif it.shape == "circle":
                p.setPen(QPen(color, 1.3))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QPointF(x, cy), 3.5, 3.5)
            else:
                p.setPen(QPen(color, 2))
                p.drawLine(QPointF(x, row.top() + 3), QPointF(x, row.bottom() - 3))
