# Dialogue model — real fields and extractor behavior

## Dialogue identity / localization

The verified 3.6 chain is `QuestId -> PlotHandBook.Data -> FlowListName/FlowId/StateId -> StateKey -> Actions[].ActionId -> ShowTalk.Params.TalkItems[].Id`. A TalkItem's localized line is `TidTalk`; numeric `TextId` is also present on many records. `WhoId` resolves through `Speaker_<WhoId>_Name` in MultiText. Options are `Options[].TidTalkOption`, with optional nested Actions. Original keys/IDs and their source files are preserved in [the canonical Quest 168800009 output](../output/canonical/168800009.json).

## Ordering and branches

`PlotHandBook.Data` is the ordered quest-level list of flow states. In a state, `Actions[]` is the authored action order. A ShowTalk action may contain `TalkSequence` arrays of local TalkItem IDs; its `SequenceTransitions` maps a source sequence to `NextSequenceIndex` and carries `OptionTextKey`. `JumpTalk.Params.TalkId` provides another explicit branch/jump representation. This yields directed edges for identified choices. It does not reveal the runtime condition or which branch a player actually took.

When TalkSequence is absent, preserve the stored TalkItems array and any JumpTalk targets. Treat adjacency as source-list order rather than verified execution order. The PoC marks the ordering basis on dialogue/action edges.

## Non-dialogue actions and narration

FlowState rows interleave `ShowTalk` with actions like `SetPlotMode`, `BeginFlowTemplate`, `SetFlowTemplate`, `FadeInScreen`, `SetPlayerPos`, `PostAkEvent`, `PlaySequenceData`, `PlayMovie`, `SetAudioState`, and `Wait`. These action names/IDs are retained as `game_action`/`cutscene` nodes. TalkItem types include `CenterText`, `AvgNarration`, `AvgTalk`, `PhoneMessage`, `QTE`, and player/system option types; the canonical extractor maps narration, player_choice and dialogue types explicitly while retaining each raw `source_type`.

The data contains at least 416 `PhoneMessage` TalkItems, but complete WavesLine coverage is not verified. Walking/battle speech and ambient NPC coverage remains a known limitation reported by RealNath's extractor documentation.

## Actual parser runs

RealNath was run with unmodified upstream source on current Arikatsu 3.6 data. Successful IDs include:

| Kind | QuestId | Transcript lines | State-key headings | Speaker-rendered lines | Choice markers | Raw localization keys |
|---|---:|---:|---:|---:|---:|---:|
| Main story | 139000039 — When the Night Knocks | 1,276 | 164 | 669 | 164 | 0 |
| Main story | 168800009 — Gold Suspended in Shadows | 1,436 | 126 | 982 | 101 | 0 |
| Main story | 119000000 — Beneath a Melting Night Sky | 600 | 38 | 434 | 57 | 1 |
| Side/activity | 880000034 — Set Sail! Pro Angler! | 97 | 6 | 67 | 14 | 0 |
| Side/activity | 880000036 — Love in the Time of Fishing | 144 | 9 | 96 | 23 | 0 |
| Side/activity | 880000038 — Old Man and the Whale | 194 | 17 | 134 | 18 | 0 |
| Cutscene sample | 915700000 — When the Unknown Thrums | 62 | 4 | 44 | 7 | 0 |

The transcript files are in `output/realnath/`. Counts describe generated wiki-style output markers, not a perfect count of raw game dialogue records; the final column counts literal raw localization keys printed in transcript output. The canonical extraction has two unresolved narration keys for `168800009` (`Main_LahaiRoi_YXBLDHJ_101_37` and `_38`) and one unresolved key for `119000000` (`Flow_119000000_8659`). Missing strings are retained by key in canonical output.

## WutheringDialog test

Unmodified `mrzjy/WutheringDialog` ran successfully on a data subset from Dimbreath 3.1 containing shared quest flow states `139000039` and `880000034`. Its JSONL is under `output/wuthering-dialog/data/`. For `139000039`, 167 unique states emit 767 actions (605 gameplay-labeled actions), 651 dialogue records, 21 plot records, and only 1 option record; for `880000034`, 7 states emit 57 actions, 51 gameplay, 67 dialogue, and 1 option record. The parser records ActionIds and generic gameplay actions, but its source checks `if "TidTalk" in talk` before `elif "Options" in talk`, so inline options attached to a line are consumed as dialogue and not emitted as choices. On the same 139000039 RealNath transcript, 164 choice markers are printed.

The full unfiltered Dimbreath 3.1 FlowState input fails in the unmodified parser with `KeyError: 'ActionId'` on an action lacking that field, after warning about unrecognized `TalkItem.Type=Option` shapes. Filtering to shared PlotHandBook quest states lets us compare relevant output without modifying upstream parser files.

## Extractor comparison

- **Ordering:** RealNath explicitly traverses TalkSequence, SequenceTransitions and JumpTalk to print branches; WutheringDialog emits the stored state/action/TalkItem lists but does not encode most TalkSequence branches. RealNath is better for transcript order; WutheringDialog's order remains useful as raw per-state evidence.
- **IDs:** WutheringDialog preserves ActionId in the output. RealNath's formatted transcript preserves StateKey when requested, but not action IDs. Our canonical PoC preserves both.
- **Branches:** RealNath renders 164 choices for 139000039; WutheringDialog renders one option record due to the inline-options condition order.
- **Gameplay:** WutheringDialog keeps non-dialogue action names/IDs and labels them `gameplay`; RealNath outputs dialogue transcript only.
- **Shared tables:** both use FlowState/TalkItems/MultiText concepts, but RealNath joins through Arikatsu `PlotHandBook`, `flow`, `flowstate`, and split MultiText, while WutheringDialog directly reads Dimbreath `ConfigDB/FlowState.json` and unified `TextMap/<lang>/MultiText.json`.
- **Content present in only one output:** parser output alone cannot determine an exact line-only diff because WutheringDialog's schema drops TextId/TidTalk identifiers. The code-level mismatch is explicit: WutheringDialog ignores nested Options on lines that already have TidTalk; RealNath retains options and TalkSequence branches. A semantic text diff keyed by raw localization ID should be a next refinement.
