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
import uuid

import fitz  # PyMuPDF

from anki.collection import Collection
from anki.models import NotetypeDict


_MEDIA_PREFIX = "pdf_occ_"


# ----------------------------------------------------------------- note type

_FRONT_TMPL = """\
<div id="io-wrapper">
  <img id="io-original" src="{{Image}}">
  <img id="io-overlay"  src="{{Question Mask}}">
</div>
<div id="io-header">{{Header}}</div>"""

_BACK_TMPL = """\
{{FrontSide}}
<script>
(function(){
  var a = "{{Answer Mask}}".replace(/&amp;/g,"&");
  document.getElementById("io-overlay").src = a;
  window._ioAnswerMask = a;
  window._ioAllHidden = false;
})();
</script>
<div id="io-extra">{{Remarks}}</div>
<div id="io-toggle-bar">
  <button id="io-toggle-btn" onclick="
    var el = document.getElementById('io-overlay');
    if (window._ioAllHidden) {
      el.src = window._ioAnswerMask;
      window._ioAllHidden = false;
      this.textContent = 'Show All';
    } else {
      el.src = 'data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22/>';
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
#io-wrapper {
  position: relative;
  display: inline-block;
  max-width: 100%;
}
#io-original {
  display: block;
  max-width: 100%;
  height: auto;
}
#io-overlay {
  position: absolute;
  top: 0; left: 0;
  width: 100%; height: 100%;
  pointer-events: none;
}
#io-header {
  margin-top: 6px;
  color: #888;
  font-size: 11px;
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
  background: #4a90d9;
  color: white;
  font-weight: bold;
  font-size: 13px;
  padding: 6px 20px;
  border: none;
  border-radius: 4px;
  cursor: pointer;
}
#io-toggle-btn:hover {
  background: #357abd;
}"""


def ensure_note_type(col: Collection, name: str = "PDF Image Occlusion") -> NotetypeDict:
    mm = col.models
    nt = mm.by_name(name)
    if nt:
        nt["css"] = _CSS
        for tmpl in nt["tmpls"]:
            tmpl["qfmt"] = _FRONT_TMPL
            tmpl["afmt"] = _BACK_TMPL
        mm.save(nt)
        return nt

    nt = mm.new(name)
    nt["css"] = _CSS

    for fname in ("Image", "Question Mask", "Answer Mask", "Header", "Remarks"):
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
    attrs = (
        f'x="{box["x"]}" y="{box["y"]}" '
        f'width="{box["w"]}" height="{box["h"]}" '
        f'fill="{fill}" fill-opacity="{fill_opacity}"'
    )
    if stroke != "none":
        attrs += (f' stroke="{stroke}" stroke-opacity="{stroke_opacity}"'
                  f' stroke-width="{stroke_width}"')
    return f'<rect {attrs}/>'


def _svg(W: int, H: int, rects: list[str]) -> bytes:
    body = "\n  ".join(rects)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}">\n'
        f'  {body}\n</svg>'
    ).encode("utf-8")


def _color_str(color: tuple) -> str:
    return f"rgb({color[0]},{color[1]},{color[2]})"


# Distinct highlight color for the box currently being tested
_HIGHLIGHT_COLOR = (255, 140, 0)


def _make_masks(
    W: int, H: int,
    active: list[dict],
    all_boxes: list[dict],
    color: tuple,
    mode: str,
) -> tuple[bytes, bytes]:
    """
    Returns (q_svg_bytes, a_svg_bytes) for the given mode.

    active    – boxes being tested on this card
    all_boxes – every box on this slide
    """
    c = _color_str(color)
    h = _color_str(_HIGHLIGHT_COLOR)
    non_active = [b for b in all_boxes if b not in active]
    multiple = len(all_boxes) > 1

    if mode == "ao":
        # ── Front: non-active boxes opaque; active highlighted when multiple ─
        q_rects = [_rect(b, c, 1.0) for b in non_active]
        if multiple:
            q_rects += [_rect(b, h, 1.0) for b in active]
        else:
            q_rects += [_rect(b, c, 1.0) for b in active]

        # ── Back: non-active stay opaque; active disappears completely ───────
        a_rects = [_rect(b, c, 1.0) for b in non_active]

    else:  # oa
        # ── Front: only active opaque; others as faint outlines ──────────────
        q_rects  = [_rect(b, c, 0.15, c, 0.5, 2) for b in non_active]
        q_rects += [_rect(b, c, 1.0)              for b in active]

        # ── Back: all boxes disappear completely (no outlines) ───────────────
        a_rects = []

    return _svg(W, H, q_rects), _svg(W, H, a_rects)


# ------------------------------------------------------------------- media

def _save_media(col: Collection, data: bytes, ext: str = ".png") -> str:
    fname = f"{_MEDIA_PREFIX}{uuid.uuid4().hex}{ext}"
    col.media.write_data(fname, data)
    return fname


# ------------------------------------------------------------ note creation

def create_occlusion_notes(
    col: Collection,
    deck_id: int,
    note_type: NotetypeDict,
    pages: list[tuple[int, fitz.Pixmap, list[dict]]],
    mask_color: tuple = (46, 120, 217),
    mask_opacity: int = 200,
    lecture_name: str = "",
    total_slides: int = 0,
    mode: str = "ao",
) -> int:
    total_created = 0

    for page_idx, pix, boxes in pages:
        W, H = pix.width, pix.height

        slide_label = f"Slide {page_idx + 1}/{total_slides}" if total_slides else f"Slide {page_idx + 1}"
        header_prefix = f"{lecture_name} · {slide_label}" if lecture_name else slide_label

        img_fname = _save_media(col, pix.tobytes("png"), ".png")

        ungrouped = [b for b in boxes if b.get("group") is None]
        grouped: dict[int, list[dict]] = {}
        for b in boxes:
            gid = b.get("group")
            if gid is not None:
                grouped.setdefault(gid, []).append(b)

        # ── individual boxes ─────────────────────────────────────────────
        for box_num, box in enumerate(ungrouped):
            q_svg, a_svg = _make_masks(W, H, [box], boxes, mask_color, mode)
            q_fname = _save_media(col, q_svg, ".svg")
            a_fname = _save_media(col, a_svg, ".svg")

            note = col.new_note(note_type)
            note["Image"] = img_fname
            note["Question Mask"] = q_fname
            note["Answer Mask"] = a_fname
            note["Header"] = header_prefix
            col.add_note(note, deck_id)
            total_created += 1

        # ── groups ───────────────────────────────────────────────────────
        for gid, grp_boxes in grouped.items():
            q_svg, a_svg = _make_masks(W, H, grp_boxes, boxes, mask_color, mode)
            q_fname = _save_media(col, q_svg, ".svg")
            a_fname = _save_media(col, a_svg, ".svg")

            note = col.new_note(note_type)
            note["Image"] = img_fname
            note["Question Mask"] = q_fname
            note["Answer Mask"] = a_fname
            note["Header"] = header_prefix
            col.add_note(note, deck_id)
            total_created += 1

    return total_created
