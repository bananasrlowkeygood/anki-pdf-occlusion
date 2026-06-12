"""
PDF page rendering backed by pypdfium2.

pypdfium2 binds to the pdfium binary via ctypes, so its wheels depend only on
the OS/architecture — not the CPython version Anki happens to ship. One
vendored copy per platform (see build.py) works on every Anki build, unlike
PyMuPDF whose compiled extension must match the exact Python ABI.

Pages are returned as QImage so the rest of the add-on never touches the
rendering backend directly.
"""
from aqt.qt import QImage, QBuffer, QIODevice


def _import_pdfium():
    try:
        import pypdfium2 as pdfium
        return pdfium
    except ImportError as exc:
        raise ImportError(
            "PDF Occlusion: the bundled pypdfium2 library could not be loaded "
            "for this platform. Please report this at "
            "https://github.com/bananasrlowkeygood/anki-pdf-occlusion/issues "
            f"with your OS and Anki version.\n\nOriginal error: {exc}"
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
