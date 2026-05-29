# PDF Image Occlusion

> Create image occlusion cards directly from PDF lecture slides — no more importing one image at a time.

Open any PDF, draw boxes over what you want to memorize, and generate cards in bulk. Built for studying from slide-heavy lectures.

---

## Overview

| | |
|---|---|
| **Anki version** | 2.1.50 or later |
---

## How It Works

**1. Open the dialog**
Go to `Tools → PDF Image Occlusion`, or use the toolbar button in the card editor.

**2. Load a PDF**
Every page renders as a slide preview.

**3. Draw occlusion boxes**
Click and drag to place boxes over the content you want to memorize.

**4. Group related boxes** *(optional)*
Select multiple boxes and press `G` to group them into a single card region.

**5. Skip slides you don't need**
Press `Space` to mark a slide as skipped.

**6. Generate cards**
Click **Create All Cards** — done.

---

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

---

## Configuration

`Tools → Add-ons → PDF Image Occlusion → Config`

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `occlusion_mode` | `string` | `"ao"` | `"ao"` = Hide All, Show One · `"oa"` = Hide One, Show One |
| `mask_color` | RGB | — | Color of the occlusion box on cards |
| `render_dpi_scale` | `float` | `1.0` | Set to `2.0` for sharper rendering on high-DPI screens |
| `default_zoom` | `float` | — | Starting zoom level when a PDF opens |
| `default_deck` | `string` | — | Route new cards to a specific deck by name |
| `add_editor_button` | `bool` | `true` | Show or hide the toolbar button in the card editor |

---

## Notes

- PyMuPDF is bundled — no separate install required
- A **PDF Image Occlusion** note type is created automatically on first use
- Compatible with [Image Occlusion Enhanced](https://github.com/glutanimate/image-occlusion-enhanced) — both add-ons can run in the same profile

---

## Credits

Inspired by [Image Occlusion Enhanced](https://github.com/glutanimate/image-occlusion-enhanced) by [Glutanimate](https://github.com/glutanimate).

---

## Links

- **Source & bug reports:** [github.com/bananasrlowkeygood/anki-pdf-occlusion](https://github.com/bananasrlowkeygood/anki-pdf-occlusion)
