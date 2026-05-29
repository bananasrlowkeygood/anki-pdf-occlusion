import sys
import os
import re
from typing import Sequence

vendor_path = os.path.join(os.path.dirname(__file__), "vendor")
if vendor_path not in sys.path:
    sys.path.insert(0, vendor_path)

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
action = QAction("PDF Image Occlusion", mw)
action.triggered.connect(lambda: open_pdf_occlusion())
mw.form.menuTools.addAction(action)


# ── Editor toolbar button ─────────────────────────────────────────────────────
def _add_editor_button(buttons: list, editor: Editor) -> None:
    if not _get_config().get("add_editor_button", True):
        return
    icon_path = os.path.join(os.path.dirname(__file__), "icon.svg")
    btn = editor.addButton(
        icon=icon_path,
        cmd="pdf_image_occlusion",
        func=lambda ed: open_pdf_occlusion(editor=ed),
        tip="PDF Image Occlusion (Ctrl+Shift+P)",
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
    note_type_name = cfg.get("note_type_name", "PDF Image Occlusion")
    nt = col.models.by_name(note_type_name)
    if not nt:
        return
    nt_id = nt["id"]

    candidates: set[str] = set()
    for nid in ids:
        try:
            note = col.get_note(nid)
            if note.mid != nt_id:
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
