# Current game data and historical observations

The current product priority is content available in the current game. Map/client
asset versions identify the source dataset, not the patch that introduced a region.
The map selector therefore names regions, while Layers & display exposes the client
version as "Map data". It chooses the latest available root per game-map/layer;
explicit shared links still resolve their original snapshot and marker IDs.

## Recommended source model

- Import the current GitHub text/configuration snapshot for quests, dialogue,
  items, names, and authored links.
- Download the current game client and voice packages for maps, placements,
  textures, images, video, and audio. Keep the asset build/manifest identity.
- Historical GitHub snapshots are optional evidence for "first observed in" and
  change history. They do not require downloading historical game clients.
- First observed in retained tables is not proof of a public introduction patch:
  files can contain unreleased, unused, or inaccessible content.
- A retained video configuration row does not prove the referenced media binary
  exists or is unchanged. Publication still requires successful asset resolution.

To queue the current text/configuration snapshot with the existing pipeline:

```sh
wuwa-story-worker enqueue-snapshot --version 3.7
```

This pins the upstream commit and enqueues a job; a running snapshot worker then
checks out, compiles, and imports it. Asset jobs are separate. The command is not
a replacement for migrations or a guarantee of coverage before the job succeeds.
No full snapshot import was started as part of the map-label correction.

## Source comparison, 2026-10-03

Compared exact IDs in Arikatsu/WutheringWaves_Data commits:

- 1.0: `a1b7b62d0476364d4af70202756853a91304e59b`
- 3.7: `9218d612ad815e398e064e577e42aaf878899968`

| Table / identity | 1.0 IDs | 3.7 IDs | Old IDs retained | Old IDs absent |
| --- | ---: | ---: | ---: | ---: |
| QuestData/questdata / QuestId | 364 | 1,899 | 364 | 0 |
| item/iteminfo / Id | 626 | 2,292 | 626 | 0 |
| area/area / AreaId | 110 | 524 | 110 | 0 |
| cgVedio/videodata / CgId | 24 | 342 | 24 | 0 |
| flowState/flowstate / StateKey | 3,076 | 21,156 | 3,046 | 30 |

These are configuration identities under `BinData`, not counts of playable quests,
public regions, exported videos, or identical dialogue. The opening quest
139000025 is present in the current QuestData table. This comparison supports using
current tables for old content still present; it does not assert every old line,
audio recording, or condition survives unchanged.

## Pipeline audit and boundaries

`snapshot_jobs.py` compiles JSON datamine snapshots. Exporting ConfigDB from a game
client does not currently replace that compiler input: client FlatBuffer readers
cover specific map/media needs, rather than the full narrative compiler contract.
Thus the supported current-content path is **current client plus current JSON
snapshot**, not a client-only import.

`import-series` sorts compiled manifests by numeric game version. Cross-major
ranges now stop each earlier major at its latest supplied minor instead of
requiring imaginary minors through .99. It still checks explicit endpoints,
missing major versions, and gaps inside each supplied minor range. It cannot
discover a missing tail release without an authoritative upstream manifest list.

Canonical graph keys and per-release revisions already link repeated identities,
localizations retain release/locale associations, and map publication uses an
immutable asset-job identity. Historical imports are not yet a fully independent
versioned view of every typed entity:

- `Node.created_release_id` records the initial database insertion. It must not be
  used as the earliest chronological observation when imports arrive out of order.
  The story-map first-observed calculation instead reads versioned evidence.
- Character/item/location projections are overwritten by import order; most other
  typed projections keep their first inserted row. Merely importing every snapshot
  does not make all typed fields accurately versioned.
- Search documents have one row per entity/category/locale and are rebuilt by each
  import. They are not a per-release historical search index.
- The catalog currently combines active identities; absence from a newer snapshot
  is not automatically proof of removal and does not remove the old catalog row.

Consequently, importing 1.0 through 3.7 is not claimed here as a complete solution
for current-only browsing or historical reconstruction. Further work must define
snapshot-specific typed projections and current membership before enabling that
as a production invariant. Historical rows have not been deleted or assigned
guessed release dates by this change.
