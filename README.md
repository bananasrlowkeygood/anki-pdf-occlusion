# anki-pdf-occlusion

Create image occlusion cards directly from PDF lecture slides — no more importing one image at a time.

Open a PDF, draw occlusion boxes over the content you want to memorize, and generate cards in bulk. Supports grouping boxes, hide-all and hide-one modes, zoom, and per-slide skip. Each card uses an SVG overlay so the box disappears cleanly on the answer side without swapping images.

## Features

- Open any PDF — each page renders as a slide
- Draw, move, and resize occlusion boxes by dragging
- Group boxes together (one card masks all boxes in the group)
- Hide All / Hide One mode (like Image Occlusion Enhanced)
- Zoom in/out for precise box placement
- Skip slides you don't need
- Lecture name auto-filled from filename, editable
- Header shows lecture name + slide number on every card
- Appears as a toolbar button in the card editor (`Ctrl+Shift+P`)
- Media files auto-cleaned when cards are deleted
- Configurable mask color, DPI, deck, and more via the built-in config

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `← →` | Navigate slides |
| `Space` | Skip slide |
| `G` | Group selected boxes |
| `U` | Ungroup |
| `Del / Backspace` | Remove selected box(es) |
| `Ctrl+A` | Select all boxes on slide |
| `Ctrl+Shift+P` | Open from card editor |

## Installation

### From AnkiWeb
Search for **PDF Image Occlusion** in `Tools → Add-ons → Get Add-ons`.

### Manual
1. Clone this repo
2. Copy the folder into your Anki addons directory:
   ```
   ~/Library/Application Support/Anki2/addons21/pdf_image_occlusion/
   ```
3. Install the `PyMuPDF` dependency into `vendor/`:
   ```
   pip install PyMuPDF --target vendor/
   ```
4. Restart Anki

## Configuration

Go to `Tools → Add-ons → PDF Image Occlusion → Config`:

| Key | Default | Description |
|-----|---------|-------------|
| `default_zoom` | `1.0` | Starting zoom when a PDF opens |
| `render_dpi_scale` | `1.0` | PDF render quality (`2.0` = sharper) |
| `mask_color` | `[46, 120, 217]` | RGB color of occlusion boxes on cards |
| `occlusion_mode` | `"ao"` | `"ao"` = Hide All Show One, `"oa"` = Hide One Show One |
| `add_editor_button` | `true` | Show toolbar button in card editor |
| `close_after_creating` | `true` | Close dialog after creating cards |
| `note_type_name` | `"PDF Image Occlusion"` | Note type name |
| `default_deck` | `""` | Force a specific deck (empty = use active deck) |

## Dependencies

[PyMuPDF](https://pymupdf.readthedocs.io/) is used for PDF rendering. It must be installed into the `vendor/` folder (not included in the repo due to binary size).

## Inspired By

[Image Occlusion Enhanced](https://github.com/glutanimate/image-occlusion-enhanced) by Glutanimate.

## License

MIT
