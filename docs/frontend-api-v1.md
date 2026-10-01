# Frontend API contract — iteration 1

Base URL in local Compose: `http://localhost:8000`. All responses are JSON. Search and card responses preserve canonical keys and source provenance where applicable. Relationship evidence is deterministic; authored order and runtime traversal are labeled separately.

## Browse categories

```http
GET /categories
```

`browse_categories` is the UI facet list: `character`, `item`, `location`, `quest`, `speaker`, and `dialogue`. `categories` retains the raw graph node-type inventory. The UI should use `browse_categories`; `location` maps to raw node types `area` and `location`.

The frontend catalog pages use `GET /catalog?category=character&locale=en&limit=24&offset=0` for alphabetized browse pages without a search term. Supported categories are character, item, location, quest, and speaker.

## Search catalog

```http
GET /search?q=pecok&category=item&locale=en&sort_by=relevance&sort_order=desc&limit=20&offset=0
```

- Repeat `category` to search across multiple categories: `category=character&category=location`.
- `location` searches raw node types `area` and `location`.
- `scope=dialogue` searches localized dialogue text through the same `/search` endpoint; optionally pass `character=character%3A1211` or `quest_id=119000000`. Dialogue results use authored order. `category=dialogue` is a shorthand for this scope.
- `locale`: source locale code such as `en`, `ja`, `zh-Hans` (Simplified Chinese), or `zh-Hant` (Traditional Chinese). The 1.0 snapshot also has substantial `ko`, `de`, `es`, and `fr` text. Seeded locale codes alone do not guarantee translated content; `ru` is nearly empty in this snapshot.
- `sort_by`: `relevance` or `name`; `sort_order`: `asc` or `desc`.
- `limit` is 1–100; `offset` is zero-based.
- Each result has `canonical_key`, raw `node_type`, UI `category`, matched alias, and lexical score. `snapshot_version` identifies the imported search index. `semantic_available` remains false.
- Entity search runs against the active imported snapshot. Requesting a different `game_version` returns `409` rather than silently searching the wrong snapshot.

## Entity cards

```http
GET /characters/{canonical_key}/profile?locale=en&game_version=3.6.0
GET /items/{canonical_key}/profile?locale=en&game_version=3.6.0
GET /locations/{canonical_key}/profile?locale=en
GET /quests/{game_quest_id}/profile?locale=en&game_version=3.6.0
```

Character cards return deterministic speaker crosswalks, quests with dialogue, co-present speakers/characters, explicit links, and grouped config evidence. Co-presence means speakers occur in a shared authored flow state; it is not a personal relationship. `media.asset_references` contains raw Unreal paths; media bytes are not served yet.

Item cards return localized name and description, raw Icon paths for later media delivery, exact `ItemAccess[] → AccessPath.Id` joins with localized labels, and exact item references in `EnrichmentAreaConfig`. `harvest_sources` preserve `LevelId`, `EnrichmentId`, and entity IDs; `harvest_world_maps` joins those IDs to `EntityVoxelInfo.EntityId` and returns proven map IDs. A map ID can span several named areas/floors, so the card does not claim a precise harvest location until that final mapping is available. Quest references are grouped by quest and retain the specific QuestNodeData records.

Location cards expose the `area.Father` parent/child hierarchy and direct source-backed links.

Quest cards expose QuestNodeData nodes and exact `Data.ParentNodeId` hierarchy. `source_array_order` is source array order, not player traversal. `flow_states` contain actions ordered by `action_index`; scenes are ordered by authored scene order. `quest_prerequisites` are explicit `PreQuest` runtime conditions, not a claim that one quest immediately follows another. `tree_edges` retains imported source relations.

## Narrative reading and graph browsing

```http
GET /quests/{game_quest_id}/transcript?locale=en&game_version=3.6.0
GET /quests/{game_quest_id}/transcript?character=character%3A1211&locale=en&game_version=3.6.0
GET /search?q=Hiyuki&scope=dialogue&locale=en&game_version=3.6.0
GET /dialogue/search?q=Hiyuki&locale=en&game_version=3.6.0
GET /nodes/{canonical_key}/related?direction=both&category=character&limit=50
```

Omit the transcript `character` filter for the full authored conversation. Dialogue search finds text mentions and reports the actual speaker; a name mention does not establish speaker identity. `/related` hides raw source-reference rows by default and includes a grouped `source_evidence` summary; use `include_evidence=true` and `source_file=...` to inspect those records.

## Current data boundaries

The API returns upstream logical asset paths, not image URLs or media bytes. Search indexes the currently imported snapshot and reports its version; it does not yet select historical search indexes. Exact item acquisition path labels are available for Pecok Flower, but its enrichment entity records currently resolve only to map-level references; the API leaves named harvest locations unresolved instead of guessing.
