"""
PDF page rendering backed by pypdfium2.

pypdfium2 binds to the pdfium binary via ctypes, so its wheels depend only on
the OS/architecture — not the CPython version Anki happens to ship. One
vendored copy per platform (see build.py) works on every Anki build, unlike
PyMuPDF whose compiled extension must match the exact Python ABI.

Pages are returned as QImage so the rest of the add-on never touches the
rendering backend directly.
"""
import os
import platform
import sys

from aqt.qt import QImage, QBuffer, QIODevice


def _platform_tag() -> str:
    machine = platform.machine().lower()
    if sys.platform == "darwin":
        return "mac_arm64" if machine == "arm64" else "mac_x86_64"
    if sys.platform.startswith("win"):
        return "win_arm64" if machine in ("arm64", "aarch64") else "win_amd64"
    return "linux_aarch64" if machine in ("arm64", "aarch64") else "linux_x86_64"


def ensure_vendor_on_path() -> None:
    """Put vendor/<platform>/ on sys.path. Idempotent.

    Called here (not just in __init__.py) so it also runs when the add-on was
    updated in a live Anki session: __init__ stays loaded from the old version,
    but this module is imported fresh the next time the dialog opens.
    """
    root = os.path.join(os.path.dirname(__file__), "vendor")
    for p in (os.path.join(root, _platform_tag()), root):
        if os.path.isdir(p) and p not in sys.path:
            sys.path.insert(0, p)


def _import_pdfium():
    ensure_vendor_on_path()
    try:
        import pypdfium2 as pdfium
        return pdfium
    except ImportError as exc:
        root = os.path.join(os.path.dirname(__file__), "vendor")
        bundled = sorted(os.listdir(root)) if os.path.isdir(root) else "MISSING"
        raise ImportError(
            "PDF Occlusion: the bundled pypdfium2 library could not be loaded. "
            "If you just updated the add-on, restart Anki and try again. "
            "If the error persists, please report it at "
            "https://github.com/bananasrlowkeygood/anki-pdf-occlusion/issues "
            "with your OS, Anki version, and this message.\n\n"
            f"Detected platform: {_platform_tag()} "
            f"(sys.platform={sys.platform}, machine={platform.machine()})\n"
            f"Bundled platforms: {bundled}\n"
            f"Original error: {exc}"
        ) from exc


def render_pdf(path: str, scale: float = 1.0, on_progress=None) -> list[QImage]:
    """Render every page of the PDF at `scale` and return a list of QImages.

    on_progress(done, total) is called before each page; returning False
    cancels the render and the pages rendered so far are returned.
    """
    pdfium = _import_pdfium()
    doc = pdfium.PdfDocument(path)
    images: list[QImage] = []
    try:
        n = len(doc)
        for i, page in enumerate(doc):
            if on_progress and on_progress(i, n) is False:
                page.close()
                break
            bitmap = page.render(scale=scale, rev_byteorder=True)
            buf = bytes(bitmap.buffer)
            fmt = (
                QImage.Format.Format_RGBA8888
                if bitmap.n_channels == 4
                else QImage.Format.Format_RGB888
            )
            img = QImage(buf, bitmap.width, bitmap.height, bitmap.stride, fmt)
            # copy() detaches from `buf`, which is freed when bitmap closes
            images.append(img.copy())
            bitmap.close()
            page.close()
    finally:
        doc.close()
    return images


def qimage_to_png_bytes(img: QImage) -> bytes:
    ba = QBuffer()
    ba.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(ba, "PNG")
    return bytes(ba.data())


# ------------------------------------------------------------- text detection

def get_text_word_rects(path: str, page_index: int, scale: float) -> list:
    """Detect text on a page and return one rect per word.

    Returns (x, y, w, h) tuples in rendered-image pixel space (i.e. already
    multiplied by `scale`, matching the QImages from render_pdf). Empty list
    if the page has no extractable text (e.g. scanned images).

    A word, not a line: a card that hides a whole line of a table cell asks
    you to recall the line, which is a different and much harder question
    than the one the slide is actually teaching. pdfium gives a box per
    character, so the words are the runs between the whitespace.
    """
    pdfium = _import_pdfium()
    words: list = []
    doc = pdfium.PdfDocument(path)
    try:
        page = doc[page_index]
        try:
            _, page_h = page.get_size()
            textpage = page.get_textpage()
            try:
                text = textpage.get_text_range()
                n = min(textpage.count_chars(), len(text))
                cur = None
                for i in range(n):
                    if text[i].isspace():
                        if cur:
                            words.append(cur)
                            cur = None
                        continue
                    try:
                        left, bottom, right, top = textpage.get_charbox(i)
                    except Exception:
                        continue
                    if right <= left or top <= bottom:
                        continue    # a zero-width glyph carries no box
                    if cur is None:
                        cur = [left, bottom, right, top]
                    else:
                        cur[0] = min(cur[0], left)
                        cur[1] = min(cur[1], bottom)
                        cur[2] = max(cur[2], right)
                        cur[3] = max(cur[3], top)
                if cur:
                    words.append(cur)
            finally:
                textpage.close()
        finally:
            page.close()
    finally:
        doc.close()

    out = []
    for left, bottom, right, top in words:
        w, h = right - left, top - bottom
        if w < 1.0 or h < 1.0:
            continue
        # a little air so the mask covers ascenders and descenders
        pad = h * 0.15
        out.append(((left - pad) * scale, (page_h - top - pad) * scale,
                    (w + pad * 2) * scale, (h + pad * 2) * scale))
    out.sort(key=lambda r: (round(r[1]), r[0]))
    return out


# ------------------------------------------------------------ text extraction

def get_page_text(path: str, page_index: int) -> str:
    """The page's text as it reads, for pasting into a cloze.

    pdfium hands back one long run with hard line breaks where the layout
    had them, plus the odd stray blank line; those are tidied here so the
    result drops straight into a text field. Empty string if the page has
    no extractable text (e.g. a scanned image).
    """
    pdfium = _import_pdfium()
    doc = pdfium.PdfDocument(path)
    try:
        page = doc[page_index]
        try:
            textpage = page.get_textpage()
            try:
                raw = textpage.get_text_range()
            finally:
                textpage.close()
        finally:
            page.close()
    finally:
        doc.close()
    return _tidy_page_text(raw)


def _tidy_page_text(raw: str) -> str:
    """Normalise line endings, drop blank lines, and rejoin split words."""
    lines = []
    for line in (raw or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = line.replace("\u00a0", " ").strip()
        if line:
            lines.append(line)

    out: list[str] = []
    for line in lines:
        # a word broken across a line by a soft hyphen belongs back together
        if out and out[-1].endswith("-") and not out[-1].endswith("--"):
            out[-1] = out[-1][:-1] + line
        else:
            out.append(line)
    return "\n".join(out)




# ------------------------------------------------------------ table detection
#
# Tables are found from the page's vector rules, not its text. The rules are
# split into separate tables first (two tables side by side must not become
# one grid with a phantom column down the gap between them), each table's
# grid is read off its own rules, and a cell only survives if rules actually
# bound it — which is also what lets a merged cell come out as one box
# instead of the several it would be split into.

_RULE_PT = 4.0       # a path this thin in one axis is a ruling line, not a box
_RULE_RATIO = 0.05   # ...or this slender, which catches a thick but long border
_CLUSTER_PT = 3.0    # rules within this of one another are the same gridline
_MIN_CELL_PT = 6.0   # smaller than this and it is a border artefact, not a cell
_MAX_CELL_FRAC = 0.8  # a "cell" this much of the page is the page, not a cell


def get_table_cell_rects(path: str, page_index: int, scale: float) -> list:
    """Detect ruled tables on a page and return one rect per cell.

    Same (x, y, w, h) image-pixel space as get_text_line_rects, in reading
    order. Every ruled table on the page is detected; scoping the result to
    one of them is the caller's job. Empty list if the page has no ruled
    table — a borderless one, or a picture of one, has no rules to read.
    """
    vrules, hrules, page_w, page_h = _page_rules(path, page_index)

    cells: list = []
    for vs, hs in _split_tables(vrules, hrules):
        cells.extend(_cells_from_rules(vs, hs))

    page_area = max(page_w * page_h, 1.0)
    out = []
    for left, bottom, right, top in cells:
        w, h = right - left, top - bottom
        if w < _MIN_CELL_PT or h < _MIN_CELL_PT:
            continue
        # A slide with a full-page background panel contributes that panel's
        # four sides as rules. On its own it forms a 1x1 "grid" covering the
        # whole slide, which is how detection used to hand back one box over
        # the entire page. Nothing that big is a table cell.
        if w * h >= page_area * _MAX_CELL_FRAC:
            continue
        out.append((left * scale, (page_h - top) * scale, w * scale, h * scale))
    out.sort(key=lambda r: (round(r[1]), r[0]))    # down the page, then across
    return out


def _page_rules(path: str, page_index: int):
    """Every path object on the page, reduced to vertical and horizontal rules.

    Rules keep their extent, not just their position: it is what says which
    table a rule belongs to, and which cells it actually bounds. A rect that
    is not a rule contributes its four sides, so a table drawn as one stroked
    box per cell forms a grid just like one drawn as lines.
    """
    pdfium = _import_pdfium()
    import pypdfium2.raw as pdfium_c

    doc = pdfium.PdfDocument(path)
    try:
        page = doc[page_index]
        try:
            page_w, page_h = page.get_size()
            page_area = max(page_w * page_h, 1.0)
            vrules, hrules = [], []
            for obj in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_PATH]):
                try:
                    left, bottom, right, top = obj.get_bounds()
                except Exception:
                    continue
                w, h = right - left, top - bottom
                if w <= 0 or h <= 0:
                    continue
                # the slide's own background panel is not part of any table
                if w * h >= page_area * _MAX_CELL_FRAC:
                    continue
                thin, long = min(w, h), max(w, h)
                if long <= _RULE_PT * 2:
                    continue
                if thin <= _RULE_PT or thin / long <= _RULE_RATIO:
                    if h > w:
                        vrules.append(((left + right) / 2.0, bottom, top))
                    else:
                        hrules.append(((bottom + top) / 2.0, left, right))
                else:
                    vrules.append((left, bottom, top))
                    vrules.append((right, bottom, top))
                    hrules.append((bottom, left, right))
                    hrules.append((top, left, right))
        finally:
            page.close()
    finally:
        doc.close()
    return vrules, hrules, page_w, page_h


def _split_tables(vrules: list, hrules: list) -> list:
    """Group rules into one bundle per table.

    Two rules belong together when they cross. Crossing is transitive here —
    a shared rule chains a whole grid into one bundle — so this is a
    union-find over the page's rules, and each component that has enough of
    both kinds is a table.
    """
    n = len(vrules)
    parent = list(range(n + len(hrules)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        a, b = find(i), find(j)
        if a != b:
            parent[a] = b

    tol = _CLUSTER_PT
    for i, (x, y0, y1) in enumerate(vrules):
        for j, (y, x0, x1) in enumerate(hrules):
            if (x0 - tol <= x <= x1 + tol) and (y0 - tol <= y <= y1 + tol):
                union(i, n + j)

    groups: dict = {}
    for i, r in enumerate(vrules):
        groups.setdefault(find(i), ([], []))[0].append(r)
    for j, r in enumerate(hrules):
        groups.setdefault(find(n + j), ([], []))[1].append(r)

    # 3 lines each way is the smallest thing worth calling a table: two rules
    # in one direction describe a single strip, not a grid.
    return [(vs, hs) for vs, hs in groups.values()
            if len(vs) >= 2 and len(hs) >= 2]


def _cluster(vals: list, tol: float) -> list:
    """Collapse near-identical coordinates to one value each."""
    vals = sorted(vals)
    out, run = [], [vals[0]]
    for v in vals[1:]:
        if v - run[-1] <= tol:
            run.append(v)
        else:
            out.append(sum(run) / len(run))
            run = [v]
    out.append(sum(run) / len(run))
    return out


def _cells_from_rules(vrules: list, hrules: list) -> list:
    """One table's cells, merged cells included as single boxes."""
    xs = _cluster([r[0] for r in vrules], _CLUSTER_PT)
    ys = _cluster([r[0] for r in hrules], _CLUSTER_PT)
    # A 1x1 grid is a rectangle someone drew, not a table.
    if len(xs) < 3 and len(ys) < 3:
        return []
    if len(xs) < 2 or len(ys) < 2:
        return []

    # where each gridline actually has ink, so a missing edge can be told
    # from a present one
    vspan = [_spans(vrules, x) for x in xs]
    hspan = [_spans(hrules, y) for y in ys]

    def has_v(xi, y0, y1):
        return _covers(vspan[xi], y0, y1)

    def has_h(yi, x0, x1):
        return _covers(hspan[yi], x0, x1)

    cells, taken = [], set()
    for yi in range(len(ys) - 1):
        for xi in range(len(xs) - 1):
            if (xi, yi) in taken:
                continue
            # PDF y grows upwards, so a cell is anchored at its bottom-left
            # and grows up/right. No left or bottom edge drawn means this is
            # the middle of a merged cell, already swallowed by its anchor.
            if not has_v(xi, ys[yi], ys[yi + 1]) or \
                    not has_h(yi, xs[xi], xs[xi + 1]):
                continue
            xj = xi
            while xj + 1 < len(xs) - 1 and \
                    not has_v(xj + 1, ys[yi], ys[yi + 1]):
                xj += 1
            yj = yi
            while yj + 1 < len(ys) - 1 and \
                    not has_h(yj + 1, xs[xi], xs[xj + 1]):
                yj += 1
            for a in range(xi, xj + 1):
                for b in range(yi, yj + 1):
                    taken.add((a, b))
            cells.append((xs[xi], ys[yi], xs[xj + 1], ys[yj + 1]))
    return cells


def _spans(rules: list, coord: float) -> list:
    """The merged intervals a gridline at `coord` is actually drawn over."""
    segs = sorted((lo, hi) for c, lo, hi in rules
                  if abs(c - coord) <= _CLUSTER_PT)
    if not segs:
        return []
    out = [list(segs[0])]
    for lo, hi in segs[1:]:
        if lo <= out[-1][1] + _CLUSTER_PT:
            out[-1][1] = max(out[-1][1], hi)
        else:
            out.append([lo, hi])
    return out


def _covers(spans: list, lo: float, hi: float) -> bool:
    """Is [lo, hi] drawn end to end? Short of that, the edge is not there."""
    pad = min(_CLUSTER_PT, (hi - lo) * 0.25)
    return any(a - _CLUSTER_PT <= lo + pad and b + _CLUSTER_PT >= hi - pad
               for a, b in spans)
