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

def get_text_line_rects(path: str, page_index: int, scale: float) -> list[tuple]:
    """Detect text on a page and return merged line-level rects.

    Returns (x, y, w, h) tuples in rendered-image pixel space (i.e. already
    multiplied by `scale`, matching the QImages from render_pdf). Empty list
    if the page has no extractable text (e.g. scanned images).
    """
    pdfium = _import_pdfium()
    raw: list[list[float]] = []
    doc = pdfium.PdfDocument(path)
    try:
        page = doc[page_index]
        try:
            _, page_h = page.get_size()
            textpage = page.get_textpage()
            try:
                n = textpage.count_rects(0, -1)
                for i in range(n):
                    left, bottom, right, top = textpage.get_rect(i)
                    # PDF coords are bottom-left origin in points; the
                    # rendered image is top-left origin in px.
                    x = left * scale
                    y = (page_h - top) * scale
                    w = (right - left) * scale
                    h = (top - bottom) * scale
                    if w >= 3 and h >= 3:
                        raw.append([x, y, w, h])
            finally:
                textpage.close()
        finally:
            page.close()
    finally:
        doc.close()
    return _merge_text_rects(raw)


def _merge_text_rects(rects: list[list[float]]) -> list[tuple]:
    """Cluster raw pdfium text rects into readable line boxes.

    Rects whose vertical centers align are treated as one line; within a
    line, segments separated by less than ~1 line-height are merged (keeps
    separate labels/columns as separate boxes). A small padding makes the
    resulting masks cover ascenders/descenders comfortably.
    """
    if not rects:
        return []

    # group into lines by vertical-center proximity
    rects = sorted(rects, key=lambda r: (r[1] + r[3] / 2, r[0]))
    lines: list[list[list[float]]] = []
    for r in rects:
        cy = r[1] + r[3] / 2
        placed = False
        for line in lines:
            ly = sum(s[1] + s[3] / 2 for s in line) / len(line)
            lh = max(s[3] for s in line)
            if abs(cy - ly) < max(lh, r[3]) * 0.5:
                line.append(r)
                placed = True
                break
        if not placed:
            lines.append([r])

    merged: list[tuple] = []
    for line in lines:
        line.sort(key=lambda r: r[0])
        cur = list(line[0])
        for seg in line[1:]:
            gap = seg[0] - (cur[0] + cur[2])
            if gap < max(cur[3], seg[3]) * 1.0:
                right = max(cur[0] + cur[2], seg[0] + seg[2])
                top = min(cur[1], seg[1])
                bottom = max(cur[1] + cur[3], seg[1] + seg[3])
                cur = [cur[0], top, right - cur[0], bottom - top]
            else:
                merged.append(tuple(cur))
                cur = list(seg)
        merged.append(tuple(cur))

    padded = []
    for x, y, w, h in merged:
        pad = h * 0.15
        padded.append((max(0.0, x - pad), max(0.0, y - pad),
                       w + pad * 2, h + pad * 2))
    return padded


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
