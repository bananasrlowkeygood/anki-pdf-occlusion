import sys
import os
import platform
import re
from typing import Sequence


def _platform_tag() -> str:
    machine = platform.machine().lower()
    if sys.platform == "darwin":
        return "mac_arm64" if machine == "arm64" else "mac_x86_64"
    if sys.platform.startswith("win"):
        return "win_arm64" if machine in ("arm64", "aarch64") else "win_amd64"
    return "linux_aarch64" if machine in ("arm64", "aarch64") else "linux_x86_64"


# Vendored deps live in vendor/<platform>/ — pypdfium2 ships a separate
# binary per OS/arch, so the right one is picked at import time.
_vendor_root = os.path.join(os.path.dirname(__file__), "vendor")
for p in (os.path.join(_vendor_root, _platform_tag()), _vendor_root):
    if os.path.isdir(p) and p not in sys.path:
        sys.path.insert(0, p)

from aqt import mw, gui_hooks
from aqt.qt import QAction, QTimer
from aqt.editor import Editor


def _get_config():
    return mw.addonManager.getConfig(__name__) or {}


def open_pdf_occlusion(editor: Editor = None):
    from .pdf_occlusion_dialog import PDFOcclusionDialog
    dlg = PDFOcclusionDialog(mw, editor=editor)
    dlg.exec()


# ── Tools menu entry ──────────────────────────────────────────────────────────
action = QAction("PDF Occlusion", mw)
action.triggered.connect(lambda: open_pdf_occlusion())
mw.form.menuTools.addAction(action)


# ── Editor toolbar button ─────────────────────────────────────────────────────
def _add_editor_button(buttons: list, editor: Editor) -> None:
    if not _get_config().get("add_editor_button", True):
        return
    icon_path = os.path.join(os.path.dirname(__file__), "icon.svg")
    btn = editor.addButton(
        icon=icon_path,
        cmd="pdf_occlusion",
        func=lambda ed: open_pdf_occlusion(editor=ed),
        tip="PDF Occlusion (Ctrl+Shift+P)",
        keys="ctrl+shift+p",
    )
    buttons.append(btn)


gui_hooks.editor_did_init_buttons.append(_add_editor_button)


# ── Auto-cleanup media when our notes are deleted ─────────────────────────────
#
# Strategy:
#   1. notes_will_be_deleted fires BEFORE deletion — we read each note's fields
#      and collect the pdf_occ_* filenames it references.
#   2. We defer the actual file deletion with QTimer.singleShot so it runs
#      after Anki has committed the deletion to the DB.
#   3. For each candidate file we do a fast collection search; if no remaining
#      note references it, we delete it from disk.
#
# This handles the shared-image case correctly: if slide 5 produced 3 cards
# they all reference the same pdf_occ_xxx.png — the file is only deleted once
# the last card for that slide is gone.

_FNAME_RE = re.compile(r'pdf_occ_[0-9a-f]+\.[a-z]+')
_pending: set[str] = set()


def _on_notes_will_be_deleted(col, ids: Sequence) -> None:
    cfg = _get_config()
    note_type_name = cfg.get("note_type_name", "PDF Occlusion")
    # "PDF Image Occlusion" was the note type name before the add-on was
    # renamed — keep matching it so cleanup still works for older cards.
    nt_ids = {
        nt["id"]
        for name in {note_type_name, "PDF Image Occlusion"}
        if (nt := col.models.by_name(name))
    }
    if not nt_ids:
        return

    candidates: set[str] = set()
    for nid in ids:
        try:
            note = col.get_note(nid)
            if note.mid not in nt_ids:
                continue
            for val in note.fields:
                candidates.update(_FNAME_RE.findall(val))
        except Exception:
            pass

    if candidates:
        _pending.update(candidates)
        QTimer.singleShot(500, _do_cleanup)


def _do_cleanup() -> None:
    if not _pending or not mw.col:
        return

    media_dir = mw.col.media.dir()
    to_delete = set()

    for fname in list(_pending):
        # Search the whole collection for any note still containing this filename.
        # find_notes() does a full-text search across all fields.
        try:
            still_used = bool(mw.col.find_notes(fname))
        except Exception:
            still_used = True  # be safe — don't delete if unsure

        if not still_used:
            to_delete.add(fname)

    for fname in to_delete:
        fpath = os.path.join(media_dir, fname)
        try:
            if os.path.exists(fpath):
                os.remove(fpath)
        except Exception:
            pass

    _pending.difference_update(to_delete)


try:
    import anki.hooks as _anki_hooks
    _anki_hooks.notes_will_be_deleted.append(_on_notes_will_be_deleted)
except AttributeError:
    pass  # hook unavailable in this Anki build; media cleanup via Check Media instead
