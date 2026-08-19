"""
Cloze composer — a panel docked to the right of the slide, and the column of
cards it has already made docked to the left.

Not every slide wants occlusion. Text-heavy ones are better as a cloze card
with the slide kept underneath as the extra, and that is what this is for:
Ctrl+Shift+V opens the panel, you type the fact, wrap the bit to test, and
Ctrl+Return files it.

Both halves live inside the occlusion window rather than floating over it:
a separate window covers the very slide you are reading from, and has to be
moved out of the way by hand every time. On creation the card flies across
to the left column and stays there, truncated, as a record of what this
slide has already produced — per slide, and saved with the session.

Notes are plain Cloze notes (see card_builder.create_cloze_note), so they
survive this add-on being removed.
"""
import re
from typing import Optional

from aqt import mw
from aqt.qt import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
    QPlainTextEdit, QLineEdit, QFrame, QPixmap, QPainter, QPainterPath,
    QPropertyAnimation, QEasingCurve, QPoint, QRect, QRectF, QSize, Qt,
    QShortcut, QKeySequence, QTextCursor, QTimer, QScrollArea, QEvent,
    QFontMetrics, QGraphicsOpacityEffect, QSizePolicy, pyqtSignal,
)
from aqt.utils import showWarning

from . import card_builder


_PURPLE = "#836EAA"
PANEL_W = 306          # composer, right of the slide
COLUMN_W = 178         # made-cards column, left of the slide
_CHIP_CHARS = 78       # how much of a card's text a chip keeps

_QSS = f"""
#clozeTitle {{ font-size: 14px; font-weight: bold; }}
#clozeSlide, #clozeHint, #clozeDeck {{
    color: rgba(127,127,127,0.95); font-size: 11px;
}}
#clozeStatus {{ font-size: 11px; font-weight: bold; }}
QPlainTextEdit {{
    border: 1px solid rgba(127,127,127,0.30);
    border-radius: 8px; padding: 8px; font-size: 13px;
    background: rgba(127,127,127,0.06);
}}
QPlainTextEdit:focus {{ border: 1px solid {_PURPLE}; }}
QLineEdit {{
    border: 1px solid rgba(127,127,127,0.30);
    border-radius: 8px; padding: 5px 8px; background: transparent;
}}
QLineEdit:focus {{ border: 1px solid {_PURPLE}; }}
#slideCard {{
    border: 1px solid rgba(127,127,127,0.25); border-radius: 10px;
}}
#slideCard:hover {{ border-color: {_PURPLE}; }}
QCheckBox {{ font-size: 12px; }}
QPushButton#clozeAdd {{
    background: {_PURPLE}; color: white; font-weight: bold;
    padding: 6px 16px; border-radius: 7px; border: none;
}}
QPushButton#clozeAdd:hover {{ background: #75619b; }}
QPushButton#clozeAdd:pressed {{ background: #67548c; }}
"""

_CHIP_QSS = f"""
QFrame#clozeCard {{
    background: rgba(131,110,170,0.11);
    border: 1px solid rgba(127,127,127,0.20);
    border-left: 3px solid {_PURPLE};
    border-radius: 7px;
}}
QLabel {{ font-size: 11px; }}
"""


def _native(keys: str) -> str:
    return QKeySequence(keys).toString(QKeySequence.SequenceFormat.NativeText)


def _plain(text: str) -> str:
    """A cloze's text as it reads, for the chip: markers out, spacing tidied."""
    out = re.sub(r"\{\{c\d+::(.*?)(?:::[^}]*)?\}\}", r"\1", text or "",
                 flags=re.DOTALL)
    return re.sub(r"\s+", " ", out).strip()


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


# ------------------------------------------------------------------- column

class ClozeChip(QFrame):
    """One made card, truncated. The full text is on the tooltip."""

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setObjectName("clozeCard")
        self.setStyleSheet(_CHIP_QSS)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        full = _plain(text)
        short = full if len(full) <= _CHIP_CHARS else full[:_CHIP_CHARS - 1] + "…"
        label = QLabel(short)
        label.setWordWrap(True)
        # A wrapped QLabel only knows its height once it knows its width, and
        # a QVBoxLayout asked to size one ends up handing out the leftover
        # space between the chips instead of below them. The column is a
        # fixed width, so settle both here and let the chip be Fixed-height:
        # then the layout has nothing to guess at and the stretch gets it all.
        inner = COLUMN_W - 22
        label.setFixedWidth(inner)
        label.setFixedHeight(label.heightForWidth(inner))
        lay.addWidget(label)
        self.setSizePolicy(QSizePolicy.Policy.Preferred,
                           QSizePolicy.Policy.Fixed)
        self.setToolTip(full)


class ClozeColumn(QWidget):
    """The cards made from the slide on screen, newest last."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(COLUMN_W)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        self._count = QLabel("")
        self._count.setObjectName("clozeHint")
        self._count.setStyleSheet("color:rgba(127,127,127,0.95);font-size:11px;")
        outer.addWidget(self._count)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        inner = QWidget()
        self._list = QVBoxLayout(inner)
        self._list.setContentsMargins(0, 0, 0, 0)
        self._list.setSpacing(6)
        self._list.addStretch()
        self._scroll.setWidget(inner)
        outer.addWidget(self._scroll, stretch=1)

    def set_cards(self, cards: list):
        """Show the cards for one slide (replacing whatever was shown)."""
        while self._list.count() > 1:
            item = self._list.takeAt(0)
            w = item.widget()
            if w is not None:
                # unparent now rather than only scheduling the delete: this
                # runs inside a modal dialog's event loop, where a widget
                # waiting on deleteLater would keep painting over the column
                w.setParent(None)
                w.deleteLater()
        for card in cards:
            self._list.insertWidget(self._list.count() - 1,
                                    ClozeChip(card["text"]))
        n = len(cards)
        self._count.setText(f"{n} cloze card{'s' if n != 1 else ''}")
        self.setVisible(n > 0)

    def add_card(self, card: dict) -> ClozeChip:
        """Append one card and hand back its chip (hidden, for the fly-in)."""
        chip = ClozeChip(card["text"])
        effect = QGraphicsOpacityEffect(chip)
        effect.setOpacity(0.0)
        chip.setGraphicsEffect(effect)
        self._list.insertWidget(self._list.count() - 1, chip)
        n = self._list.count() - 1
        self._count.setText(f"{n} cloze card{'s' if n != 1 else ''}")
        self.setVisible(True)
        return chip

    def scroll_to_end(self):
        bar = self._scroll.verticalScrollBar()
        bar.setValue(bar.maximum())


# ----------------------------------------------------------------- composer

class ClozeComposer(QWidget):
    """Right-hand panel; `owner` is the PDFOcclusionDialog it reads slides from."""

    card_created = pyqtSignal(dict)

    def __init__(self, owner):
        super().__init__(owner)
        self._owner = owner
        self._added = 0
        self.setFixedWidth(PANEL_W)
        self.setStyleSheet(_QSS)
        self._build_ui()

    # ------------------------------------------------------------------ ui

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 0, 0, 0)
        root.setSpacing(8)

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
        self._text.setMinimumHeight(120)
        root.addWidget(self._text, stretch=1)

        hint = QLabel(f"{_native('Ctrl+Shift+C')} wraps the selection")
        hint.setObjectName("clozeHint")
        hint.setToolTip(
            f"{_native('Ctrl+Shift+C')} wraps the selection in a new cloze\n"
            f"{_native('Ctrl+Alt+Shift+C')} reuses the number already used")
        root.addWidget(hint)

        self._extra = QLineEdit()
        self._extra.setPlaceholderText("Extra")
        self._extra.installEventFilter(self)   # Return adds, never "Create All"
        root.addWidget(self._extra)

        # ── the slide, as the card's extra ────────────────────────────────
        card = QFrame()
        card.setObjectName("slideCard")
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(10, 10, 10, 8)
        cl.setSpacing(7)
        self._thumb = QLabel()
        self._thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._slide_check = QCheckBox("Add slide as extra")
        self._slide_check.setChecked(True)
        self._slide_check.toggled.connect(self._sync_thumb_state)
        self._caption = QLabel("")
        self._caption.setObjectName("clozeHint")
        self._caption.setStyleSheet("color:rgba(127,127,127,0.95);font-size:11px;")
        cl.addWidget(self._thumb)
        cl.addWidget(self._slide_check)
        cl.addWidget(self._caption)
        # the whole card is the hit target, not just the checkbox
        card.mousePressEvent = lambda _e: self._slide_check.toggle()
        root.addWidget(card)

        self._deck_label = QLabel("")
        self._deck_label.setObjectName("clozeDeck")
        root.addWidget(self._deck_label)

        foot = QHBoxLayout()
        self._status = QLabel("")
        self._status.setObjectName("clozeStatus")
        self._status.setWordWrap(True)
        self._add_btn = QPushButton(f"Add Card  {_native('Ctrl+Return')}")
        self._add_btn.setObjectName("clozeAdd")
        self._add_btn.setAutoDefault(False)
        self._add_btn.setDefault(False)
        self._add_btn.clicked.connect(self._add)
        foot.addWidget(self._status, stretch=1)
        foot.addWidget(self._add_btn)
        root.addLayout(foot)

        # Scoped to the panel: they must not fire while the canvas has focus,
        # and Ctrl+Return especially must never reach the dialog's default
        # button (which is Create All Cards).
        for keys, fn in (("Ctrl+Return", self._add),
                         ("Ctrl+Enter", self._add),
                         ("Ctrl+Shift+C", lambda: self._wrap_cloze(False)),
                         ("Ctrl+Alt+Shift+C", lambda: self._wrap_cloze(True))):
            sc = QShortcut(QKeySequence(keys), self, fn)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)

    def eventFilter(self, obj, event):
        if (obj is self._extra and event.type() == QEvent.Type.KeyPress
                and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)):
            self._add()
            return True
        return super().eventFilter(obj, event)

    def focus_editor(self):
        self._text.setFocus()

    # -------------------------------------------------------------- slide

    def refresh_slide(self):
        """Re-aim at whatever slide the main window is showing."""
        info = self._owner.cloze_slide()
        if not info:
            self._slide_label.setText("")
            self._thumb.clear()
            self._caption.setText("")
            return
        self._slide_label.setText(info["label"])
        self._caption.setText(info["caption"])
        width = PANEL_W - 46
        pm = QPixmap.fromImage(info["image"]).scaled(
            QSize(width, width), Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        self._thumb.setPixmap(_rounded(pm))
        self._sync_thumb_state()

        deck = self._owner.cloze_deck_name() or "Current deck"
        self._deck_label.setToolTip(deck)
        self._deck_label.setText("→ " + QFontMetrics(self._deck_label.font())
                                 .elidedText(deck, Qt.TextElideMode.ElideLeft,
                                             PANEL_W - 40))

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
        self.card_created.emit({
            "nid": int(result["note_id"]),
            "text": text,
            "page": info["page"] if info else -1,
        })

    # ------------------------------------------------------------ fly-in

    def source_rect(self) -> QRect:
        """Where a new card should appear to come from — the editor itself."""
        return QRect(self._text.mapTo(self.window(), QPoint(0, 0)),
                     self._text.size())


def fly_to_chip(host: QWidget, text: str, start: QRect, chip: ClozeChip):
    """Send a copy of the new card from the composer across to its chip.

    The real chip is already in the column at zero opacity holding its place
    in the layout; a throwaway copy does the travelling and hands over on
    landing. Returns the animation — the caller has to keep a reference to
    it or Qt collects it mid-flight.
    """
    end = QRect(chip.mapTo(host, QPoint(0, 0)), chip.size())
    ghost = ClozeChip(text, host)
    ghost.setGeometry(start)
    ghost.show()
    ghost.raise_()

    anim = QPropertyAnimation(ghost, b"geometry", host)
    anim.setDuration(320)
    anim.setStartValue(start)
    anim.setEndValue(end)
    anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

    def landed():
        # unparent before scheduling the delete — inside a modal dialog a
        # widget still waiting on deleteLater keeps painting where it landed
        ghost.setParent(None)
        ghost.deleteLater()
        # drop the effect rather than just turning it up: a widget wearing a
        # QGraphicsEffect is painted through it, which offsets it under
        # QWidget.render(), and it has nothing left to do once it has landed
        chip.setGraphicsEffect(None)

    anim.finished.connect(landed)
    anim.start()
    return anim
