# Quest runtime blocks and illustrated dialogue

The transcript combines source-authored quest/LevelPlay tree positions with explicit
runtime references. Tree order describes authored branches, not a recorded player
playthrough. Conditional branches remain labelled. Entity interactions have no
invented chronological position: they appear as expandable activities within the
owning `CheckLevelPlay` objective.

## Client fallback

The existing `tree_order` media job reads `db_LevelPlayData.db` and
`db_level_entity.db` from its pinned ConfigDB export. It stores a compact interaction
index on the root of `core.authored_tree_order.nodes`, alongside the existing tree
index. This avoids retaining or importing the entire large entity table for every
patch. Index receipt schema 3 supersedes the previous schema 2 receipt.

An interaction needs explicit `Reference`/`Children` entity membership and a
`PlayFlow` action or a `Type: Flow` option. Weak references, nearby IDs and matching
titles do not establish ownership. Client entity IDs are checked against their
FlatBuffer payloads. Entries preserve the entity ID, option index, resource paths,
conditions and blob SHA-256; the enclosing index pins the client version/download.

Historical recovery requires matching scenario membership and matching tree-node
records. A changed client scenario is not silently substituted into an older patch.
Source graph repair adds idempotent `references_dungeon_flow_state` evidence so
node ownership, quest analysis and the transcript can follow the same references.

`PlayMp4` teleport transitions are explicit quest cutscene references even when no
`PlayMovie` flow action exists. For example, `M0355_Loading` belongs to Starlights
from Yesterdays through quest node `121000040_119`; it is unrelated to Denia's quest.

## Pictures and engine scenes

`TalkItems[].BackgroundConfig` supports `Image`, `ImageByMcGender`, `SpineImage`
and `Clean`. Static backgrounds attach to their authored talk items, persist across
subsequent talks in that state and clear explicitly. Both Rover variants retain
their original paths. Image extraction uses existing entity-media jobs and immutable
R2 objects. Unexported pictures remain visible as missing exports, not a guessed image.

Spine backgrounds resolve their resource ID through the pinned SpineBackground and
UiResource ConfigDB tables. Existing image workers archive a deterministic ZIP with
portable `.skel`/`.atlas` data, decoded atlas-page textures, original game packages,
prefab/material JSON, hashes and client identity. FileReference `story_spine` binds
the immutable R2 object to its authored background. Shared default UI sprites stay
explicit references rather than forcing a huge common-atlas export. Successful
static pictures survive a failed Spine dependency; missing bundles remain retryable.
Verified 4.1 mesh scenes now play through an independent open JSON adapter and
WebGL player; unsupported features retain the archive and an explicit diagnostic. `PlaySequenceData` remains a distinct engine
scene with its source path. It is not relabelled as an MP4 or replaced with a nearby
sequence asset's text. Static picture dialogue renders inline without an MP4.

The quest reader groups consecutive illustrated lines into a scene with the actual
line's speaker, text and existing voice track. Navigation pauses audio; a transcript
view and direct line anchors preserve access to every recorded line. Complete,
disjoint choice branches appear as parallel alternatives with a shared continuation
shown once. Partial, overlapping and nested branches retain the source transcript
rather than inventing a runtime path. On narrow screens, alternatives scroll within
their own rail instead of widening the page.

## Scoped repair

For the complete story repair pipeline, preview and then queue:

```sh
python -m wuwa_story.ingestion.story_repair --first 1.0.0 --last 3.7.0 --complete --dry-run
python -m wuwa_story.ingestion.story_repair --first 1.0.0 --last 3.7.0 --complete
```

This first repairs retained links and queues the pinned client structure index.
Only after that index finishes successfully does its worker repair the expanded
graph and enqueue story illustrations (including Spine archives) and cutscene
media. The follow-up includes the existing multilingual movie-audio preparation,
but does not import standalone voice lines, character models or catalog artwork.
Existing publication/provenance checks reuse matching files. Partial indexes retain
diagnostics and do not silently proceed as complete. Repeating the command also
resumes media dispatch when the index was already completed, including broker retries.

The browser uses the existing Three.js WebGL dependency, not the official Spine
Runtime or an installed editor. Adapter provenance, feature limits and verification
are documented in [spine-open-playback.md](spine-open-playback.md).

After deploying the code, inspect retained-source repairs first:

```sh
python -m wuwa_story.ingestion.story_repair --first 2.0.0 --last 3.7.0 --dry-run
```

Then queue only the client structure index and illustrated dialogue images:

```sh
python -m wuwa_story.ingestion.story_repair --first 2.0.0 --last 3.7.0 --client-index --illustrations
```

This reuses normal client preparation and existing queue deduplication. It does not
queue voice conversion, cutscene transcoding or full patch imports. Each structure
job repairs graph links after indexing, including when its pinned client index is
reused. Repeating the command adds missing evidence and reuses completed tasks.
Illustration jobs use schema 3 and Spine recipe 1 so previously completed image-only
or archive-only jobs can acquire browser JSON and textures without repeating unrelated media work.
Workspace retention/cleanup follows the existing media workspace lifecycle.

No schema migration is required. Rolling back the application leaves older readers
able to ignore the extra root-node metadata; source evidence remains auditable.

## Verified cases

Local 3.7 client data matched all 90 retained quest-node records and 136 scenario-node
records for Denia's quest. The index recovered ten explicit room interactions,
including optional flow states 41–45. The corresponding scenario membership hash
matched the retained snapshot. This is source verification, not a claim that every
branch of every game quest has been replayed or recovered.
