import os
from typing import Optional

from aqt import mw
from aqt.theme import theme_manager
from aqt.qt import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QToolButton, QLabel,
    QLineEdit, QComboBox, QFileDialog, QScrollArea, QShortcut, QKeySequence,
    Qt, QImage, QProgressDialog, QMenu,
)
from aqt.utils import askUser, showInfo, showWarning

from . import session_store
from .occlusion_canvas import OcclusionCanvas
from .card_builder import ensure_note_type, create_occlusion_notes
from .pdf_renderer import render_pdf, get_text_line_rects


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
_CARET_PATH = os.path.join(
    os.path.dirname(__file__), "caret.svg").replace("\\", "/")
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
        self._skipped: set[int] = set()
        self._boxes: dict[int, list[dict]] = {}
        self._page_modes: dict[int, str] = {}   # global page idx -> "ao"/"oa"

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
        if kind == "b":
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
        self._open_btn.setToolTip("Open PDFs · the arrow lists recent sessions")
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

        row2.addWidget(self._lecture_edit, stretch=3)
        row2.addWidget(self._deck_combo, stretch=2)
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

        self._detect_btn = QPushButton("Detect Text")
        self._detect_btn.setToolTip("Auto-box the text on this slide (T)")
        self._detect_btn.clicked.connect(self._detect_text)

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
        row3.addStretch()
        row3.addWidget(slide_mode_label)
        row3.addWidget(self._page_mode_combo)
        root.addLayout(row3)

        # ── Canvas ────────────────────────────────────────────────────────
        self._canvas = OcclusionCanvas()
        self._canvas.boxes_changed.connect(self._on_boxes_changed)
        self._canvas.slide_nav.connect(self._on_slide_nav)
        self._canvas.zoom_gesture.connect(self._on_zoom_gesture)
        self._scroll = QScrollArea()
        self._scroll.setWidget(self._canvas)
        self._scroll.setWidgetResizable(False)
        self._scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._scroll, stretch=1)

        # ── Bottom nav / create ───────────────────────────────────────────
        bot = QHBoxLayout()

        self._prev_btn = QPushButton("← Prev")
        self._prev_btn.setToolTip("Previous slide (PgUp)")
        self._prev_btn.clicked.connect(self._prev_page)
        self._skip_btn = QPushButton("Skip")
        self._skip_btn.setToolTip("Skip / unskip this slide (Space)")
        self._skip_btn.clicked.connect(self._toggle_skip)
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

        for w in (self._prev_btn, self._skip_btn, self._next_btn):
            bot.addWidget(w)
        bot.addStretch()
        bot.addWidget(self._count_label)
        bot.addSpacing(8)
        bot.addWidget(self._create_btn)
        root.addLayout(bot)

        # ── Shortcuts ─────────────────────────────────────────────────────
        # Left/Right live on the canvas (they nudge when boxes are selected);
        # PgUp/PgDn always flip slides regardless of focus. Plain-letter
        # shortcuts never fire while a text field has focus — the field
        # consumes them first.
        QShortcut(QKeySequence(Qt.Key.Key_PageUp), self, self._prev_page)
        QShortcut(QKeySequence(Qt.Key.Key_PageDown), self, self._next_page)
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, self._toggle_skip)
        QShortcut(QKeySequence("D"), self, lambda: self._set_tool("draw"))
        QShortcut(QKeySequence("V"), self, lambda: self._set_tool("select"))
        QShortcut(QKeySequence("T"), self, self._detect_text)
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
                "note_map": {},
                "image_map": {},
            })
            all_pages.extend(pages)

        self._docs = docs
        self._pages = all_pages
        self._page_index = 0
        self._skipped.clear()
        self._boxes.clear()
        self._page_modes.clear()

        self._maybe_resume_sessions(silent=silent_resume)

        self._show_page()
        self._apply_default_zoom()

    # ------------------------------------------------------------- sessions --

    def _maybe_resume_sessions(self, silent: bool = False):
        sessions = {i: session_store.load(doc["path"])
                    for i, doc in enumerate(self._docs)}
        resumable = {
            i: s for i, s in sessions.items()
            if s and (s.get("boxes") or s.get("note_map"))
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

            for local in s.get("skipped", []):
                if 0 <= int(local) < doc["count"]:
                    self._skipped.add(doc["start"] + int(local))

            for k, m in (s.get("page_modes") or {}).items():
                try:
                    local = int(k)
                except ValueError:
                    continue
                if m in ("ao", "oa") and 0 <= local < doc["count"]:
                    self._page_modes[doc["start"] + local] = m

            doc["lecture"] = s.get("lecture") or doc["lecture"]
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
            if not boxes and not doc.get("note_map"):
                session_store.delete(doc["path"])
                continue
            session_store.save(doc["path"], {
                "lecture": doc["lecture"],
                "boxes": boxes,
                "skipped": sorted(
                    i - start for i in self._skipped if start <= i < start + count),
                "page_modes": {
                    str(i - start): m for i, m in self._page_modes.items()
                    if start <= i < start + count},
                "note_map": doc.get("note_map", {}),
                "image_map": doc.get("image_map", {}),
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

    def _set_zoom(self, z: float):
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

    def _toggle_skip(self):
        if not self._pages:
            return
        idx = self._page_index
        self._skipped.discard(idx) if idx in self._skipped else self._skipped.add(idx)
        self._update_controls()
        self._refresh_count()

    def _on_boxes_changed(self):
        self._refresh_count()

    # ------------------------------------------------------- text detection --

    def _detect_text(self):
        if not self._pages:
            return
        doc = self._doc_for_page(self._page_index)
        if not doc:
            return
        local = self._page_index - doc["start"]
        try:
            rects = get_text_line_rects(doc["path"], local, self._render_scale)
        except Exception as exc:
            showWarning(f"Text detection failed:\n{exc}")
            return
        if not rects:
            showInfo(
                "No text found on this slide.\n\n"
                "It is probably a scanned image — draw boxes by hand instead."
            )
            return
        self._canvas.add_boxes([
            {"x": int(x), "y": int(y), "w": int(w), "h": int(h),
             "group": None, "shape": "rect"}
            for x, y, w, h in rects
        ])

    # -------------------------------------------------------------- counter --

    def _expected_cards(self) -> int:
        """Cards that Create All Cards would make right now, across all slides."""
        total = 0
        for i in range(len(self._pages)):
            if i in self._skipped:
                continue
            if i == self._page_index and self._canvas.has_image():
                boxes = self._canvas.get_boxes()
            else:
                boxes = self._boxes.get(i, [])
            gids = {b["group"] for b in boxes if b.get("group") is not None}
            total += len(gids) + sum(1 for b in boxes if b.get("group") is None)
        return total

    def _refresh_count(self):
        if not self._pages:
            self._count_label.setText("")
            return
        n = self._expected_cards()
        self._count_label.setText(f"{n} card{'s' if n != 1 else ''}")

    def _update_controls(self):
        has = bool(self._pages)
        self._prev_btn.setEnabled(has and self._page_index > 0)
        self._next_btn.setEnabled(has and self._page_index < len(self._pages) - 1)
        self._skip_btn.setEnabled(has)
        self._create_btn.setEnabled(has)
        self._zoom_in_btn.setEnabled(has)
        self._zoom_out_btn.setEnabled(has)
        self._fit_btn.setEnabled(has)
        self._detect_btn.setEnabled(has)
        self._page_mode_combo.setEnabled(has)

        if has:
            idx = self._page_index
            n = len(self._pages)
            doc = self._doc_for_page(idx)
            prefix = ""
            if doc and len(self._docs) > 1:
                prefix = f"{os.path.basename(doc['path'])} — "
            skipped = "  [SKIPPED]" if idx in self._skipped else ""
            self._page_label.setText(f"{prefix}Slide {idx + 1} / {n}{skipped}")
            self._skip_btn.setStyleSheet(
                f"color:{_accent_color()}; font-weight:bold;"
                if idx in self._skipped else ""
            )
        else:
            self._page_label.setText("No PDF loaded")

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
                if i not in self._skipped and self._boxes.get(i)
            ]
            if to_create:
                per_doc.append((doc, to_create))

        if not per_doc:
            showWarning(
                "No occlusion boxes found on any non-skipped slide.\n"
                "Draw at least one box on a slide to create cards."
            )
            return

        mode = self._default_mode()

        deck_name = self._deck_combo.currentText().strip()
        deck_id = mw.col.decks.id(deck_name) if deck_name else mw.col.decks.selected()

        note_type_name = _cfg("note_type_name", "PDF Occlusion")
        mask_color = tuple(_cfg("mask_color", [120, 120, 120]))
        mask_opacity = int(_cfg("mask_opacity", 255))
        highlight_color = tuple(_cfg("highlight_color", [131, 110, 170]))

        grand_total = sum(len(tc) for _, tc in per_doc)
        progress = QProgressDialog("Creating cards…", None, 0, grand_total, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(300)

        note_type = ensure_note_type(mw.col, note_type_name)

        created = updated = unchanged = slides = 0
        stale_nids: list[int] = []
        offset = 0
        for doc, to_create in per_doc:
            start, count = doc["start"], doc["count"]
            local_modes = {
                str(i - start): m for i, m in self._page_modes.items()
                if start <= i < start + count
            }

            def on_progress(done: int, total: int, _off=offset):
                progress.setValue(_off + done)
                mw.app.processEvents()

            result = create_occlusion_notes(
                mw.col, deck_id, note_type, to_create,
                mask_color=mask_color,
                mask_opacity=mask_opacity,
                highlight_color=highlight_color,
                lecture_name=doc["lecture"],
                total_slides=count,
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

        progress.close()

        deleted = 0
        if stale_nids and askUser(
            f"Delete {len(stale_nids)} card(s) whose boxes were removed?",
            title="PDF Occlusion",
        ):
            mw.col.remove_notes(stale_nids)
            deleted = len(stale_nids)

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
        showInfo(msg)
        if _cfg("close_after_creating", True):
            self.accept()
