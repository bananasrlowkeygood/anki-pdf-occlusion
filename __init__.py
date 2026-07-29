import os
import re
from typing import Sequence

from .pdf_renderer import ensure_vendor_on_path

ensure_vendor_on_path()

from aqt import mw, gui_hooks
from aqt.qt import QAction, QTimer
from aqt.editor import Editor


def _get_config():
    return mw.addonManager.getConfig(__name__) or {}


def open_pdf_occlusion(editor: Editor = None):
    from .pdf_occlusion_dialog import PDFOcclusionDialog
    # From the Browse/Add editor: if the current note is one of ours, open
    # its session directly and focus the slide + box behind that card.
    note_id = None
    if editor is not None:
        note = getattr(editor, "note", None)
        if note is not None and getattr(note, "id", 0):
            note_id = note.id
    dlg = PDFOcclusionDialog(mw, editor=editor, note_id=note_id)
    dlg.exec()


# ── Tools menu entry ──────────────────────────────────────────────────────────
action = QAction("PDF Occlusion", mw)
action.triggered.connect(lambda: open_pdf_occlusion())
mw.form.menuTools.addAction(action)


# ── One-time upgrade prompt after add-on updates ──────────────────────────────
#
# Card templates/CSS are stored in the user's collection, not in the add-on,
# so updating the add-on does NOT touch existing cards. Bump TEMPLATE_VERSION
# whenever the templates/CSS change in a way existing users should receive;
# on the next profile load they get a one-time offer to apply it. Declining
# is fine too — templates are refreshed anyway the next time cards are
# created (ensure_note_type runs then).
# The seen-version marker lives in user_files/ (preserved across add-on
# updates) rather than in config, so config.json defaults stay live.

TEMPLATE_VERSION = 3  # v3: Remarks field renamed to Notes; v2: flicker-free reveal


def _template_version_file() -> str:
    d = os.path.join(os.path.dirname(__file__), "user_files")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "template_version")


def _seen_template_version() -> int:
    try:
        with open(_template_version_file()) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return 0


def _record_template_version() -> None:
    try:
        with open(_template_version_file(), "w") as f:
            f.write(str(TEMPLATE_VERSION))
    except OSError:
        pass


def _maybe_offer_template_upgrade() -> None:
    from aqt.utils import askUser
    from .card_builder import ensure_note_type

    if _seen_template_version() >= TEMPLATE_VERSION:
        return

    cfg = _get_config()
    name = cfg.get("note_type_name", "PDF Occlusion")
    has_notes = bool(
        mw.col
        and (mw.col.models.by_name(name) or mw.col.models.by_name("PDF Image Occlusion"))
    )
    if has_notes and askUser(
        "PDF Occlusion's card templates were updated.\n"
        "Update your existing cards now?",
        title="PDF Occlusion",
    ):
        ensure_note_type(mw.col, name)
        mw.reset()
    # Record either way — never nag on every startup.
    _record_template_version()


gui_hooks.profile_did_open.append(_maybe_offer_template_upgrade)


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
