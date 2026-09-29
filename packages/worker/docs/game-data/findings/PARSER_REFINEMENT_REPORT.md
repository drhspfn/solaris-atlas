# Parser refinement report — Game 3.6.0

## Summary

The existing deterministic compiler was extended in place. It now emits explicit locale resolution states, a field-strength table inventory, additional exact ancillary references, source-backed coverage metrics, exact whitespace-only cutscene normalization, and a separate strict-coverage gate. The 3.6.0 build was run with both `--strict` and `--strict-coverage`; validation reports 400,297 entities and 615,328 edges with zero duplicate IDs, dangling edges, missing edge provenance, raw-evidence hash mismatches, or build errors. No LLM, database or fuzzy entity matching was used.

## Gap table

| Gap | Before | After | Remaining |
| --- | ---: | ---: | ---: |
| Localization missing/empty distinction | Present keys treated as resolved | Four states; 324,306 identities per locale | 706 referenced identities missing from English source; locale file absence is 0 in this snapshot |
| Empty localization values | Not separately counted | English: 56,407 empty; Russian/Vietnamese: all 324,306 empty | Empty values are source data, not translations |
| Tier-1 unsupported tables | Classifier mixed path heuristics and strong refs | Tiers + 9 normalized / 15 partial / 49 raw-evidence-only Tier-1 tables; strict coverage passes | 49 Tier-1 tables retain raw evidence without full semantic normalizers |
| Unclassified TalkItems | No dedicated metric | 107,591 / 107,591 accounted; 0 unclassified | Unknown actions inside TalkItems remain source-level diagnostics if encountered |
| PlotAudio without direct TalkItem relation | Not summarized | 415 of 40,597 IDs unreferenced by direct `TidTalk` join; 40,701 occurrences | Corpus purpose is not proven from names/text collisions |
| Unresolved cutscenes | 8 raw unresolved action values | 146 resolved, 7 unresolved after unique whitespace normalization | 6 nonempty names and one empty value remain unresolved |
| Unresolved speakers | ID equality risk | Exact namespaces kept separate; 114 name-only candidates are diagnostic | No explicit speaker↔role/NPC foreign key found |
| Walking/ambient dialogue | No separate source path proven | Inspected FlowState, audio/interjection, entity audio, NPC actions, interaction/runtime candidates | Current data does not prove separate transcripts outside FlowState/TalkItem |
| Battle dialogue | Ordinary FlowState only | PhantomBattle provides 155 exact flow-state refs and 17 win-sequence source rows | No guaranteed transcript coverage outside those refs |

## Confirmed new deterministic relations

- Exact `RelatedQuestId` from `instance_dungeon/instancedungeon.json` to a canonical QuestId (only nonzero record in the snapshot).
- Exact `QuestRefMapBlock.MapBlockId[]` → `DownLoad/mapblockinfo.BlockId` (54 of 54 pointers resolve).
- Exact flow triples from BubbleData, InteractData, LevelPlayData/NodeData, GuessJokerCard, and PhantomBattle dialogue when the full identity tuple is present in one raw object.
- Exact FlowState `PlayMovie.VideoName` → `CgName`; whitespace is normalized only when it uniquely identifies one CgName, while raw input is retained.
- Exact `videoqte.CgName` → cutscene group.

Every emitted reference retains source file, row, raw path and basis. Conditions remain runtime conditions; they are not converted into a linear timeline.

## Still unresolved

- Walking/ambient voice content cannot be deterministically reconstructed from `entity_audio`, `audio_interjection` and opaque `npc_actions` records: no proven line/speaker/text/flow bridge was found.
- 415 PlotAudio IDs have no direct TalkItem reference; classification as dead, ambient, battle or cut content is unsupported.
- Six nonempty PlayMovie names (`M0320`, `M0323`, `WJZY`, `C3zhuashanghen`, `C3shanghenBOSS`, `ShangHenBaoPo1`) and one empty value remain unresolved. Similar tokens in sequence asset paths are not sufficient evidence.
- No explicit speaker-to-character/NPC ID crosswalk was found; name candidates are not canonical edges.
- 706 referenced localization keys are missing in English, with 814 conflicting supplementary localization records in diagnostics.
- 179 external flow-state and 462 external quest references remain unresolved; unsupported Tier 1/2 schemas are retained as raw evidence.
- Physical Wwise/movie media bytes and package membership remain outside this compiler.

## Proposed next phase

Freeze and review the source canonical output and diagnostics, then design a separate semantic layer over this deterministic dataset. Any later database, embeddings, event extraction or graph inference should retain source IDs and provenance and must not rewrite this source layer.
