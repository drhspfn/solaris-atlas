# Tier 1 table investigation

Tier 1 classification is driven by strong field evidence (quest/flow/state/action/talk/sequence/cutscene references) before path/name hints. Tier 2 is strong narrative content/localization/speaker/audio metadata; Tier 3 is filename-only; out-of-scope tables have neither. Full file inventory, shape, row count and field paths are in `dist/3.6.0/coverage.json`.

Investigated Tier 1 schemas and treatment:

| Table | Observed evidence | Deterministic treatment |
|---|---|---|
| BubbleData | FlowListName + FlowId + StateId inside Params | Exact flow-state reference; raw row retained |
| InteractData | Type.Flow / Params flow triples, QuestId | Exact flow pointer and explicit/runtime quest refs by field path |
| LevelPlayData | conditions and nested quest prerequisites | Preserve raw; quest IDs in condition/pre-child contexts are runtime-condition edges |
| LevelPlayNodeData | Flow triples and quest conditions | Exact flow-state and runtime-condition refs |
| PhantomBattle dialog | PlotName + FlowId + StateId | Exact flow-state edge; all 155 observed records resolve |
| GuessJokerCard | full flow triple | Exact flow-state edge |
| QuestRefMapBlock | QuestId, MapBlockId[] | Quest config link and exact MapBlockId to DownLoad/mapblockinfo.BlockId (54/54 refs resolve) |
| QuestTreeCustomJumpConfig | QuestId, precondition/jump types and ParamsId | Quest link only; ParamsId unresolved because no exact target join was proven |
| RefResourceQuestList | QuestId | Explicit quest association |
| custom_sequence | empty table in snapshot | Classified; no records to normalize |
| instance_dungeon | RelatedQuestId | Added partial normalizer; nonzero ID links to QuestData by exact quest identity |

Generic Tier 1 candidates without a dedicated semantic normalizer retain the complete raw row and only materialize explicit QuestId or same-object FlowListName/FlowId/StateId references. No numeric coincidence joins are made. The 3.6 strict-coverage check now reports no new Tier 1 tables, unclassified Tier 1 tables, missing required tables or unknown strong fields. Current inventory totals are emitted in `coverage-summary.json`; Tier 3 filename candidates do not fail strict coverage.
