"""
Creates Anki notes using IOE-style SVG overlays.

Occlusion modes
───────────────
ao  (Hide All, Show One)  — default
    Front : every box on the slide is masked (opaque).
    Back  : the tested box disappears; all other boxes stay masked.
    → forces recall of what was under *that specific* box while
      hiding the rest so you can't peek.

oa  (Hide One, Show One)
    Front : only the tested box is masked; other boxes shown as faint outlines.
    Back  : the tested box disappears; all other boxes remain as faint outlines.
    → good for slides with many independent facts.
"""
import html
import os
import re
import uuid

from aqt.qt import QImage

from anki.collection import Collection
from anki.models import NotetypeDict

from .pdf_renderer import qimage_to_png_bytes


_MEDIA_PREFIX = "pdf_occ_"


# ----------------------------------------------------------------- note type

# Fields store the full <img> tag (so the Browse editor renders thumbnails like
# Image Occlusion Enhanced). norm() wraps any older card that still holds a bare
# filename, so both formats render. The answer mask is preloaded in #io-preload
# so revealing the answer paints both layers from cache at once — no flash.
#
# Flicker guard (Image Occlusion Enhanced technique): #io-original starts
# visibility:hidden and is only revealed once the overlay mask has finished
# loading, so the bare slide never flashes before the mask paints over it.
# visibility (not display) keeps the layout box, so there is no reflow either.
# A short timeout + error handler guarantee the card can never stay blank.
_REVEAL_JS = """\
  var orig = document.getElementById("io-original");
  var mask = document.querySelector("#io-overlay img");
  function reveal(){ if (orig) orig.style.visibility = "visible"; }
  if (!mask || mask.complete) {
    reveal();
  } else {
    mask.addEventListener("load", reveal);
    mask.addEventListener("error", reveal);
    setTimeout(reveal, 400);
  }"""

_FRONT_TMPL = """\
<div id="io-wrapper">
  <div id="io-original">{{Image}}</div>
  <div id="io-overlay">{{Question Mask}}</div>
</div>
<div id="io-header">{{Header}}</div>
<div id="io-preload" aria-hidden="true">{{Answer Mask}}</div>
<script>
(function(){
  function norm(id){
    var b = document.getElementById(id);
    if(b && !b.querySelector("img")){
      var n = b.textContent.trim();
      if(n) b.innerHTML = '<img src="'+n+'">';
    }
  }
  norm("io-original"); norm("io-overlay"); norm("io-preload");
""" + _REVEAL_JS + """
})();
</script>"""

# The back shows the answer mask directly as the overlay — no JS src swap, and
# nothing is hidden via JS, so a card can never get stuck blank (e.g. on mobile).
_BACK_TMPL = """\
<div id="io-wrapper">
  <div id="io-original">{{Image}}</div>
  <div id="io-overlay">{{Answer Mask}}</div>
</div>
<div id="io-header">{{Header}}</div>
<script>
(function(){
  function norm(id){
    var b = document.getElementById(id);
    if(b && !b.querySelector("img")){
      var n = b.textContent.trim();
      if(n) b.innerHTML = '<img src="'+n+'">';
    }
  }
  norm("io-original"); norm("io-overlay");
  var img = document.querySelector("#io-overlay img");
  window._ioAnswerMask = img ? img.getAttribute("src") : "";
  window._ioAllHidden = false;
""" + _REVEAL_JS + """
})();
</script>
<div id="io-extra">{{Notes}}</div>
<div id="io-toggle-bar">
  <button id="io-toggle-btn" onclick="
    var img = document.querySelector('#io-overlay img');
    if (!img) return;
    if (window._ioAllHidden) {
      img.src = window._ioAnswerMask;
      window._ioAllHidden = false;
      this.textContent = 'Show All';
    } else {
      img.src = 'data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22/>';
      window._ioAllHidden = true;
      this.textContent = 'Hide All';
    }
  ">Show All</button>
</div>"""

_CSS = """\
.card {
  font-family: Arial, sans-serif;
  font-size: 14px;
  text-align: center;
  background: #fff;
  color: #333;
}
.card.night_mode {
  background: #2c2c2e;
  color: #d6d6d6;
}
#io-wrapper {
  position: relative;
  display: inline-block;
  max-width: 100%;
}
#io-original {
  /* Hidden until the overlay mask has loaded (see template JS) so the
     unmasked slide never flashes on screen. Don't edit. */
  visibility: hidden;
}
#io-original img {
  display: block;
  max-width: 100%;
  height: auto;
}
#io-overlay {
  position: absolute;
  top: 0; left: 0;
  width: 100%;
  pointer-events: none;
}
#io-overlay img {
  display: block;
  width: 100%;
  height: auto;
}
#io-preload {
  position: absolute;
  width: 1px; height: 1px;
  opacity: 0;
  overflow: hidden;
  pointer-events: none;
}
#io-header {
  margin-top: 6px;
  color: #888;
  font-size: 11px;
}
.night_mode #io-header {
  color: #9b9b9b;
}
#io-extra {
  margin-top: 10px;
  font-size: 13px;
}
#io-toggle-bar {
  margin-top: 10px;
  text-align: center;
}
#io-toggle-btn {
  background: #836EAA;
  color: white;
  font-weight: bold;
  font-size: 13px;
  padding: 6px 20px;
  border: none;
  border-radius: 4px;
  cursor: pointer;
}
#io-toggle-btn:hover {
  background: #75619b;
}
.night_mode #io-toggle-btn {
  background: #836EAA;
  color: #1a1a1a;
}
.night_mode #io-toggle-btn:hover {
  background: #9885b8;
}"""


_IMG_FIELDS = ("Image", "Question Mask", "Answer Mask")


def _migrate_field_format(col: Collection, nt: NotetypeDict) -> None:
    """Wrap bare-filename fields of existing notes in <img> tags.

    Older cards stored just the media filename; the current templates and the
    Browse editor expect a full <img> tag. This converts them in place and is
    idempotent (fields already containing an <img> are left untouched).
    """
    updated = []
    for nid in col.models.nids(nt):
        note = col.get_note(nid)
        changed = False
        for f in _IMG_FIELDS:
            if f not in note:
                continue
            v = note[f].strip()
            if v and "<img" not in v.lower():
                note[f] = f'<img src="{v}">'
                changed = True
        if changed:
            updated.append(note)
    if updated:
        col.update_notes(updated)


def ensure_note_type(col: Collection, name: str = "PDF Occlusion") -> NotetypeDict:
    mm = col.models
    nt = mm.by_name(name)
    # Migrate the pre-rename note type so existing cards keep working
    if not nt and name == "PDF Occlusion":
        old = mm.by_name("PDF Image Occlusion")
        if old:
            old["name"] = name
            mm.save(old)
            nt = old
    if nt:
        # field was called "Remarks" before v3 — rename in place so existing
        # notes keep their content
        for fld in nt["flds"]:
            if fld["name"] == "Remarks":
                try:
                    mm.rename_field(nt, fld, "Notes")
                except Exception:
                    fld["name"] = "Notes"
                break
        nt["css"] = _CSS
        for tmpl in nt["tmpls"]:
            tmpl["qfmt"] = _FRONT_TMPL
            tmpl["afmt"] = _BACK_TMPL
        mm.save(nt)
        _migrate_field_format(col, nt)
        return nt

    nt = mm.new(name)
    nt["css"] = _CSS

    for fname in ("Image", "Question Mask", "Answer Mask", "Header", "Notes"):
        mm.add_field(nt, mm.new_field(fname))

    tmpl = mm.new_template("Card")
    tmpl["qfmt"] = _FRONT_TMPL
    tmpl["afmt"] = _BACK_TMPL
    mm.add_template(nt, tmpl)
    mm.add(nt)
    return nt


# ---------------------------------------------------------------- SVG helpers

def _rect(box: dict, fill: str, fill_opacity: float,
          stroke: str = "none", stroke_opacity: float = 1.0,
          stroke_width: float = 0) -> str:
    """One SVG mask shape for a box — <rect> or <ellipse> per box["shape"]."""
    common = f'fill="{fill}" fill-opacity="{fill_opacity}"'
    if stroke != "none":
        common += (f' stroke="{stroke}" stroke-opacity="{stroke_opacity}"'
                   f' stroke-width="{stroke_width}"')
    if box.get("shape") == "ellipse":
        cx = box["x"] + box["w"] / 2
        cy = box["y"] + box["h"] / 2
        return (f'<ellipse cx="{cx:g}" cy="{cy:g}" '
                f'rx="{box["w"] / 2:g}" ry="{box["h"] / 2:g}" {common}/>')
    return (f'<rect x="{box["x"]}" y="{box["y"]}" '
            f'width="{box["w"]}" height="{box["h"]}" {common}/>')


def _region_shapes(region: list[dict], fill: str, fill_opacity: float,
                   stroke: str = "none", stroke_opacity: float = 1.0,
                   stroke_width: float = 0.0) -> str:
    """Render one card region (a single box, or all boxes of a group).

    A multi-box region is drawn as a flat union: each shape is first drawn
    with a double-width stroke, then all fills are drawn on top, covering
    the stroke halves that fall inside the union. Only the outline around
    the OUTSIDE of the whole group survives — no borders slicing through a
    merged region. Opacity is applied on the wrapping <g>, so overlapping
    boxes don't stack darker either.

    Boxes in the group are dilated by a few px first, so members that sit
    a hair apart (adjacent text lines) fuse into one blob instead of
    keeping hairline borders in the gap.
    """
    if len(region) == 1:
        return _rect(region[0], fill, fill_opacity, stroke,
                     stroke_opacity, stroke_width)
    pad = 4
    fat = [dict(b, x=max(0, b["x"] - pad), y=max(0, b["y"] - pad),
                w=b["w"] + 2 * pad, h=b["h"] + 2 * pad) for b in region]
    parts = []
    if stroke != "none":
        parts += [_rect(b, fill, 1.0, stroke, stroke_opacity, stroke_width * 2)
                  for b in fat]
    parts += [_rect(b, fill, 1.0) for b in fat]
    return f'<g opacity="{fill_opacity:g}">' + "".join(parts) + "</g>"


def _split_regions(boxes: list[dict]) -> list[list[dict]]:
    """Partition boxes into card regions: groups stay together, the rest solo."""
    singles: list[list[dict]] = []
    groups: dict = {}
    for b in boxes:
        gid = b.get("group_uid") or b.get("group")
        if gid is None:
            singles.append([b])
        else:
            groups.setdefault(gid, []).append(b)
    return singles + list(groups.values())


def _svg(W: int, H: int, rects: list[str]) -> bytes:
    body = "\n  ".join(rects)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}">\n'
        f'  {body}\n</svg>'
    ).encode("utf-8")


def _color_str(color: tuple) -> str:
    return f"rgb({color[0]},{color[1]},{color[2]})"


# Default colours: boxes NOT being tested are neutral grey; the box being
# tested is purple. Both are configurable (mask_color /
# highlight_color). The highlight stroke is derived (darker highlight).
_DEFAULT_MASK_COLOR = (120, 120, 120)
_DEFAULT_HIGHLIGHT_COLOR = (131, 110, 170)


def _make_masks(
    W: int, H: int,
    active: list[dict],
    all_boxes: list[dict],
    color: tuple,
    mode: str,
    opacity: float = 1.0,
    highlight: tuple = _DEFAULT_HIGHLIGHT_COLOR,
) -> tuple[bytes, bytes]:
    """
    Returns (q_svg_bytes, a_svg_bytes) for the given mode.

    active    – boxes being tested on this card
    all_boxes – every box on this slide
    opacity   – 0..1 fill opacity of masking rects (from mask_opacity config)
    highlight – colour of the box(es) being tested
    """
    c = _color_str(color)
    h = _color_str(highlight)
    hs = _color_str(tuple(int(v * 0.6) for v in highlight))
    non_active = [b for b in all_boxes if b not in active]
    na_regions = _split_regions(non_active)

    if mode == "ao":
        # ── Front: non-active boxes opaque; active always highlighted ────────
        q_rects = [_region_shapes(r, c, opacity) for r in na_regions]
        q_rects += [_region_shapes(active, h, opacity, hs, 1.0, 3)]

        # ── Back: non-active stay opaque; active disappears completely ───────
        a_rects = [_region_shapes(r, c, opacity) for r in na_regions]

    else:  # oa
        # ── Front: only active opaque; others as faint outlines ──────────────
        q_rects = [_region_shapes(r, c, 0.15, c, 0.5, 2) for r in na_regions]
        q_rects += [_region_shapes(active, c, opacity, hs, 1.0, 3)]

        # ── Back: all boxes disappear completely (no outlines) ───────────────
        a_rects = []

    return _svg(W, H, q_rects), _svg(W, H, a_rects)


# ------------------------------------------------------------------- media

def _save_media(col: Collection, data: bytes, ext: str = ".png") -> str:
    fname = f"{_MEDIA_PREFIX}{uuid.uuid4().hex}{ext}"
    col.media.write_data(fname, data)
    return fname


def _media_exists(col: Collection, fname: str) -> bool:
    return bool(fname) and os.path.exists(os.path.join(col.media.dir(), fname))


def _trash_media(col: Collection, fnames: list[str]) -> None:
    """Discard superseded mask files (each is owned by exactly one note)."""
    fnames = [f for f in fnames if f]
    if not fnames:
        return
    try:
        col.media.trash_files(fnames)
    except Exception:
        for f in fnames:
            try:
                os.remove(os.path.join(col.media.dir(), f))
            except OSError:
                pass


_SVG_FNAME_RE = re.compile(r'pdf_occ_[0-9a-f]+\.svg')


def _svg_fnames_in(field_val: str) -> list[str]:
    return _SVG_FNAME_RE.findall(field_val or "")


def _media_equals(col: Collection, fname: str, data: bytes) -> bool:
    try:
        with open(os.path.join(col.media.dir(), fname), "rb") as f:
            return f.read() == data
    except OSError:
        return False


def _notes_field_val(note) -> str:
    for f in ("Notes", "Remarks"):
        if f in note:
            return note[f]
    return ""


# ------------------------------------------------------------ note creation

def _resolve_mode(region_boxes: list[dict], page_mode, default_mode: str) -> str:
    """Region override > slide override > PDF default."""
    for b in region_boxes:
        if b.get("mode") in ("ao", "oa"):
            return b["mode"]
    if page_mode in ("ao", "oa"):
        return page_mode
    return default_mode


def _region_note(region_boxes: list[dict]) -> str:
    for b in region_boxes:
        if b.get("note"):
            return b["note"]
    return ""


def _regions_for(page_idx: int, boxes: list[dict]) -> list[tuple[str, list[dict]]]:
    """Split a slide's boxes into card regions with stable keys.

    Ungrouped box → one region keyed by the box id; group → one region keyed
    by the group uid. The keys persist in the session's note_map so a later
    "Create All Cards" updates the same notes instead of duplicating them.
    """
    regions: list[tuple[str, list[dict]]] = []
    grouped: dict[str, list[dict]] = {}
    for b in boxes:
        if b.get("group") is None:
            key = f"{page_idx}:b:{b.get('id') or uuid.uuid4().hex}"
            regions.append((key, [b]))
        else:
            guid = b.get("group_uid") or f"gid{b['group']}"
            grouped.setdefault(guid, []).append(b)
    for guid, members in grouped.items():
        regions.append((f"{page_idx}:g:{guid}", members))
    return regions


def create_occlusion_notes(
    col: Collection,
    deck_id: int,
    note_type: NotetypeDict,
    pages: list[tuple[int, QImage, list[dict]]],
    mask_color: tuple = _DEFAULT_MASK_COLOR,
    mask_opacity: int = 255,
    highlight_color: tuple = _DEFAULT_HIGHLIGHT_COLOR,
    lecture_name: str = "",
    total_slides: int = 0,
    default_mode: str = "ao",
    page_modes: dict = None,
    tags: list = None,
    note_map: dict = None,
    image_map: dict = None,
    on_progress=None,
) -> dict:
    """Create or update occlusion notes for one document.

    note_map maps region keys (see _regions_for) to note ids from a previous
    run; matching regions have their existing notes updated in place, so
    review history survives edits. image_map maps page index (as str) to the
    already-saved slide PNG so re-runs don't duplicate slide images.

    Returns {"created", "updated", "unchanged", "note_map", "image_map",
    "stale_nids"} — stale_nids are notes whose region was deleted since the
    last run (the caller decides whether to remove them). A note whose
    masks, header, and notes are all identical to the current boxes is left
    completely untouched and counted as unchanged.
    """
    page_modes = page_modes or {}
    tags = [t for t in (tags or []) if t]
    note_map = dict(note_map or {})
    image_map = dict(image_map or {})

    created = updated = unchanged = 0
    new_note_map: dict[str, int] = {}
    new_image_map: dict[str, str] = {}
    opacity = max(0.0, min(1.0, mask_opacity / 255))

    for done, (page_idx, img, boxes) in enumerate(pages):
        if on_progress:
            on_progress(done, len(pages))
        W, H = img.width(), img.height()
        page_mode = page_modes.get(page_idx, page_modes.get(str(page_idx)))

        slide_label = f"Slide {page_idx + 1}/{total_slides}" if total_slides else f"Slide {page_idx + 1}"
        header_prefix = f"{lecture_name} · {slide_label}" if lecture_name else slide_label

        # reuse the slide PNG from a previous run when possible
        img_fname = image_map.get(str(page_idx))
        if not _media_exists(col, img_fname):
            img_fname = _save_media(col, qimage_to_png_bytes(img), ".png")
        new_image_map[str(page_idx)] = img_fname

        for key, region_boxes in _regions_for(page_idx, boxes):
            mode = _resolve_mode(region_boxes, page_mode, default_mode)
            q_svg, a_svg = _make_masks(W, H, region_boxes, boxes, mask_color,
                                       mode, opacity, highlight_color)
            remarks = _region_note(region_boxes)
            remarks_html = html.escape(remarks).replace("\n", "<br>")

            note = None
            old_nid = note_map.get(key)
            if old_nid:
                try:
                    note = col.get_note(old_nid)
                except Exception:
                    note = None  # deleted in Anki since last run → recreate

            # the field was "Remarks" before v3 — tolerate custom note types
            # that haven't been migrated
            def _set_notes(n):
                if remarks:
                    for f in ("Notes", "Remarks"):
                        if f in n:
                            n[f] = remarks_html
                            return

            if note is not None:
                old_q = _svg_fnames_in(note["Question Mask"])
                old_a = _svg_fnames_in(note["Answer Mask"])
                # Nothing about this card would change? Leave it completely
                # alone so "updated" counts only real edits.
                if (old_q and old_a
                        and note["Image"] == f'<img src="{img_fname}">'
                        and note["Header"] == header_prefix
                        and (not remarks
                             or _notes_field_val(note) == remarks_html)
                        and _media_equals(col, old_q[0], q_svg)
                        and _media_equals(col, old_a[0], a_svg)):
                    unchanged += 1
                    new_note_map[key] = note.id
                    continue

                q_fname = _save_media(col, q_svg, ".svg")
                a_fname = _save_media(col, a_svg, ".svg")
                note["Image"] = f'<img src="{img_fname}">'
                note["Question Mask"] = f'<img src="{q_fname}">'
                note["Answer Mask"] = f'<img src="{a_fname}">'
                note["Header"] = header_prefix
                _set_notes(note)
                for t in tags:
                    if t not in note.tags:
                        note.tags.append(t)
                col.update_note(note)
                _trash_media(col, old_q + old_a)
                updated += 1
            else:
                q_fname = _save_media(col, q_svg, ".svg")
                a_fname = _save_media(col, a_svg, ".svg")
                note = col.new_note(note_type)
                note["Image"] = f'<img src="{img_fname}">'
                note["Question Mask"] = f'<img src="{q_fname}">'
                note["Answer Mask"] = f'<img src="{a_fname}">'
                note["Header"] = header_prefix
                _set_notes(note)
                note.tags.extend(tags)
                col.add_note(note, deck_id)
                created += 1

            new_note_map[key] = note.id

    # Pages not part of this run (skipped, or all boxes removed) keep their
    # existing card links and slide images untouched — only a region missing
    # from a page we actually processed counts as stale.
    processed = {page_idx for page_idx, _, _ in pages}

    def _page_of(key: str) -> int:
        try:
            return int(key.split(":", 1)[0])
        except ValueError:
            return -1

    for k, fname in image_map.items():
        if k not in new_image_map:
            new_image_map[k] = fname

    stale_nids = []
    for key, nid in note_map.items():
        if key in new_note_map:
            continue
        if _page_of(key) not in processed:
            new_note_map[key] = nid
            continue
        try:
            col.get_note(nid)
            stale_nids.append(nid)
        except Exception:
            pass  # already gone

    return {
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "note_map": new_note_map,
        "image_map": new_image_map,
        "stale_nids": stale_nids,
    }
