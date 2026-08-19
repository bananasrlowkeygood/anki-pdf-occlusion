"""
Cloze composer — a small, always-available panel beside the occlusion window.

Not every slide wants occlusion. Text-heavy ones are better as a cloze card
with the slide kept underneath as the extra, and that is what this is for:
open it with Ctrl+Shift+V, type the fact, wrap the bit to test, hit Add.

Two behaviours worth knowing:
  - The slide shown in the thumbnail is always the slide the main window is
    on, so flipping slides there re-aims the composer without touching it.
  - After each card the panel hops to the other side of the screen. It is a
    floating window over your slides; parking it where it just was would put
    it over whatever you are about to read next.

Notes are plain Cloze notes (see card_builder.create_cloze_note), so they
survive this add-on being removed.
"""
from typing import Optional

from aqt import mw
from aqt.qt import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
    QPlainTextEdit, QLineEdit, QFrame, QPixmap, QPainter, QPainterPath,
    QPropertyAnimation, QEasingCurve, QPoint, QRect, QRectF, QSize, Qt,
    QShortcut, QKeySequence, QTextCursor, QTimer, QGuiApplication,
)
from aqt.utils import showWarning

from . import card_builder


_PURPLE = "#836EAA"
_THUMB_W = 148

_QSS = f"""
QDialog {{ background: palette(window); }}
#clozeTitle {{ font-size: 15px; font-weight: bold; }}
#clozeSlide {{ color: rgba(127,127,127,0.95); font-size: 11px; }}
#clozeHint  {{ color: rgba(127,127,127,0.8); font-size: 11px; }}
#clozeDeck  {{ color: rgba(127,127,127,0.95); font-size: 11px; }}
#clozeStatus {{ font-size: 11px; font-weight: bold; }}
QPlainTextEdit {{
    border: 1px solid rgba(127,127,127,0.30);
    border-radius: 8px; padding: 9px; font-size: 14px;
    background: rgba(127,127,127,0.05);
}}
QPlainTextEdit:focus {{ border: 1px solid {_PURPLE}; }}
QLineEdit {{
    border: 1px solid rgba(127,127,127,0.30);
    border-radius: 8px; padding: 6px 9px;
    background: transparent;
}}
QLineEdit:focus {{ border: 1px solid {_PURPLE}; }}
#slideCard {{
    border: 1px solid rgba(127,127,127,0.25);
    border-radius: 10px;
}}
#slideCard:hover {{ border-color: {_PURPLE}; }}
QCheckBox {{ font-size: 12px; }}
QPushButton#clozeAdd {{
    background: {_PURPLE}; color: white; font-weight: bold;
    padding: 7px 20px; border-radius: 7px; border: none;
}}
QPushButton#clozeAdd:hover {{ background: #75619b; }}
QPushButton#clozeAdd:pressed {{ background: #67548c; }}
QPushButton#clozeChip {{
    padding: 3px 10px; border-radius: 6px; font-size: 11px;
    border: 1px solid rgba(127,127,127,0.35); background: transparent;
}}
QPushButton#clozeChip:hover {{ background: rgba(131,110,170,0.16); }}
"""


def _native(keys: str) -> str:
    return QKeySequence(keys).toString(QKeySequence.SequenceFormat.NativeText)


def _rounded(pm: QPixmap, radius: int = 6) -> QPixmap:
    """Round a thumbnail's corners so the slide sits in the panel as a card."""
    out = QPixmap(pm.size())
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, pm.width(), pm.height()), radius, radius)
    p.setClipPath(path)
    p.drawPixmap(0, 0, pm)
    p.end()
    return out


class ClozeComposer(QDialog):
    """Non-modal panel; `owner` is the PDFOcclusionDialog it reads slides from."""

    def __init__(self, owner):
        super().__init__(owner)
        self._owner = owner
        self._added = 0
        self._anim: Optional[QPropertyAnimation] = None
        self.setWindowTitle("Cloze")
        self.setWindowFlag(Qt.WindowType.Tool, True)
        self.setModal(False)
        self.setStyleSheet(_QSS)
        self.setMinimumWidth(430)

        self._build_ui()
        self._place_initial()

    # ------------------------------------------------------------------ ui

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        head = QHBoxLayout()
        title = QLabel("Cloze")
        title.setObjectName("clozeTitle")
        self._slide_label = QLabel("")
        self._slide_label.setObjectName("clozeSlide")
        head.addWidget(title)
        head.addStretch()
        head.addWidget(self._slide_label)
        root.addLayout(head)

        self._text = QPlainTextEdit()
        self._text.setPlaceholderText(
            "The {{c1::hippocampus}} is required for forming new memories."
        )
        self._text.setMinimumHeight(132)
        root.addWidget(self._text)

        tools = QHBoxLayout()
        tools.setSpacing(6)
        chip = QPushButton("Cloze")
        chip.setObjectName("clozeChip")
        chip.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        chip.setToolTip("Wrap the selection in a new cloze deletion")
        chip.clicked.connect(lambda: self._wrap_cloze(same=False))
        same = QPushButton("Same #")
        same.setObjectName("clozeChip")
        same.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        same.setToolTip("Wrap the selection in the cloze number already used")
        same.clicked.connect(lambda: self._wrap_cloze(same=True))
        hint = QLabel(f"{_native('Ctrl+Shift+C')} · select text, then wrap")
        hint.setObjectName("clozeHint")
        tools.addWidget(chip)
        tools.addWidget(same)
        tools.addSpacing(6)
        tools.addWidget(hint)
        tools.addStretch()
        root.addLayout(tools)

        self._extra = QLineEdit()
        self._extra.setPlaceholderText("Extra (optional) — shown under the answer")
        root.addWidget(self._extra)

        # ── the slide, as the card's extra ────────────────────────────────
        card = QFrame()
        card.setObjectName("slideCard")
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        cl = QHBoxLayout(card)
        cl.setContentsMargins(10, 10, 10, 10)
        cl.setSpacing(12)
        self._thumb = QLabel()
        self._thumb.setFixedWidth(_THUMB_W)
        self._thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._slide_check = QCheckBox("Add slide as extra")
        self._slide_check.setChecked(True)
        self._slide_check.toggled.connect(self._sync_thumb_state)
        self._caption = QLabel("")
        self._caption.setObjectName("clozeHint")
        self._caption.setWordWrap(True)
        right = QVBoxLayout()
        right.setSpacing(2)
        right.addStretch()
        right.addWidget(self._slide_check)
        right.addWidget(self._caption)
        right.addStretch()
        cl.addWidget(self._thumb)
        cl.addLayout(right, stretch=1)
        # the whole card is the hit target, not just the checkbox
        card.mousePressEvent = lambda _e: self._slide_check.toggle()
        root.addWidget(card)

        foot = QHBoxLayout()
        self._deck_label = QLabel("")
        self._deck_label.setObjectName("clozeDeck")
        self._status = QLabel("")
        self._status.setObjectName("clozeStatus")
        self._add_btn = QPushButton(f"Add Card  {_native('Ctrl+Return')}")
        self._add_btn.setObjectName("clozeAdd")
        self._add_btn.clicked.connect(self._add)
        foot.addWidget(self._deck_label)
        foot.addStretch()
        foot.addWidget(self._status)
        foot.addSpacing(8)
        foot.addWidget(self._add_btn)
        root.addLayout(foot)

        QShortcut(QKeySequence("Ctrl+Return"), self, self._add)
        QShortcut(QKeySequence("Ctrl+Enter"), self, self._add)
        QShortcut(QKeySequence("Ctrl+Shift+C"), self,
                  lambda: self._wrap_cloze(same=False))
        QShortcut(QKeySequence("Ctrl+Alt+Shift+C"), self,
                  lambda: self._wrap_cloze(same=True))

    # -------------------------------------------------------------- slide

    def refresh_slide(self):
        """Re-aim at whatever slide the main window is showing."""
        info = self._owner.cloze_slide()
        if not info:
            self._slide_label.setText("")
            self._thumb.clear()
            self._caption.setText("")
            return
        img, caption, label = info["image"], info["caption"], info["label"]
        self._slide_label.setText(label)
        self._caption.setText(caption)
        pm = QPixmap.fromImage(img).scaled(
            QSize(_THUMB_W, _THUMB_W), Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        self._thumb.setPixmap(_rounded(pm))
        self._sync_thumb_state()
        deck = self._owner.cloze_deck_name() or "Current deck"
        self._deck_label.setText(f"→ {deck}")

    def _sync_thumb_state(self):
        # unchecked reads as "this slide is not coming along" — dim it
        self._thumb.setEnabled(self._slide_check.isChecked())

    # -------------------------------------------------------------- cloze

    def _wrap_cloze(self, same: bool):
        cur = self._text.textCursor()
        if not cur.hasSelection():
            cur.select(QTextCursor.SelectionType.WordUnderCursor)
        # QTextCursor hands back U+2029 for line breaks in a selection
        sel = cur.selectedText().replace("\u2029", "\n")
        if not sel.strip():
            self._say("Select the text to hide first.", ok=False)
            return
        n = card_builder.next_cloze_number(self._text.toPlainText())
        if same:
            n = max(1, n - 1)
        cur.insertText("{{c%d::%s}}" % (n, sel))
        self._text.setTextCursor(cur)
        self._text.setFocus()

    # ---------------------------------------------------------------- add

    def _say(self, msg: str, ok: bool = True):
        self._status.setText(msg)
        self._status.setStyleSheet(
            f"color:{_PURPLE};" if ok else "color:#c0392b;")
        QTimer.singleShot(2600, lambda: self._status.setText("")
                          if self._status.text() == msg else None)

    def _add(self):
        text = self._text.toPlainText().strip()
        if not text:
            self._say("Nothing to add yet.", ok=False)
            return
        if not card_builder.has_cloze(text):
            self._say(f"Wrap something first ({_native('Ctrl+Shift+C')}).",
                      ok=False)
            return

        note_type = card_builder.cloze_note_type(mw.col)
        if note_type is None:
            showWarning(
                "No cloze note type found in this collection.\n\n"
                "Add Anki's stock “Cloze” note type "
                "(Tools → Manage Note Types → Add) and try again."
            )
            return

        info = self._owner.cloze_slide()
        deck_name = self._owner.cloze_deck_name()
        deck_id = (mw.col.decks.id(deck_name) if deck_name
                   else mw.col.decks.selected())

        use_slide = self._slide_check.isChecked() and info is not None
        try:
            result = card_builder.create_cloze_note(
                mw.col, deck_id, note_type, text,
                extra=self._extra.text(),
                image=info["image"] if use_slide else None,
                image_fname=info["image_fname"] if use_slide else "",
                caption=info["caption"] if use_slide else "",
            )
        except Exception as exc:
            showWarning(f"Could not create the cloze card:\n{exc}")
            return

        if use_slide and result["image_fname"]:
            self._owner.remember_cloze_image(info["page"], result["image_fname"])

        self._added += 1
        self._text.clear()
        self._extra.clear()
        self._text.setFocus()
        self._say(f"Added · {self._added} this session")
        self._hop()

    # --------------------------------------------------------- placement

    def _screen_area(self) -> QRect:
        screen = self.screen() or QGuiApplication.primaryScreen()
        return screen.availableGeometry()

    def _place_initial(self):
        """Open on the emptier side of the screen: whichever half the main
        window is not sitting in."""
        area = self._screen_area()
        self.adjustSize()
        g = self.frameGeometry()
        owner_c = self._owner.frameGeometry().center()
        margin = 28
        x = (area.left() + margin
             if owner_c.x() > area.center().x()
             else area.right() - g.width() - margin)
        y = area.center().y() - g.height() // 2
        self.move(int(x), int(max(area.top() + margin, y)))

    def _hop(self):
        """Move to the other side of the screen after a card is filed."""
        area = self._screen_area()
        g = self.frameGeometry()
        margin = 28
        on_left = g.center().x() < area.center().x()
        x = (area.right() - g.width() - margin) if on_left else area.left() + margin
        y = min(max(g.y(), area.top() + margin),
                area.bottom() - g.height() - margin)
        target = QPoint(int(x), int(y))

        self._anim = QPropertyAnimation(self, b"pos", self)
        self._anim.setDuration(260)
        self._anim.setStartValue(self.pos())
        self._anim.setEndValue(target)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.start()

    # ------------------------------------------------------------- window

    def closeEvent(self, event):
        # One refresh for the whole burst — resetting the main window after
        # every card would fight for focus mid-typing.
        if self._added:
            mw.reset()
            self._added = 0
        super().closeEvent(event)
