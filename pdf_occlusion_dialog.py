import os
from typing import Optional

from aqt import mw
from aqt.theme import theme_manager
from aqt.qt import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit,
    QComboBox, QFileDialog, QScrollArea, QShortcut, QKeySequence, Qt, QImage,
    QProgressDialog,
)
from aqt.utils import showInfo, showWarning

from .occlusion_canvas import OcclusionCanvas
from .card_builder import ensure_note_type, create_occlusion_notes
from .pdf_renderer import render_pdf


_ZOOM_STEPS = [0.25, 0.33, 0.5, 0.67, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0]

# purple
_NU_PURPLE = "#4E2A84"
_NU_PURPLE_DARK = "#401F68"
_NU_PURPLE_DARKER = "#361d5c"

def _accent_color() -> str:
    """Purple accent readable on the current Anki theme."""
    try:
        night = theme_manager.night_mode
    except Exception:
        night = False
    return "#B6ACD1" if night else _NU_PURPLE


# One purple for every action button so the dialog reads as a single palette.
_PRIMARY_BTN_QSS = (
    "QPushButton{background:#836EAA;color:white;font-weight:bold;"
    "padding:6px 18px;border-radius:4px;border:none;}"
    "QPushButton:hover{background:#75619b;}"
    "QPushButton:pressed{background:#67548c;}"
    # neutral gray when disabled so it doesn't read as a second shade of purple
    "QPushButton:disabled{background:#b5b2ba;color:#efefef;}"
)


def _cfg(key, default):
    cfg = mw.addonManager.getConfig(__name__) or {}
    return cfg.get(key, default)


class PDFOcclusionDialog(QDialog):
    def __init__(self, parent=None, editor=None):
        super().__init__(parent)
        self._editor = editor
        self.setWindowTitle("PDF Occlusion")
        self.resize(1100, 860)

        self._pages: list[QImage] = []
        self._render_scale: float = 1.0
        self._page_index: int = 0
        self._skipped: set[int] = set()
        self._boxes: dict[int, list[dict]] = {}

        self._build_ui()

        self._canvas.set_mask_color(tuple(_cfg("mask_color", [120, 120, 120])))
        self._apply_default_zoom()

    # ------------------------------------------------------------------ UI --

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(6)

        # ── Row 1: open button · slide counter · zoom ─────────────────────
        row1 = QHBoxLayout()

        self._open_btn = QPushButton("Open PDF…")
        self._open_btn.setStyleSheet(_PRIMARY_BTN_QSS)
        self._open_btn.clicked.connect(self._open_pdf)

        self._page_label = QLabel("No PDF loaded")
        self._page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._page_label.setStyleSheet("font-weight:bold;")

        self._zoom_out_btn = QPushButton("−")
        self._zoom_out_btn.setFixedWidth(28)
        self._zoom_out_btn.clicked.connect(self._zoom_out)
        self._zoom_label = QLabel("100%")
        self._zoom_label.setFixedWidth(44)
        self._zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._zoom_in_btn = QPushButton("+")
        self._zoom_in_btn.setFixedWidth(28)
        self._zoom_in_btn.clicked.connect(self._zoom_in)
        self._fit_btn = QPushButton("Fit")
        self._fit_btn.setFixedWidth(40)
        self._fit_btn.setToolTip("Fit the slide to the window width (Ctrl+0)")
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

        # ── Row 2: lecture name · deck · occlusion mode ───────────────────
        row2 = QHBoxLayout()

        lec_label = QLabel("Lecture:")
        self._lecture_edit = QLineEdit()
        self._lecture_edit.setMinimumWidth(220)

        deck_label = QLabel("Deck:")
        self._deck_combo = QComboBox()
        self._deck_combo.setEditable(True)
        self._deck_combo.setMinimumWidth(180)
        self._deck_combo.setToolTip(
            "Deck the cards go into. Type a new name to create a deck."
        )
        self._populate_decks()

        mode_label = QLabel("Mode:")
        self._mode_combo = QComboBox()
        self._mode_combo.addItem("Hide All, Show One", "ao")
        self._mode_combo.addItem("Hide One, Show One", "oa")
        self._mode_combo.setToolTip(
            "Hide All, Show One — every box is masked on the front; the back\n"
            "reveals only the tested box (no peeking at the others).\n\n"
            "Hide One, Show One — only the tested box is masked; the back\n"
            "reveals everything. Good for many independent facts per slide."
        )
        # apply config default
        default_mode = _cfg("occlusion_mode", "ao")
        idx = self._mode_combo.findData(default_mode)
        if idx >= 0:
            self._mode_combo.setCurrentIndex(idx)

        row2.addWidget(lec_label)
        row2.addWidget(self._lecture_edit, stretch=2)
        row2.addSpacing(12)
        row2.addWidget(deck_label)
        row2.addWidget(self._deck_combo, stretch=1)
        row2.addSpacing(12)
        row2.addWidget(mode_label)
        row2.addWidget(self._mode_combo)
        root.addLayout(row2)

        # ── Canvas ────────────────────────────────────────────────────────
        self._canvas = OcclusionCanvas()
        self._canvas.boxes_changed.connect(self._on_boxes_changed)
        self._canvas.slide_nav.connect(self._on_slide_nav)
        self._scroll = QScrollArea()
        self._scroll.setWidget(self._canvas)
        self._scroll.setWidgetResizable(False)
        self._scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._scroll, stretch=1)

        # ── Group toolbar ─────────────────────────────────────────────────
        grp_bar = QHBoxLayout()

        self._group_btn = QPushButton("Group selected  (G)")
        self._group_btn.setToolTip(
            "Assign all selected boxes to one group.\n"
            "They will all be masked together on a single card."
        )
        self._group_btn.clicked.connect(self._canvas.group_selected)

        self._ungroup_btn = QPushButton("Ungroup  (U)")
        self._ungroup_btn.setToolTip("Remove group assignment from selected boxes.")
        self._ungroup_btn.clicked.connect(self._canvas.ungroup_selected)

        self._sel_all_btn = QPushButton("Select all  (Ctrl+A)")
        self._sel_all_btn.clicked.connect(self._canvas.select_all)

        self._group_status = QLabel("")
        self._group_status.setStyleSheet(
            f"color:{_accent_color()}; font-size:11px; font-weight:bold;"
        )

        grp_bar.addWidget(self._group_btn)
        grp_bar.addWidget(self._ungroup_btn)
        grp_bar.addWidget(self._sel_all_btn)
        grp_bar.addStretch()
        grp_bar.addWidget(self._group_status)
        root.addLayout(grp_bar)

        # ── Bottom nav / create ───────────────────────────────────────────
        bot = QHBoxLayout()

        self._prev_btn = QPushButton("← Prev")
        self._prev_btn.clicked.connect(self._prev_page)
        self._skip_btn = QPushButton("Skip slide  (Space)")
        self._skip_btn.clicked.connect(self._toggle_skip)
        self._next_btn = QPushButton("Next →")
        self._next_btn.clicked.connect(self._next_page)

        self._create_btn = QPushButton("Create All Cards")
        self._create_btn.setDefault(True)
        self._create_btn.clicked.connect(self._create_cards)
        self._create_btn.setStyleSheet(_PRIMARY_BTN_QSS)

        for w in (self._prev_btn, self._skip_btn, self._next_btn):
            bot.addWidget(w)
        bot.addStretch()
        bot.addWidget(self._create_btn)
        root.addLayout(bot)

        # ── Shortcuts ─────────────────────────────────────────────────────
        # Left/Right live on the canvas (they nudge when boxes are selected);
        # PgUp/PgDn always flip slides regardless of focus.
        QShortcut(QKeySequence(Qt.Key.Key_PageUp), self, self._prev_page)
        QShortcut(QKeySequence(Qt.Key.Key_PageDown), self, self._next_page)
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, self._toggle_skip)
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

    # ---------------------------------------------------------------- PDF --

    def _open_pdf(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open PDF", "", "PDF Files (*.pdf)"
        )
        if not path:
            return

        # Auto-fill lecture name from filename if field is empty
        if not self._lecture_edit.text().strip():
            stem = os.path.splitext(os.path.basename(path))[0]
            self._lecture_edit.setText(stem)

        self._render_scale = float(_cfg("render_dpi_scale", 2.0))

        progress = QProgressDialog("Rendering PDF…", "Cancel", 0, 100, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(300)

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
            showWarning(f"Could not open this PDF:\n{exc}")
            return

        # read the cancel state BEFORE close() — closing a QProgressDialog
        # emits canceled(), which flips wasCanceled() to True
        canceled = progress.wasCanceled()
        progress.close()
        if canceled or not pages:
            return

        self._pages = pages
        self._page_index = 0
        self._skipped.clear()
        self._boxes.clear()
        self._show_page()
        self._apply_default_zoom()

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
        self._update_controls()
        self._refresh_group_status()

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
        self._refresh_group_status()

    def _on_boxes_changed(self):
        self._refresh_group_status()

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

    def _refresh_group_status(self):
        if not self._pages:
            self._group_status.setText("")
            return
        n = self._expected_cards()
        self._group_status.setText(
            f"{n} card{'s' if n != 1 else ''} will be created"
        )

    def _update_controls(self):
        has = bool(self._pages)
        self._prev_btn.setEnabled(has and self._page_index > 0)
        self._next_btn.setEnabled(has and self._page_index < len(self._pages) - 1)
        self._skip_btn.setEnabled(has)
        self._create_btn.setEnabled(has)
        self._zoom_in_btn.setEnabled(has)
        self._zoom_out_btn.setEnabled(has)
        self._fit_btn.setEnabled(has)

        if has:
            idx = self._page_index
            n = len(self._pages)
            skipped = "  [SKIPPED]" if idx in self._skipped else ""
            self._page_label.setText(f"Slide {idx + 1} / {n}{skipped}")
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

        to_create = [
            (i, self._pages[i], self._boxes.get(i, []))
            for i in range(len(self._pages))
            if i not in self._skipped and self._boxes.get(i)
        ]

        if not to_create:
            showWarning(
                "No occlusion boxes found on any non-skipped slide.\n"
                "Draw at least one box on a slide to create cards."
            )
            return

        # Lecture name: user input or PDF filename (already auto-filled)
        lecture_name = self._lecture_edit.text().strip()

        # Occlusion mode from combo
        mode = self._mode_combo.currentData()

        deck_name = self._deck_combo.currentText().strip()
        deck_id = mw.col.decks.id(deck_name) if deck_name else mw.col.decks.selected()

        note_type_name = _cfg("note_type_name", "PDF Occlusion")
        mask_color = tuple(_cfg("mask_color", [120, 120, 120]))
        mask_opacity = int(_cfg("mask_opacity", 255))
        highlight_color = tuple(_cfg("highlight_color", [131, 110, 170]))

        progress = QProgressDialog(
            "Creating cards…", None, 0, len(to_create), self
        )
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(300)

        def on_progress(done: int, total: int):
            progress.setValue(done)
            mw.app.processEvents()

        note_type = ensure_note_type(mw.col, note_type_name)
        total = create_occlusion_notes(
            mw.col, deck_id, note_type, to_create,
            mask_color=mask_color,
            mask_opacity=mask_opacity,
            highlight_color=highlight_color,
            lecture_name=lecture_name,
            total_slides=len(self._pages),
            mode=mode,
            on_progress=on_progress,
        )
        progress.close()
        mw.col.reset()
        mw.reset()

        msg = f"Created {total} card(s) from {len(to_create)} slide(s)."
        if _cfg("close_after_creating", True):
            showInfo(msg)
            self.accept()
        else:
            showInfo(msg)
