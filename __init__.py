import os
import re
from typing import Sequence

from .pdf_renderer import ensure_vendor_on_path

ensure_vendor_on_path()

from aqt import mw, gui_hooks
from aqt.qt import QAction, QDesktopServices, QTimer, QUrl
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
# Markers live in user_files/ (preserved across add-on updates) rather than in
# config, so config.json defaults stay live. TEMPLATE_VERSION is NOT used to
# decide whether the templates need updating — _notetype_is_current() asks the
# note type itself. It only scopes a decline to one release.

# v6: unclamp the mask overlay — AnkiMobile caps bare <img> at 95% of its
#     container, which squeezed every mask ~5% horizontally on iOS
# v5: explicit object-fit on the mask overlay (AnkiMobile alignment)
# v4: Notes / Slides buttons + the two PDF-path fields behind them
# v3: Remarks field renamed to Notes; v2: flicker-free reveal
TEMPLATE_VERSION = 6

# Bumped when already-written media files need rewriting, as opposed to the
# card templates. Unlike a template change this needs no permission and can't
# be declined — it repairs files that are simply wrong.
# v2: re-register those files through col.media — v1 wrote them directly,
#     which left Anki's media DB holding the old checksums, so they were
#     never marked dirty and never reached AnkiWeb or the phone
# v1: viewBox on mask SVGs so AnkiMobile scales them to the slide
MEDIA_VERSION = 2


def _marker_file(name: str) -> str:
    d = os.path.join(os.path.dirname(__file__), "user_files")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)


def _seen_version(marker: str) -> int:
    try:
        with open(_marker_file(marker)) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return 0


def _record_version(marker: str, value: int) -> None:
    try:
        with open(_marker_file(marker), "w") as f:
            f.write(str(value))
    except OSError:
        pass


def _notetype_is_current(nt) -> bool:
    """Does the note type already hold what ensure_note_type would write?

    Asking the note type rather than a version marker is the point. A marker
    only records that we *asked*: it gets written whether the user accepts,
    declines, or the update throws, so a fix that never landed looks applied
    and is skipped forever. Comparing the real templates means a declined or
    failed upgrade is simply noticed again next launch.
    """
    from .card_builder import DOC_FIELDS, _CSS, _FRONT_TMPL, _back_tmpl

    names = {f["name"] for f in nt["flds"]}
    if "Remarks" in names:          # pre-v3 field name, renamed on upgrade
        return False
    if nt.get("css") != _CSS:
        return False
    # Someone who declined the one-time full sync has no document fields and
    # gets templates without the buttons — for them that IS up to date, so
    # compare against the same thing ensure_note_type would write for them.
    afmt = _back_tmpl(all(f in names for f in DOC_FIELDS))
    return all(t.get("qfmt") == _FRONT_TMPL and t.get("afmt") == afmt
               for t in nt["tmpls"])


def _maybe_offer_template_upgrade() -> None:
    from aqt.utils import askUser
    from .card_builder import ensure_note_type

    if not mw.col:
        return
    cfg = _get_config()
    name = cfg.get("note_type_name", "PDF Occlusion")
    nt = (mw.col.models.by_name(name)
          or mw.col.models.by_name("PDF Image Occlusion"))
    if nt is None:
        return  # no cards yet — ensure_note_type runs when the first is made
    if _notetype_is_current(nt):
        return  # already applied, by us or by an earlier run

    # Only the decline is remembered, and only for this release: enough to
    # avoid nagging every startup, without burying the fix for good.
    if _seen_version("declined_version") >= TEMPLATE_VERSION:
        return

    if askUser(
        "PDF Occlusion's card templates were updated.\n\n"
        "This release fixes occlusion masks rendering slightly narrow on "
        "AnkiMobile, which left them shifted from the text they cover.\n\n"
        "Update your existing cards now?\n"
        "(if your cards predate the Notes/Slides buttons this also adds two "
        "fields to the note type, so Anki will ask for a one-time full sync)",
        title="PDF Occlusion",
    ):
        ensure_note_type(mw.col, name)
        mw.reset()
    else:
        _record_version("declined_version", TEMPLATE_VERSION)


def _migrate_media() -> None:
    """Repair mask files written by older versions.

    Runs before the template prompt so that if the user accepts it, the CSS
    and the files it depends on land together. Rewriting media only queues
    the files for upload — it can't push them — so the user is told to sync
    rather than left wondering why their phone hasn't changed.
    """
    from aqt.utils import tooltip
    from .card_builder import repair_mask_media

    seen = _seen_version("media_version")
    if not mw.col or seen >= MEDIA_VERSION:
        return
    # Progress is best-effort — a failure to show it must not stop the repair.
    try:
        mw.progress.start(label="PDF Occlusion: repairing masks…")
    except Exception:
        pass
    try:
        # A collection already through v1 has correct files on disk but stale
        # media-DB entries, so those need rewriting too.
        res = repair_mask_media(mw.col, reregister=seen >= 1)
    except Exception:
        return  # leave the marker unset so the next launch retries
    finally:
        try:
            mw.progress.finish()
        except Exception:
            pass
    _record_version("media_version", MEDIA_VERSION)

    if res["registered"]:
        msg = (f"PDF Occlusion repaired {res['registered']} mask files — "
               f"sync to update your other devices.")
        if res["failed"]:
            msg += f" ({res['failed']} could not be rewritten.)"
        tooltip(msg, period=6000)


def _on_profile_did_open() -> None:
    _migrate_media()
    _maybe_offer_template_upgrade()


gui_hooks.profile_did_open.append(_on_profile_did_open)


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


# ── Notes / Slides buttons on the card ────────────────────────────────────────
#
# The card templates only send back which document was asked for; the path
# itself lives in the note's "Slides PDF" / "Notes PDF" field and is read here,
# so nothing the webview says decides what gets opened. Works in the reviewer,
# the browser preview and the card-layout preview — each supplies a different
# bridge context, hence the three ways of reaching the note below.

def _note_from_context(context):
    card = getattr(context, "card", None)
    if callable(card):          # Previewer.card() is a method
        try:
            card = card()
        except Exception:
            card = None
    if card is None and mw.reviewer is not None:
        card = mw.reviewer.card
    if card is not None:
        try:
            return card.note()
        except Exception:
            return None
    return getattr(context, "note", None)  # card layout


def _open_linked_pdf(kind: str, context) -> None:
    from aqt.utils import showWarning
    from .card_builder import DOC_FIELD_BY_KIND, pdf_path_in

    field = DOC_FIELD_BY_KIND.get(kind)
    note = _note_from_context(context)
    if not field or note is None:
        return
    label = "lecture notes" if kind == "notes" else "slides"
    path = pdf_path_in(note[field]) if field in note else ""
    if not path:
        showWarning(
            f"No {label} PDF is attached to this card.\n\n"
            "Open the PDF Occlusion window, load the deck this card came "
            "from, attach the PDF and click Create All Cards."
        )
        return
    if os.path.splitext(path)[1].lower() != ".pdf":
        showWarning(f"This card's {label} link is not a PDF:\n{path}")
        return
    if not os.path.exists(path):
        showWarning(
            f"The {label} PDF can no longer be found:\n{path}\n\n"
            "It was moved or renamed. Re-attach it in the PDF Occlusion "
            "window and click Create All Cards to update these cards."
        )
        return
    QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def _on_js_message(handled, message: str, context):
    from .card_builder import JS_PREFIX

    if not isinstance(message, str) or not message.startswith(JS_PREFIX):
        return handled
    _open_linked_pdf(message[len(JS_PREFIX):], context)
    return (True, None)


gui_hooks.webview_did_receive_js_message.append(_on_js_message)


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
