# PDF Occlusion — Config

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `default_zoom` | `"fit"` or float | `"fit"` | Zoom applied when a PDF is opened. `"fit"` fits the slide to the window width; a number (e.g. `0.75`, `1.5`) is a fixed zoom level. |
| `render_dpi_scale` | float | `2.0` | Resolution multiplier when rendering PDF pages to images. Higher = sharper cards at the cost of memory. Display size is unaffected — zoom stays relative to the page's natural size. |
| `mask_color` | [R, G, B] | `[120, 120, 120]` | RGB fill colour of occlusion boxes that are NOT being tested (neutral grey). Used on the cards and for the editing preview. |
| `highlight_color` | [R, G, B] | `[131, 110, 170]` | RGB fill colour of the box being tested on the question side (purple). Its outline is derived automatically (a darker shade). |
| `mask_opacity` | int 0–255 | `255` | Opacity of the mask rectangles on the card. 255 = fully opaque. Lower values let the slide show through the masks — usually not what you want for occlusion. |
| `editing_mask_opacity` | int 0–255 | `150` | Opacity of the mask rectangles **in the editing canvas**, before any card is made. `150` (the default) keeps the slide readable while you draw. Raise it to `255` and each box hides what it covers the moment it is drawn, so building the deck is itself a recall exercise. Press **P** at any time to see through the masks without changing this setting. Does not affect the cards — that is `mask_opacity`. |
| `add_editor_button` | bool | `true` | Show a PDF icon in the Anki card-editor toolbar (like Image Occlusion Enhanced). |
| `close_after_creating` | bool | `true` | Close the dialog automatically after cards are created. |
| `note_type_name` | string | `"PDF Occlusion"` | Name of the note type to create/reuse. Change this to adopt an existing type. |
| `default_deck` | string | `""` | Deck preselected in the dialog's deck picker. Empty string = the deck currently selected in Anki. You can always change it per session in the dialog. |
| `show_all_shortcut` | string | `"g"` | Key that presses the **Show All** button while reviewing, revealing the whole slide (press again to put the masks back). Only does anything on the answer side of a PDF Occlusion card. Any Qt key name works — `"h"`, `"Shift+G"`, `"Ctrl+Shift+H"`. Set it to `""` to add no shortcut at all. Anki lets the last shortcut registered win, so choosing a key Anki already uses in the reviewer (`e`, `m`, `r`, `v`, `o`, `i`, `u`, `1`-`7`, space) takes that key over for as long as you are reviewing. |
| `occlusion_mode` | `"ao"` / `"oa"` | `"ao"` | Default occlusion mode: `"ao"` = Hide All, Show One; `"oa"` = Hide One, Show One. Changeable per slide via the "This slide" picker and per box via right-click → Occlusion Mode. |
