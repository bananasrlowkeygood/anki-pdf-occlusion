import os
import uuid
from typing import Optional

from aqt import mw
from aqt.theme import theme_manager
from aqt.qt import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QToolButton, QLabel,
    QLineEdit, QComboBox, QFileDialog, QScrollArea, QShortcut, QKeySequence,
    Qt, QImage, QProgressDialog, QMenu, QDesktopServices, QUrl, QTimer,
)
from aqt.utils import askUser, showInfo, showWarning

from . import session_store
from .cloze_dialog import ClozeColumn, ClozeComposer, fly_to_chip
from .occlusion_canvas import OcclusionCanvas
from .card_builder import (ensure_note_type, create_occlusion_notes,
                           create_cloze_notes, cloze_note_type,
                           cloze_card_count)
from .pdf_renderer import (render_pdf, get_text_line_rects, get_page_text,
                           get_table_cell_rects)


_ZOOM_STEPS = [0.25, 0.33, 0.5, 0.67, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0]

# purple
_NU_PURPLE = "#4E2A84"

def _accent_color() -> str:
    """Purple accent readable on the current Anki theme."""
    try:
        night = theme_manager.night_mode
    except Exception:
        night = False
    return "#B6ACD1" if night else _NU_PURPLE


# One purple for the two primary actions (Open PDF / Create All Cards);
# everything else stays quiet so the dialog reads light and uncluttered.
_PRIMARY_BTN_QSS = (
    "QPushButton{background:#836EAA;color:white;font-weight:bold;"
    "padding:6px 18px;border-radius:6px;border:none;}"
    "QPushButton:hover{background:#75619b;}"
    "QPushButton:pressed{background:#67548c;}"
    "QPushButton:disabled{background:#b5b2ba;color:#efefef;}"
)

# Split button: main area opens the file picker, the arrow lists recent
# sessions. Same purple as the primary buttons. Qt's stock menu arrow is
# huge, so a small bundled caret.svg replaces it.
def _asset(name: str) -> str:
    return os.path.join(os.path.dirname(__file__), name).replace("\\", "/")


_CARET_PATH = _asset("caret.svg")
_OPEN_BTN_QSS = (
    "QToolButton{background:#836EAA;color:white;font-weight:bold;"
    "padding:6px 18px 6px 16px;border-radius:6px;border:none;}"
    "QToolButton:hover{background:#75619b;}"
    "QToolButton:pressed{background:#67548c;}"
    "QToolButton::menu-button{border:none;width:14px;"
    "border-top-right-radius:6px;border-bottom-right-radius:6px;}"
    "QToolButton::menu-button:hover{background:#67548c;}"
    f'QToolButton::menu-arrow{{image:url("{_CARET_PATH}");'
    "width:8px;height:5px;}"
)

# Quiet secondary buttons: hairline border, transparent fill, purple tint on
# hover. rgba keeps it legible on both light and night themes.
_DIALOG_QSS = (
    "QPushButton{padding:5px 14px;border-radius:6px;"
    "border:1px solid rgba(127,127,127,0.35);background:transparent;}"
    "QPushButton:hover{background:rgba(131,110,170,0.14);}"
    "QPushButton:pressed{background:rgba(131,110,170,0.26);}"
    "QPushButton:disabled{color:rgba(127,127,127,0.45);"
    "border-color:rgba(127,127,127,0.18);}"
    "QPushButton:checked{background:rgba(131,110,170,0.28);"
    "border-color:#836EAA;}"
)

# The notes-PDF attach control: a quiet split button matching the secondary
# QPushButtons above (QSS selectors are per-class, so it needs its own copy).
# Its caret is grey rather than the white one on the purple Open PDF button.
_NOTES_BTN_QSS = (
    "QToolButton{padding:5px 8px 5px 14px;border-radius:6px;"
    "border:1px solid rgba(127,127,127,0.35);background:transparent;"
    "text-align:left;}"
    "QToolButton:hover{background:rgba(131,110,170,0.14);}"
    "QToolButton:pressed{background:rgba(131,110,170,0.26);}"
    "QToolButton:disabled{color:rgba(127,127,127,0.45);"
    "border-color:rgba(127,127,127,0.18);}"
    "QToolButton::menu-button{border:none;width:14px;"
    "border-top-right-radius:6px;border-bottom-right-radius:6px;}"
    "QToolButton::menu-button:hover{background:rgba(131,110,170,0.26);}"
    f'QToolButton::menu-arrow{{image:url("{_asset("caret_muted.svg")}");'
    "width:8px;height:5px;}"
)


def _centre_inside(rect: tuple, region: tuple) -> bool:
    """Is a detected rect's centre within the scan region?

    Centres rather than full containment: a table cell whose border sits a
    pixel outside the rectangle you drew round it still belongs to it.
    """
    x, y, w, h = rect
    rx, ry, rw, rh = region
    cx, cy = x + w / 2.0, y + h / 2.0
    return rx <= cx <= rx + rw and ry <= cy <= ry + rh


def _cfg(key, default):
    cfg = mw.addonManager.getConfig(__name__) or {}
    return cfg.get(key, default)


class PDFOcclusionDialog(QDialog):
    def __init__(self, parent=None, editor=None, note_id=None):
        super().__init__(parent)
        self._editor = editor
        self.setWindowTitle("PDF Occlusion")
        self.resize(1100, 860)
        self.setStyleSheet(_DIALOG_QSS)

        # Documents: one entry per opened PDF. Pages are flattened into
        # self._pages; each doc records its slice via start/count.
        # {"path", "lecture", "start", "count", "note_map", "image_map"}
        self._docs: list[dict] = []
        self._pages: list[QImage] = []
        self._render_scale: float = 1.0
        self._page_index: int = 0
        self._boxes: dict[int, list[dict]] = {}
        self._page_modes: dict[int, str] = {}   # global page idx -> "ao"/"oa"
        # Cloze: the composer is a panel to the right of the slide, the
        # cards written from it are chips to the left, per slide. Like the
        # boxes, they are only records until Create All Cards runs —
        # _cloze_stale holds the notes of records deleted since.
        self._cloze_cards: dict[int, list] = {}
        self._cloze_stale: list[int] = []
        self._fly = None            # in-flight card animation, kept alive
        self._fitted = False        # is the current zoom a fit? (see _refit)

        self._build_ui()

        self._canvas.set_mask_color(tuple(_cfg("mask_color", [120, 120, 120])))
        self._apply_default_zoom()

        # Opened from a PDF Occlusion card in Browse? Jump straight to the
        # slide and box that made it.
        if note_id:
            self._open_note(note_id)

    def _open_note(self, nid: int):
        found = session_store.find_note(nid)
        if not found:
            return  # card predates sessions or its session was discarded
        path, region_key = found
        if not os.path.exists(path):
            showWarning(f"The PDF for this card can no longer be found:\n{path}")
            return
        self._open_paths([path], silent_resume=True)
        self._focus_region(region_key)

    def _focus_region(self, key: str):
        if not self._docs:
            return
        try:
            page_s, kind, ident = key.split(":", 2)
            local = int(page_s)
        except ValueError:
            return
        doc = self._docs[0]
        if not 0 <= local < doc["count"]:
            return
        self._save_current_boxes()
        self._page_index = doc["start"] + local
        self._show_page()
        if kind == "c":
            for card in self._cloze_cards.get(self._page_index, []):
                if card.get("uid") == ident:
                    self._on_cloze_activated(card)
                    return
        elif kind == "b":
            self._canvas.select_region(box_id=ident)
        else:
            self._canvas.select_region(group_uid=ident)

    # ------------------------------------------------------------------ UI --

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # ── Row 1: open / recent · slide counter · zoom ───────────────────
        row1 = QHBoxLayout()

        self._open_btn = QToolButton()
        self._open_btn.setText("Open PDF")
        self._open_btn.setStyleSheet(_OPEN_BTN_QSS)
        self._open_btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self._open_btn.clicked.connect(self._open_pdf)
        self._recent_menu = QMenu(self)
        self._recent_menu.aboutToShow.connect(self._fill_recent_menu)
        self._open_btn.setMenu(self._recent_menu)

        self._page_label = QLabel("No PDF loaded")
        self._page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._page_label.setStyleSheet("font-weight:bold;")

        self._zoom_out_btn = QPushButton("−")
        self._zoom_out_btn.setFixedWidth(30)
        self._zoom_out_btn.clicked.connect(self._zoom_out)
        self._zoom_label = QLabel("100%")
        self._zoom_label.setFixedWidth(44)
        self._zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._zoom_in_btn = QPushButton("+")
        self._zoom_in_btn.setFixedWidth(30)
        self._zoom_in_btn.clicked.connect(self._zoom_in)
        self._fit_btn = QPushButton("Fit")
        self._fit_btn.setFixedWidth(44)
        self._fit_btn.setToolTip("Fit slide (Ctrl+0)")
        self._fit_btn.clicked.connect(self._zoom_fit)

        row1.addWidget(self._open_btn)
        row1.addStretch()
        row1.addWidget(self._page_label)
        row1.addStretch()
        row1.addWidget(self._zoom_out_btn)
        row1.addWidget(self._zoom_label)
        row1.addWidget(self._zoom_in_btn)
        row1.addWidget(self._fit_btn)
        root.addLayout(row1)

        # ── Row 2: lecture · deck · tags · default mode ───────────────────
        row2 = QHBoxLayout()

        self._lecture_edit = QLineEdit()
        self._lecture_edit.setPlaceholderText("Lecture")
        self._lecture_edit.setToolTip("Card header text · auto-filled from the filename")
        self._lecture_edit.setMinimumWidth(180)
        self._lecture_edit.textEdited.connect(self._on_lecture_edited)

        self._deck_combo = QComboBox()
        self._deck_combo.setEditable(True)
        self._deck_combo.setMinimumWidth(160)
        self._deck_combo.setToolTip("Type a new name to create a deck")
        self._populate_decks()

        self._notes_pdf_btn = QToolButton()
        self._notes_pdf_btn.setStyleSheet(_NOTES_BTN_QSS)
        self._notes_pdf_btn.setPopupMode(
            QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self._notes_pdf_btn.setMinimumWidth(150)
        self._notes_pdf_btn.clicked.connect(self._choose_notes_pdf)
        self._notes_pdf_menu = QMenu(self)
        self._notes_pdf_menu.aboutToShow.connect(self._fill_notes_pdf_menu)
        self._notes_pdf_btn.setMenu(self._notes_pdf_menu)

        row2.addWidget(self._lecture_edit, stretch=3)
        row2.addWidget(self._deck_combo, stretch=2)
        row2.addWidget(self._notes_pdf_btn, stretch=2)
        root.addLayout(row2)

        # ── Row 3: tools · detect · per-slide mode ────────────────────────
        row3 = QHBoxLayout()

        self._draw_btn = QPushButton("Draw")
        self._draw_btn.setCheckable(True)
        self._draw_btn.setChecked(True)
        self._draw_btn.setToolTip("Draw boxes (D)")
        self._draw_btn.clicked.connect(lambda: self._set_tool("draw"))

        self._select_btn = QPushButton("Select")
        self._select_btn.setCheckable(True)
        self._select_btn.setToolTip("Drag to select boxes (V)")
        self._select_btn.clicked.connect(lambda: self._set_tool("select"))

        self._detect_btn = QPushButton("Detect")
        self._detect_btn.setCheckable(True)
        self._detect_btn.setToolTip(
            "Auto-box part of this slide (T)\n\n"
            "Click, then drag out the part to scan. A ruled table there is "
            "boxed cell by cell, anything else line by line.\n"
            "Esc cancels.")
        self._detect_btn.clicked.connect(self._arm_detect)

        self._cloze_btn = QPushButton("Cloze")
        self._cloze_btn.setToolTip(
            "Make a cloze card from this slide instead of occluding it "
            "(Ctrl+Shift+V)"
        )
        self._cloze_btn.clicked.connect(self._open_cloze)

        slide_mode_label = QLabel("This slide:")
        slide_mode_label.setStyleSheet("color:rgba(127,127,127,0.9);")
        self._page_mode_combo = QComboBox()
        self._page_mode_combo.addItem("Hide All, Show One", "ao")
        self._page_mode_combo.addItem("Hide One, Show One", "oa")
        self._page_mode_combo.setToolTip("Occlusion mode for this slide")
        self._page_mode_combo.currentIndexChanged.connect(self._on_page_mode_changed)

        row3.addWidget(self._draw_btn)
        row3.addWidget(self._select_btn)
        row3.addSpacing(10)
        row3.addWidget(self._detect_btn)
        row3.addWidget(self._cloze_btn)
        row3.addSpacing(10)
        self._detect_hint = QLabel("")
        self._detect_hint.setStyleSheet(
            f"color:{_accent_color()}; font-size:11px; font-weight:bold;")
        row3.addWidget(self._detect_hint)
        row3.addStretch()
        row3.addWidget(slide_mode_label)
        row3.addWidget(self._page_mode_combo)
        root.addLayout(row3)

        # ── Canvas ────────────────────────────────────────────────────────
        self._canvas = OcclusionCanvas()
        self._canvas.boxes_changed.connect(self._on_boxes_changed)
        self._canvas.slide_nav.connect(self._on_slide_nav)
        self._canvas.zoom_gesture.connect(self._on_zoom_gesture)
        self._canvas.scan_region.connect(self._on_scan_region)
        self._canvas.scan_cancelled.connect(self._on_scan_cancelled)
        self._scroll = QScrollArea()
        self._scroll.setWidget(self._canvas)
        self._scroll.setWidgetResizable(False)
        self._scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Cloze lives in the window, not over it: cards already made on the
        # left, the composer on the right, the slide keeping the middle.
        # Both start hidden and cost nothing until used.
        self._cloze_column = ClozeColumn()
        self._cloze_column.setVisible(False)
        self._cloze = ClozeComposer(self)
        # The panel is capped at half the slide's height and pinned to the
        # top of its side, so the Add button can't end up next to Create All
        # Cards — two buttons that do very different things.
        self._cloze_side = QWidget()
        side = QVBoxLayout(self._cloze_side)
        side.setContentsMargins(0, 0, 0, 0)
        side.addWidget(self._cloze)
        side.addStretch()
        self._cloze_side.setVisible(False)
        self._cloze.card_created.connect(self._on_cloze_created)
        self._cloze.card_updated.connect(self._on_cloze_updated)
        self._cloze_column.card_activated.connect(self._on_cloze_activated)
        self._cloze_column.card_delete_requested.connect(self._on_cloze_delete)

        mid = QHBoxLayout()
        mid.setContentsMargins(0, 0, 0, 0)
        mid.setSpacing(8)
        mid.addWidget(self._cloze_column)
        mid.addWidget(self._scroll, stretch=1)
        mid.addWidget(self._cloze_side)
        root.addLayout(mid, stretch=1)

        # ── Bottom nav / create ───────────────────────────────────────────
        bot = QHBoxLayout()

        self._prev_btn = QPushButton("← Prev")
        self._prev_btn.setToolTip("Previous slide (PgUp)")
        self._prev_btn.clicked.connect(self._prev_page)
        self._next_btn = QPushButton("Next →")
        self._next_btn.setToolTip("Next slide (PgDn)")
        self._next_btn.clicked.connect(self._next_page)

        self._count_label = QLabel("")
        self._count_label.setStyleSheet(
            f"color:{_accent_color()}; font-size:11px; font-weight:bold;"
        )

        self._create_btn = QPushButton("Create All Cards")
        self._create_btn.setDefault(True)
        self._create_btn.clicked.connect(self._create_cards)
        self._create_btn.setStyleSheet(_PRIMARY_BTN_QSS)

        for w in (self._prev_btn, self._next_btn):
            bot.addWidget(w)
        bot.addStretch()
        bot.addWidget(self._count_label)
        bot.addSpacing(8)
        bot.addWidget(self._create_btn)
        root.addLayout(bot)

        # ── Keep keyboard focus on the canvas ─────────────────────────────
        # The canvas only receives key events while it holds focus, and it is
        # given focus as soon as a PDF loads. But a QPushButton takes focus
        # when clicked, so hitting Detect Text (or Draw, or Fit, or Next) used
        # to leave focus on the button and silently kill every canvas key —
        # arrows, Delete, G/U/N, Esc — until you clicked the slide again.
        # These are tool-palette buttons, so they don't need focus at all;
        # every one of them also has a keyboard shortcut (D, V, T, Ctrl+0,
        # Ctrl+±, PgUp/PgDn, Space), so dropping them from the Tab chain
        # doesn't put anything out of reach of the keyboard.
        #
        # Deliberately NOT in this list: the lecture field, the two combos and
        # Open PDF / notes-PDF buttons, which do need focus to be usable — the
        # first three because you type into them, the last two because they
        # have no shortcut and would otherwise be mouse-only.
        for w in (self._draw_btn, self._select_btn, self._detect_btn,
                  self._cloze_btn,
                  self._zoom_out_btn, self._zoom_in_btn, self._fit_btn,
                  self._prev_btn, self._next_btn):
            w.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # Clicking the grey margin around the slide targets the scroll area,
        # not the canvas; without this it would take focus and cause the same
        # dead-keys problem.
        self._scroll.setFocusProxy(self._canvas)

        # ── Shortcuts ─────────────────────────────────────────────────────
        # Left/Right nudge the selection and otherwise flip slides. They're
        # bound here as well as on the canvas because the canvas only sees key
        # events while it holds focus — and flipping slides is exactly what you
        # do before ever clicking on one. PgUp/PgDn always flip slides.
        # None of these fire while a text field has focus: QLineEdit claims the
        # arrows (and Ctrl+Z, and plain letters) for editing before Qt looks at
        # window shortcuts, so typing a lecture name is unaffected.
        for keys, key in (("Left", Qt.Key.Key_Left), ("Right", Qt.Key.Key_Right),
                          ("Shift+Left", Qt.Key.Key_Left),
                          ("Shift+Right", Qt.Key.Key_Right)):
            QShortcut(QKeySequence(keys), self,
                      lambda k=key, s=keys.startswith("Shift"): self._arrow(k, s))
        QShortcut(QKeySequence(Qt.Key.Key_PageUp), self, self._prev_page)
        QShortcut(QKeySequence(Qt.Key.Key_PageDown), self, self._next_page)
        QShortcut(QKeySequence("D"), self, lambda: self._set_tool("draw"))
        QShortcut(QKeySequence("V"), self, lambda: self._set_tool("select"))
        QShortcut(QKeySequence("T"), self, self._arm_detect)
        # V for Vasu, who asked for the cloze composer. Plain V is already
        # the Select tool, so it takes the modifiers.
        QShortcut(QKeySequence("Ctrl+Shift+V"), self, self._open_cloze)
        QShortcut(QKeySequence("Ctrl+="), self, self._zoom_in)
        QShortcut(QKeySequence("Ctrl++"), self, self._zoom_in)
        QShortcut(QKeySequence("Ctrl+-"), self, self._zoom_out)
        QShortcut(QKeySequence("Ctrl+0"), self, self._zoom_fit)
        # Window-level so they work when a button has focus. QLineEdit
        # overrides these while it has focus, so text editing is unaffected.
        QShortcut(QKeySequence("Ctrl+Z"), self, self._canvas.undo)
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self, self._canvas.redo)
        QShortcut(QKeySequence("Ctrl+Y"), self, self._canvas.redo)
        QShortcut(QKeySequence("Ctrl+C"), self, self._canvas.copy_selected)
        QShortcut(QKeySequence("Ctrl+V"), self, self._canvas.paste)

        self._update_controls()

    def _populate_decks(self):
        current = mw.col.decks.name(mw.col.decks.selected())
        default = _cfg("default_deck", "") or current
        names = sorted(
            d.name for d in mw.col.decks.all_names_and_ids(include_filtered=False)
        )
        self._deck_combo.addItems(names)
        idx = self._deck_combo.findText(default)
        if idx >= 0:
            self._deck_combo.setCurrentIndex(idx)
        else:
            self._deck_combo.setEditText(default)

    def _set_tool(self, tool: str):
        self._canvas.set_tool(tool)
        self._draw_btn.setChecked(tool == "draw")
        self._select_btn.setChecked(tool == "select")

    def _default_mode(self) -> str:
        """PDF-wide occlusion mode — the config value; slides override it."""
        mode = _cfg("occlusion_mode", "ao")
        return mode if mode in ("ao", "oa") else "ao"

    # ---------------------------------------------------------------- docs --

    def _doc_for_page(self, idx: int) -> Optional[dict]:
        for doc in self._docs:
            if doc["start"] <= idx < doc["start"] + doc["count"]:
                return doc
        return None

    def _on_lecture_edited(self, text: str):
        doc = self._doc_for_page(self._page_index)
        if doc:
            doc["lecture"] = text.strip()

    # ---------------------------------------------------------- notes PDF --
    #
    # A separate lecture-notes PDF, attached per document. Its path (and the
    # slide deck's) is written onto every card this document makes, so the
    # Notes / Slides buttons on the back of the card can open them. Only the
    # path is stored — the PDF is never copied into the collection.

    def _notes_pdf(self) -> str:
        doc = self._doc_for_page(self._page_index)
        return doc.get("notes_pdf", "") if doc else ""

    def _choose_notes_pdf(self):
        doc = self._doc_for_page(self._page_index)
        if not doc:
            return
        start_dir = os.path.dirname(doc.get("notes_pdf") or doc["path"])
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose lecture notes PDF", start_dir, "PDF Files (*.pdf)"
        )
        if path:
            doc["notes_pdf"] = path
            self._sync_notes_pdf_btn()

    def _remove_notes_pdf(self):
        doc = self._doc_for_page(self._page_index)
        if doc:
            doc["notes_pdf"] = ""
            self._sync_notes_pdf_btn()

    def _fill_notes_pdf_menu(self):
        self._notes_pdf_menu.clear()
        path = self._notes_pdf()
        self._notes_pdf_menu.addAction(
            "Choose PDF…" if not path else "Replace", self._choose_notes_pdf)
        act = self._notes_pdf_menu.addAction(
            "Open", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(path)))
        act.setEnabled(bool(path) and os.path.exists(path))
        act = self._notes_pdf_menu.addAction("Remove", self._remove_notes_pdf)
        act.setEnabled(bool(path))

    def _sync_notes_pdf_btn(self):
        """Reflect the current document's attachment on the button."""
        self._notes_pdf_btn.setEnabled(bool(self._docs))
        path = self._notes_pdf()
        if not path:
            self._notes_pdf_btn.setText("Notes PDF")
            self._notes_pdf_btn.setToolTip(
                "Attach a lecture-notes PDF · the cards get a Notes button "
                "that opens it"
            )
            return
        name = os.path.basename(path)
        if len(name) > 22:
            name = name[:21] + "…"
        missing = not os.path.exists(path)
        self._notes_pdf_btn.setText(("⚠ " if missing else "") + name)
        self._notes_pdf_btn.setToolTip(
            path + ("\n\n(this file can no longer be found)" if missing else "")
        )

    # ---------------------------------------------------------------- PDF --

    def _open_pdf(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Open PDF", "", "PDF Files (*.pdf)"
        )
        self._open_paths(paths)

    def _fill_recent_menu(self):
        self._recent_menu.clear()
        sessions = session_store.list_sessions(8)
        if not sessions:
            act = self._recent_menu.addAction("No recent sessions")
            act.setEnabled(False)
            return
        for s in sessions:
            path = s["pdf_path"]
            name = s.get("lecture") or os.path.splitext(os.path.basename(path))[0]
            n_boxes = sum(len(v) for v in s.get("boxes", {}).values())
            label = f"{name}  ·  {n_boxes} box{'es' if n_boxes != 1 else ''}"
            self._recent_menu.addAction(
                label, lambda p=path: self._open_recent(p))

    def _open_recent(self, path: str):
        if not os.path.exists(path):
            if askUser(
                f"The PDF can no longer be found:\n{path}\n\n"
                "Forget this session?"
            ):
                session_store.delete(path)
            return
        self._open_paths([path])

    def _render_paths(self, paths: list[str]) -> Optional[list[tuple[str, list[QImage]]]]:
        """Render every PDF, one shared progress dialog. None if cancelled."""
        progress = QProgressDialog("Rendering PDF…", "Cancel", 0, 100, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(300)

        rendered: list[tuple[str, list[QImage]]] = []
        for path in paths:
            progress.setLabelText(f"Rendering {os.path.basename(path)}…")

            def on_progress(done: int, total: int):
                progress.setMaximum(total)
                progress.setValue(done)
                mw.app.processEvents()
                return not progress.wasCanceled()

            try:
                pages = render_pdf(path, scale=self._render_scale,
                                   on_progress=on_progress)
            except Exception as exc:
                progress.close()
                showWarning(f"Could not open this PDF:\n{path}\n\n{exc}")
                return None
            if progress.wasCanceled():
                progress.close()
                return None
            if pages:
                rendered.append((path, pages))
        # read the cancel state BEFORE close() — closing a QProgressDialog
        # emits canceled(), which flips wasCanceled() to True
        canceled = progress.wasCanceled()
        progress.close()
        if canceled or not rendered:
            return None
        return rendered

    def _open_paths(self, paths: list[str], silent_resume: bool = False):
        paths = [p for p in paths if p]
        if not paths:
            return

        # keep whatever was in progress before switching documents
        self._persist_sessions()

        self._render_scale = float(_cfg("render_dpi_scale", 2.0))
        rendered = self._render_paths(paths)
        if rendered is None:
            return

        docs, all_pages = [], []
        for path, pages in rendered:
            docs.append({
                "path": path,
                "lecture": os.path.splitext(os.path.basename(path))[0],
                "start": len(all_pages),
                "count": len(pages),
                "notes_pdf": "",
                "note_map": {},
                "image_map": {},
            })
            all_pages.extend(pages)

        self._docs = docs
        self._pages = all_pages
        self._page_index = 0
        self._boxes.clear()
        self._page_modes.clear()

        self._maybe_resume_sessions(silent=silent_resume)

        self._show_page()
        self._apply_default_zoom()
        # so the arrows, Delete, G/U etc. reach the canvas without a click first
        self._canvas.setFocus()

    # ------------------------------------------------------------- sessions --

    def _maybe_resume_sessions(self, silent: bool = False):
        sessions = {i: session_store.load(doc["path"])
                    for i, doc in enumerate(self._docs)}
        resumable = {
            i: s for i, s in sessions.items()
            if s and (s.get("boxes") or s.get("note_map")
                      or s.get("notes_pdf") or s.get("cloze_cards"))
        }
        if not resumable:
            return

        if not silent:
            names = ", ".join(
                os.path.basename(self._docs[i]["path"]) for i in resumable)
            if not askUser(
                f"Resume your saved session for {names}?",
                title="PDF Occlusion",
            ):
                for i in resumable:
                    session_store.delete(self._docs[i]["path"])
                return

        applied_settings = False
        for i, s in resumable.items():
            doc = self._docs[i]
            saved_scale = float(s.get("render_scale") or self._render_scale)
            factor = self._render_scale / saved_scale if saved_scale else 1.0

            for k, blist in (s.get("boxes") or {}).items():
                try:
                    local = int(k)
                except ValueError:
                    continue
                if not 0 <= local < doc["count"]:
                    continue
                if factor != 1.0:
                    for b in blist:
                        b["x"] = int(b["x"] * factor)
                        b["y"] = int(b["y"] * factor)
                        b["w"] = int(b["w"] * factor)
                        b["h"] = int(b["h"] * factor)
                self._boxes[doc["start"] + local] = blist

            self._cloze_stale += [int(n) for n in (s.get("cloze_stale") or [])]

            for k, cards in (s.get("cloze_cards") or {}).items():
                try:
                    local = int(k)
                except ValueError:
                    continue
                if cards and 0 <= local < doc["count"]:
                    page = doc["start"] + local
                    restored = []
                    for card in cards:
                        entry = dict(card)
                        # Sessions written before records had their own id
                        # get one now — without it every such record keys on
                        # None, and they collide with each other.
                        entry.setdefault("uid", uuid.uuid4().hex)
                        entry.setdefault("nid", None)
                        entry["page"] = page
                        restored.append(entry)
                    self._cloze_cards[page] = restored

            for k, m in (s.get("page_modes") or {}).items():
                try:
                    local = int(k)
                except ValueError:
                    continue
                if m in ("ao", "oa") and 0 <= local < doc["count"]:
                    self._page_modes[doc["start"] + local] = m

            doc["lecture"] = s.get("lecture") or doc["lecture"]
            doc["notes_pdf"] = s.get("notes_pdf") or ""
            doc["note_map"] = s.get("note_map") or {}
            # the slide PNGs only match if the render scale is unchanged
            doc["image_map"] = (s.get("image_map") or {}) if factor == 1.0 else {}

            if not applied_settings:
                applied_settings = True
                if s.get("deck"):
                    self._deck_combo.setEditText(s["deck"])

    def _persist_sessions(self):
        if not self._docs or not self._pages:
            return
        self._save_current_boxes()
        for doc in self._docs:
            start, count = doc["start"], doc["count"]
            boxes = {
                str(i - start): self._boxes[i]
                for i in range(start, start + count)
                if self._boxes.get(i)
            }
            cloze_cards = {
                str(i - start): self._cloze_cards[i]
                for i in range(start, start + count)
                if self._cloze_cards.get(i)
            }
            if (not boxes and not cloze_cards and not self._cloze_stale
                    and not doc.get("note_map") and not doc.get("notes_pdf")):
                session_store.delete(doc["path"])
                continue
            session_store.save(doc["path"], {
                "lecture": doc["lecture"],
                "notes_pdf": doc.get("notes_pdf", ""),
                "boxes": boxes,
                "page_modes": {
                    str(i - start): m for i, m in self._page_modes.items()
                    if start <= i < start + count},
                "note_map": doc.get("note_map", {}),
                "image_map": doc.get("image_map", {}),
                "cloze_cards": cloze_cards,
                "cloze_stale": [n for n in self._cloze_stale],
                "render_scale": self._render_scale,
                "page_count": count,
                "deck": self._deck_combo.currentText().strip(),
            })

    def done(self, result: int):
        # runs on accept, reject (Esc) and window close — work is never lost
        self._persist_sessions()
        super().done(result)

    # ---------------------------------------------------------------- zoom --

    def _apply_default_zoom(self):
        z = _cfg("default_zoom", "fit")
        if z == "fit":
            self._zoom_fit()
        else:
            try:
                self._set_zoom(float(z))
            except (TypeError, ValueError):
                self._zoom_fit()

    def _refit(self):
        """Re-fit after the side panels change how much room the slide has —
        but only if the slide was fitted to begin with; a zoom the user set
        by hand is theirs to keep. Deferred by a tick so the new layout is
        settled before the viewport is measured."""
        if self._pages and self._fitted:
            QTimer.singleShot(0, self._zoom_fit)

    def _set_zoom(self, z: float):
        self._fitted = False        # _zoom_fit sets it back after this call
        self._canvas.set_zoom(z)
        self._zoom_label.setText(f"{int(self._canvas.zoom() * 100)}%")

    def _zoom_in(self):
        z = self._canvas.zoom()
        bigger = [s for s in _ZOOM_STEPS if s > z + 0.01]
        if bigger:
            self._set_zoom(bigger[0])

    def _zoom_out(self):
        z = self._canvas.zoom()
        smaller = [s for s in _ZOOM_STEPS if s < z - 0.01]
        if smaller:
            self._set_zoom(smaller[-1])

    def _on_zoom_gesture(self, delta: float, pos):
        """Pinch / Ctrl+scroll: smooth zoom anchored at the cursor."""
        if not self._pages:
            return
        old = self._canvas.zoom()
        new = max(0.1, min(4.0, old * (1.0 + delta)))
        if abs(new - old) < 1e-4:
            return
        hbar = self._scroll.horizontalScrollBar()
        vbar = self._scroll.verticalScrollBar()
        # keep the image point under the cursor stationary: its canvas
        # coordinate scales by (new/old), so shift the viewport by the same
        ratio = new / old
        hv, vv = hbar.value(), vbar.value()
        self._set_zoom(new)
        hbar.setValue(int(pos.x() * ratio - (pos.x() - hv)))
        vbar.setValue(int(pos.y() * ratio - (pos.y() - vv)))

    def _zoom_fit(self):
        """Zoom so the entire slide is visible in the scroll area."""
        if not self._pages:
            return
        img = self._pages[self._page_index]
        natural_w = img.width() / self._render_scale
        natural_h = img.height() / self._render_scale
        if natural_w <= 0 or natural_h <= 0:
            return
        vp = self._scroll.viewport()
        self._set_zoom(min((vp.width() - 6) / natural_w,
                           (vp.height() - 6) / natural_h))
        self._fitted = True

    # --------------------------------------------------------------- pages --

    def _save_current_boxes(self):
        if self._canvas.has_image():
            self._boxes[self._page_index] = self._canvas.get_boxes()

    def _show_page(self):
        if not self._pages:
            return
        self._canvas.set_image(
            self._pages[self._page_index],
            self._boxes.get(self._page_index, []),
            render_scale=self._render_scale,
        )
        doc = self._doc_for_page(self._page_index)
        if doc and self._lecture_edit.text().strip() != doc["lecture"]:
            self._lecture_edit.blockSignals(True)
            self._lecture_edit.setText(doc["lecture"])
            self._lecture_edit.blockSignals(False)
        self._sync_page_mode_combo()
        self._update_controls()
        self._refresh_count()
        if self._cloze.isVisible():
            self._cloze.refresh_slide()
        self._refresh_cloze_column()

    def _sync_page_mode_combo(self):
        """Show the slide's effective mode: its override, else the config default."""
        mode = self._page_modes.get(self._page_index) or self._default_mode()
        idx = self._page_mode_combo.findData(mode)
        self._page_mode_combo.blockSignals(True)
        self._page_mode_combo.setCurrentIndex(max(idx, 0))
        self._page_mode_combo.blockSignals(False)

    def _on_page_mode_changed(self):
        if not self._pages:
            return
        mode = self._page_mode_combo.currentData()
        # picking the default again = no override
        if mode == self._default_mode():
            self._page_modes.pop(self._page_index, None)
        else:
            self._page_modes[self._page_index] = mode

    def _arrow(self, key, shift: bool):
        if self._pages:
            self._canvas.handle_arrow(key, shift)

    def _on_slide_nav(self, direction: int):
        self._next_page() if direction > 0 else self._prev_page()

    def _prev_page(self):
        if self._pages and self._page_index > 0:
            self._save_current_boxes()
            self._page_index -= 1
            self._show_page()

    def _next_page(self):
        if self._pages and self._page_index < len(self._pages) - 1:
            self._save_current_boxes()
            self._page_index += 1
            self._show_page()

        self._update_controls()
        self._refresh_count()

    def _on_boxes_changed(self):
        self._refresh_count()

    # ------------------------------------------------------------ detection --
    #
    # Detect never runs on the whole slide. Click it and it arms the canvas;
    # the next drag says which part to scan, and only that part is scanned.
    # A slide usually has one table worth boxing and a title, a footer and a
    # page number that are not — so "everything on the slide" was almost
    # never the right answer, and undoing it was busywork.

    def _arm_detect(self):
        """Click 1 of 2: wait for the region. Clicking again stands down."""
        if not self._pages:
            self._detect_btn.setChecked(False)
            return
        if self._canvas.scan_armed():
            self._canvas.arm_scan(False)
            self._detect_btn.setChecked(False)
            self._say_detect("")
            return
        self._canvas.arm_scan(True)
        self._detect_btn.setChecked(True)
        # No prompt: the sunk button, the crosshair and the dimming that
        # follows the drag already say what is going on.
        self._say_detect("")

    def _on_scan_cancelled(self):
        self._detect_btn.setChecked(False)
        self._say_detect("")

    def _on_scan_region(self, x: float, y: float, w: float, h: float):
        """Click 2 of 2: box whatever is inside the region just dragged out.

        Cells if a ruled table is in there, lines of text if not — which is
        what the slide itself decides, so there is nothing for the button to
        ask about.
        """
        self._detect_btn.setChecked(False)
        self._say_detect("")
        doc = self._doc_for_page(self._page_index)
        if not doc:
            return
        local = self._page_index - doc["start"]
        region = (x, y, w, h)

        try:
            rects = [r for r in get_table_cell_rects(
                doc["path"], local, self._render_scale)
                if _centre_inside(r, region)]
            kind = "table"
            if len(rects) < 2:
                rects = [r for r in get_text_line_rects(
                    doc["path"], local, self._render_scale)
                    if _centre_inside(r, region)]
                kind = "text"
        except Exception as exc:
            showWarning(f"Detection failed:\n{exc}")
            return

        found = len(rects)
        rects = [r for r in rects if not self._already_boxed(r)]
        if not rects:
            if found:
                # scanned the same place twice — say so rather than claim the
                # region was empty, which it plainly was not
                self._say_detect("Already boxed", transient=True)
                return
            showInfo(
                "Nothing found to box in there.\n\n"
                "A ruled table is boxed cell by cell and anything else line "
                "by line, but both read the PDF's own text and vector data — "
                "a slide that is just a picture has neither. Draw the boxes "
                "by hand there."
            )
            return

        self._canvas.add_boxes([
            {"x": int(rx), "y": int(ry), "w": int(rw), "h": int(rh),
             "group": None, "shape": "rect", "id": uuid.uuid4().hex}
            for rx, ry, rw, rh in rects])
        self._say_detect(
            f"{len(rects)} {'cell' if kind == 'table' else 'line'}"
            f"{'s' if len(rects) != 1 else ''} boxed", transient=True)

    def _already_boxed(self, rect: tuple, slack: int = 3) -> bool:
        """Is this rect already on the slide? Scanning the same region twice
        should not leave two boxes stacked on every line of it."""
        x, y, w, h = rect
        for b in self._canvas.get_boxes():
            if (abs(b["x"] - x) <= slack and abs(b["y"] - y) <= slack
                    and abs(b["w"] - w) <= slack and abs(b["h"] - h) <= slack):
                return True
        return False

    def _say_detect(self, msg: str, transient: bool = False):
        """Say what Detect is waiting for, or what it just did."""
        self._detect_hint.setText(msg)
        if transient and msg:
            QTimer.singleShot(3000, lambda: self._detect_hint.setText("")
                              if self._detect_hint.text() == msg else None)

    # -------------------------------------------------------------- counter --

    def _expected_cards(self) -> int:
        """Cards that Create All Cards would make right now, across all slides."""
        total = 0
        for i in range(len(self._pages)):
            if i == self._page_index and self._canvas.has_image():
                boxes = self._canvas.get_boxes()
            else:
                boxes = self._boxes.get(i, [])
            gids = {b["group"] for b in boxes if b.get("group") is not None}
            total += len(gids) + sum(1 for b in boxes if b.get("group") is None)
        return total

    def _cloze_cards_total(self) -> int:
        """Cards the cloze notes made — one per c-number, every slide."""
        return sum(cloze_card_count(c["text"])
                   for cards in self._cloze_cards.values() for c in cards)

    def _refresh_count(self):
        if not self._pages:
            self._count_label.setText("")
            self._count_label.setToolTip("")
            return
        occlusion = self._expected_cards()
        cloze = self._cloze_cards_total()
        n = occlusion + cloze
        self._count_label.setText(
            f"{n} card{'s' if n != 1 else ''}"
            + (f"  ·  {cloze} cloze" if cloze else ""))
        self._count_label.setToolTip(
            f"{occlusion} occlusion card{'s' if occlusion != 1 else ''}"
            + (f"\n{cloze} cloze card{'s' if cloze != 1 else ''}" if cloze else "")
            + "\nCreate All Cards writes both."
        )

    def _update_controls(self):
        has = bool(self._pages)
        self._prev_btn.setEnabled(has and self._page_index > 0)
        self._next_btn.setEnabled(has and self._page_index < len(self._pages) - 1)
        self._create_btn.setEnabled(has)
        self._zoom_in_btn.setEnabled(has)
        self._zoom_out_btn.setEnabled(has)
        self._fit_btn.setEnabled(has)
        self._detect_btn.setEnabled(has)
        self._cloze_btn.setEnabled(has)
        self._page_mode_combo.setEnabled(has)
        self._sync_notes_pdf_btn()

        if has:
            idx = self._page_index
            n = len(self._pages)
            doc = self._doc_for_page(idx)
            prefix = ""
            if doc and len(self._docs) > 1:
                prefix = f"{os.path.basename(doc['path'])} — "
            self._page_label.setText(f"{prefix}Slide {idx + 1} / {n}")
        else:
            self._page_label.setText("No PDF loaded")

    # -------------------------------------------------------------- cloze --
    #
    # Slides that are too text-heavy to occlude usefully are better as cloze
    # cards with the slide kept as the extra. The composer is a separate
    # non-modal panel (cloze_dialog.py); everything it needs about the
    # current slide comes through the three methods below.

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_panel_height()

    def _sync_panel_height(self):
        """Keep the composer to half the height the slide has."""
        panel = getattr(self, "_cloze", None)
        if panel is None:
            return
        h = self._scroll.height()
        if h > 0:
            panel.setMaximumHeight(max(300, h // 2))

    def _open_cloze(self):
        """Ctrl+Shift+V — show or hide the composer panel."""
        if not self._pages:
            return
        showing = not self._cloze.isVisible()
        self._sync_panel_height()
        self._cloze_side.setVisible(showing)
        if showing:
            self._cloze.refresh_slide()
            self._cloze.focus_editor()
        else:
            self._canvas.setFocus()
        self._refit()

    def _refresh_cloze_column(self):
        """Show this slide's cards. The column appears and disappears with
        them, so the slide's room changes — hence the re-fit."""
        self._cloze_column.set_cards(self._cloze_cards.get(self._page_index, []))
        self._refit()

    def _find_cloze(self, uid: str):
        """(page, index) of a stored card, or (None, None)."""
        for page, cards in self._cloze_cards.items():
            for i, card in enumerate(cards):
                if card.get("uid") == uid:
                    return page, i
        return None, None

    def _on_cloze_created(self, card: dict):
        """File the new card against its slide and send it to the column."""
        page = card.get("page", self._page_index)
        entry = dict(card)
        self._cloze_cards.setdefault(page, []).append(entry)
        self._refresh_count()
        if page != self._page_index:
            return
        chip = self._cloze_column.add_card(entry)
        self._refit()               # the column may have just appeared
        start = self._cloze.source_rect()

        def fly():
            self._cloze_column.scroll_to_end()
            self._fly = fly_to_chip(self, card["text"], start, chip)

        QTimer.singleShot(0, fly)   # let the column lay the chip out first

    def _on_cloze_activated(self, entry: dict):
        """A chip was clicked — put that card back in the panel to edit."""
        if not self._cloze.isVisible():
            self._open_cloze()
        self._cloze.load_card(entry)

    def _on_cloze_updated(self, card: dict):
        page, i = self._find_cloze(card.get("uid"))
        if page is None:
            return
        entry = dict(card)
        entry["page"] = page
        self._cloze_cards[page][i] = entry
        self._refresh_cloze_column()
        self._refresh_count()

    def _on_cloze_delete(self, entry: dict):
        n = cloze_card_count(entry.get("text", ""))
        made = bool(entry.get("nid"))
        if not askUser(
            f"Remove this cloze card ({n} card{'s' if n != 1 else ''})?"
            + ("\n\nIts note is deleted the next time you create cards."
               if made else ""),
            title="PDF Occlusion",
        ):
            return
        page, i = self._find_cloze(entry.get("uid"))
        if page is not None:
            del self._cloze_cards[page][i]
            if not self._cloze_cards[page]:
                del self._cloze_cards[page]
        if made:
            self._cloze_stale.append(int(entry["nid"]))
        if self._cloze.editing_uid() == entry.get("uid"):
            self._cloze.reset_editing()
        self._refresh_cloze_column()
        self._refresh_count()

    def cloze_deck_name(self) -> str:
        return self._deck_combo.currentText().strip()

    def cloze_slide(self, page: Optional[int] = None) -> Optional[dict]:
        """Which slide a card belongs to — the one on screen unless asked
        for another (a card being edited keeps its own)."""
        if not self._pages:
            return None
        idx = self._page_index if page is None else page
        if not 0 <= idx < len(self._pages):
            idx = self._page_index
        return {"page": idx, "label": self._slide_label_for(idx)}

    def cloze_slide_text(self, page: Optional[int] = None) -> str:
        """The text printed on a slide, for the composer's Slide Text button.

        Empty string when the slide has none to give (a scanned image, or a
        PDF pdfium cannot read text out of) — the composer says so."""
        if not self._pages:
            return ""
        idx = self._page_index if page is None else page
        if not 0 <= idx < len(self._pages):
            idx = self._page_index
        doc = self._doc_for_page(idx)
        if not doc:
            return ""
        try:
            return get_page_text(doc["path"], idx - doc["start"])
        except Exception:
            return ""

    def _slide_label_for(self, idx: int) -> str:
        doc = self._doc_for_page(idx)
        local = idx - doc["start"] if doc else idx
        total = doc["count"] if doc else len(self._pages)
        return f"Slide {local + 1}/{total}"

    # -------------------------------------------------------- card creation --

    def _create_cards(self):
        if not self._pages:
            return

        self._save_current_boxes()

        per_doc = []
        for doc in self._docs:
            start, count = doc["start"], doc["count"]
            to_create = [
                (i - start, self._pages[i], self._boxes[i])
                for i in range(start, start + count)
                if self._boxes.get(i)
            ]
            cloze = []
            for i in range(start, start + count):
                for card in self._cloze_cards.get(i, []):
                    entry = dict(card)
                    entry["page"] = i - start
                    cloze.append(entry)
            if to_create or cloze:
                per_doc.append((doc, to_create, cloze))

        if not per_doc:
            showWarning(
                "Nothing to create yet.\n\n"
                "Draw a box on a slide, or write a cloze card "
                "(Ctrl+Shift+V)."
            )
            return

        mode = self._default_mode()

        deck_name = self._deck_combo.currentText().strip()
        deck_id = mw.col.decks.id(deck_name) if deck_name else mw.col.decks.selected()

        note_type_name = _cfg("note_type_name", "PDF Occlusion")
        mask_color = tuple(_cfg("mask_color", [120, 120, 120]))
        mask_opacity = int(_cfg("mask_opacity", 255))
        highlight_color = tuple(_cfg("highlight_color", [131, 110, 170]))

        grand_total = sum(len(tc) for _, tc, _c in per_doc)
        progress = QProgressDialog("Creating cards…", None, 0, grand_total, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(300)

        # Only when there are boxes: a cloze-only session shouldn't plant
        # the occlusion note type in a collection that has never used it.
        note_type = (ensure_note_type(mw.col, note_type_name)
                     if any(tc for _, tc, _c in per_doc) else None)

        cloze_type = cloze_note_type(mw.col)
        if cloze_type is None and any(cloze for _, _, cloze in per_doc):
            showWarning(
                "No cloze note type found in this collection, so the cloze "
                "cards were skipped.\n\nAdd Anki's stock “Cloze” note type "
                "(Tools → Manage Note Types → Add) and create again."
            )

        created = updated = unchanged = slides = cloze_made = 0
        stale_nids: list[int] = list(self._cloze_stale)
        offset = 0
        for doc, to_create, cloze in per_doc:
            start, count = doc["start"], doc["count"]
            local_modes = {
                str(i - start): m for i, m in self._page_modes.items()
                if start <= i < start + count
            }

            def on_progress(done: int, total: int, _off=offset):
                progress.setValue(_off + done)
                mw.app.processEvents()

            result = {"created": 0, "updated": 0, "unchanged": 0,
                      "note_map": doc.get("note_map", {}),
                      "image_map": doc.get("image_map", {}),
                      "stale_nids": []}
            if to_create:
                result = create_occlusion_notes(
                    mw.col, deck_id, note_type, to_create,
                    mask_color=mask_color,
                    mask_opacity=mask_opacity,
                    highlight_color=highlight_color,
                    lecture_name=doc["lecture"],
                    total_slides=count,
                    slides_pdf=doc["path"],
                    notes_pdf=doc.get("notes_pdf", ""),
                    default_mode=mode,
                    page_modes=local_modes,
                    note_map=doc.get("note_map"),
                    image_map=doc.get("image_map"),
                    on_progress=on_progress,
                )
            doc["note_map"] = result["note_map"]
            doc["image_map"] = result["image_map"]
            created += result["created"]
            updated += result["updated"]
            unchanged += result["unchanged"]
            stale_nids += result["stale_nids"]
            slides += len(to_create)
            offset += len(to_create)

            if cloze and cloze_type is not None:
                def caption_for(local, _doc=doc, _count=count):
                    label = f"Slide {local + 1}/{_count}"
                    lecture = (_doc.get("lecture") or "").strip()
                    return f"{lecture} · {label}" if lecture else label

                cres = create_cloze_notes(
                    mw.col, deck_id, cloze_type, cloze,
                    page_images={c["page"]: self._pages[start + c["page"]]
                                 for c in cloze},
                    image_map=doc.get("image_map"),
                    caption_for=caption_for,
                )
                doc["image_map"] = cres["image_map"]
                # hand the new note ids back to the records they came from
                by_uid = {c["uid"]: c["nid"] for c in cres["cards"]
                          if c.get("uid")}
                for i in range(start, start + count):
                    for card in self._cloze_cards.get(i, []):
                        uid = card.get("uid")
                        if uid and uid in by_uid:
                            card["nid"] = by_uid[uid]
                created += cres["created"]
                updated += cres["updated"]
                unchanged += cres["unchanged"]
                cloze_made += cres["created"] + cres["updated"]

        progress.close()

        # The records now know their note ids. Get that to disk before
        # anything else can go wrong — losing it would mean creating every
        # one of these notes a second time on the next run.
        self._persist_sessions()

        deleted = 0
        if stale_nids and askUser(
            f"Delete {len(stale_nids)} note(s) whose boxes or cloze cards "
            "were removed?",
            title="PDF Occlusion",
        ):
            mw.col.remove_notes(stale_nids)
            deleted = len(stale_nids)
        self._cloze_stale = []

        mw.col.reset()
        mw.reset()
        self._persist_sessions()

        bits = []
        if created:
            bits.append(f"{created} created")
        if updated:
            bits.append(f"{updated} updated")
        if deleted:
            bits.append(f"{deleted} deleted")
        if not bits:
            msg = (f"All {unchanged} card{'s' if unchanged != 1 else ''} "
                   "already up to date.")
        else:
            if unchanged:
                bits.append(f"{unchanged} unchanged")
            msg = "Cards: " + ", ".join(bits) + "."
        if cloze_made:
            msg += f"\n\n{cloze_made} of them cloze."
        showInfo(msg)
        if _cfg("close_after_creating", True):
            self.accept()
