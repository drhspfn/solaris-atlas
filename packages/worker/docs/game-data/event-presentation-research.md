# Event presentation research

Scope: client 3.7 JavaScript/configuration extracted for the event catalog. This
is an inventory of observed mechanisms, not proof that every event has been
decoded or that any specific animation can already be played on the website.

## Character trials

`ActivitySubViewRoleTrial` selects a trial entry from the activity's live
server-supplied `RoleTrialTaskList`. `RoleTrialInfo` resolves that entry to a
`RoleId` and `TrialRoleId`; `RoleTrialRoleConfig` is then looked up by `RoleId`.
Its `FormationSpineSkeletonData`, `FormationSpineAtlas`, `SpineParam`,
`RoleStand2` and `UiConfigId` define the portrait, positioning and backdrop.
The client sets the Spine animation `idle` to loop. The character compiler
records this exact character-to-resource association in character metadata.

The client receives active trial membership and start/end times from the
server. Do not infer a historical event-to-character association from a shared
title, a similar numeric ID or the mere existence of a portrait. Persist those
links only when an authored or captured source proves them, including its
version, identity and time range. The metadata paths are not exported assets;
they must still be fetched, bundled, converted and published before playback.

## Other activity presentation mechanisms

The shared `CommonActivityView` loads the activity `BgTexturePath`, creates an
activity-specific prefab/subview, and plays UI level sequences. Those layers
can coexist, so a single background path does not describe the whole screen.

* **Spine:** trial portraits; `db_DangoBrawl` explicitly contains
  `SpineAtlasPath` and `SpineSkeletonDataPath` for Cubie Wars characters.
  That does not establish that the large Cubie Wars entry-screen background
  is itself a Spine animation.
* **UI textures and atlases:** `UITexture` assets and sprite layers can be
  moved or faded by authored sequences.
* **Level sequences and prefabs:** control animation, transitions and nested
  UI. Prefab dependency traversal currently finds images and walks Spine
  dependencies, but event imports do not publish portable Spine bundles or
  reconstruct these compositions.
* **3D scene/animation:** Cubie Wars data also references character blueprints,
  animation blueprints and skeletal animation montages; these are distinct
  from its Spine assets.
* **Video and embedded web content:** appear in other activity modules. The
  Waking Moon Fishing activity uses H5View and a dedicated
  `UiItem_GoldfishScoopingMainBg` prefab; the exact background mechanism has
  not yet been established.
* **Effects and materials:** effect, particle and material dependencies may
  contribute to composition. UI sequences, materials, shaders and timing
  must be inspected rather than assumed to be baked into Spine.

A literal-name scan of the extracted `ActivityContent` JavaScript found Spine
references in 46 files across 21 module directories, level-sequence references
in 204 files across 56 directories, and video-related names in 3 files across
3 directories. These counts indicate leads only: they do not enumerate
prefab-only implementations, validate every match, or cover all game screens.

For actual website playback, extract the specific prefab and dependencies,
retain their provenance and failed dependencies, determine sequence/animation
control and rendering layers, then convert and validate the Spine bundle with
the independent engine. Preserve a static fallback until the full composition
is supported. Never treat an unrelated event's assets as a verified match.

## Conversion and storage investigation

Story Spine already uses `build_spine_bundle` and `portable_bundle`: the ZIP
contains cooked packages, decoded JSON, binary skeletons, atlas text, texture
pages, checksums, and where available control/presentation information. It is
registered as `story_spine`; optional `story_spine_part` references expose
individual browser assets. Character-trial paths are not yet scheduled through
this worker and these ZIPs are not yet converted by Riglyph.

The independent engine is now at `E:/Projects/Riglyph` (version 0.15.1 at the
time of investigation) and its CLI is published on npm. It supports `riglyph convert <file> --atlas
<file> --images <directory> --out-dir <directory>`, producing a manifest, IR
and texture pages. Its documentation states that unsupported semantics fail
explicitly. The worker image installs the pinned CLI using a lockfile. With
`WUWA_RIGLYPH_ENABLED=true`, newly built, single-rig story bundles are converted
and saved as a file variant; source bundles are retained. This does not yet
convert already-published archives or publish a browser player.

Migration must handle multiple skeletons, variants, nested atlas paths and
controller-only archives. Validate each archive, all conversions and uploaded
objects before an atomic DB reference swap. Files are content-addressed and
`FileReference` and `FileVariant` may share a `FileObject`, so a successful
conversion of one owner is not evidence that the old object is safe to delete.
Delete only after checking all remaining references and variants with suitable
concurrency protection, and retain the original whenever conversion is
unsupported, ambiguous or fails. Conversion and publication must be resumable
and idempotent; incomplete outputs must never replace the source.
