# <img alt="pdf-occlusion-icon" src="icon.svg" height="60"> ‎ ‎ ‎PDF Occlusion

[![Anki](https://img.shields.io/badge/Anki-23.10%2B-836EAA?style=flat-square)](https://apps.ankiweb.net)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-836EAA?style=flat-square)](https://github.com/bananasrlowkeygood/anki-pdf-occlusion)
[![License](https://img.shields.io/badge/license-MIT-836EAA?style=flat-square)](LICENSE)

**PDF Occlusion** makes image occlusion cards straight from PDF lecture slides — no importing one image at a time. Open a PDF (or several), draw boxes over what you want to memorize, and generate cards in bulk.

- **Detect** — drag over part of a slide and it boxes it for you: a ruled table cell by cell, anything else word by word, including picture-only slides via macOS text recognition
- **Cloze too** — text-heavy slides are better as cloze cards; write them in the same window, with the slide kept as the extra
- **Edit after the fact** — reopen a card from Browse and drag its boxes; *Create All Cards* updates the existing cards in place, review history intact
- **Occlusion mode per PDF, per slide, or per box** — mix *Hide All, Show One* and *Hide One, Show One* freely
- **Grouping, box labels, notes, text written onto slides, rotation, marquee select, pinch-to-zoom**

## Contents

[Installation](#installation) · [Quick Start](#quick-start) · [Editing Tools](#editing-tools) · [Cloze Cards](#cloze-cards) · [Editing Cards Later](#editing-cards-later) · [Occlusion Modes](#occlusion-modes) · [Reviewing](#reviewing) · [Shortcuts](#keyboard-shortcuts) · [Configuration](#configuration)

## Installation

Install with the add-on code `783821131` and restart Anki.

> [!NOTE]
> The PDF renderer ([pypdfium2](https://github.com/pypdfium2-team/pypdfium2)) is bundled for macOS (Apple Silicon and Intel), Windows (x64) and Linux (x64, ARM). Nothing else to install, on any Anki Python version.

## Quick Start

1. `Tools → PDF Occlusion`, or the card editor's toolbar button (`Ctrl+Shift+P`).
2. `Open PDF` — one file or several. The arrow beside it lists recent sessions.
3. Drag boxes over what you want to memorize, or press `F` and drag over a region to have them placed for you.
4. *(Optional)* Select several boxes and press `G` to make them one card; double-click a box to attach a note.
5. Pick a deck (type a new name to create one) and click `Create All Cards`.

## Editing Tools

- **Draw** (`D`) — drag on empty space for a new box; `Shift`-drag joins it to the selection's group, so a multi-part card can be sketched in one pass.
- **Select** (`V`) — marquee-select; `Shift` adds to the selection.
- **Detect** (`F`) — press it, then **drag over the part of the slide to scan**; everything outside dims. It never runs on a whole slide, since most carry a title and a footer not worth boxing.
  - **Ruled tables** are boxed **cell by cell** from the table's own borders; merged cells come out as one box.
  - Everything else is found **word by word** and merged back up inside your region only, so a phrase becomes one box hugging the text while two columns sharing a line stay two.
  - A region with no text in the PDF — a scanned deck, a screenshot of a table — falls back to **reading the pixels** via macOS text recognition. Windows and Linux report nothing found there.
  - Rescanning the same area won't stack duplicates.
- **Text** (`T`) — typed in place, no dialogs. **Drag on the slide** to write onto it: the text joins the picture, so it shows on every card from that slide and a box can hide it like anything the lecturer printed there. **Click a box** instead to label its mask — the label shows only while that box is the one being asked about, prompting the kind of answer wanted ("enzyme", "artery") without giving it away.
- **Move / resize** — drag a box or any corner handle; `Shift`-drag a grouped box moves its whole group.
- **Rotate** — hover just outside a corner until the cursor curves, then drag; `Shift` snaps to 15°. Right-click → `Reset Rotation` to straighten.
- **Peek** (`P`) — see through the masks for a moment. Useful with `editing_mask_opacity` raised: at `255` a box hides its content the instant you draw it, which turns building the deck into a recall exercise.
- Grouping, mode overrides, notes and the rest are also in the right-click menu.

## Cloze Cards

Not every slide wants occlusion. `Ctrl+Shift+V` opens a composer beside the slide: type the fact, `Ctrl+Shift+C` wraps the selection as a cloze (`Ctrl+Alt+Shift+C` reuses the last number), `Ctrl+Return` files it. `Slide Text` pastes the slide's own text to edit down. Filed cards stack as chips beside the slide and are written by `Create All Cards` alongside the occlusion ones — plain Anki **Cloze** notes, so they outlive this add-on.

## Editing Cards Later

Work saves automatically per PDF — boxes, slide text, mode overrides, lecture name. Reopen the PDF and you're offered to resume.

**From Browse:** click a PDF Occlusion card and press `Ctrl+Shift+P`; the window opens on that slide with that card's box selected, ready to drag. If the session is gone — cleared, or made on another machine — it is rebuilt from the cards themselves, which carry their own boxes.

Because each box remembers the card it made, **`Create All Cards` is safe to run again**: existing cards are updated in place with scheduling and review history untouched, new boxes become new cards, and deleted boxes prompt before their cards go.

## Occlusion Modes

Three levels, most specific wins:

1. **Default** — the `occlusion_mode` config value
2. **Per slide** — the `This slide` picker above the canvas
3. **Per box / group** — right-click → `Occlusion Mode`

Boxes with an override show a small `AO` / `OA` badge in the editor.

## Reviewing

- **`g`** reveals the whole slide (press again to put the masks back) — the `Show All` button on the answer, without the mouse. Rebind or disable it with `show_all_shortcut`.
- **`Notes` / `Slides`** buttons on the answer open the source PDFs the card was cut from. They appear only when the card has that PDF attached, and are inert on mobile — the paths point at your own machine.

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `←` / `→` | Slides (nudges selected boxes instead) |
| `PgUp` / `PgDn` | Slides (always) |
| `D` / `V` / `T` | Draw / Select / Text tool |
| `F` | Detect, then drag over the area to scan |
| `P` | Peek through the masks |
| `G` / `U` | Group / ungroup selection |
| `N` | Note on selected box(es) |
| `Del` / `Backspace` | Remove selected box(es) |
| `Esc` | Clear selection |
| `Shift`+drag box | Move a grouped box's whole group |
| `Shift`+draw | New box joins the selection's group |
| `Shift`+rotate | Snap to 15° |
| Pinch / `Ctrl`+scroll | Zoom at the pointer |
| `Ctrl+A` | Select all boxes on the slide |
| `Ctrl+C` / `Ctrl+V` | Copy / paste boxes (across slides too) |
| `Ctrl+Z` / `Ctrl+Shift+Z` | Undo / redo |
| `Ctrl+` / `Ctrl-` / `Ctrl+0` | Zoom in / out / fit |
| `Ctrl+Shift+V` | Cloze composer |
| `Ctrl+Shift+C` | Wrap selection as a cloze |
| `Ctrl+Return` | File the cloze card |
| `Ctrl+Shift+P` | Open from the card editor |
| `g` | *(reviewing)* Show all / hide all |

## Configuration

`Tools → Add-ons → PDF Occlusion → Config`

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `occlusion_mode` | `string` | `"ao"` | `"ao"` = Hide All, Show One · `"oa"` = Hide One, Show One |
| `mask_color` | RGB | `[120, 120, 120]` | Boxes not being tested, on cards and in the editor |
| `highlight_color` | RGB | `[131, 110, 170]` | The box being tested |
| `mask_opacity` | int 0–255 | `255` | Mask opacity **on cards**. 255 = fully opaque |
| `editing_mask_opacity` | int 0–255 | `150` | Mask opacity **in the editor**. Raise to `255` to hide each box's content as you draw it; `P` peeks through either way |
| `show_all_shortcut` | `string` | `"g"` | Key that reveals the whole slide while reviewing. `""` for none |
| `render_dpi_scale` | `float` | `2.0` | Render resolution multiplier — higher = sharper cards, more memory |
| `default_zoom` | `"fit"` / `float` | `"fit"` | Zoom when a PDF opens |
| `default_deck` | `string` | `""` | Deck preselected in the picker |
| `note_type_name` | `string` | `"PDF Occlusion"` | Note type to create or reuse |
| `add_editor_button` | `bool` | `true` | Show the toolbar button in the card editor |
| `close_after_creating` | `bool` | `true` | Close the dialog once cards are created |

## Contact

- Ravi Bandaru: ravi.bandaru@northwestern.edu
- Johanna Lee: johanna.lee@students.jefferson.edu

## License

MIT. See the included `LICENSE` file.
