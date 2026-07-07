"""
Interactive canvas for drawing/removing/grouping occlusion boxes.

Box coordinates are stored in original (1×) image space.
Zoom only affects display.

Editing:
  - Drag on empty space to draw a box
  - Drag a box to move it (multi-selection moves together)
  - Drag any corner handle of a selected box to resize
  - Arrow keys nudge selected boxes by 1 px (Shift = 10 px)
  - Ctrl+Z / Ctrl+Shift+Z (or Ctrl+Y) undo / redo
  - Ctrl+C / Ctrl+V copy / paste boxes — the clipboard survives slide
    changes, so a repeating layout can be stamped onto every slide

Grouping:
  - Shift-click boxes to multi-select
  - Press G (or toolbar button) to group selected boxes → same group ID
  - Press U (or toolbar button) to ungroup selected boxes
  - Each group produces one card with ALL boxes in that group masked together
  - Ungrouped boxes each produce their own card
"""
from typing import Optional

from aqt.qt import (
    QWidget, QPainter, QPen, QColor, QRect, QPoint, QPointF,
    QPixmap, QImage, Qt, QCursor, QMenu, QAction, QKeyEvent,
    pyqtSignal,
)

# ------------------------------------------------------------------ colours

# Default mask colour (neutral grey, matching card output for boxes that
# aren't being tested); the dialog overrides it from config via
# set_mask_color() so the editing preview matches the card output.
_DEFAULT_MASK_RGB = (120, 120, 120)

_SEL_FILL         = QColor(255, 199, 44, 185)   # gold — selection pops on purple
_SEL_BORDER       = QColor(178, 128, 0, 240)
_HANDLE_OUTLINE   = QColor(255, 255, 255, 240)
_HANDLE_SIZE      = 5   # half-size in screen px

# One colour per group index (cycles if > len).
# No purple entry — purple is reserved for ungrouped masks.
_GROUP_PALETTE = [
    (220,  60,  60),   # red
    ( 50, 180,  80),   # green
    ( 60, 120, 220),   # blue
    (220, 160,   0),   # amber
    ( 20, 180, 180),   # teal
    (230,  90, 170),   # pink
    (120, 100,  60),   # olive-brown
    ( 90, 170, 240),   # sky
]

_NUDGE_STEP = 1
_NUDGE_STEP_BIG = 10
_MAX_HISTORY = 100


def _group_color(gid: int, selected: bool, alpha: int = 160) -> QColor:
    r, g, b = _GROUP_PALETTE[gid % len(_GROUP_PALETTE)]
    if selected:
        # brighten border when selected
        return QColor(min(r + 40, 255), min(g + 40, 255), min(b + 40, 255), alpha + 30)
    return QColor(r, g, b, alpha)


def _pix_to_qpixmap(img: QImage) -> QPixmap:
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

    def corner_handles(self, zoom: float) -> dict[str, QRect]:
        """Screen rects for the four corner resize handles."""
        r = self.screen_rect(zoom)
        s = _HANDLE_SIZE
        return {
            "tl": QRect(r.left() - s,  r.top() - s,    s * 2, s * 2),
            "tr": QRect(r.right() - s, r.top() - s,    s * 2, s * 2),
            "bl": QRect(r.left() - s,  r.bottom() - s, s * 2, s * 2),
            "br": QRect(r.right() - s, r.bottom() - s, s * 2, s * 2),
        }

    def anchor_for(self, corner: str) -> QPointF:
        """Image-space corner opposite to the one being dragged."""
        n = self.norm()
        return {
            "tl": QPointF(n.x + n.w, n.y + n.h),
            "tr": QPointF(n.x,       n.y + n.h),
            "bl": QPointF(n.x + n.w, n.y),
            "br": QPointF(n.x,       n.y),
        }[corner]


# ---------------------------------------------------------- OcclusionCanvas

class OcclusionCanvas(QWidget):
    # Emitted whenever boxes change so the dialog can update group count label
    boxes_changed = pyqtSignal()
    # Emitted with -1/+1 when Left/Right is pressed with no selection —
    # the dialog flips slides. (Arrows nudge when boxes are selected, so
    # slide navigation can't be a window-level shortcut.)
    slide_nav = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap: Optional[QPixmap] = None
        self._orig_w = 0
        self._orig_h = 0
        self._zoom: float = 1.0
        self._render_scale: float = 1.0
        self._boxes: list[_Box] = []
        self._selected: set[_Box] = set()
        self._next_gid: int = 0   # monotonic group-id counter
        self.set_mask_color(_DEFAULT_MASK_RGB)

        # undo/redo — per slide, reset on set_image
        self._undo: list[list[dict]] = []
        self._redo: list[list[dict]] = []
        self._pre_drag: Optional[list[dict]] = None
        self._nudging = False   # coalesce consecutive arrow-key nudges

        # clipboard survives slide changes (same canvas instance is reused)
        self._clipboard: list[dict] = []

        # interaction state
        self._drawing = False
        self._drag_start: Optional[QPointF] = None
        self._drag_current: Optional[QPointF] = None
        self._resizing: Optional[_Box] = None
        self._resize_anchor: Optional[QPointF] = None
        self._moving: Optional[_Box] = None
        self._move_offset = QPointF(0, 0)

        self.setMouseTracking(True)
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # ---------------------------------------------------------------- public

    def has_image(self) -> bool:
        return self._pixmap is not None

    def set_mask_color(self, rgb: tuple):
        """Match the editing preview to the configured card mask colour.
        Kept semi-transparent on the canvas so the slide stays readable
        while drawing."""
        r, g, b = rgb[:3]
        self._ungrouped_fill = QColor(r, g, b, 150)
        self._ungrouped_border = QColor(
            int(r * 0.6), int(g * 0.6), int(b * 0.6), 225)

    def set_image(self, img: QImage, boxes: list[dict], render_scale: float = 1.0):
        self._pixmap = _pix_to_qpixmap(img)
        self._orig_w = img.width()
        self._orig_h = img.height()
        self._render_scale = max(render_scale, 0.01)
        self._boxes = [_Box.from_dict(d) for d in boxes]
        self._selected = set()
        self._undo.clear()
        self._redo.clear()
        self._nudging = False
        # keep _next_gid monotonic across slides so IDs never collide
        existing_gids = [b.group for b in self._boxes if b.group is not None]
        if existing_gids:
            self._next_gid = max(existing_gids) + 1
        self._apply_size()
        self.update()

    def get_boxes(self) -> list[dict]:
        return [b.to_dict() for b in self._boxes]

    def set_zoom(self, zoom: float):
        self._zoom = max(0.1, min(4.0, zoom))
        self._apply_size()
        self.update()

    def zoom(self) -> float:
        return self._zoom

    def group_selected(self):
        """Assign selected boxes to a new shared group."""
        if len(self._selected) < 2:
            return
        self._push_undo()
        gid = self._next_gid
        self._next_gid += 1
        for b in self._selected:
            b.group = gid
        self.boxes_changed.emit()
        self.update()

    def ungroup_selected(self):
        """Remove group membership from selected boxes."""
        if not any(b.group is not None for b in self._selected):
            return
        self._push_undo()
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

    # ----------------------------------------------------------- undo / redo

    def _push_undo(self):
        self._undo.append(self.get_boxes())
        if len(self._undo) > _MAX_HISTORY:
            self._undo.pop(0)
        self._redo.clear()
        self._nudging = False

    def _restore(self, state: list[dict]):
        self._boxes = [_Box.from_dict(d) for d in state]
        self._selected = set()
        existing_gids = [b.group for b in self._boxes if b.group is not None]
        if existing_gids:
            self._next_gid = max(max(existing_gids) + 1, self._next_gid)
        self.boxes_changed.emit()
        self.update()

    def undo(self):
        if not self._undo:
            return
        self._redo.append(self.get_boxes())
        self._restore(self._undo.pop())
        self._nudging = False

    def redo(self):
        if not self._redo:
            return
        self._undo.append(self.get_boxes())
        self._restore(self._redo.pop())
        self._nudging = False

    # ----------------------------------------------------------- copy / paste

    def copy_selected(self):
        if self._selected:
            self._clipboard = [b.to_dict() for b in self._selected]

    def paste(self):
        if not self._clipboard:
            return
        self._push_undo()
        # remap clipboard group ids to fresh ones so pasting never merges
        # with an existing group on this slide
        gid_map: dict[int, int] = {}
        # offset only if an identical box already sits at the same spot
        # (i.e. pasting onto the slide the boxes were copied from)
        existing = {(d["x"], d["y"], d["w"], d["h"]) for d in self.get_boxes()}
        collide = any(
            (d["x"], d["y"], d["w"], d["h"]) in existing for d in self._clipboard
        )
        off = 14 if collide else 0

        pasted = []
        for d in self._clipboard:
            gid = d.get("group")
            if gid is not None:
                if gid not in gid_map:
                    gid_map[gid] = self._next_gid
                    self._next_gid += 1
                gid = gid_map[gid]
            box = _Box(d["x"] + off, d["y"] + off, d["w"], d["h"], gid)
            self._boxes.append(box)
            pasted.append(box)

        self._selected = set(pasted)
        self.boxes_changed.emit()
        self.update()

    # ---------------------------------------------------------------- private

    @property
    def _disp(self) -> float:
        # Display scale: image pixels → screen. Pages are rendered at
        # _render_scale× resolution for sharpness, so 100% zoom shows the
        # page at its natural size, drawn from the higher-res pixels.
        return self._zoom / self._render_scale

    def _apply_size(self):
        if self._pixmap:
            self.setFixedSize(int(self._orig_w * self._disp),
                              int(self._orig_h * self._disp))

    def _to_img(self, spos: QPoint) -> QPointF:
        return QPointF(spos.x() / self._disp, spos.y() / self._disp)

    # ----------------------------------------------------------------- paint

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        if self._pixmap:
            p.drawPixmap(
                QRect(0, 0, int(self._orig_w * self._disp),
                      int(self._orig_h * self._disp)),
                self._pixmap,
            )

        for box in self._boxes:
            sel = box in self._selected
            r = box.screen_rect(self._disp)

            if box.group is not None:
                fill = _group_color(box.group, sel, alpha=160)
                border = _group_color(box.group, sel, alpha=230)
            else:
                fill = _SEL_FILL if sel else self._ungrouped_fill
                border = _SEL_BORDER if sel else self._ungrouped_border

            p.fillRect(r, fill)
            pen = QPen(border, 2.0 if sel else 1.5)
            p.setPen(pen)
            p.drawRect(r)

            # group label
            if box.group is not None:
                p.setPen(QColor(255, 255, 255, 220))
                p.drawText(r.adjusted(3, 2, -3, -2),
                           Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
                           f"G{box.group + 1}")

            # corner resize handles — only on selected boxes to keep the
            # canvas uncluttered. Filled with the box's border colour and
            # outlined white so they read as handles on both the mask and
            # the page background.
            if sel:
                hfill = QColor(border)
                hfill.setAlpha(255)
                p.setPen(QPen(_HANDLE_OUTLINE, 1))
                for h in box.corner_handles(self._disp).values():
                    p.fillRect(h, hfill)
                    p.drawRect(h)

        # in-progress draw
        if self._drawing and self._drag_start and self._drag_current:
            tmp = _Box(self._drag_start.x(), self._drag_start.y(),
                       self._drag_current.x() - self._drag_start.x(),
                       self._drag_current.y() - self._drag_start.y())
            r = tmp.screen_rect(self._disp)
            p.fillRect(r, self._ungrouped_fill)
            p.setPen(QPen(self._ungrouped_border, 1.5))
            p.drawRect(r)

        p.end()

    # --------------------------------------------------------------- mouse

    def _handle_at(self, spos: QPoint):
        """Return (box, corner) if spos hits a corner handle of a selected box."""
        for box in reversed(self._boxes):
            if box not in self._selected:
                continue
            for corner, rect in box.corner_handles(self._disp).items():
                if rect.contains(spos):
                    return box, corner
        return None, None

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

        # corner handle of a selected box?
        box, corner = self._handle_at(spos)
        if box is not None:
            self._pre_drag = self.get_boxes()
            self._resizing = box
            self._resize_anchor = box.anchor_for(corner)
            self.update()
            return

        # click inside a box?
        for box in reversed(self._boxes):
            if box.contains_screen(spos.x(), spos.y(), self._disp):
                if shift:
                    if box in self._selected:
                        self._selected.discard(box)
                    else:
                        self._selected.add(box)
                else:
                    if box not in self._selected:
                        self._selected = {box}
                    # if already selected, allow move without deselecting others
                self._pre_drag = self.get_boxes()
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
            a = self._resize_anchor
            self._resizing.x = a.x()
            self._resizing.y = a.y()
            self._resizing.w = ipos.x() - a.x()
            self._resizing.h = ipos.y() - a.y()
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
        box, corner = self._handle_at(spos)
        if box is not None:
            self.setCursor(QCursor(
                Qt.CursorShape.SizeFDiagCursor if corner in ("tl", "br")
                else Qt.CursorShape.SizeBDiagCursor))
            return
        for box in reversed(self._boxes):
            if box.contains_screen(spos.x(), spos.y(), self._disp):
                self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
                return
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))

    def _commit_drag(self):
        """Push the pre-drag snapshot onto undo, unless nothing changed."""
        if self._pre_drag is not None and self._pre_drag != self.get_boxes():
            self._undo.append(self._pre_drag)
            if len(self._undo) > _MAX_HISTORY:
                self._undo.pop(0)
            self._redo.clear()
            self._nudging = False
        self._pre_drag = None

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return

        if self._resizing:
            # normalize in place so future anchor math starts clean
            n = self._resizing.norm()
            self._resizing.x, self._resizing.y = n.x, n.y
            self._resizing.w, self._resizing.h = n.w, n.h
            self._resizing = None
            self._resize_anchor = None
            self._commit_drag()
            self.boxes_changed.emit()
            return

        if self._moving:
            self._moving = None
            self._commit_drag()
            self.boxes_changed.emit()
            return

        if self._drawing and self._drag_start and self._drag_current:
            tmp = _Box(self._drag_start.x(), self._drag_start.y(),
                       self._drag_current.x() - self._drag_start.x(),
                       self._drag_current.y() - self._drag_start.y())
            n = tmp.norm()
            if n.w > 5 and n.h > 5:
                self._push_undo()
                new_box = _Box(n.x, n.y, n.w, n.h)
                self._boxes.append(new_box)
                self._selected = {new_box}
                self.boxes_changed.emit()
            self._drawing = False
            self._drag_start = None
            self._drag_current = None
            self.update()

    # --------------------------------------------------------------- keyboard

    def _nudge(self, dx: int, dy: int):
        if not self._selected:
            return
        # coalesce a run of nudges into a single undo step
        if not self._nudging:
            self._push_undo()
            self._nudging = True
        for b in self._selected:
            n = b.norm()
            b.x, b.y, b.w, b.h = n.x + dx, n.y + dy, n.w, n.h
        self.boxes_changed.emit()
        self.update()

    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()
        ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)

        if ctrl and key == Qt.Key.Key_Z:
            self.redo() if shift else self.undo()
            return
        if ctrl and key == Qt.Key.Key_Y:
            self.redo()
            return
        if ctrl and key == Qt.Key.Key_C:
            self.copy_selected()
            return
        if ctrl and key == Qt.Key.Key_V:
            self.paste()
            return
        if ctrl and key == Qt.Key.Key_A:
            self.select_all()
            return

        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
            if self._selected:
                step = _NUDGE_STEP_BIG if shift else _NUDGE_STEP
                dx = {Qt.Key.Key_Left: -step, Qt.Key.Key_Right: step}.get(key, 0)
                dy = {Qt.Key.Key_Up: -step, Qt.Key.Key_Down: step}.get(key, 0)
                self._nudge(dx, dy)
            elif key == Qt.Key.Key_Left:
                self.slide_nav.emit(-1)
            elif key == Qt.Key.Key_Right:
                self.slide_nav.emit(+1)
            return

        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if self._selected:
                self._delete_selected()
                return

        if key == Qt.Key.Key_G and self._selected:
            self.group_selected()
            return

        if key == Qt.Key.Key_U and self._selected:
            self.ungroup_selected()
            return

        if key == Qt.Key.Key_Escape:
            if self._selected:
                self._selected = set()
                self.update()
                return

        super().keyPressEvent(event)

    # --------------------------------------------------------------- context

    def _context_menu(self, spos: QPoint):
        # find clicked box
        clicked = None
        for box in reversed(self._boxes):
            if box.contains_screen(spos.x(), spos.y(), self._disp):
                clicked = box
                break

        menu = QMenu(self)

        if clicked is not None:
            # select the clicked box if not already selected
            if clicked not in self._selected:
                self._selected = {clicked}
                self.update()

            remove_act = QAction(
                f"Remove box{'es' if len(self._selected) > 1 else ''}  (Del)", menu
            )
            remove_act.triggered.connect(self._delete_selected)
            menu.addAction(remove_act)

            copy_act = QAction("Copy  (Ctrl+C)", menu)
            copy_act.triggered.connect(self.copy_selected)
            menu.addAction(copy_act)

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
        else:
            paste_act = QAction("Paste  (Ctrl+V)", menu)
            paste_act.setEnabled(bool(self._clipboard))
            paste_act.triggered.connect(self.paste)
            menu.addAction(paste_act)

        menu.exec(self.mapToGlobal(spos))

    def _delete_selected(self):
        if not self._selected:
            return
        self._push_undo()
        for b in list(self._selected):
            if b in self._boxes:
                self._boxes.remove(b)
        self._selected = set()
        self.boxes_changed.emit()
        self.update()

    def _select_group(self, gid: int):
        self._selected = {b for b in self._boxes if b.group == gid}
        self.update()
