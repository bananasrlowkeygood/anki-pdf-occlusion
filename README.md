# <img alt="pdf-occlusion-icon" src="icon.svg" height="60"> ‎ ‎ ‎PDF Occlusion

[![Anki](https://img.shields.io/badge/Anki-2.1.50%2B-4a90d9?style=flat-square)](https://apps.ankiweb.net)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-8e6bc8?style=flat-square)](https://github.com/bananasrlowkeygood/anki-pdf-occlusion)
[![License](https://img.shields.io/badge/license-MIT-e8739e?style=flat-square)](LICENSE)

**PDF Occlusion** is an Anki add-on that creates image occlusion cards directly from PDF lecture slides. No more importing one image at a time. Open any PDF, draw boxes over what you want to memorize, and generate cards in bulk. Built for studying from slide-heavy lectures.

## Table of Contents

- [1. Installation](#installation)
- [2. Quick Start](#quick-start)
- [3. Keyboard Shortcuts](#keyboard-shortcuts)
- [4. Configuration](#configuration)
- [5. Notes](#notes)
- [6. Credits](#credits)
- [7. Contact](#contact)
- [8. License](#license)

## Installation

1. Install via Anki using the add-on code `783821131`.

2. Restart Anki.

> [!NOTE]
> The PDF rendering library ([pypdfium2](https://github.com/pypdfium2-team/pypdfium2)) is bundled for macOS (Apple Silicon and Intel), Windows (x64), and Linux (x64 and ARM). No separate install required, on any Anki Python version.

## Quick Start

1. Go to `Tools → PDF Occlusion`, or use the toolbar button in the card editor (`Ctrl+Shift+P`).

2. Click `Open PDF`

3. Click and drag to place boxes over the content you want to memorize.

4. *(Optional)* Select multiple boxes and press `G` to group them into a single card region.

5. Press `Space` to mark a slide as skipped.

6. Click `Create All Cards`.

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `←` / `→` | Navigate slides |
| `Space` | Skip / unskip current slide |
| `G` | Group selected boxes |
| `U` | Ungroup |
| `Del` / `Backspace` | Remove selected box(es) |
| `Ctrl+A` | Select all boxes on slide |
| `Ctrl+Shift+P` | Open from card editor |
| `Ctrl+` / `Ctrl-` | Zoom in / out |

## Configuration

`Tools → Add-ons → PDF Occlusion → Config`

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `occlusion_mode` | `string` | `"ao"` | `"ao"` = Hide All, Show One · `"oa"` = Hide One, Show One |
| `mask_color` | RGB | `[46, 120, 217]` | Color of the occlusion box on cards |
| `mask_opacity` | int 0–255 | `200` | Opacity of the mask rectangle drawn on the card image |
| `render_dpi_scale` | `float` | `2.0` | Resolution multiplier for rendered pages — higher = sharper cards, more memory |
| `default_zoom` | `float` | `1.0` | Starting zoom level when a PDF opens |
| `default_deck` | `string` | `""` | Route new cards to a specific deck by name |
| `note_type_name` | `string` | `"PDF Occlusion"` | Name of the note type to create/reuse |
| `add_editor_button` | `bool` | `true` | Show or hide the toolbar button in the card editor |
| `close_after_creating` | `bool` | `true` | Close the dialog automatically after cards are created |

## Notes

- A **PDF Occlusion** note type is created automatically on first use.
- Media files are cleaned up automatically when the last card referencing a slide is deleted
- Compatible with [Image Occlusion Enhanced](https://github.com/glutanimate/image-occlusion-enhanced) — both add-ons can run in the same profile

## Contact

- Ravi Bandaru: ravi.bandaru@northwestern.edu
- Johanna Lee: johanna.lee@students.jefferson.edu

## License

This project falls under an MIT license. See the included `LICENSE` file for details.
