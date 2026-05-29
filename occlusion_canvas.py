"""
Interactive canvas for drawing/removing/grouping occlusion boxes.

Box coordinates are stored in original (1×) image space.
Zoom only affects display.

Grouping:
  - Shift-click boxes to multi-select
  - Press G (or toolbar button) to group selected boxes → same group ID
  - Press U (or toolbar button) to ungroup selected boxes
  - Each group produces one card with ALL boxes in that group masked together
  - Ungrouped boxes each produce their own card
"""
from typing import Optional

import fitz  # PyMuPDF

from aqt.qt import (
    QWidget, QPainter, QPen, QColor, QRect, QPoint, QPointF,
    QPixmap, QImage, Qt, QCursor, QMenu, QAction, QKeyEvent,
    pyqtSignal,
)

# ------------------------------------------------------------------ colours

_UNGROUPED_FILL   = QColor(46, 120, 217, 160)
_UNGROUPED_BORDER = QColor(20,  70, 160, 220)
_SEL_FILL         = QColor(255, 165,   0, 180)
_SEL_BORDER       = QColor(200, 110,   0, 240)
_HANDLE_COLOR     = QColor(255, 255, 255, 230)
_HANDLE_SIZE      = 6   # half-size in screen px

# One colour per group index (cycles if > len)
_GROUP_PALETTE = [
    (220,  60,  60),   # red
    ( 50, 180,  80),   # green
    (160,  60, 200),   # purple
    (220, 160,   0),   # amber
    ( 20, 180, 180),   # teal
    (230,  90, 170),   # pink
    (100, 140, 220),   # periwinkle
    (180, 120,  50),   # brown
]


def _group_color(gid: int, selected: bool, alpha: int = 160) -> QColor:
    r, g, b = _GROUP_PALETTE[gid % len(_GROUP_PALETTE)]
    if selected:
        # brighten border when selected
        return QColor(min(r + 40, 255), min(g + 40, 255), min(b + 40, 255), alpha + 30)
    return QColor(r, g, b, alpha)


def _pix_to_qpixmap(pix: fitz.Pixmap) -> QPixmap:
    fmt = QImage.Format.Format_RGBA8888 if pix.n == 4 else QImage.Format.Format_RGB888
    img = QImage(pix.samples, pix.width, pix.height, pix.stride, fmt)
    return QPixmap.fromImage(img)


# -------------------------------------------------------------------- _Box

class _Box:
    __slots__ = ("x", "y", "w", "h", "group")

    def __init__(self, x: float, y: float, w: float, h: float,
                 group: Optional[int] = None):
        self.x, self.y, self.w, self.h = x, y, w, h
        self.group = group

    def norm(self) -> "_Box":
        x, y, w, h = self.x, self.y, self.w, self.h
        if w < 0: x += w; w = -w
        if h < 0: y += h; h = -h
        return _Box(x, y, w, h, self.group)

    def to_dict(self) -> dict:
        n = self.norm()
        return {"x": int(n.x), "y": int(n.y),
                "w": int(n.w), "h": int(n.h),
                "group": self.group}

    @classmethod
    def from_dict(cls, d: dict) -> "_Box":
        return cls(d["x"], d["y"], d["w"], d["h"], d.get("group"))

    def screen_rect(self, zoom: float) -> QRect:
        n = self.norm()
        return QRect(int(n.x * zoom), int(n.y * zoom),
                     max(1, int(n.w * zoom)), max(1, int(n.h * zoom)))

    def contains_screen(self, sx: int, sy: int, zoom: float) -> bool:
        return self.screen_rect(zoom).contains(sx, sy)

    def handle_rect(self, zoom: float) -> QRect:
        r = self.screen_rect(zoom)
        return QRect(r.right() - _HANDLE_SIZE, r.bottom() - _HANDLE_SIZE,
                     _HANDLE_SIZE * 2, _HANDLE_SIZE * 2)


# ---------------------------------------------------------- OcclusionCanvas

class OcclusionCanvas(QWidget):
    # Emitted whenever boxes change so the dialog can update group count label
    boxes_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap: Optional[QPixmap] = None
        self._orig_w = 0
        self._orig_h = 0
        self._zoom: float = 1.0
        self._boxes: list[_Box] = []
        self._selected: set[_Box] = set()
        self._next_gid: int = 0   # monotonic group-id counter

        # interaction state
        self._drawing = False
        self._drag_start: Optional[QPointF] = None
        self._drag_current: Optional[QPointF] = None
        self._resizing: Optional[_Box] = None
        self._moving: Optional[_Box] = None
        self._move_offset = QPointF(0, 0)

        self.setMouseTracking(True)
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # ---------------------------------------------------------------- public

    def has_image(self) -> bool:
        return self._pixmap is not None

    def set_image(self, pix: fitz.Pixmap, boxes: list[dict]):
        self._pixmap = _pix_to_qpixmap(pix)
        self._orig_w = pix.width
        self._orig_h = pix.height
        self._boxes = [_Box.from_dict(d) for d in boxes]
        self._selected = set()
        # keep _next_gid monotonic across slides so IDs never collide
        existing_gids = [b.group for b in self._boxes if b.group is not None]
        if existing_gids:
            self._next_gid = max(existing_gids) + 1
        self._apply_size()
        self.update()

    def get_boxes(self) -> list[dict]:
        return [b.to_dict() for b in self._boxes]

    def set_zoom(self, zoom: float):
        self._zoom = max(0.25, min(4.0, zoom))
        self._apply_size()
        self.update()

    def zoom(self) -> float:
        return self._zoom

    def group_selected(self):
        """Assign selected boxes to a new shared group."""
        if len(self._selected) < 2:
            return
        gid = self._next_gid
        self._next_gid += 1
        for b in self._selected:
            b.group = gid
        self.boxes_changed.emit()
        self.update()

    def ungroup_selected(self):
        """Remove group membership from selected boxes."""
        for b in self._selected:
            b.group = None
        self.boxes_changed.emit()
        self.update()

    def select_all(self):
        self._selected = set(self._boxes)
        self.update()

    def group_summary(self) -> dict:
        """Return {"groups": count_of_distinct_groups, "ungrouped": count}."""
        gids = {b.group for b in self._boxes if b.group is not None}
        ungrouped = sum(1 for b in self._boxes if b.group is None)
        return {"groups": len(gids), "ungrouped": ungrouped}

    # ---------------------------------------------------------------- private

    def _apply_size(self):
        if self._pixmap:
            self.setFixedSize(int(self._orig_w * self._zoom),
                              int(self._orig_h * self._zoom))

    def _to_img(self, spos: QPoint) -> QPointF:
        return QPointF(spos.x() / self._zoom, spos.y() / self._zoom)

    # ----------------------------------------------------------------- paint

    def paintEvent(self, _event):
        p = QPainter(self)

        if self._pixmap:
            p.drawPixmap(
                QRect(0, 0, int(self._orig_w * self._zoom),
                      int(self._orig_h * self._zoom)),
                self._pixmap,
            )

        for box in self._boxes:
            sel = box in self._selected
            r = box.screen_rect(self._zoom)

            if box.group is not None:
                fill = _group_color(box.group, sel, alpha=160)
                border = _group_color(box.group, sel, alpha=230)
            else:
                fill = _SEL_FILL if sel else _UNGROUPED_FILL
                border = _SEL_BORDER if sel else _UNGROUPED_BORDER

            p.fillRect(r, fill)
            pen = QPen(border, 1.5)
            p.setPen(pen)
            p.drawRect(r)

            # group label
            if box.group is not None:
                p.setPen(QColor(255, 255, 255, 220))
                p.drawText(r.adjusted(3, 2, -3, -2),
                           Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
                           f"G{box.group + 1}")

            # resize handle
            h = box.handle_rect(self._zoom)
            p.fillRect(h, _HANDLE_COLOR)
            p.setPen(border)
            p.drawRect(h)

        # in-progress draw
        if self._drawing and self._drag_start and self._drag_current:
            tmp = _Box(self._drag_start.x(), self._drag_start.y(),
                       self._drag_current.x() - self._drag_start.x(),
                       self._drag_current.y() - self._drag_start.y())
            r = tmp.screen_rect(self._zoom)
            p.fillRect(r, _UNGROUPED_FILL)
            p.setPen(QPen(_UNGROUPED_BORDER, 1.5))
            p.drawRect(r)

        p.end()

    # --------------------------------------------------------------- mouse

    def mousePressEvent(self, event):
        self.setFocus()

        if event.button() == Qt.MouseButton.RightButton:
            self._context_menu(event.pos())
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return

        spos = event.pos()
        ipos = self._to_img(spos)
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)

        # resize handle?
        for box in reversed(self._boxes):
            if box.handle_rect(self._zoom).contains(spos):
                self._resizing = box
                if not shift:
                    self._selected = {box}
                else:
                    self._selected.add(box)
                self.update()
                return

        # click inside a box?
        for box in reversed(self._boxes):
            if box.contains_screen(spos.x(), spos.y(), self._zoom):
                if shift:
                    if box in self._selected:
                        self._selected.discard(box)
                    else:
                        self._selected.add(box)
                else:
                    if box not in self._selected:
                        self._selected = {box}
                    # if already selected, allow move without deselecting others
                self._moving = box
                n = box.norm()
                self._move_offset = ipos - QPointF(n.x, n.y)
                self.update()
                return

        # empty space — deselect (unless shift) and start drawing
        if not shift:
            self._selected = set()
        self._drawing = True
        self._drag_start = ipos
        self._drag_current = ipos
        self.update()

    def mouseMoveEvent(self, event):
        spos = event.pos()
        ipos = self._to_img(spos)

        if self._resizing:
            n = self._resizing.norm()
            self._resizing.w = ipos.x() - n.x
            self._resizing.h = ipos.y() - n.y
            self.update()
            return

        if self._moving:
            delta = ipos - self._move_offset
            # Move all selected boxes together if the dragged box is selected
            if self._moving in self._selected and len(self._selected) > 1:
                n = self._moving.norm()
                dx = delta.x() - n.x
                dy = delta.y() - n.y
                for b in self._selected:
                    bn = b.norm()
                    b.x = bn.x + dx
                    b.y = bn.y + dy
                    b.w = bn.w; b.h = bn.h
                # update offset so next frame delta is correct
                self._move_offset = ipos - QPointF(
                    self._moving.norm().x, self._moving.norm().y
                )
            else:
                self._moving.x = delta.x()
                self._moving.y = delta.y()
            self.update()
            return

        if self._drawing:
            self._drag_current = ipos
            self.update()
            return

        # cursor hints
        for box in reversed(self._boxes):
            if box.handle_rect(self._zoom).contains(spos):
                self.setCursor(QCursor(Qt.CursorShape.SizeFDiagCursor))
                return
            if box.contains_screen(spos.x(), spos.y(), self._zoom):
                self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
                return
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return

        if self._resizing:
            self._resizing = None
            self.boxes_changed.emit()
            return

        if self._moving:
            self._moving = None
            self.boxes_changed.emit()
            return

        if self._drawing and self._drag_start and self._drag_current:
            tmp = _Box(self._drag_start.x(), self._drag_start.y(),
                       self._drag_current.x() - self._drag_start.x(),
                       self._drag_current.y() - self._drag_start.y())
            n = tmp.norm()
            if n.w > 5 and n.h > 5:
                new_box = _Box(n.x, n.y, n.w, n.h)
                self._boxes.append(new_box)
                self._selected = {new_box}
                self.boxes_changed.emit()
            self._drawing = False
            self._drag_start = None
            self._drag_current = None
            self.update()

    # --------------------------------------------------------------- keyboard

    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()

        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if self._selected:
                for b in list(self._selected):
                    if b in self._boxes:
                        self._boxes.remove(b)
                self._selected = set()
                self.boxes_changed.emit()
                self.update()
                return

        if key == Qt.Key.Key_G and self._selected:
            self.group_selected()
            return

        if key == Qt.Key.Key_U and self._selected:
            self.ungroup_selected()
            return

        if key == Qt.Key.Key_A and (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.select_all()
            return

        super().keyPressEvent(event)

    # --------------------------------------------------------------- context

    def _context_menu(self, spos: QPoint):
        # find clicked box
        clicked = None
        for box in reversed(self._boxes):
            if box.contains_screen(spos.x(), spos.y(), self._zoom):
                clicked = box
                break

        if clicked is None:
            return

        # select the clicked box if not already selected
        if clicked not in self._selected:
            self._selected = {clicked}
            self.update()

        menu = QMenu(self)

        remove_act = QAction(
            f"Remove box{'es' if len(self._selected) > 1 else ''}  (Del)", menu
        )
        remove_act.triggered.connect(self._delete_selected)
        menu.addAction(remove_act)

        menu.addSeparator()

        group_act = QAction("Group selected  (G)", menu)
        group_act.setEnabled(len(self._selected) >= 2)
        group_act.triggered.connect(self.group_selected)
        menu.addAction(group_act)

        ungroup_act = QAction("Ungroup selected  (U)", menu)
        ungroup_act.setEnabled(any(b.group is not None for b in self._selected))
        ungroup_act.triggered.connect(self.ungroup_selected)
        menu.addAction(ungroup_act)

        # "select whole group" if clicked box is grouped
        if clicked.group is not None:
            sel_grp = QAction(f"Select all in Group {clicked.group + 1}", menu)
            sel_grp.triggered.connect(
                lambda _, gid=clicked.group: self._select_group(gid)
            )
            menu.addSeparator()
            menu.addAction(sel_grp)

        menu.exec(self.mapToGlobal(spos))

    def _delete_selected(self):
        for b in list(self._selected):
            if b in self._boxes:
                self._boxes.remove(b)
        self._selected = set()
        self.boxes_changed.emit()
        self.update()

    def _select_group(self, gid: int):
        self._selected = {b for b in self._boxes if b.group == gid}
        self.update()
