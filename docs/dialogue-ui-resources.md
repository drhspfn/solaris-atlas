# Native dialogue HUD resources

Extracted from the user's installed Wuthering Waves client on 2026-11-08.
These are game assets, not SVG replacements or crops of the supplied screenshots.
Artwork remains the property of its respective rights holders.

The frontend files are in `packages/web_new/public/game-ui/dialogue`.
`src/data/dialogueUiAssets.ts` provides semantic URLs and existing game color tokens.
This change supplies resources; it does not replace the website's reader layout.

## Verified composition

`/Game/Aki/UI/UIResources/UiPlot/Prefabs/UiView_Plot` is a native LGUI prefab.
Its `ReferenceAssets`, component hierarchy and compact tagged properties identify:

| Frontend key | Prefab control / sprite | Meaning |
| --- | --- | --- |
| `history` | `BtnReview/SprReview`, `SP_PlotSkipBgIcon5` | Dialogue history |
| `manual` | `BtnAuto/PnlManual/SprIcon1`, `SP_PlotSkipBgIcon1` | Manual mode; play glyph |
| `auto` | `BtnAuto/PnlSemiAuto/SprIcon2` and `PnlFullAuto/SprIcon3`, `SP_PlotSkipBgIcon3` | Auto active; pause glyph |
| `skip` | `BtnSkip/.../SprSkipArrow`, `SP_PlotSkipBgIcon2` | Skip |
| `hide` | `BtnHide/HideCtrl/SprHideIcon`, `SP_PlotSkipBgIcon4` | Hide interface |
| `continue` | `SprNext`, `SP_PlotSubtitleNext` | White continuation ornament |
| `continueTriangle` | `SprNextB`, `SP_PlotSubtitleNextB` | Alternate triangle |
| `separatorLine` | `SprLineLeft` / `SprLineRight`, `SP_ComLineA` | Sliced line, alpha 0.6 |
| `separatorOrnament` | `SprLineIcon`, `SP_ComLineAIcon` | Central ornament, alpha 0.6 |

The hexagonal buttons include their frames in the raster sprite. Auto also has
separate arrow, frame, color and mask textures; those layers are included without
pretending each one is a complete button. `PlotView.Oeo()` selects three authored
states (`PnlManual`, `PnlSemiAuto`, `PnlFullAuto`), rather than swapping an arbitrary
play icon for a pause icon.

`TxtNpcName` has native FColor bytes `a6 eb f7 ff` in BGRA order: **#F7EBA6FF**,
already exported as `GAME_TEXT_COLORS.Highlight`. Dialogue text `TxtList` is
**#ECE5D8FF**, matching `WhiteA`. These are FColor bytes, not linear floats.
The reference prefab canvas is 2560 × 1440; preserve proportions when scaling.

## Continue animation

The moving indicator is a sprite driven by a UI LevelSequence, not a video or an
animated PNG. Prefab sequence `AutoBtnNextPage` points to
`/Game/Aki/UI/UIResources/UiPlot/LevelSequence/Ani_UiView_Plot_Loop`.
Its playback range is 0–48000 ticks at 24000 ticks/second: **2 seconds**.

The `SprNext` track animates `widget.anchorOffsetY` with values
40 → 50 → 40 → 50 → 40 at 0, 10000, 24000, 34000 and 48000 ticks.
`SprNextB` uses -10 → 0 → -10 at 0, 28000 and 48000 ticks.
`continue-motion.json` preserves native bindings, interpolation modes, tangents
and tangent weights. A future web implementation can use this data, with a
stationary `prefers-reduced-motion` presentation; the exported PNG is static.

## Extraction verification

Existing FModelCLI extracted the selected cooked assets, preserving patch
precedence. Existing CUE4Parse converted textures to RGBA PNG and decoded the
prefab, atlas importer and animation metadata. Atlas sprite crops use the native
`TPI_UiPlot.SpriteInfoMap` UV rectangles and recorded dimensions, with no resize,
recolor or background substitution. Common separator textures come directly from
the client's `_SpriteTextures` exports.

`manifest.json` records source-package hashes, atlas image hash, crop rectangles,
sprite metadata, output hashes and dimensions. No game keys or machine credentials
are included. The raw client scripts and cooked assets remain outside Git.
