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
| `continueTriangle` | `SprNextB`, `SP_PlotSubtitleNextB` | New subtitle style continuation indicator |
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

## Indicator visibility: verified handoff

See `dialogue-indicator-evidence.json` beside this document for the decoded
prefab hierarchy, registry bindings and original game method excerpts. This
file is checked into the project; the scratch extraction is not required.

The two sprites are ALTERNATIVES, not two layers of one indicator:

- registry item 19 = actor 100010 `SprNext`; parent `PnlNextAph` (100723).
- registry item 53 = actor 100717 `SprNextB`; parent `PnlBg` (100025).
- `Yeo(allowed)` sets `CanClick = allowed`, then shows item 19 only when
  `allowed && !IsNewSubtitleStyleActive()`, and item 53 only when
  `allowed && IsNewSubtitleStyleActive()`.
- Both sprites are initially inactive in the prefab. The animation contains
  tracks for both, but visibility selects one. Do not compose them together.
- Registry item 20 is `PnlPoint`, a separate waiting indicator. `Cto(waiting)`
  shows it only for the old subtitle style. It is not `SprNextB`.

For each new subtitle, `Veo` enables continuation immediately for
`IsInteraction || !PlotConfig.CanInteractive`. Otherwise it waits for
`max(line.WaitTime ?? JumpWaitTime, PlotTemplate.MinWaitingTime)` seconds.
The installed client has `Talk.JumpWaitTime = 0.3` seconds. `MinWaitingTime`
is template dependent; no universal numeric value has been established here.
The timer starts at subtitle presentation, not after typewriter completion.

The continuation indicator can therefore be visible during text reveal.
A click during reveal completes the current text; the next permitted click
submits it. Submitting calls `Yeo(false)` in the click/auto paths. Choice
handling transfers control to options after reveal and the mandatory wait.
The game also checks control permissions before accepting the click.

Website adaptation: these game fields may not exist in the website API.
That does NOT invalidate the recovered game behavior. If absent, use an
explicitly documented website fallback of 0.3 seconds from line presentation
and template minimum 0, rather than claiming those are per-line imported
values. Select one website subtitle style explicitly. Keep optional authored
wait/minimum values in the UI interface if available, without pretending the
backend already supplies them. Respect reader visibility and choices.

Animation curves are in
`packages/web_new/public/game-ui/dialogue/continue-motion.json`:
2 seconds, 24000 ticks/second; preserve curve interpolation and tangents.
Both native sprites are 56x56 on a 2560x1440 reference canvas; invert vertical
axis when translating game anchor offsets into CSS transforms.
