"""Rebuild an editing session from the cards it produced.

A session (user_files/sessions/*.json) is what makes a card editable: it
holds the boxes and the region -> note map. Sessions go missing — cleared
out, written on another machine, or predating the store — and a card whose
session is gone used to open an empty window.

The notes carry enough to reconstruct one. Each records the PDF it came
from (Slides PDF) and the slide it is on (Header), and its question mask is
an SVG that draws *every* box on that slide: one note recovers a whole
slide's geometry, and each note on the slide identifies its own region as
the one drawn with the highlight stroke.

What comes back: geometry, shape, rotation, grouping, labels, the note
text, the occlusion mode, the slide images already in the collection, and
the region -> note map — so a later "Create All Cards" updates those same
notes in place rather than duplicating them.

Coordinates come back in the SVG's own pixel space, which is whatever
render scale the cards were made at. The caller scales them to the pages it
has actually rendered; see PDFOcclusionDialog._apply_recovery.
"""
import html
import os
import re
import uuid
from typing import Optional
from xml.etree import ElementTree

from .card_builder import NOTES_PDF_FIELD, SLIDES_PDF_FIELD, pdf_path_in

_NS = "{http://www.w3.org/2000/svg}"

# Boxes of a multi-box region are dilated before being drawn, so the group
# fuses into one blob. Undone here. Keep in step with card_builder.
_GROUP_PAD = 4

# The region being tested is stroked at 3 (6 for a group, which is drawn
# with a double-width stroke first). Nothing else is stroked above 2/4.
_ACTIVE_STROKE = 3.0
_ACTIVE_GROUP_STROKE = 5.0

_HEADER_RE = re.compile(r"^(?:(?P<lecture>.*) · )?Slide (?P<n>\d+)(?:/(?P<of>\d+))?$")
_ROTATE_RE = re.compile(r"rotate\(\s*(-?[\d.]+)")
_SVG_FNAME_RE = re.compile(r"pdf_occ_[0-9a-f]+\.svg")
_IMG_FNAME_RE = re.compile(r"pdf_occ_[0-9a-f]+\.(?:png|jpg|jpeg)")
_TAG_RE = re.compile(r"<[^>]+>")


# ------------------------------------------------------------------ parsing

def _num(el, attr: str, default: float = 0.0) -> float:
    try:
        return float(el.get(attr, default))
    except (TypeError, ValueError):
        return default


def _shape_box(el) -> Optional[dict]:
    """One <rect>/<ellipse> back to a box dict, or None for anything else."""
    tag = el.tag.rsplit("}", 1)[-1]
    if tag == "rect":
        box = {"x": _num(el, "x"), "y": _num(el, "y"),
               "w": _num(el, "width"), "h": _num(el, "height")}
    elif tag == "ellipse":
        rx, ry = _num(el, "rx"), _num(el, "ry")
        box = {"x": _num(el, "cx") - rx, "y": _num(el, "cy") - ry,
               "w": rx * 2, "h": ry * 2, "shape": "ellipse"}
    else:
        return None
    if box["w"] <= 0 or box["h"] <= 0:
        return None
    m = _ROTATE_RE.search(el.get("transform") or "")
    if m:
        box["angle"] = float(m.group(1))
    return box


def _undilate(box: dict) -> dict:
    """Undo the padding a group's members are drawn with."""
    return dict(box,
                x=box["x"] + _GROUP_PAD, y=box["y"] + _GROUP_PAD,
                w=max(1.0, box["w"] - 2 * _GROUP_PAD),
                h=max(1.0, box["h"] - 2 * _GROUP_PAD))


def _geom(box: dict) -> tuple:
    """Rounded geometry, for matching the same box across two masks."""
    return (round(box["x"]), round(box["y"]),
            round(box["w"]), round(box["h"]))


def parse_mask(svg_bytes: bytes) -> Optional[dict]:
    """One question mask -> {"w", "h", "regions", "mode"}.

    A region is {"boxes": [...], "active": bool, "label": str}; exactly one
    is active — the one this card asks about.
    """
    try:
        root = ElementTree.fromstring(svg_bytes)
    except ElementTree.ParseError:
        return None

    regions: list[dict] = []
    label = ""
    stroked_inactive = False

    for el in root:
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "text":
            label = (el.text or "").strip() or label
            continue
        if tag == "g":
            # A group is drawn stroked-then-filled, so each member appears
            # twice; the geometry is the same both times.
            boxes, seen = [], set()
            stroke = 0.0
            for child in el:
                b = _shape_box(child)
                if b is None:
                    continue
                stroke = max(stroke, _num(child, "stroke-width"))
                key = _geom(b)
                if key not in seen:
                    seen.add(key)
                    boxes.append(_undilate(b))
            if not boxes:
                continue
            active = stroke >= _ACTIVE_GROUP_STROKE
            if not active and stroke:
                stroked_inactive = True
            regions.append({"boxes": boxes, "active": active, "label": ""})
            continue
        b = _shape_box(el)
        if b is None:
            continue
        stroke = _num(el, "stroke-width")
        active = stroke >= _ACTIVE_STROKE
        if not active and stroke:
            stroked_inactive = True
        regions.append({"boxes": [b], "active": active, "label": ""})

    if not regions:
        return None

    # Fall back to draw order — the tested region is always drawn last —
    # for a mask with no stroke to go on.
    if not any(r["active"] for r in regions):
        regions[-1]["active"] = True
    if label:
        for r in regions:
            if r["active"]:
                r["label"] = label

    # "Hide One, Show One" is the only mode that outlines the boxes it is
    # not asking about; "Hide All" leaves them unstroked.
    return {
        "w": _num(root, "width"), "h": _num(root, "height"),
        "regions": regions,
        "mode": "oa" if stroked_inactive else "ao",
    }


# -------------------------------------------------------------- collection

def _first(pattern, field_val: str) -> str:
    m = pattern.search(field_val or "")
    return m.group(0) if m else ""


def _plain(field_val: str) -> str:
    """A note field back to the plain text it was written from."""
    txt = _TAG_RE.sub("\n", (field_val or "").replace("<br>", "\n"))
    return html.unescape(txt).replace("\u00a0", " ").strip()


def _read_mask(col, note) -> Optional[dict]:
    fname = _first(_SVG_FNAME_RE, note["Question Mask"])
    if not fname:
        return None
    try:
        with open(os.path.join(col.media.dir(), fname), "rb") as f:
            return parse_mask(f.read())
    except OSError:
        return None


def _slide_of(note) -> Optional[tuple]:
    """(lecture, local page index) from the Header field."""
    m = _HEADER_RE.match(_plain(note["Header"]))
    if not m:
        return None
    return html.unescape(m.group("lecture") or ""), int(m.group("n")) - 1


def _sibling_nids(col, note, path: str, on_progress=None) -> list:
    """Every note of the same type made from the same PDF."""
    out = []
    nids = col.find_notes(f"mid:{note.mid}")
    for i, nid in enumerate(nids):
        if on_progress and i % 200 == 0:
            on_progress(i, len(nids))
        try:
            n = col.get_note(nid)
        except Exception:
            continue
        if SLIDES_PDF_FIELD in n and pdf_path_in(n[SLIDES_PDF_FIELD]) == path:
            out.append(n)
    return out


def recover(col, nid: int, on_progress=None) -> Optional[dict]:
    """Rebuild everything the cards of one PDF know about their session.

    Returns None when the note is not one of ours, records no PDF, or the
    PDF is no longer where it was. Otherwise:

        {"pdf_path", "notes_pdf", "lecture", "focus",
         "pages": {local page: {"w", "h", "boxes", "note_map",
                                "mode", "image"}}}

    `focus` is the region key of the card asked about, so the caller can
    open on it. Boxes are in the mask SVG's pixel space.
    """
    try:
        note = col.get_note(nid)
    except Exception:
        return None
    if SLIDES_PDF_FIELD not in note or "Question Mask" not in note:
        return None
    path = pdf_path_in(note[SLIDES_PDF_FIELD])
    if not path or not os.path.exists(path):
        return None

    lecture = ""
    notes_pdf = ""
    # slide -> the notes on it, newest last
    by_page: dict = {}
    for n in _sibling_nids(col, note, path, on_progress):
        slide = _slide_of(n)
        mask = _read_mask(col, n) if slide else None
        if not slide or not mask:
            continue
        lecture = lecture or slide[0]
        if not notes_pdf and NOTES_PDF_FIELD in n:
            notes_pdf = pdf_path_in(n[NOTES_PDF_FIELD])
        by_page.setdefault(slide[1], []).append({
            "nid": n.id, "mod": n.mod, "mask": mask,
            "note": _plain(n["Notes"]) if "Notes" in n else "",
            "image": _first(_IMG_FNAME_RE, n["Image"]),
        })

    if not by_page:
        return None

    focus = None
    pages: dict = {}
    for page, entries in by_page.items():
        entries.sort(key=lambda e: e["mod"])
        built = _build_page(page, entries)
        if not built:
            continue
        pages[page] = built
        if nid in built["note_map"].values():
            focus = next(k for k, v in built["note_map"].items() if v == nid)

    if not pages:
        return None
    return {"pdf_path": path, "notes_pdf": notes_pdf, "lecture": lecture,
            "pages": pages, "focus": focus}


def _region_id(region: dict) -> frozenset:
    """What identifies a region across the cards that draw it."""
    return frozenset(_geom(b) for b in region["boxes"])


def _merged_order(entries: list) -> list:
    """The slide's regions in the order its boxes were in.

    A card draws the region it tests last, so no single card shows the real
    order — but every other card on the slide draws that region in place.
    Merging what they agree on (a topological sort over the pairs each card
    orders) puts it back, which is what keeps the masks regenerated from
    these boxes byte-identical to the ones already on the cards.
    """
    after: dict = {}
    nodes: list = []

    def node(rid):
        if rid not in after:
            after[rid] = set()
            nodes.append(rid)

    for e in reversed(entries):        # newest first, so it sets the base order
        regions = e["mask"]["regions"]
        for r in regions:
            node(_region_id(r))
        seq = [_region_id(r) for r in regions if not r["active"]]
        for i, rid in enumerate(seq):
            after[rid].update(seq[i + 1:])

    out = []
    left = list(nodes)
    while left:
        pending = set(left)
        blocked = set()
        for rid in left:
            blocked |= after[rid] & pending
        # A cycle would mean two cards disagree about the order; take the
        # base order's next region and move on rather than give up.
        pick = next((rid for rid in left if rid not in blocked), left[0])
        out.append(pick)
        left.remove(pick)
    return out


def _build_page(page: int, entries: list) -> Optional[dict]:
    """One slide's boxes, keys and note ids, from the cards made off it."""
    # Prefer the most recently written card's copy of a region — every card
    # on a slide is rewritten from the same boxes, so they only differ if
    # one is left over from an older run.
    source: dict = {}
    for e in entries:
        for r in e["mask"]["regions"]:
            source[_region_id(r)] = r

    boxes: list[dict] = []
    key_of: dict = {}          # region id -> region key
    boxes_of: dict = {}        # region key -> the boxes in it
    for gid, rid in enumerate(_merged_order(entries)):
        region = source.get(rid)
        if region is None:
            continue
        members = region["boxes"]
        if len(members) > 1:
            guid = uuid.uuid4().hex
            key = f"{page}:g:{guid}"
            for b in members:
                b.update(group=gid, group_uid=guid)
        else:
            box_id = uuid.uuid4().hex
            key = f"{page}:b:{box_id}"
            members[0]["id"] = box_id
        key_of[rid] = key
        boxes_of[key] = members
        boxes.extend(members)

    if not boxes:
        return None

    note_map: dict = {}
    cards: list = []           # (region key, card) for the ones we placed
    for e in entries:
        active = next((r for r in e["mask"]["regions"] if r["active"]), None)
        if active is None:
            continue
        key = key_of.get(_region_id(active))
        if key is None:
            continue   # this card's region has been edited away since
        note_map[key] = e["nid"]
        cards.append((key, e, active))
        # A label and the note text live on the card that asks about the
        # region, so they can only be read off that card.
        for b in boxes_of[key]:
            if active["label"]:
                b["label"] = active["label"]
            if e["note"]:
                b["note"] = e["note"]

    # The occlusion mode is per card, so a slide can be mixed. Whatever most
    # of its cards use becomes the slide's setting; the rest keep theirs as
    # a per-box override, exactly as _resolve_mode reads them back.
    modes = [e["mask"]["mode"] for _k, e, _a in cards]
    page_mode = max(set(modes), key=modes.count) if modes else "ao"
    for key, e, _a in cards:
        if e["mask"]["mode"] != page_mode:
            for b in boxes_of[key]:
                b["mode"] = e["mask"]["mode"]

    return {
        "w": entries[-1]["mask"]["w"], "h": entries[-1]["mask"]["h"],
        "boxes": boxes,
        "note_map": note_map,
        "mode": page_mode,
        "image": entries[-1]["image"],
    }
