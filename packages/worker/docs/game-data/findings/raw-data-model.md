# Raw data model — Arikatsu 3.6 / Dimbreath 3.1

## Snapshot provenance

- Arikatsu branch `3.6`, commit `353f2eaed119bc9f680eab92807d20ac75a79b40`, pushed 2026-08-20: Game 3.6.0, Resource 3.6.6, changelist 8499915.
- Dimbreath `master`, commit `e9234ffe094b2d944d16b222d31102e8ab32d954`, dated 2026-03-13: launcher/resource 3.1.19, changelist 6674016.
- Arikatsu is sparse checked out to story, Quest/QuestNodeData/QuestData/QuestRefVideo, map/NPC/role/item/area, English localization, and CG metadata. Its `BinData` directory inventory is in `arikatsu-bindata-directories.txt`.

## Files and records observed

| Data | Current file | Observed size/rows | Role |
|---|---|---:|---|
| Flow index | `BinData/flow/flow.json` | 1.1 MB / 9,729 rows | `Id` is a flow-list name; row also has `DungeonId` and `States`. |
| Flow states | `BinData/flowState/flowstate.json` | 50 MB / 20,198 rows | `StateKey`, `Id`, flags, `Pos`, and `Actions` (JSON-encoded string). |
| Quest chronology | `BinData/PlotHandBook/plothandbookconfig.json` | 936 KB / 94 rows | `QuestId`; `Data` is a JSON-encoded ordered list of `TidTip`, `Flow{FlowListName, FlowId, StateId}`, `IsHideUi`. |
| Quest metadata | `BinData/QuestData/questdata.json` | 1,846 rows | `QuestId` plus `Data` (`Id`, `Type`, `RegionId`, `DungeonId`, `Key`, name/desc localization IDs, prequest conditions, references). |
| Quest graph definitions | `BinData/QuestNodeData/questnodedata.json` | 24,909 rows | `Key` plus `Data` nodes (e.g. sequence node type); used as RealNath fallback for quest state resolution. |
| Quest video packages | `BinData/QuestRefVideo/questrefvideoconfig.json` | 380 rows | `QuestId`, `GirlOrBoy`, `PakName`, online branch. |
| Base quest config | `BinData/quest/quest.json` | 86 rows | `Id`, `QuestKey`, `QuestName`, `QuestType`, etc.; distinct table/key namespace from many PlotHandBook IDs. |
| Video metadata | `BinData/cgVedio/videodata.json` | 330 rows | `CgId`, `CgName`, `GirlOrBoy`, `CgFile`, skip/aspect. Note upstream spelling `cgVedio`. |
| Video sounds | `BinData/cgVedio/videosound.json` | 166 rows | `CaptionId`, `CgName`, `GirlOrBoy`, `EventPath`, start/end moments. |
| Captions | `BinData/cgVedio/videocaption.json` | 1,278 rows | `CgName`, caption id, `ShowMoment`, `Duration`, localization key `CaptionText`, language-specific timing. |
| Roles/NPCs/areas/items | `BinData/role/roleinfo.json`, `npc_headinfo/npcheadinfo.json`, `area/area.json`, `item/iteminfo.json` | inspected examples | Separate config families; only direct speaker-name resolution was established, not a universal WhoId crosswalk. |

English Textmaps are split across `multi_text/MultiText.json` (49 MB), `multi_text_1sthalf/MultiText.json` (326 KB), `multi_text_2ndhalf/MultiText.json` (109 KB). Each row has `Id`, `Content`, and redirect metadata. Other text families include `flow_text`, `quest`, `quest_step`, `map`, `npc_headinfo`, and `custom_sequence`.

## Flow/action records

In `FlowState.Actions` (a JSON string), each action has `Name`, optional `Params`, `ActionId`, and often `ActionGuid`. A `ShowTalk` action puts `TalkItems`, optional `TalkSequence`, and `SequenceTransitions` under Params. Real values observed include `SetPlotMode`, `ShowTalk`, `BeginFlowTemplate`, `SetFlowTemplate`, `FadeInScreen`, `FadeOutScreen`, `SetPlayerPos`, `PostAkEvent`, `PlaySequenceData`, `PlayMovie`, `SetAudioState`, `Wait`, `FinishState`, and `CameraLookAt`.

Arikatsu 3.6 contains 153 `PlayMovie` action instances. Across all FlowState records, observed TalkItem types/counts include Talk 102,653; unnamed 2,307; CenterText 905; Option 917; SystemOption 34; QTE 51; NoTextItem 79; PhoneMessage 416; AvgNarration 122; AvgTalk 99; AvgCenterText 8. `PhoneMessage` is present in FlowState (example `StateKey=剧情_3_0_一级POI_天文台主线任务_1_1`, `TalkItem.Type=PhoneMessage`); this shows at least some phone-style dialogue data is available there. It does not prove complete WavesLine coverage.

## Quest-to-text chain

`PlotHandBook.QuestId -> ordered Data[] FlowListName/FlowId/StateId -> StateKey -> FlowState.Actions -> ShowTalk.TalkItems -> TidTalk/TidTalkOption -> localized MultiText.Id -> Content`.

For the observed current data, speaker display name is `WhoId -> Textmaps/<lang>/multi_text*/MultiText.json` key `Speaker_<WhoId>_Name`. Concrete Denia/Nivora values in QuestId `119000000` are WhoId 200144 / 150058. These IDs are preserved as speaker source IDs. A universal crosswalk to `roleinfo` or all NPC tables is not established.

## Branch and ordering fields

- Quest-level state order: array order in PlotHandBook `Data`.
- Action order: array order in a FlowState row's `Actions`.
- Talk item order: `ShowTalk.Params.TalkSequence` (arrays of local TalkItem `Id`) when present; TalkItems array and explicit `JumpTalk` otherwise.
- Choice nodes: TalkItem `Options[]` with `TidTalkOption` and optional `Actions`; ShowTalk `SequenceTransitions` maps a sequence index to `NextSequenceIndex` and `OptionTextKey`; `JumpTalk.Params.TalkId` also appears in older/alternate flow shapes.
- IDs: `QuestId`, flow-list name/FlowId/StateId, `ActionId`/`ActionGuid`, local TalkItem `Id`, numeric `TextId`, localization `TidTalk`/`TidTalkOption`, and `WhoId` are all retained where present. Do not assume `TalkItem.Id` is globally unique.

## Structural limits

The public data can restore deterministic authored order where these fields exist. It cannot establish actual player traversal of mutually exclusive branches; conditions, externally triggered scenes, environment state and ordinary gameplay timing still matter. `TalkItems` without `TalkSequence` retain their source array order, but that order is not promoted to confirmed branch chronology. RealNath documents additional walking/battle/NPC/WavesLine omissions in its FlowState-focused transcript method.
