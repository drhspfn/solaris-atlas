# Phase 0 — final investigation report

## 1. Executive summary

Public datamined tables are sufficient for a deterministic, source-traceable quest extraction without an installed game or an LLM. Using Arikatsu's 3.6 snapshot, `./extract-quest 168800009` produced 132 ordered scene records, 1,712 typed nodes, and 2,894 edges. The output retains source file names and original game IDs, localized text and speaker references, choice branches, gameplay actions, narration records, and two cutscene metadata links. Quest `119000000`, **Beneath a Melting Night Sky**, provided the Denia benchmark: 38 scenes, 804 nodes, including dialogue mentioning Denia, Nivora, Fractsidus, and Aleph-1.

This meets the practical Phase 0 extraction gate for these sampled quests, with explicit limits: list order is not always proof of player traversal; two English narration keys are missing in Quest `168800009`; the benchmark has no resolved cutscene record; and this Mac run did not export encrypted media from an installed game. Parser runs, graph outputs, canonical JSON, raw samples, and source checkouts are included in this workspace.

## 2. Available upstream sources

The seven required repositories were cloned or sparsely checked out under `upstream/`. Additional relevant repositories and source reliability are recorded in [upstreams.md](upstreams.md). Arikatsu 3.6 is the current primary table source; Dimbreath 3.1 supplies an independently exported historical overlap. RealNath and WutheringDialog were both run. SunsetMkt's data-to-media workflow and FModelCLI/Ludiglot local extraction paths were inspected.

| Source | Role | Evidence / status |
|---|---|---|
| Arikatsu/WutheringWaves_Data | Primary current public raw tables and Textmaps | Game 3.6.0 / Resource 3.6.6, pinned commit; used for extraction |
| Dimbreath/WutheringData | Secondary 3.1 ConfigDB/TextMap snapshot | Pinned commit; used for overlapping version comparison and parser input |
| RealNath/wuwa-dialogue-generator | Transcript extraction | Ran against 3.6 quests; transcript-oriented, documented coverage gaps |
| mrzjy/WutheringDialog | Action/dialogue JSONL extraction | Ran on selected 3.1 subsets; full scan fails on missing ActionId |
| SunsetMkt/WuWa-Cutscenes | Caption and media assembly reference | Workflow inspected; metadata relation cross-checked in game tables |
| FModelCLI / Ludiglot | Local resource extraction fallback/reference | Source/docs inspected; local game extraction not run |

## 3. Raw game data model

Arikatsu uses `BinData/` JSON tables plus split `Textmaps/`. The main story path is `PlotHandBook -> flow -> flowState`; metadata is supplemented by `QuestData`, `QuestNodeData`, and `QuestRefVideo`. Cinematic metadata is in the upstream-spelled `cgVedio/` directory (`videodata`, `videosound`, `videocaption`). Role, NPC, area, and item data exist in separate table families. The inspected data inventory and representative records are in [raw-data-model.md](raw-data-model.md), [dataset-inventory.json](../output/dataset-inventory.json), and [samples/raw/](../samples/raw/).

Arikatsu 3.6 contains 9,729 flow rows, 20,198 FlowState rows, 94 PlotHandBook rows, 1,846 QuestData rows, 24,909 QuestNodeData rows, and 380 QuestRefVideo rows. Dimbreath flattens the related tables into `ConfigDB/` and keeps a unified `TextMap/<language>/MultiText.json`.

## 4. Dialogue model

For the inspected records, the deterministic chain is:

```text
QuestId
 -> PlotHandBook.Data[] (ordered FlowListName / FlowId / StateId references)
 -> flow StateKey
 -> FlowState.Actions[] (ActionId, ActionGuid, Name, Params)
 -> ShowTalk.Params.TalkItems[]
 -> TidTalk / TidTalkOption
 -> localized MultiText Id / Content
```

Speaker display text resolves as `WhoId -> Speaker_<WhoId>_Name -> MultiText.Content`. TalkItem `Id`, numeric `TextId`, `ActionId`, `ActionGuid`, `QuestId`, state and flow references are retained when present. `TalkSequence` orders talk items; transitions and JumpTalk references express some branch navigation. Full field details and measured parser results are in [dialogue-model.md](dialogue-model.md).

## 5. Quest chronology

The tables provide authored ordering in PlotHandBook's `Data[]`, action-array order within a state, and TalkSequence ordering where supplied. The canonical extractor preserves these source positions and creates explicit branch edges when targets exist. It also emits adjacency/order edges for the assembled sequence; these describe authored data order and must not be mistaken for proof that every player traverses every node. Runtime conditions, world triggers, and optional gameplay can change actual traversal.

## 6. Branches

Choices are represented in FlowState TalkItems through `Options[]` (`TidTalkOption` and optional nested Actions), with `SequenceTransitions` mapping sequence positions and `NextSequenceIndex`; JumpTalk references also occur. The canonical output turns available targets into directed choice edges and retains missing targets as diagnostics. This is enough to recover explicit branches in the sampled quests, but not a complete runtime condition model.

WutheringDialog drops inline options when a TalkItem also has `TidTalk`: its parser handles the line first and never enters its `elif Options` branch. For Quest `139000039`, it emitted one option record while RealNath produced 164 choice markers. See [dialogue-extractor-comparison.md](dialogue-extractor-comparison.md).

## 7. Scenes and cutscenes

Canonical scene groupings follow the quest's ordered flow/state references. In Quest `168800009`, FlowState `PlayMovie.Params.VideoName` matches `CgName` rows `C0026` and `M0346`. From there, `videodata.CgFile` supplies movie asset names/paths, `videosound.EventPath` supplies Wwise event references, and `videocaption` supplies caption keys and timing. Caption keys resolve through MultiText. A sanitized concrete join is saved as [cutscene-join-168800009.json](../samples/raw/cutscene-join-168800009.json).

This verifies a quest-action-to-cutscene-metadata-to-caption/audio-reference chain. It does not mean video/audio bytes were exported. SunsetMkt documents encrypted post-2.1 movies and a workflow using FModel, Wwise tools, and ffmpeg. Quest `119000000` has no resolved cutscene node in the current extraction; no link should be inferred from its transcript alone.

## 8. Localization

Arikatsu English MultiText is split across `multi_text`, `multi_text_1sthalf`, and `multi_text_2ndhalf`; keys are matched against `Id`, with content in `Content`. Additional text tables cover quests, maps, NPC headings, flow, and sequences. The extractor preserves both localization key and resolved value/source. Two narration keys are unresolved for Quest `168800009`; Quest `119000000` has one unresolved key (`Flow_119000000_8659`). Missing text is retained as a key and diagnostic rather than fabricated.

## 9. Character / NPC resolution

Observed dialogue speaker resolution uses `WhoId` and the localized key `Speaker_<WhoId>_Name`. In Denia Quest `119000000`, Denia is `WhoId=200144`, Nivora is `WhoId=150058`. These IDs and text keys are preserved. A universal mapping from every speaker ID to Role/NPC entity tables is not established; NPC conversations outside this flow path may be absent.

## 10. Version stability

The comparison uses Arikatsu 3.6 and Dimbreath 3.1 snapshots. For the two shared quests tested, the sampled StateKeys, ActionIds, TalkItem IDs, TextIds, localization keys, and WhoIds matched exactly: Quest `139000039` had 167/167 shared states and Quest `915700000` had 6/6. This is evidence for those records, not a universal stability guarantee. The 3.1 snapshot predates Denia Quest `119000000`, which is absent there. Details and script outputs are in [version-stability.md](version-stability.md) and `output/version-comparisons/`.

## 11. Missing data

- RealNath documents omissions for walking dialogue, battle dialogue, WavesLine, and some NPC conversation; these paths are not proven complete by FlowState extraction.
- Two English narration keys in Quest `168800009` and one key in Quest `119000000` are unresolved in the selected snapshot.
- The WutheringDialog full-data run is not robust to actions without `ActionId`; it also warns on unhandled TalkItem types and drops inline options in a known case.
- No full runtime condition/trigger model or actual player-selected chronology is reconstructed.
- Cutscene metadata is linked, but encrypted media bytes and extracted audio were not produced on this Mac.
- `QuestRefVideo` provides package/branch references; a universal mapping from every QuestId/flow action to every referenced video package is not established.
- The cross-version sample is small and compares identifier sets, not every text change, ActionGuid, or semantic graph edit.
- Denia is a useful deterministic benchmark, but its current extraction does not resolve a cutscene node or all story material outside FlowState.

## 12. Upstream reliability

- **Primary data:** Arikatsu/WutheringWaves_Data 3.6 for the current public snapshot; source IDs and file origins verified in records.
- **Secondary data:** Dimbreath/WutheringData 3.1 for an independent historical overlap, not a current complete corpus.
- **Primary transcript comparator:** RealNath, because it ran against the current snapshot and follows talk ordering/branch formatting; known coverage omissions remain.
- **Secondary action reference:** WutheringDialog, useful for action identifiers and gameplay rows, with parser robustness and option-loss limitations.
- **Reference:** SunsetMkt for cinematic caption/media workflow; no media export was performed.
- **Fallback candidate:** FModelCLI/FModel for local extraction. FModelCLI documents game files, AES key(s), Unreal package handling and Windows x64 distribution; Mac build and game extraction were not verified. Ludiglot is a Windows-focused integrated workflow.

The upstream table includes licenses and pinned commit details. The two raw data repositories did not expose a LICENSE file in the checked-out snapshots; downstream reuse terms should be confirmed before redistribution.

## 13. Recommended ingestion architecture

For a next investigation phase, keep version-pinned raw snapshots and source provenance; load the known tables with deterministic readers; preserve raw identifiers, array positions, source file, and localization keys on every canonical node. Build explicit choice edges only from source target fields and distinguish them from authored-order edges. Add cutscene/audio/caption joins only on matching data keys. Treat local media export as an independent optional process. This is a small evidence-based ingestion recommendation, not a production database or knowledge-graph design.

## 14. Proposed Phase 1

1. Pin an additional adjacent Arikatsu release and repeat the same shared-quest comparison, including text content, ActionGuid, graph topology, and changed records.
2. Extend the parser comparison to use raw TalkItem/TextId keys so one-sided line differences can be reported exactly; preserve TalkItem options before formatting.
3. Expand one known quest through QuestNodeData and explicit conditions to distinguish sequence order from reachable paths.
4. Investigate Denia's quest/sequence/cutscene references and missing FlowState coverage using deterministic table searches; only add links with source evidence.
5. If a game installation and keys become available on a supported environment, execute the documented FModel extraction and verify selected ConfigDB, TextMap, Sequence, movie, and audio assets.

## Completion gate

For Quest `168800009`, the local command `./extract-quest 168800009` reproducibly emits a canonical JSON and DOT graph with quest metadata, ordered flow/state/action records, localized dialogue, speaker IDs, player choices/branch edges, narration, gameplay actions, two cutscene references, and source-file provenance. Denia Quest `119000000` is separately extracted as a benchmark. Extraction is deterministic and uses no LLM. The remaining limits above are explicit gaps in source coverage or asset access, not blockers to the demonstrated data-table pipeline.
