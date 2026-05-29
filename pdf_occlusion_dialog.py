import os
from typing import Optional

import fitz  # PyMuPDF

from aqt import mw
from aqt.qt import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit,
    QComboBox, QFileDialog, QScrollArea, QShortcut, QKeySequence, Qt,
)
from aqt.utils import showInfo, showWarning

from .occlusion_canvas import OcclusionCanvas
from .card_builder import ensure_note_type, create_occlusion_notes


_ZOOM_STEPS = [0.25, 0.33, 0.5, 0.67, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0]


def _cfg(key, default):
    cfg = mw.addonManager.getConfig(__name__) or {}
    return cfg.get(key, default)


class PDFOcclusionDialog(QDialog):
    def __init__(self, parent=None, editor=None):
        super().__init__(parent)
        self._editor = editor
        self.setWindowTitle("PDF Image Occlusion")
        self.resize(1100, 860)

        self._pages: list[fitz.Pixmap] = []
        self._page_index: int = 0
        self._skipped: set[int] = set()
        self._boxes: dict[int, list[dict]] = {}

        self._build_ui()

        z = float(_cfg("default_zoom", 1.0))
        self._canvas.set_zoom(z)
        self._zoom_label.setText(f"{int(z * 100)}%")

    # ------------------------------------------------------------------ UI --

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(4)

        # ── Row 1: open button · slide counter · zoom ─────────────────────
        row1 = QHBoxLayout()

        self._open_btn = QPushButton("Open PDF…")
        self._open_btn.clicked.connect(self._open_pdf)

        self._page_label = QLabel("No PDF loaded")
        self._page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._zoom_out_btn = QPushButton("−")
        self._zoom_out_btn.setFixedWidth(28)
        self._zoom_out_btn.clicked.connect(self._zoom_out)
        self._zoom_label = QLabel("100%")
        self._zoom_label.setFixedWidth(44)
        self._zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._zoom_in_btn = QPushButton("+")
        self._zoom_in_btn.setFixedWidth(28)
        self._zoom_in_btn.clicked.connect(self._zoom_in)

        row1.addWidget(self._open_btn)
        row1.addStretch()
        row1.addWidget(self._page_label)
        row1.addStretch()
        row1.addWidget(self._zoom_out_btn)
        row1.addWidget(self._zoom_label)
        row1.addWidget(self._zoom_in_btn)
        root.addLayout(row1)

        # ── Row 2: lecture name · occlusion mode ──────────────────────────
        row2 = QHBoxLayout()

        lec_label = QLabel("Lecture:")
        lec_label.setFixedWidth(52)
        self._lecture_edit = QLineEdit()
        self._lecture_edit.setPlaceholderText("")
        self._lecture_edit.setMinimumWidth(300)

        mode_label = QLabel("Mode:")
        mode_label.setFixedWidth(38)
        self._mode_combo = QComboBox()
        self._mode_combo.addItem("Hide All, Show One", "ao")
        self._mode_combo.addItem("Hide One, Show One", "oa")
        # apply config default
        default_mode = _cfg("occlusion_mode", "ao")
        idx = self._mode_combo.findData(default_mode)
        if idx >= 0:
            self._mode_combo.setCurrentIndex(idx)

        row2.addWidget(lec_label)
        row2.addWidget(self._lecture_edit, stretch=1)
        row2.addSpacing(12)
        row2.addWidget(mode_label)
        row2.addWidget(self._mode_combo)
        root.addLayout(row2)

        # ── Canvas ────────────────────────────────────────────────────────
        self._canvas = OcclusionCanvas()
        self._canvas.boxes_changed.connect(self._on_boxes_changed)
        scroll = QScrollArea()
        scroll.setWidget(self._canvas)
        scroll.setWidgetResizable(False)
        scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(scroll, stretch=1)

        # ── Group toolbar ─────────────────────────────────────────────────
        grp_bar = QHBoxLayout()

        self._group_btn = QPushButton("Group selected  (G)")
        self._group_btn.setToolTip(
            "Assign all selected (orange) boxes to one group.\n"
            "They will all be masked together on a single card."
        )
        self._group_btn.clicked.connect(self._canvas.group_selected)

        self._ungroup_btn = QPushButton("Ungroup  (U)")
        self._ungroup_btn.setToolTip("Remove group assignment from selected boxes.")
        self._ungroup_btn.clicked.connect(self._canvas.ungroup_selected)

        self._sel_all_btn = QPushButton("Select all  (Ctrl+A)")
        self._sel_all_btn.clicked.connect(self._canvas.select_all)

        self._group_status = QLabel("")
        self._group_status.setStyleSheet("color:#555; font-size:11px;")

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
        self._create_btn.setStyleSheet(
            "QPushButton{background:#4a90d9;color:white;font-weight:bold;"
            "padding:6px 18px;border-radius:4px;}"
            "QPushButton:hover{background:#357abd;}"
        )

        for w in (self._prev_btn, self._skip_btn, self._next_btn):
            bot.addWidget(w)
        bot.addStretch()
        bot.addWidget(self._create_btn)
        root.addLayout(bot)

        # ── Shortcuts ─────────────────────────────────────────────────────
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, self._next_page)
        QShortcut(QKeySequence(Qt.Key.Key_Left),  self, self._prev_page)
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, self._toggle_skip)
        QShortcut(QKeySequence("Ctrl+="), self, self._zoom_in)
        QShortcut(QKeySequence("Ctrl++"), self, self._zoom_in)
        QShortcut(QKeySequence("Ctrl+-"), self, self._zoom_out)

        self._update_controls()

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

        scale = float(_cfg("render_dpi_scale", 1.0))
        mat = fitz.Matrix(scale, scale)
        doc = fitz.open(path)
        self._pages = [page.get_pixmap(matrix=mat) for page in doc]
        doc.close()

        self._page_index = 0
        self._skipped.clear()
        self._boxes.clear()
        self._show_page()

    # ---------------------------------------------------------------- zoom --

    def _zoom_in(self):
        z = self._canvas.zoom()
        bigger = [s for s in _ZOOM_STEPS if s > z + 0.01]
        if bigger:
            self._canvas.set_zoom(bigger[0])
            self._zoom_label.setText(f"{int(bigger[0] * 100)}%")

    def _zoom_out(self):
        z = self._canvas.zoom()
        smaller = [s for s in _ZOOM_STEPS if s < z - 0.01]
        if smaller:
            self._canvas.set_zoom(smaller[-1])
            self._zoom_label.setText(f"{int(smaller[-1] * 100)}%")

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
        )
        self._update_controls()
        self._refresh_group_status()

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

    def _on_boxes_changed(self):
        self._refresh_group_status()

    def _refresh_group_status(self):
        if not self._canvas.has_image():
            self._group_status.setText("")
            return
        s = self._canvas.group_summary()
        parts = []
        if s["ungrouped"]:
            parts.append(f"{s['ungrouped']} individual")
        if s["groups"]:
            parts.append(f"{s['groups']} group{'s' if s['groups'] != 1 else ''}")
        total_cards = s["ungrouped"] + s["groups"]
        self._group_status.setText(
            f"{', '.join(parts)}  →  {total_cards} card{'s' if total_cards != 1 else ''} this slide"
            if parts else ""
        )

    def _update_controls(self):
        has = bool(self._pages)
        self._prev_btn.setEnabled(has and self._page_index > 0)
        self._next_btn.setEnabled(has and self._page_index < len(self._pages) - 1)
        self._skip_btn.setEnabled(has)
        self._create_btn.setEnabled(has)
        self._zoom_in_btn.setEnabled(has)
        self._zoom_out_btn.setEnabled(has)

        if has:
            idx = self._page_index
            n = len(self._pages)
            skipped = "  [SKIPPED]" if idx in self._skipped else ""
            self._page_label.setText(f"Slide {idx + 1} / {n}{skipped}")
            self._skip_btn.setStyleSheet(
                "color:#e07b39; font-weight:bold;" if idx in self._skipped else ""
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

        deck_name = _cfg("default_deck", "")
        deck_id = mw.col.decks.id(deck_name) if deck_name else mw.col.decks.selected()

        note_type_name = _cfg("note_type_name", "PDF Image Occlusion")
        mask_color = tuple(_cfg("mask_color", [46, 120, 217]))
        mask_opacity = int(_cfg("mask_opacity", 200))

        note_type = ensure_note_type(mw.col, note_type_name)
        total = create_occlusion_notes(
            mw.col, deck_id, note_type, to_create,
            mask_color=mask_color,
            mask_opacity=mask_opacity,
            lecture_name=lecture_name,
            total_slides=len(self._pages),
            mode=mode,
        )
        mw.col.reset()
        mw.reset()

        msg = f"Created {total} card(s) from {len(to_create)} slide(s)."
        if _cfg("close_after_creating", True):
            showInfo(msg)
            self.accept()
        else:
            showInfo(msg)
