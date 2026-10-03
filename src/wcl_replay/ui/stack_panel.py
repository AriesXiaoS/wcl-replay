# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Floating stack-order cards over the bottom-right of the map. The top card is drawn in front."""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPoint, QPropertyAnimation, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .controller import ReplayController
from .stack_order import merge_back_to_front, present_stack_groups, splice_visible

_GAP = 4
_PAD = 6
_SLIDE_MS = 170
_DROP_MS = 210
_LIFT_MS = 120


class _CardColumn(QWidget):
    """Places cards itself so a drag can slide the others instead of snapping."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("stackColumn")
        self._anims: dict[int, QPropertyAnimation] = {}

    def slide(self, card: QWidget, pos: QPoint, duration: int) -> None:
        key = id(card)
        running = self._anims.pop(key, None)
        if running is not None:
            running.stop()
        if duration <= 0 or card.pos() == pos:
            card.move(pos)
            return
        anim = QPropertyAnimation(card, b"pos", self)
        anim.setDuration(duration)
        anim.setStartValue(card.pos())
        anim.setEndValue(pos)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anims[key] = anim
        anim.finished.connect(
            lambda k=key, a=anim: self._anims.pop(k, None) if self._anims.get(k) is a else None
        )
        anim.start()

    def stop(self, card: QWidget) -> None:
        running = self._anims.pop(id(card), None)
        if running is not None:
            running.stop()

    def stop_all(self) -> None:
        running = list(self._anims.values())
        self._anims.clear()
        for anim in running:
            anim.stop()

    @property
    def sliding(self) -> bool:
        return bool(self._anims)


class _StackCard(QFrame):
    def __init__(self, group: str, label: str, panel: UnitStackPanel):
        super().__init__(panel._column)
        self.group = group
        self.label = label
        self._panel = panel
        self._lift_anim: QPropertyAnimation | None = None
        self.setObjectName("stackCard")
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        text = QLabel(label)
        text.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lay.addWidget(text)

    def set_lifted(self, lifted: bool) -> None:
        if self._lift_anim is not None:
            self._lift_anim.stop()
            self._lift_anim = None
        self.setProperty("lifted", lifted)
        self.style().unpolish(self)
        self.style().polish(self)
        if not lifted:
            self.setGraphicsEffect(None)
            return
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(2)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 180))
        self.setGraphicsEffect(shadow)
        anim = QPropertyAnimation(shadow, b"blurRadius", shadow)
        anim.setDuration(_LIFT_MS)
        anim.setStartValue(2.0)
        anim.setEndValue(18.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._lift_anim = anim
        anim.start()

    def mousePressEvent(self, event) -> None:
        event.accept()
        if event.button() == Qt.MouseButton.LeftButton:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self._panel.begin_drag(self, event.position().toPoint())

    def mouseMoveEvent(self, event) -> None:
        event.accept()
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._panel.drag_to(event.globalPosition().toPoint())

    def mouseReleaseEvent(self, event) -> None:
        event.accept()
        self._panel.end_drag()


class UnitStackPanel(QFrame):
    """One card per unit kind in the current fight. Drag to choose who draws on top."""

    def __init__(self, ctl: ReplayController, settings=None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("stackPanel")
        self.ctl = ctl
        self.settings = settings
        self._cards: list[_StackCard] = []
        self._held: _StackCard | None = None
        self._drag: int | None = None
        self._grab_dy = 0
        self._settling = False
        self._drop_anim: QPropertyAnimation | None = None
        self._row_h = 0
        self._pitch = 0
        self._metric_count = -1
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        self._title = QLabel("层级关系")
        self._title.setObjectName("stackTitle")
        self._title.setToolTip("最上面的盖住下面的。拖动卡片改变顺序。光柱始终在最底层。")
        lay.addWidget(self._title)
        self._column = _CardColumn(self)
        lay.addWidget(self._column)
        self._load()
        ctl.sessionChanged.connect(self.refresh)
        ctl.unitOrderChanged.connect(self.refresh)
        if parent is not None:
            parent.installEventFilter(self)
        self.refresh()

    def mousePressEvent(self, event) -> None:
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        event.accept()

    def eventFilter(self, watched: QObject, event) -> bool:
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self._place()
        return False

    def refresh(self) -> None:
        session = self.ctl.session
        if session is None:
            self._cancel_drag()
            self.hide()
            return
        if self._drag is not None:
            return
        labels = dict(present_stack_groups(session.analysis))
        order = merge_back_to_front(self.ctl.unit_order, set(labels))
        display = [(group, labels.get(group, group)) for group in reversed(order)]
        if [(card.group, card.label) for card in self._cards] == display:
            self.show()
            self._place()
            return
        self._rebuild(display)
        self.show()
        self._place()

    def begin_drag(self, card: _StackCard, grab: QPoint) -> None:
        if card not in self._cards:
            return
        self._settling = False
        self._stop_drop()
        self._column.stop_all()
        self._place_flow(animate=False, hold=None)
        self._held = card
        self._drag = self._cards.index(card)
        self._grab_dy = grab.y()
        card.set_lifted(True)
        card.raise_()

    def drag_to(self, global_pos: QPoint) -> None:
        held = self._held
        if held is None or self._pitch <= 0:
            return
        cursor = self._column.mapFromGlobal(global_pos)
        y = cursor.y() - self._grab_dy
        y = max(self._slot_y(0), min(y, self._slot_y(len(self._cards) - 1)))
        self._column.stop(held)
        held.move(held.x(), y)
        held.raise_()
        index = self._index_for_center(y + held.height() / 2)
        if index != self._cards.index(held):
            self.shift_held_to(index)

    def shift_held_to(self, index: int) -> None:
        """Move the lifted card's slot. Neighbors slide; the card stays on the cursor."""
        if self._held is None or not (0 <= index < len(self._cards)):
            return
        src = self._cards.index(self._held)
        if src == index:
            return
        card = self._cards.pop(src)
        self._cards.insert(index, card)
        self._drag = index
        self._place_flow(animate=True, hold=card)
        self._commit()

    def end_drag(self) -> None:
        held = self._held
        if held is None:
            self._drag = None
            return
        target = QPoint(held.x(), self._slot_y(self._cards.index(held)))
        self._column.stop(held)
        if held.pos() == target:
            self._finish_drop()
            return
        self._settling = True
        anim = QPropertyAnimation(held, b"pos", self)
        anim.setDuration(_DROP_MS)
        anim.setStartValue(held.pos())
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(self._on_drop_finished)
        self._drop_anim = anim
        anim.start()

    def move_card(self, src: int, dst: int) -> None:
        if src == dst or not (0 <= src < len(self._cards) and 0 <= dst < len(self._cards)):
            return
        card = self._cards.pop(src)
        self._cards.insert(dst, card)
        if self._drag is not None:
            self._drag = dst
        self._column.stop_all()
        self._place_flow(animate=False, hold=self._held)
        self._commit()
        if self._held is None:
            self._place()

    @property
    def cards(self) -> list[_StackCard]:
        return self._cards

    @property
    def sliding(self) -> bool:
        return self._column.sliding or self._drop_anim is not None

    def _on_drop_finished(self) -> None:
        self._drop_anim = None
        if not self._settling:
            return
        self._finish_drop()

    def _finish_drop(self) -> None:
        held = self._held
        self._settling = False
        self._held = None
        self._drag = None
        self._stop_drop()
        if held is not None:
            held.set_lifted(False)
            held.setCursor(Qt.CursorShape.OpenHandCursor)
        self._place_flow(animate=False, hold=None)
        self.refresh()

    def _cancel_drag(self) -> None:
        self._settling = False
        self._stop_drop()
        self._column.stop_all()
        if self._held is not None:
            self._held.set_lifted(False)
        self._held = None
        self._drag = None

    def _stop_drop(self) -> None:
        anim = self._drop_anim
        self._drop_anim = None
        if anim is not None:
            anim.stop()

    def _rebuild(self, display: list[tuple[str, str]]) -> None:
        self._cancel_drag()
        self._pitch = 0
        self._metric_count = -1
        while self._cards:
            card = self._cards.pop()
            card.hide()
            card.deleteLater()
        for group, label in display:
            card = _StackCard(group, label, self)
            card.show()
            self._cards.append(card)
        self._place_flow(animate=False, hold=None)

    def _slot_y(self, index: int) -> int:
        return _PAD + index * self._pitch

    def _index_for_center(self, center_y: float) -> int:
        if self._pitch <= 0 or not self._cards:
            return 0
        local = center_y - _PAD
        index = int((local - self._row_h / 2) / self._pitch + 0.5)
        return max(0, min(len(self._cards) - 1, index))

    def _ensure_metrics(self) -> None:
        if self._pitch and self._metric_count == len(self._cards):
            return
        if not self._cards:
            self._row_h = 0
            self._pitch = 0
            self._metric_count = 0
            self._column.setFixedSize(0, 0)
            return
        limit = 16_777_215
        hints = []
        for card in self._cards:
            card.setMinimumSize(0, 0)
            card.setMaximumSize(limit, limit)
            hints.append(card.sizeHint())
        self._row_h = max(hint.height() for hint in hints)
        width = max(self._title.sizeHint().width(), max(hint.width() for hint in hints))
        self._pitch = self._row_h + _GAP
        self._metric_count = len(self._cards)
        for card in self._cards:
            card.setFixedSize(width, self._row_h)
        count = len(self._cards)
        height = _PAD * 2 + self._pitch * count - _GAP
        self._column.setFixedSize(width + _PAD * 2, height)

    def _place_flow(self, *, animate: bool, hold: _StackCard | None) -> None:
        self._ensure_metrics()
        if not self._cards:
            return
        duration = _SLIDE_MS if animate else 0
        for index, card in enumerate(self._cards):
            pos = QPoint(_PAD, self._slot_y(index))
            if card is hold:
                self._column.stop(card)
                card.move(pos.x(), card.y())
                card.raise_()
                continue
            self._column.slide(card, pos, duration)

    def _place(self) -> None:
        parent = self.parentWidget()
        if parent is None or not self.isVisible():
            return
        self.adjustSize()
        margin = 12
        x = max(margin, parent.width() - self.width() - margin)
        y = max(margin, parent.height() - self.height() - margin)
        self.move(x, y)
        self.raise_()

    def _commit(self) -> None:
        back = [item.group for item in reversed(self._cards)]
        self.ctl.set_unit_order(splice_visible(self.ctl.unit_order, back))
        self._save()

    def _load(self) -> None:
        if self.settings is None:
            return
        raw = self.settings.value("unit_order", "")
        if not isinstance(raw, str) or not raw.strip():
            return
        self.ctl.set_unit_order([part.strip() for part in raw.split(",") if part.strip()])

    def _save(self) -> None:
        if self.settings is not None:
            self.settings.setValue("unit_order", ",".join(self.ctl.unit_order))
