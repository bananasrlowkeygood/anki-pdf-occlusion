# <img alt="pdf-occlusion-icon" src="icon.svg" height="60"> ‎ ‎ ‎PDF Occlusion

[![Anki](https://img.shields.io/badge/Anki-23.10%2B-836EAA?style=flat-square)](https://apps.ankiweb.net)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-836EAA?style=flat-square)](https://github.com/bananasrlowkeygood/anki-pdf-occlusion)
[![License](https://img.shields.io/badge/license-MIT-836EAA?style=flat-square)](LICENSE)

Image occlusion cards straight from PDF lecture slides — open a PDF, draw boxes over what you want to memorize, make cards in bulk. Cloze cards too, for slides that don't suit occlusion.

## Install

Add-on code `783821131`, then restart Anki. The PDF renderer is bundled for macOS, Windows and Linux — nothing else to install.

## Quick Start

1. `Tools → PDF Occlusion`, or `Ctrl+Shift+P` in the card editor.
2. `Open PDF` — one file or several. The arrow beside it lists recent sessions.
3. Drag boxes over what you want to memorize, or press `F` and drag over a region to have them placed for you.
4. Pick a deck and click `Create All Cards`.

## Shortcuts

| Key | Action |
|-----|--------|
| `D` / `V` / `T` | Draw / Select / Text tool |
| `F` | Detect — then drag over the area to scan |
| `P` | Peek through the masks |
| `G` / `U` | Group / ungroup selection |
| `N` | Note on selected box(es) |
| `Del` / `Backspace` | Remove selected box(es) |
| `Esc` | Clear selection |
| `←` / `→` | Slides — nudges selected boxes instead |
| `PgUp` / `PgDn` | Slides (always) |
| `Ctrl+A` | Select all boxes on the slide |
| `Ctrl+C` / `Ctrl+V` | Copy / paste boxes, across slides too |
| `Ctrl+Z` / `Ctrl+Shift+Z` | Undo / redo |
| `Ctrl+` / `Ctrl-` / `Ctrl+0` | Zoom in / out / fit |
| `Shift`+drag box | Move a grouped box's whole group |
| `Shift`+draw | New box joins the selection's group |
| `Shift`+rotate | Snap to 15° |
| Pinch / `Ctrl`+scroll | Zoom at the pointer |
| `Ctrl+Shift+V` | Cloze composer |
| `Ctrl+Shift+C` | Wrap selection as a cloze |
| `Ctrl+Return` | File the cloze card |
| `Ctrl+Shift+P` | Open from the card editor |
| `g` | **While reviewing** — show all / hide all |

## Configuration

`Tools → Add-ons → PDF Occlusion → Config`

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `occlusion_mode` | `string` | `"ao"` | `"ao"` = Hide All, Show One · `"oa"` = Hide One, Show One |
| `mask_color` | RGB | `[120, 120, 120]` | Boxes not being tested |
| `highlight_color` | RGB | `[131, 110, 170]` | The box being tested |
| `mask_opacity` | int 0–255 | `255` | Mask opacity **on cards** |
| `editing_mask_opacity` | int 0–255 | `150` | Mask opacity **in the editor**. At `255` a box hides its content as you draw it; `P` peeks through |
| `show_all_shortcut` | `string` | `"g"` | Key that reveals the whole slide while reviewing. `""` for none |
| `render_dpi_scale` | `float` | `2.0` | Render resolution — higher = sharper cards, more memory |
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
