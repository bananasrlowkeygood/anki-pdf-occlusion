# PDF Image Occlusion — Config

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `default_zoom` | float | `1.0` | Starting zoom level when a PDF is opened. E.g. `0.75`, `1.5`. |
| `render_dpi_scale` | float | `1.0` | Scale factor applied when rendering PDF pages to images. `2.0` gives sharper images at the cost of memory. |
| `mask_color` | [R, G, B] | `[46, 120, 217]` | RGB fill colour of occlusion boxes on the question side of the card. |
| `mask_opacity` | int 0–255 | `200` | Opacity of the mask rectangle drawn on the card image. 255 = fully opaque. |
| `add_editor_button` | bool | `true` | Show a PDF icon in the Anki card-editor toolbar (like Image Occlusion Enhanced). |
| `close_after_creating` | bool | `true` | Close the dialog automatically after cards are created. |
| `note_type_name` | string | `"PDF Image Occlusion"` | Name of the note type to create/reuse. Change this to adopt an existing type. |
| `default_deck` | string | `""` | Deck name to use by default. Empty string = use whatever is selected in Anki. |
