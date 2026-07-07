# <img alt="pdf-occlusion-icon" src="icon.svg" height="60"> ‎ ‎ ‎PDF Occlusion

[![Anki](https://img.shields.io/badge/Anki-23.10%2B-836EAA?style=flat-square)](https://apps.ankiweb.net)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-836EAA?style=flat-square)](https://github.com/bananasrlowkeygood/anki-pdf-occlusion)
[![License](https://img.shields.io/badge/license-MIT-836EAA?style=flat-square)](LICENSE)

**PDF Occlusion** is an Anki add-on that creates image occlusion cards directly from PDF lecture slides. No more importing one image at a time. Open any PDF, draw boxes over what you want to memorize, and generate cards in bulk. Built for studying from slide-heavy lectures.

## Table of Contents

- [1. Installation](#installation)
- [2. Quick Start](#quick-start)
- [3. Editing Tools](#editing-tools)
- [4. Keyboard Shortcuts](#keyboard-shortcuts)
- [5. Configuration](#configuration)
- [6. Contact](#contact)
- [7. License](#license)

## Installation

1. Install via Anki using the add-on code `783821131`

2. Restart Anki

> [!NOTE]
> The PDF rendering library ([pypdfium2](https://github.com/pypdfium2-team/pypdfium2)) is bundled for macOS (Apple Silicon and Intel), Windows (x64), and Linux (x64 and ARM). No separate install required, on any Anki Python version.

## Quick Start

1. Go to `Tools → PDF Occlusion`, or use the toolbar button in the card editor (`Ctrl+Shift+P`).

2. Click `Open PDF`

3. Click and drag to place boxes over the content you want to memorize.

4. *(Optional)* Select multiple boxes and press `G` to group them into a single card region.

5. Press `Space` to mark a slide as skipped.

6. Pick a deck (type a new name to create one), choose an occlusion mode, and click `Create All Cards`.

## Editing Tools

- **Draw** — drag on empty space
- **Move** — drag a box (a multi-selection moves together)
- **Resize** — drag any corner handle of a selected box
- **Nudge** — arrow keys move selected boxes 1 px; `Shift` + arrows = 10 px
- **Copy / paste** — `Ctrl+C` / `Ctrl+V`; the clipboard survives slide changes, so a repeating layout can be stamped onto every slide of a deck
- **Undo / redo** — `Ctrl+Z` / `Ctrl+Shift+Z`, per slide
- **Group** — `Shift`-click to multi-select, then `G`; each group becomes one card

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `←` / `→` | Navigate slides (nudge instead when boxes are selected) |
| `PgUp` / `PgDn` | Navigate slides (always) |
| `Space` | Skip / unskip current slide |
| `G` / `U` | Group / ungroup selected boxes |
| `Del` / `Backspace` | Remove selected box(es) |
| `Ctrl+A` | Select all boxes on slide |
| `Ctrl+C` / `Ctrl+V` | Copy / paste boxes (works across slides) |
| `Ctrl+Z` / `Ctrl+Shift+Z` | Undo / redo |
| `Ctrl+` / `Ctrl-` | Zoom in / out |
| `Ctrl+0` | Fit slide to window width |
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
