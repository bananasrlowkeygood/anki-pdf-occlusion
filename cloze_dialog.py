"""
Cloze composer — a panel docked to the right of the slide, and the column of
cards it has already made docked to the left.

Not every slide wants occlusion. Text-heavy ones are better as a cloze card
with the slide kept underneath as the extra, and that is what this is for:
Ctrl+Shift+V opens the panel, you type the fact, wrap the bit to test, and
Ctrl+Return files it.

The panel takes the top half of the window's height and no more, so the
Add button never crowds Create All Cards below it. Both halves live inside
the occlusion window rather than floating over it:
a separate window covers the very slide you are reading from, and has to be
moved out of the way by hand every time. On creation the card flies across
to the left column and stays there, truncated, as a record of what this
slide has already produced — per slide, and saved with the session.

Notes are plain Cloze notes, written by Create All Cards along with the
occlusion ones (see card_builder.create_cloze_notes) — nothing here touches
the collection — so they survive this add-on being removed.
"""
import re
import uuid
from typing import Optional

from aqt.qt import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
    QPlainTextEdit, QLineEdit, QFrame, QPropertyAnimation, QEasingCurve,
    QPoint, QRect, Qt, QShortcut, QKeySequence, QTextCursor, QTimer,
    QScrollArea, QEvent, QGraphicsOpacityEffect, QSizePolicy, QMenu,
    QApplication, pyqtSignal,
)
from . import card_builder


_PURPLE = "#836EAA"
PANEL_W = 306          # composer, right of the slide
COLUMN_W = 178         # made-cards column, left of the slide
_CHIP_CHARS = 78       # how much of a card's text a chip keeps

_QSS = f"""
#clozeTitle {{ font-size: 14px; font-weight: bold; }}
#clozeSlide, #clozeHint {{
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
QLabel#chipCount {{ color: rgba(127,127,127,0.85); font-size: 10px; }}
"""


def _native(keys: str) -> str:
    return QKeySequence(keys).toString(QKeySequence.SequenceFormat.NativeText)


def _plain(text: str) -> str:
    """A cloze's text as it reads, for the chip: markers out, spacing tidied."""
    out = re.sub(r"\{\{c\d+::(.*?)(?:::[^}]*)?\}\}", r"\1", text or "",
                 flags=re.DOTALL)
    return re.sub(r"\s+", " ", out).strip()


# ------------------------------------------------------------------- column

class ClozeChip(QFrame):
    """One made card, truncated. The full text is on the tooltip.

    Click to load it back into the composer, right-click for the rest.
    `entry` is the stored card record, or None for the throwaway copy that
    does the flying.
    """

    activated = pyqtSignal(object)
    delete_requested = pyqtSignal(object)

    def __init__(self, text: str, parent=None, entry: Optional[dict] = None):
        super().__init__(parent)
        self.entry = entry
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

        n = card_builder.cloze_card_count(text)
        count = QLabel(f"{n} card{'s' if n != 1 else ''}")
        count.setObjectName("chipCount")
        count.setAlignment(Qt.AlignmentFlag.AlignRight)
        lay.addWidget(count)

        self.setSizePolicy(QSizePolicy.Policy.Preferred,
                           QSizePolicy.Policy.Fixed)
        self._full = full
        self.setToolTip(full if entry is None else
                        full + "\n\nClick to edit · right-click for more")
        if entry is not None:
            self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event):
        if (self.entry is not None
                and event.button() == Qt.MouseButton.LeftButton
                and self.rect().contains(event.pos())):
            self.activated.emit(self.entry)
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        if self.entry is None:
            return
        menu = QMenu(self)
        menu.addAction("Edit…", lambda: self.activated.emit(self.entry))
        menu.addAction(
            "Copy Text",
            lambda: QApplication.clipboard().setText(self.entry["text"]))
        menu.addSeparator()
        menu.addAction("Delete Card…",
                       lambda: self.delete_requested.emit(self.entry))
        menu.exec(event.globalPos())


class ClozeColumn(QWidget):
    """The cards made from the slide on screen, newest last."""

    card_activated = pyqtSignal(object)
    card_delete_requested = pyqtSignal(object)

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
            self._list.insertWidget(self._list.count() - 1, self._chip(card))
        n = len(cards)
        self._count.setText(f"{n} cloze card{'s' if n != 1 else ''}")
        self.setVisible(n > 0)

    def _chip(self, card: dict) -> ClozeChip:
        chip = ClozeChip(card["text"], entry=card)
        chip.activated.connect(self.card_activated)
        chip.delete_requested.connect(self.card_delete_requested)
        return chip

    def add_card(self, card: dict) -> ClozeChip:
        """Append one card and hand back its chip (hidden, for the fly-in)."""
        chip = self._chip(card)
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
    card_updated = pyqtSignal(dict)

    def __init__(self, owner):
        super().__init__(owner)
        self._owner = owner
        self._added = 0
        self._editing: Optional[dict] = None
        self.setFixedWidth(PANEL_W)
        self.setStyleSheet(_QSS)
        self._build_ui()

    # ------------------------------------------------------------------ ui

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 0, 0, 0)
        root.setSpacing(8)

        head = QHBoxLayout()
        self._title = QLabel("Cloze")
        self._title.setObjectName("clozeTitle")
        self._slide_label = QLabel("")
        self._slide_label.setObjectName("clozeSlide")
        head.addWidget(self._title)
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

        self._slide_check = QCheckBox("Add slide as extra")
        self._slide_check.setChecked(True)
        self._slide_check.setToolTip(
            "File this slide under the answer, captioned with the lecture "
            "and slide number")
        root.addWidget(self._slide_check)

        foot = QHBoxLayout()
        self._status = QLabel("")
        self._status.setObjectName("clozeStatus")
        self._status.setWordWrap(True)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setAutoDefault(False)
        self._cancel_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._cancel_btn.setVisible(False)
        self._cancel_btn.clicked.connect(self.reset_editing)
        self._add_btn = QPushButton(f"Add Card  {_native('Ctrl+Return')}")
        self._add_btn.setObjectName("clozeAdd")
        self._add_btn.setAutoDefault(False)
        self._add_btn.setDefault(False)
        self._add_btn.clicked.connect(self._add)
        foot.addWidget(self._status, stretch=1)
        foot.addWidget(self._cancel_btn)
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

    # ------------------------------------------------------------- editing

    def editing_uid(self) -> Optional[str]:
        """Which stored card is in the panel, if any. Records are keyed by
        their own uid, not a note id — they may not have a note yet."""
        return self._editing.get("uid") if self._editing else None

    def load_card(self, entry: dict):
        """Put an already-made card back in the panel to be rewritten."""
        self._editing = dict(entry)
        self._text.setPlainText(entry.get("text", ""))
        self._extra.setText(entry.get("extra", ""))
        self._slide_check.setChecked(bool(entry.get("slide", True)))
        self._title.setText("Editing card")
        self._add_btn.setText(f"Save  {_native('Ctrl+Return')}")
        self._cancel_btn.setVisible(True)
        self.refresh_slide()
        self._text.setFocus()
        self._text.moveCursor(QTextCursor.MoveOperation.End)

    def reset_editing(self):
        """Back to composing a new card."""
        self._editing = None
        self._text.clear()
        self._extra.clear()
        self._slide_check.setChecked(True)
        self._title.setText("Cloze")
        self._add_btn.setText(f"Add Card  {_native('Ctrl+Return')}")
        self._cancel_btn.setVisible(False)
        self.refresh_slide()
        self._text.setFocus()

    def _slide_info(self) -> Optional[dict]:
        """The slide this card belongs to — the one being edited keeps its
        own, so flipping slides mid-edit can't swap the image under it."""
        page = self._editing.get("page") if self._editing else None
        return self._owner.cloze_slide(page)

    # -------------------------------------------------------------- slide

    def refresh_slide(self):
        """Re-aim at whatever slide the main window is showing."""
        info = self._slide_info()
        if not info:
            self._slide_label.setText("")
            return
        # The panel says which slide only while editing, where the card's
        # slide can differ from the one on screen. Otherwise the window's own
        # counter, the thumbnail and the deck picker already say it.
        self._slide_label.setText(
            f"from {info['label']}" if self._editing else "")

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
        """Put the card on the pile. Nothing reaches the collection here —
        cloze cards are written by Create All Cards along with everything
        else, so a session can be closed, resumed and rewritten first."""
        text = self._text.toPlainText().strip()
        if not text:
            self._say("Nothing to add yet.", ok=False)
            return
        if not card_builder.has_cloze(text):
            self._say(f"Wrap something first ({_native('Ctrl+Shift+C')}).",
                      ok=False)
            return

        info = self._slide_info()
        editing = self._editing
        card = {
            "uid": editing.get("uid") if editing else uuid.uuid4().hex,
            "nid": editing.get("nid") if editing else None,
            "text": text,
            "extra": self._extra.text(),
            "slide": bool(self._slide_check.isChecked()),
            "page": (editing.get("page") if editing
                     else (info["page"] if info else -1)),
        }
        if editing:
            self.reset_editing()
            self._say("Saved")
            self.card_updated.emit(card)
        else:
            self._added += 1
            self._text.clear()
            self._extra.clear()
            self._text.setFocus()
            self._say(f"Added · {self._added} this session")
            self.card_created.emit(card)

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
