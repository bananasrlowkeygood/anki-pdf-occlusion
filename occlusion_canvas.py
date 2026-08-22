"""
Interactive canvas for drawing/removing/grouping occlusion boxes.

Box coordinates are stored in original (1×) image space.
Zoom only affects display.

Tools (see set_tool):
  - "draw"   — drag on empty space draws a box
  - "select" — drag on empty space rubber-band selects boxes

Editing:
  - Drag a box to move it (multi-selection moves together)
  - Shift-drag a grouped box to move its whole group together
  - Drag any corner handle of a selected box to resize
  - Drag an edge of a selected box to resize it horizontally or vertically
    only. Edges have no drawn handle — hover one and the cursor changes.
  - With the Select tool, drag just outside a corner to rotate the box (a
    multi-selection turns together around its centre); Shift snaps to 15°.
    Also hover-only. Draw keeps that space for starting the next box.
  - Arrow keys nudge selected boxes by 1 px (Shift = 10 px)
  - Ctrl+Z / Ctrl+Shift+Z (or Ctrl+Y) undo / redo
  - Ctrl+C / Ctrl+V copy / paste boxes — the clipboard survives slide
    changes, so a repeating layout can be stamped onto every slide
  - Double-click a box to attach a note (goes to the card's Notes field)
  - Pinch (or Ctrl/Cmd+scroll) zooms; the dialog anchors it at the cursor

Grouping:
  - Shift-click boxes to multi-select
  - Press G (or context menu) to group selected boxes → same group ID
  - Shift-DRAW: a box drawn while holding Shift joins the selection's group
    (creating one if needed), so you can sketch a multi-part card in one go
  - Press U (or context menu) to ungroup selected boxes
  - Each group produces one card with ALL boxes in that group masked together
  - Ungrouped boxes each produce their own card

Per-box data (kept in the box dicts, consumed by card_builder):
  - "shape": "rect" | "ellipse"
  - "angle": rotation in degrees, clockwise about the box's centre
  - "mode":  None (follow slide/PDF default) | "ao" | "oa"
  - "note":  free text → card's Notes field
  - "id" / "group_uid": stable UUIDs so a re-created card can be updated
    in place instead of duplicated
"""
import math
import uuid
from typing import Optional

from aqt.qt import (
    QWidget, QPainter, QPen, QColor, QRect, QPoint, QPointF,
    QPixmap, QImage, Qt, QCursor, QMenu, QAction, QKeyEvent,
    QInputDialog, QEvent, QPolygonF, pyqtSignal,
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

_BAND_FILL        = QColor(131, 110, 170, 40)   # rubber-band marquee
_BAND_BORDER      = QColor(131, 110, 170, 220)

_BADGE_BG         = QColor(0, 0, 0, 120)
_BADGE_FG         = QColor(255, 255, 255, 230)

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
_DRAG_THRESHOLD = 4   # screen px before a press becomes a drag

# Edge-resize and rotation are hover-only affordances: nothing is drawn for
# them, the cursor is the entire hint. _EDGE_GRAB is the band either side of
# an edge that starts a one-axis resize; _ROT_RING is how far past a corner
# the rotate zone reaches (it starts where the corner handle ends).
_EDGE_GRAB = 5        # screen px
_ROT_RING = 18        # screen px
_ROT_SNAP = 15        # degrees, while Shift is held

# handle name -> (anchor corner that stays put, axes the drag is free to change)
_RESIZE_HANDLES = {
    "tl": ("br", "xy"), "tr": ("bl", "xy"),
    "bl": ("tr", "xy"), "br": ("tl", "xy"),
    "l":  ("br", "x"),  "r":  ("tl", "x"),
    "t":  ("br", "y"),  "b":  ("tl", "y"),
}

# outward direction of each handle in the box's own frame, degrees clockwise
# from +x (screen axes, y down). Used to pick a resize cursor that still
# points the right way once the box is rotated.
_HANDLE_DIR = {"l": 180, "r": 0, "t": -90, "b": 90,
               "tl": -135, "tr": -45, "bl": 135, "br": 45}

_MODE_LABELS = {"ao": "AO", "oa": "OA"}


def _group_color(gid: int, selected: bool, alpha: int = 160) -> QColor:
    r, g, b = _GROUP_PALETTE[gid % len(_GROUP_PALETTE)]
    if selected:
        # brighten border when selected
        return QColor(min(r + 40, 255), min(g + 40, 255), min(b + 40, 255), alpha + 30)
    return QColor(r, g, b, alpha)


def _pix_to_qpixmap(img: QImage) -> QPixmap:
    return QPixmap.fromImage(img)


def _rot_vec(v: QPointF, deg: float) -> QPointF:
    """Rotate a vector about the origin, clockwise (screen axes, y down)."""
    if not deg:
        return QPointF(v)
    rad = math.radians(deg)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    return QPointF(v.x() * cos_a - v.y() * sin_a,
                   v.x() * sin_a + v.y() * cos_a)


def _dir_cursor(deg: float) -> QCursor:
    """Resize cursor pointing along an outward direction in degrees."""
    d = deg % 180
    if d < 22.5 or d >= 157.5:
        shape = Qt.CursorShape.SizeHorCursor
    elif d < 67.5:
        shape = Qt.CursorShape.SizeFDiagCursor    # "\\"
    elif d < 112.5:
        shape = Qt.CursorShape.SizeVerCursor
    else:
        shape = Qt.CursorShape.SizeBDiagCursor    # "/"
    return QCursor(shape)


_ROTATE_CURSOR: Optional[QCursor] = None


def _rotate_cursor() -> QCursor:
    """Curved-arrow cursor for the rotate zone, drawn once at first use.

    Qt has no stock rotate cursor and the add-on ships no bitmap for one —
    painting it here keeps it out of build.py's asset list, and lets it be a
    dark arc inside a white halo so it stays visible on masks and slides
    alike."""
    global _ROTATE_CURSOR
    if _ROTATE_CURSOR is None:
        pm = QPixmap(26, 26)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        arc = QRect(6, 6, 14, 14)
        head = QPolygonF([QPointF(19.5, 3.0), QPointF(19.5, 11.0),
                          QPointF(12.5, 7.0)])
        for color, width in ((QColor(255, 255, 255, 240), 5.0),
                             (QColor(35, 35, 35, 245), 2.2)):
            p.setPen(QPen(color, width, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawArc(arc, -40 * 16, 285 * 16)
            p.setPen(QPen(color, width * 0.6))
            p.setBrush(color)
            p.drawPolygon(head)
        p.end()
        _ROTATE_CURSOR = QCursor(pm, 13, 13)
    return _ROTATE_CURSOR


def _local_handles(r: QRect) -> list[QRect]:
    """Corner-handle rects for an unrotated screen rect (painting only —
    the painter is already rotated when these are drawn)."""
    s = _HANDLE_SIZE
    return [QRect(r.left() - s,  r.top() - s,    s * 2, s * 2),
            QRect(r.right() - s, r.top() - s,    s * 2, s * 2),
            QRect(r.left() - s,  r.bottom() - s, s * 2, s * 2),
            QRect(r.right() - s, r.bottom() - s, s * 2, s * 2)]


# -------------------------------------------------------------------- _Box

class _Box:
    """One occlusion box.

    x/y/w/h are the *unrotated* rectangle in image space; `angle` turns it
    clockwise about its own centre. Keeping the two apart means every edit
    except rotation stays plain rectangle maths, and an unrotated box
    serialises byte-identically to how it always has.
    """

    __slots__ = ("x", "y", "w", "h", "angle", "group", "group_uid",
                 "shape", "mode", "note", "id")

    def __init__(self, x: float, y: float, w: float, h: float,
                 group: Optional[int] = None, group_uid: Optional[str] = None,
                 shape: str = "rect", mode: Optional[str] = None,
                 note: str = "", box_id: Optional[str] = None,
                 angle: float = 0.0):
        self.x, self.y, self.w, self.h = x, y, w, h
        self.angle = float(angle or 0.0)
        self.group = group
        self.group_uid = group_uid
        self.shape = shape if shape in ("rect", "ellipse") else "rect"
        self.mode = mode if mode in ("ao", "oa") else None
        self.note = note or ""
        self.id = box_id or uuid.uuid4().hex

    def norm(self) -> "_Box":
        x, y, w, h = self.x, self.y, self.w, self.h
        if w < 0: x += w; w = -w
        if h < 0: y += h; h = -h
        return _Box(x, y, w, h, self.group, self.group_uid,
                    self.shape, self.mode, self.note, self.id, self.angle)

    def to_dict(self) -> dict:
        n = self.norm()
        d = {"x": int(n.x), "y": int(n.y),
             "w": int(n.w), "h": int(n.h),
             "group": self.group, "group_uid": self.group_uid,
             "shape": self.shape, "mode": self.mode,
             "note": self.note, "id": self.id}
        # Only rotated boxes carry the key, so every card made before
        # rotation existed still round-trips to the exact same dict (and so
        # to the exact same mask SVG — no mass "updated" on the next run).
        if self.angle:
            d["angle"] = round(self.angle, 3)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "_Box":
        return cls(d["x"], d["y"], d["w"], d["h"],
                   d.get("group"), d.get("group_uid"),
                   d.get("shape", "rect"), d.get("mode"),
                   d.get("note", ""), d.get("id"),
                   d.get("angle", 0.0))

    # -- geometry ---------------------------------------------------------

    def centre(self) -> QPointF:
        n = self.norm()
        return QPointF(n.x + n.w / 2.0, n.y + n.h / 2.0)

    def to_world(self, pt: QPointF) -> QPointF:
        """Unrotated image point -> where it actually sits on the slide."""
        if not self.angle:
            return QPointF(pt)
        c = self.centre()
        return c + _rot_vec(pt - c, self.angle)

    def to_local(self, pt: QPointF) -> QPointF:
        """Slide point -> the box's own unrotated frame."""
        if not self.angle:
            return QPointF(pt)
        c = self.centre()
        return c + _rot_vec(pt - c, -self.angle)

    def local_corner(self, name: str) -> QPointF:
        n = self.norm()
        return {
            "tl": QPointF(n.x,       n.y),
            "tr": QPointF(n.x + n.w, n.y),
            "bl": QPointF(n.x,       n.y + n.h),
            "br": QPointF(n.x + n.w, n.y + n.h),
        }[name]

    def screen_rect(self, zoom: float) -> QRect:
        """The unrotated rect in screen px — what the painter draws inside
        the rotation transform."""
        n = self.norm()
        return QRect(int(n.x * zoom), int(n.y * zoom),
                     max(1, int(n.w * zoom)), max(1, int(n.h * zoom)))

    def bounds_screen(self, zoom: float) -> QRect:
        """Axis-aligned screen bounds of the box as it actually sits."""
        if not self.angle:
            return self.screen_rect(zoom)
        pts = [self.to_world(self.local_corner(c))
               for c in ("tl", "tr", "bl", "br")]
        xs = [p.x() * zoom for p in pts]
        ys = [p.y() * zoom for p in pts]
        return QRect(int(min(xs)), int(min(ys)),
                     max(1, int(max(xs) - min(xs))),
                     max(1, int(max(ys) - min(ys))))

    def contains_screen(self, sx: int, sy: int, zoom: float) -> bool:
        n = self.norm()
        p = self.to_local(QPointF(sx / zoom, sy / zoom))
        return (n.x <= p.x() <= n.x + n.w) and (n.y <= p.y() <= n.y + n.h)

    def corner_points(self, zoom: float) -> dict[str, QPoint]:
        """Where the four corners land on screen, rotation included."""
        out = {}
        for name in ("tl", "tr", "bl", "br"):
            w = self.to_world(self.local_corner(name))
            out[name] = QPoint(int(round(w.x() * zoom)), int(round(w.y() * zoom)))
        return out

    def corner_handles(self, zoom: float) -> dict[str, QRect]:
        """Screen rects for the four corner resize handles (hit testing)."""
        s = _HANDLE_SIZE
        return {name: QRect(p.x() - s, p.y() - s, s * 2, s * 2)
                for name, p in self.corner_points(zoom).items()}

    def edge_at(self, sx: int, sy: int, zoom: float,
                outside: bool = True) -> Optional[str]:
        """Which edge ("l"/"r"/"t"/"b") a screen point is grabbing, if any.

        Measured in the box's own frame, so a rotated box's edges are still
        grabbable along their real, slanted position. The corners are left
        to the corner handles.

        `outside` is off under the Draw tool: there the band must stay inside
        the box, or starting a new box a few px below the one just drawn
        would resize it instead of drawing.
        """
        n = self.norm()
        p = self.to_local(QPointF(sx / zoom, sy / zoom))
        g = _EDGE_GRAB / zoom
        out = g if outside else 0.0
        if not (n.x - out <= p.x() <= n.x + n.w + out
                and n.y - out <= p.y() <= n.y + n.h + out):
            return None
        x_edge, x_d = (("l", abs(p.x() - n.x))
                       if abs(p.x() - n.x) <= abs(p.x() - (n.x + n.w))
                       else ("r", abs(p.x() - (n.x + n.w))))
        y_edge, y_d = (("t", abs(p.y() - n.y))
                       if abs(p.y() - n.y) <= abs(p.y() - (n.y + n.h))
                       else ("b", abs(p.y() - (n.y + n.h))))
        if x_d <= g and y_d <= g:
            return None          # corner territory
        if x_d <= g:
            return x_edge
        if y_d <= g:
            return y_edge
        return None

    def rotate_corner_at(self, sx: int, sy: int, zoom: float) -> Optional[str]:
        """Corner whose rotate ring a screen point is in.

        The ring is the patch of empty slide just diagonally outside a
        corner — past the corner handle, and never over the box itself or
        an edge's grab band, so it can't steal a move or a resize.
        """
        if self.contains_screen(sx, sy, zoom) or self.edge_at(sx, sy, zoom):
            return None
        best, best_d = None, None
        for name, pt in self.corner_points(zoom).items():
            d = math.hypot(sx - pt.x(), sy - pt.y())
            if d <= _ROT_RING and (best_d is None or d < best_d):
                best, best_d = name, d
        return best


# ---------------------------------------------------------- OcclusionCanvas

class OcclusionCanvas(QWidget):
    # Emitted whenever boxes change so the dialog can update the card counter
    boxes_changed = pyqtSignal()
    # Emitted with -1/+1 when Left/Right is pressed with no selection —
    # the dialog flips slides. (Arrows nudge when boxes are selected, so
    # slide navigation can't be a window-level shortcut.)
    slide_nav = pyqtSignal(int)
    # Pinch / Ctrl+scroll: (zoom delta, cursor position in canvas coords).
    # The dialog owns zoom state, so it applies the change and re-anchors.
    zoom_gesture = pyqtSignal(float, QPoint)

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
        self._tool: str = "draw"
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
        self._banding = False           # rubber-band selection (select tool)
        self._band_base_sel: set = set()
        self._draw_shift = False        # Shift held when the draw started
        self._drag_start: Optional[QPointF] = None
        self._drag_current: Optional[QPointF] = None
        self._resizing: Optional[_Box] = None
        self._resize_anchor: Optional[QPointF] = None   # fixed point, slide coords
        self._resize_free = "xy"        # axes this drag may change
        self._resize_fixed = (0.0, 0.0) # signed extent of the axes it may not
        self._resize_angle = 0.0
        self._rotating: Optional[list] = None   # [(box, centre0, angle0), …]
        self._rot_pivot: Optional[QPointF] = None
        self._rot_start = 0.0
        self._moving: Optional[_Box] = None
        self._move_offset = QPointF(0, 0)
        self._press_spos: Optional[QPoint] = None
        self._drag_started = False
        self._shift_press_box: Optional[_Box] = None

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

    def set_tool(self, tool: str):
        """Active tool: "draw" (drag draws boxes) or "select" (drag marquee-selects)."""
        if tool in ("draw", "select"):
            self._tool = tool
            self.setCursor(QCursor(
                Qt.CursorShape.CrossCursor if tool == "draw"
                else Qt.CursorShape.ArrowCursor))

    def tool(self) -> str:
        return self._tool

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

    def add_boxes(self, dicts: list[dict], select: bool = True):
        """Add boxes programmatically (e.g. text detection). One undo step."""
        if not dicts:
            return
        self._push_undo()
        added = []
        for d in dicts:
            box = _Box.from_dict(d)
            self._boxes.append(box)
            added.append(box)
        if select:
            self._selected = set(added)
        self.boxes_changed.emit()
        self.update()

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
        # Already exactly one whole group? Nothing to do — this also makes a
        # held-down G key (auto-repeat) harmless instead of minting a fresh
        # group id on every repeat.
        sel_gids = {b.group for b in self._selected}
        if len(sel_gids) == 1 and None not in sel_gids:
            gid = next(iter(sel_gids))
            if self._selected == {b for b in self._boxes if b.group == gid}:
                return
        self._push_undo()
        gid = self._next_gid
        self._next_gid += 1
        guid = uuid.uuid4().hex
        for b in self._selected:
            b.group = gid
            b.group_uid = guid
        self.boxes_changed.emit()
        self.update()

    def ungroup_selected(self):
        """Remove group membership from selected boxes."""
        if not any(b.group is not None for b in self._selected):
            return
        self._push_undo()
        for b in self._selected:
            b.group = None
            b.group_uid = None
        self.boxes_changed.emit()
        self.update()

    def select_all(self):
        self._selected = set(self._boxes)
        self.update()

    def select_region(self, box_id: Optional[str] = None,
                      group_uid: Optional[str] = None):
        """Select the box / group backing a specific card (Browse → edit)."""
        if box_id is not None:
            self._selected = {b for b in self._boxes if b.id == box_id}
        elif group_uid is not None:
            self._selected = {b for b in self._boxes if b.group_uid == group_uid}
        self.update()

    def selection_count(self) -> int:
        return len(self._selected)

    def set_mode_selected(self, mode: Optional[str]):
        """Occlusion-mode override for the selected region(s).

        Applied to every member of any group touched, so a region always
        has one consistent mode."""
        targets = self._expand_to_groups(self._selected)
        if not targets:
            return
        self._push_undo()
        for b in targets:
            b.mode = mode
        self.boxes_changed.emit()
        self.update()

    def reset_rotation_selected(self):
        """Straighten every selected box (rotation has no drawn handle, so
        there has to be a way back that isn't a careful drag)."""
        turned = [b for b in self._selected if b.angle]
        if not turned:
            return
        self._push_undo()
        for b in turned:
            b.angle = 0.0
        self.boxes_changed.emit()
        self.update()

    def edit_note_selected(self):
        """Attach a note to the selected region (→ card's Notes field)."""
        targets = self._expand_to_groups(self._selected)
        if targets:
            self._edit_note(targets)

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
        guid_map: dict[str, str] = {}
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
            guid = d.get("group_uid")
            if gid is not None:
                if gid not in gid_map:
                    gid_map[gid] = self._next_gid
                    self._next_gid += 1
                gid = gid_map[gid]
                if guid is not None:
                    guid = guid_map.setdefault(guid, uuid.uuid4().hex)
            # fresh box id — a pasted box is a new region, not the old card
            box = _Box(d["x"] + off, d["y"] + off, d["w"], d["h"],
                       gid, guid, d.get("shape", "rect"), d.get("mode"),
                       d.get("note", ""), None, d.get("angle", 0.0))
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

    def _group_members(self, box: _Box) -> set[_Box]:
        if box.group is None:
            return {box}
        return {b for b in self._boxes if b.group == box.group}

    def _expand_to_groups(self, boxes: set) -> set:
        out: set[_Box] = set()
        for b in boxes:
            out |= self._group_members(b)
        return out

    def _band_rect(self) -> Optional[QRect]:
        if not (self._drag_start and self._drag_current):
            return None
        tmp = _Box(self._drag_start.x(), self._drag_start.y(),
                   self._drag_current.x() - self._drag_start.x(),
                   self._drag_current.y() - self._drag_start.y())
        return tmp.screen_rect(self._disp)

    def _apply_band_selection(self):
        """Live-update the selection while the marquee is dragged."""
        band = self._band_rect()
        if band is None:
            return
        hits = {b for b in self._boxes
                if band.intersects(b.bounds_screen(self._disp))}
        self._selected = self._band_base_sel | hits

    def _edit_note(self, targets: set):
        current = next((b.note for b in targets if b.note), "")
        text, ok = QInputDialog.getMultiLineText(
            self, "Card Note",
            "Shown in the card's Notes field (below the answer):",
            current,
        )
        if not ok:
            return
        self._push_undo()
        for b in targets:
            b.note = text.strip()
        self.boxes_changed.emit()
        self.update()

    # ----------------------------------------------------------------- paint

    def _paint_shape(self, p: QPainter, box: _Box, r: QRect,
                     fill: QColor, border: QColor, border_w: float):
        pen = QPen(border, border_w)
        if box.shape == "ellipse":
            p.setPen(pen)
            p.setBrush(fill)
            p.drawEllipse(r)
            p.setBrush(Qt.BrushStyle.NoBrush)
        else:
            p.fillRect(r, fill)
            p.setPen(pen)
            p.drawRect(r)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self._pixmap:
            p.drawPixmap(
                QRect(0, 0, int(self._orig_w * self._disp),
                      int(self._orig_h * self._disp)),
                self._pixmap,
            )

        font = p.font()
        font.setPointSize(8)
        font.setBold(True)
        p.setFont(font)

        for box in self._boxes:
            sel = box in self._selected
            r = box.screen_rect(self._disp)

            # Everything below draws the box unrotated; the painter is
            # turned about the box's centre instead, so labels, badges and
            # handles all follow it for free.
            turned = bool(box.angle)
            if turned:
                p.save()
                c = box.centre()
                p.translate(c.x() * self._disp, c.y() * self._disp)
                p.rotate(box.angle)
                p.translate(-c.x() * self._disp, -c.y() * self._disp)

            if box.group is not None:
                fill = _group_color(box.group, sel, alpha=160)
                border = _group_color(box.group, sel, alpha=230)
            else:
                fill = _SEL_FILL if sel else self._ungrouped_fill
                border = _SEL_BORDER if sel else self._ungrouped_border

            self._paint_shape(p, box, r, fill, border, 2.0 if sel else 1.5)

            # mode-override badge (bottom-left) — only when this region
            # deviates from the slide/PDF default
            if box.mode is not None and r.height() >= 16 and r.width() >= 26:
                label = _MODE_LABELS.get(box.mode, "")
                if label:
                    chip = QRect(r.left() + 2, r.bottom() - 13, 20, 11)
                    p.fillRect(chip, _BADGE_BG)
                    p.setPen(_BADGE_FG)
                    p.drawText(chip, Qt.AlignmentFlag.AlignCenter, label)

            # note indicator (top-right dot)
            if box.note:
                d = 7
                dot = QRect(r.right() - d - 2, r.top() + 2, d, d)
                p.setPen(QPen(QColor(60, 60, 60, 220), 1))
                p.setBrush(QColor(255, 255, 255, 235))
                p.drawEllipse(dot)
                p.setBrush(Qt.BrushStyle.NoBrush)

            # corner resize handles — only on selected boxes to keep the
            # canvas uncluttered. Filled with the box's border colour and
            # outlined white so they read as handles on both the mask and
            # the page background.
            if sel:
                hfill = QColor(border)
                hfill.setAlpha(255)
                p.setPen(QPen(_HANDLE_OUTLINE, 1))
                for h in _local_handles(r):
                    p.fillRect(h, hfill)
                    p.drawRect(h)

            if turned:
                p.restore()

        # in-progress draw
        if self._drawing and self._drag_start and self._drag_current:
            tmp = _Box(self._drag_start.x(), self._drag_start.y(),
                       self._drag_current.x() - self._drag_start.x(),
                       self._drag_current.y() - self._drag_start.y())
            r = tmp.screen_rect(self._disp)
            self._paint_shape(p, tmp, r, self._ungrouped_fill,
                              self._ungrouped_border, 1.5)

        # rubber-band marquee (select tool)
        if self._banding:
            band = self._band_rect()
            if band is not None:
                p.fillRect(band, _BAND_FILL)
                pen = QPen(_BAND_BORDER, 1)
                pen.setStyle(Qt.PenStyle.DashLine)
                p.setPen(pen)
                p.drawRect(band)

        p.end()

    # ------------------------------------------------------------- gestures

    def event(self, ev):
        # macOS trackpad pinch arrives as a native gesture, not a wheel event
        if ev.type() == QEvent.Type.NativeGesture:
            try:
                if ev.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
                    self.zoom_gesture.emit(ev.value(), ev.position().toPoint())
                    return True
            except AttributeError:
                pass
        return super().event(ev)

    def wheelEvent(self, ev):
        # Ctrl/Cmd + scroll zooms (mouse-wheel equivalent of the pinch);
        # a plain scroll is left for the scroll area to pan with.
        if ev.modifiers() & (Qt.KeyboardModifier.ControlModifier
                             | Qt.KeyboardModifier.MetaModifier):
            delta = ev.angleDelta().y() / 800.0
            if delta:
                self.zoom_gesture.emit(delta, ev.position().toPoint())
            ev.accept()
        else:
            ev.ignore()

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

    def _edge_at(self, spos: QPoint):
        """Return (box, edge) if spos is on an edge of a selected box."""
        outside = self._tool == "select"
        for box in reversed(self._boxes):
            if box not in self._selected:
                continue
            edge = box.edge_at(spos.x(), spos.y(), self._disp, outside)
            if edge:
                return box, edge
        return None, None

    def _rotate_at(self, spos: QPoint):
        """Return (box, corner) if spos is in a selected box's rotate ring.

        Select tool only. The ring sits on empty slide just outside a corner,
        which under the Draw tool is where you press to start the next box —
        drawing has to win there.
        """
        if self._tool != "select":
            return None, None
        for box in reversed(self._boxes):
            if box not in self._selected:
                continue
            corner = box.rotate_corner_at(spos.x(), spos.y(), self._disp)
            if corner:
                return box, corner
        return None, None

    def _begin_resize(self, box: _Box, handle: str):
        """Start a corner (two-axis) or edge (one-axis) resize.

        The corner opposite the handle is pinned in slide coordinates and the
        drag is measured in the box's own frame, so the maths is identical
        whether the box is rotated or not — and an edge drag is just a corner
        drag with one axis held at its current extent.
        """
        anchor_name, free = _RESIZE_HANDLES[handle]
        n = box.norm()
        sx = 1.0 if anchor_name in ("tl", "bl") else -1.0
        sy = 1.0 if anchor_name in ("tl", "tr") else -1.0
        self._pre_drag = self.get_boxes()
        self._resizing = box
        self._resize_free = free
        self._resize_angle = box.angle
        self._resize_anchor = box.to_world(box.local_corner(anchor_name))
        self._resize_fixed = (sx * n.w, sy * n.h)

    def _begin_rotate(self, box: _Box, ipos: QPointF):
        """Start a rotation drag around the selection's centre."""
        targets = (set(self._selected)
                   if box in self._selected and len(self._selected) > 1
                   else {box})
        xs: list[float] = []
        ys: list[float] = []
        for b in targets:
            n = b.norm()            # settle any inverted rect before turning it
            b.x, b.y, b.w, b.h = n.x, n.y, n.w, n.h
            for name in ("tl", "tr", "bl", "br"):
                w = b.to_world(b.local_corner(name))
                xs.append(w.x())
                ys.append(w.y())
        pivot = QPointF((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)
        self._pre_drag = self.get_boxes()
        self._rot_pivot = pivot
        self._rot_start = math.degrees(math.atan2(ipos.y() - pivot.y(),
                                                  ipos.x() - pivot.x()))
        self._rotating = [(b, b.centre(), b.angle) for b in targets]

    def _apply_rotate(self, ipos: QPointF, shift: bool):
        pivot = self._rot_pivot
        delta = math.degrees(math.atan2(ipos.y() - pivot.y(),
                                        ipos.x() - pivot.x())) - self._rot_start
        if shift:
            if len(self._rotating) == 1:
                a0 = self._rotating[0][2]
                delta = round((a0 + delta) / _ROT_SNAP) * _ROT_SNAP - a0
            else:
                delta = round(delta / _ROT_SNAP) * _ROT_SNAP
        for box, centre0, angle0 in self._rotating:
            box.angle = (angle0 + delta) % 360.0
            if len(self._rotating) > 1:
                c = pivot + _rot_vec(centre0 - pivot, delta)
                box.x = c.x() - box.w / 2.0
                box.y = c.y() - box.h / 2.0

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
        self._press_spos = spos
        self._drag_started = False
        self._shift_press_box = None

        # corner handle of a selected box?
        box, corner = self._handle_at(spos)
        if box is not None:
            self._begin_resize(box, corner)
            self.update()
            return

        # edge of a selected box — one-axis resize, no handle drawn for it
        box, edge = self._edge_at(spos)
        if box is not None:
            self._begin_resize(box, edge)
            self.update()
            return

        # just outside a corner — rotate
        box, corner = self._rotate_at(spos)
        if box is not None:
            self._begin_rotate(box, ipos)
            self.update()
            return

        # click inside a box?
        for box in reversed(self._boxes):
            if box.contains_screen(spos.x(), spos.y(), self._disp):
                if shift:
                    # Defer: a shift-CLICK toggles selection membership, a
                    # shift-DRAG moves the box's whole group. Which one it
                    # is only becomes clear once the mouse moves (or not).
                    self._shift_press_box = box
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

        # empty space — marquee-select or draw, depending on the active tool
        if self._tool == "select":
            self._banding = True
            self._band_base_sel = set(self._selected) if shift else set()
            if not shift:
                self._selected = set()
        else:
            if not shift:
                self._selected = set()
            self._drawing = True
            self._draw_shift = shift
        self._drag_start = ipos
        self._drag_current = ipos
        self.update()

    def mouseMoveEvent(self, event):
        spos = event.pos()
        ipos = self._to_img(spos)
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)

        if self._rotating:
            self._apply_rotate(ipos, shift)
            self.update()
            return

        if self._resizing:
            box = self._resizing
            a = self._resize_anchor
            v = _rot_vec(ipos - a, -self._resize_angle)
            fx, fy = self._resize_fixed
            vx = v.x() if "x" in self._resize_free else fx
            vy = v.y() if "y" in self._resize_free else fy
            # put the new centre where it has to be for the anchor to hold
            c = a + _rot_vec(QPointF(vx / 2.0, vy / 2.0), self._resize_angle)
            box.x = c.x() - vx / 2.0
            box.y = c.y() - vy / 2.0
            box.w = vx
            box.h = vy
            self.update()
            return

        if self._moving:
            if not self._drag_started:
                if (spos - (self._press_spos or spos)).manhattanLength() < _DRAG_THRESHOLD:
                    return
                self._drag_started = True
                if self._shift_press_box is not None:
                    # shift-drag → move the whole group together
                    self._selected = self._group_members(self._shift_press_box)
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

        if self._drawing or self._banding:
            self._drag_current = ipos
            if self._banding:
                self._apply_band_selection()
            self.update()
            return

        # cursor hints — edge resize and rotation are hover-only, so the
        # cursor is the only thing that announces them
        box, corner = self._handle_at(spos)
        if box is not None:
            self.setCursor(_dir_cursor(_HANDLE_DIR[corner] + box.angle))
            return
        box, edge = self._edge_at(spos)
        if box is not None:
            self.setCursor(_dir_cursor(_HANDLE_DIR[edge] + box.angle))
            return
        box, corner = self._rotate_at(spos)
        if box is not None:
            self.setCursor(_rotate_cursor())
            return
        for box in reversed(self._boxes):
            if box.contains_screen(spos.x(), spos.y(), self._disp):
                self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
                return
        self.setCursor(QCursor(
            Qt.CursorShape.CrossCursor if self._tool == "draw"
            else Qt.CursorShape.ArrowCursor))

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

        if self._rotating:
            self._rotating = None
            self._rot_pivot = None
            self._commit_drag()
            self.boxes_changed.emit()
            self.update()
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
            if not self._drag_started and self._shift_press_box is not None:
                # plain shift-click: toggle selection membership
                box = self._shift_press_box
                if box in self._selected:
                    self._selected.discard(box)
                else:
                    self._selected.add(box)
            self._moving = None
            self._shift_press_box = None
            self._commit_drag()
            self.boxes_changed.emit()
            self.update()
            return

        if self._banding:
            self._apply_band_selection()
            self._banding = False
            self._band_base_sel = set()
            self._drag_start = None
            self._drag_current = None
            self.update()
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
                # Shift-draw: the new box joins the selection's group so a
                # multi-part card can be sketched in one pass — draw, then
                # keep Shift held while drawing the rest.
                if self._draw_shift and self._selected:
                    grouped = [b for b in self._selected if b.group is not None]
                    if grouped:
                        new_box.group = grouped[0].group
                        new_box.group_uid = grouped[0].group_uid
                    else:
                        gid = self._next_gid
                        self._next_gid += 1
                        guid = uuid.uuid4().hex
                        for b in self._selected:
                            b.group = gid
                            b.group_uid = guid
                        new_box.group = gid
                        new_box.group_uid = guid
                    self._selected = set(self._selected) | {new_box}
                else:
                    self._selected = {new_box}
                self.boxes_changed.emit()
            self._drawing = False
            self._draw_shift = False
            self._drag_start = None
            self._drag_current = None
            self.update()

    def mouseDoubleClickEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        spos = event.pos()
        for box in reversed(self._boxes):
            if box.contains_screen(spos.x(), spos.y(), self._disp):
                targets = self._group_members(box)
                self._selected = set(targets)
                self.update()
                self._edit_note(targets)
                return

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

    def handle_arrow(self, key, shift: bool = False):
        """Arrow key: nudge the selection, or flip slides when nothing is selected.

        Public because the dialog also binds Left/Right window-level — the
        canvas only receives key events while it holds focus, and slide
        navigation is the one thing you do before ever clicking the slide.
        """
        if self._selected:
            step = _NUDGE_STEP_BIG if shift else _NUDGE_STEP
            dx = {Qt.Key.Key_Left: -step, Qt.Key.Key_Right: step}.get(key, 0)
            dy = {Qt.Key.Key_Up: -step, Qt.Key.Key_Down: step}.get(key, 0)
            self._nudge(dx, dy)
        elif key == Qt.Key.Key_Left:
            self.slide_nav.emit(-1)
        elif key == Qt.Key.Key_Right:
            self.slide_nav.emit(+1)

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
            self.handle_arrow(key, shift)
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

        if key == Qt.Key.Key_N and self._selected:
            self.edit_note_selected()
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

            note_act = QAction(
                "Edit Note…  (N)" if any(b.note for b in self._selected)
                else "Add Note…  (N)", menu)
            note_act.triggered.connect(self.edit_note_selected)
            menu.addAction(note_act)

            # per-region occlusion-mode override
            mode_menu = menu.addMenu("Occlusion Mode")
            region = self._expand_to_groups(self._selected)
            current_modes = {b.mode for b in region}
            for label, value in (
                ("Slide Default", None),
                ("Hide All, Show One", "ao"),
                ("Hide One, Show One", "oa"),
            ):
                act = QAction(label, mode_menu)
                act.setCheckable(True)
                act.setChecked(current_modes == {value})
                act.triggered.connect(
                    lambda _, m=value: self.set_mode_selected(m))
                mode_menu.addAction(act)

            if any(b.angle for b in self._selected):
                straighten = QAction("Reset Rotation", menu)
                straighten.triggered.connect(self.reset_rotation_selected)
                menu.addAction(straighten)

            menu.addSeparator()

            copy_act = QAction("Copy  (Ctrl+C)", menu)
            copy_act.triggered.connect(self.copy_selected)
            menu.addAction(copy_act)

            remove_act = QAction(
                f"Remove Box{'es' if len(self._selected) > 1 else ''}  (Del)", menu
            )
            remove_act.triggered.connect(self._delete_selected)
            menu.addAction(remove_act)

            menu.addSeparator()

            group_act = QAction("Group Selected  (G)", menu)
            group_act.setEnabled(len(self._selected) >= 2)
            group_act.triggered.connect(self.group_selected)
            menu.addAction(group_act)

            ungroup_act = QAction("Ungroup Selected  (U)", menu)
            ungroup_act.setEnabled(any(b.group is not None for b in self._selected))
            ungroup_act.triggered.connect(self.ungroup_selected)
            menu.addAction(ungroup_act)

            # "select whole group" if clicked box is grouped
            if clicked.group is not None:
                sel_grp = QAction(f"Select All in Group {clicked.group + 1}", menu)
                sel_grp.triggered.connect(
                    lambda _, gid=clicked.group: self._select_group(gid)
                )
                menu.addSeparator()
                menu.addAction(sel_grp)
        else:
            sel_all_act = QAction("Select All  (Ctrl+A)", menu)
            sel_all_act.setEnabled(bool(self._boxes))
            sel_all_act.triggered.connect(self.select_all)
            menu.addAction(sel_all_act)

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
