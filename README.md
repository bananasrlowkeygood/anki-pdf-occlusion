# <img alt="pdf-occlusion-icon" src="icon.svg" height="60"> ‎ ‎ ‎PDF Occlusion

[![Anki](https://img.shields.io/badge/Anki-23.10%2B-836EAA?style=flat-square)](https://apps.ankiweb.net)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-836EAA?style=flat-square)](https://github.com/bananasrlowkeygood/anki-pdf-occlusion)
[![License](https://img.shields.io/badge/license-MIT-836EAA?style=flat-square)](LICENSE)

**PDF Occlusion** is an Anki add-on that creates image occlusion cards directly from PDF lecture slides. No more importing one image at a time. Open any PDF (or several at once), draw boxes over what you want to memorize, and generate cards in bulk. Built for studying from slide-heavy lectures.

- **Auto-detect text** — one click places boxes over every text line on a slide
- **Grouping, notes, marquee selection, pinch-to-zoom**
- **Sessions** — your work saves automatically per PDF; reopen it later and *Create All Cards* **updates the existing cards in place** (review history intact) instead of duplicating them
- **Occlusion mode per PDF, per slide, or per box** — mix *Hide All, Show One* and *Hide One, Show One* freely
- **Cloze composer** (`Ctrl+Shift+V`) — for slides that are too text-heavy to occlude, write a normal cloze card with the slide kept underneath as the extra

## Table of Contents

- [1. Installation](#installation)
- [2. Quick Start](#quick-start)
- [3. Editing Tools](#editing-tools)
- [4. Cloze Cards from Slides](#cloze-cards-from-slides)
- [5. Sessions & Editing After Creation](#sessions--editing-after-creation)
- [6. Occlusion Modes](#occlusion-modes)
- [7. Keyboard Shortcuts](#keyboard-shortcuts)
- [8. Configuration](#configuration)
- [9. Contact](#contact)
- [10. License](#license)

## Installation

1. Install via Anki using the add-on code `783821131`

2. Restart Anki

> [!NOTE]
> The PDF rendering library ([pypdfium2](https://github.com/pypdfium2-team/pypdfium2)) is bundled for macOS (Apple Silicon and Intel), Windows (x64), and Linux (x64 and ARM). No separate install required, on any Anki Python version.

## Quick Start

1. Go to `Tools → PDF Occlusion`, or use the toolbar button in the card editor (`Ctrl+Shift+P`).

2. Click `Open PDF` — select one PDF or several at once. The arrow next to the button lists recent sessions to resume.

3. Click and drag to place boxes over the content you want to memorize — or click `Detect Text` to place them automatically.

4. *(Optional)* Select multiple boxes and press `G` to group them into a single card region. Double-click a box to attach a note (shown under the answer).

5. Press `Space` to mark a slide as skipped.

6. Pick a deck (type a new name to create one) and click `Create All Cards`.

## Editing Tools

- **Draw** (`D`) — drag on empty space to draw a box; hold `Shift` while drawing and the new box joins the selection's group, so a multi-part card can be sketched in one pass
- **Select** (`V`) — drag on empty space to marquee-select boxes; `Shift` adds to the selection
- **Detect Text** — auto-places boxes over each text line on the slide (`T`); detected boxes come in selected, so `Del` discards them if the result isn't useful
- **Move** — drag a box (a multi-selection moves together); `Shift`-drag a grouped box to move its whole group
- **Resize** — drag any corner handle of a selected box, or drag an **edge** to resize in one direction only. Edges have no drawn handle: hover one and the cursor changes to show it's live.
- **Rotate** — with the **Select** tool, hover just outside a corner of a selected box until the cursor turns into a curved arrow, then drag. Hold `Shift` to snap to 15°, and rotate a multi-selection to turn every box together around the middle of the selection. `Reset Rotation` in the right-click menu straightens them again. (Rotation is Select-only on purpose: under Draw, the space just outside a corner is where you press to start the next box.)
- **Zoom** — pinch on the trackpad or `Ctrl`+scroll, anchored at the pointer; `Ctrl+0` fits the slide
- **Nudge** — arrow keys move selected boxes 1 px; `Shift` + arrows = 10 px
- **Copy / paste** — `Ctrl+C` / `Ctrl+V`; the clipboard survives slide changes, so a repeating layout can be stamped onto every slide of a deck
- **Undo / redo** — `Ctrl+Z` / `Ctrl+Shift+Z`, per slide
- **Group** — `Shift`-click to multi-select, then `G`; each group becomes one card
- **Notes** — double-click a box (or press `N`) to attach a note; it fills the card's Notes field
- Grouping, mode overrides, and everything above are also in the right-click menu

## Cloze Cards from Slides

Some slides are all prose — a wall of text where occlusion boxes would just be rectangles over sentences. Press `Ctrl+Shift+V` (or click `Cloze…`) and a small composer opens beside the window:

1. Type or paste the fact, select the part to test, and press `Ctrl+Shift+C` to wrap it in `{{c1::…}}`. `Ctrl+Alt+Shift+C` reuses the current number instead of taking a new one.
2. **Add slide as extra** is on by default — the slide you're looking at is filed under the answer, captioned with the lecture and slide number. Click the thumbnail to turn it off.
3. `Ctrl+Return` adds the card. The composer clears, stays open for the next one, and hops to the other side of the screen so it isn't sitting on the part of the slide you're about to read.

The composer always follows the slide the main window is on, so flipping slides re-aims it. Cards use your collection's ordinary **Cloze** note type — nothing add-on specific — so they keep working with or without PDF Occlusion installed, and several cards made from one slide share a single copy of the image.

## Sessions & Editing After Creation

Your work is saved automatically per PDF (boxes, skipped slides, mode overrides, lecture name) when the dialog closes. Reopen the same PDF — via `Open PDF` or its recent-sessions arrow — and you'll be offered to resume.

Because each box remembers which card it produced, **Create All Cards is safe to run again**: existing cards are *updated in place* (masks, header — with scheduling and review history untouched), new boxes become new cards, and if you deleted boxes you'll be asked whether to delete their cards too.

## Occlusion Modes

Modes can be set at three levels; the most specific wins:

1. **Default** — the `occlusion_mode` config value
2. **Per slide** — the `This slide` picker above the canvas
3. **Per box / group** — right-click → `Occlusion Mode`

Boxes with an override show a small `AO` / `OA` badge in the editor.

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `←` / `→` | Navigate slides (nudge instead when boxes are selected) |
| `PgUp` / `PgDn` | Navigate slides (always) |
| `Space` | Skip / unskip current slide |
| `D` / `V` | Draw tool / Select tool |
| `T` | Detect text on current slide |
| `Ctrl+Shift+V` | Open the cloze composer for this slide |
| `G` / `U` | Group / ungroup selected boxes |
| `N` | Add / edit note on selected box(es) |
| `Shift`+drag box | Move a grouped box's whole group |
| `Shift`+draw | New box joins the selection's group |
| Pinch / `Ctrl`+scroll | Zoom at the pointer |
| `Del` / `Backspace` | Remove selected box(es) |
| `Ctrl+A` | Select all boxes on slide |
| `Ctrl+C` / `Ctrl+V` | Copy / paste boxes (works across slides) |
| `Ctrl+Z` / `Ctrl+Shift+Z` | Undo / redo |
| `Ctrl+` / `Ctrl-` | Zoom in / out |
| `Ctrl+0` | Fit slide to window |
| Drag box edge | Resize in one direction (hover to find it) |
| Drag outside a corner (`V`) | Rotate; `Shift` snaps to 15° |
| `Esc` | Clear selection |
| `Ctrl+Shift+P` | Open from card editor |

## Configuration

`Tools → Add-ons → PDF Occlusion → Config`

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `occlusion_mode` | `string` | `"ao"` | `"ao"` = Hide All, Show One · `"oa"` = Hide One, Show One |
| `mask_color` | RGB | `[120, 120, 120]` | Color of boxes not being tested (neutral grey), on cards and in the editor |
| `highlight_color` | RGB | `[131, 110, 170]` | Color of the box being tested (purple) |
| `mask_opacity` | int 0–255 | `255` | Opacity of the mask rectangles on cards. 255 = fully opaque |
| `render_dpi_scale` | `float` | `2.0` | Resolution multiplier for rendered pages — higher = sharper cards, more memory |
| `default_zoom` | `"fit"` / `float` | `"fit"` | Zoom when a PDF opens; `"fit"` fills the window width |
| `default_deck` | `string` | `""` | Deck preselected in the dialog's deck picker |
| `note_type_name` | `string` | `"PDF Occlusion"` | Name of the note type to create/reuse |
| `add_editor_button` | `bool` | `true` | Show or hide the toolbar button in the card editor |
| `close_after_creating` | `bool` | `true` | Close the dialog automatically after cards are created |

## Contact

- Ravi Bandaru: ravi.bandaru@northwestern.edu
- Johanna Lee: johanna.lee@students.jefferson.edu

## License

This project falls under an MIT license. See the included `LICENSE` file for details.
