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


def render_pdf(path: str, scale: float = 1.0) -> list[QImage]:
    """Render every page of the PDF at `scale` and return a list of QImages."""
    pdfium = _import_pdfium()
    doc = pdfium.PdfDocument(path)
    images: list[QImage] = []
    try:
        for page in doc:
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
