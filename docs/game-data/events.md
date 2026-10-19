# Event catalog from the game client

The event import reads the downloaded client, not a community repository. The
historical importer remains only for compatibility with old jobs/data; the admin
**Import client events** action does not call it.

## Sources and identities

- `Activity` supplies activities, titles, descriptions, opening-type flags,
  reward previews and resource references.
- `GachaViewInfo` supplies all convene presentations, featured/UP entity IDs,
  background/content/title textures, sprites and spine/prefab references.
- Both `lang_multi_text_1sthalf.db` and `lang_multi_text.db` supply localization.
  Activity `Title` is field 21; `Name` is field 20 and is only a fallback. Keys
  must be taken from the row, not derived from the activity ID.
- Weapon selection banners use types beyond type 3. Resolve featured IDs against
  actual RoleInfo/WeaponConf localization rather than assuming every other type
  is a character banner. Preserve unresolved marketing-text keys; do not invent
  campaign names from filenames. Some campaign text is authored as a texture.
  Exact featured entity IDs also resolve client portraits and weapon icons as
  static previews when the gacha artwork is an animated spine/3D composition.
- Generated `Config` readers identify supplementary string fields. Generated
  `ConfigQuery` declarations identify the authoritative database for each table.
  Older aggregate databases contain incompatible copies of some tables and must
  not be decoded with the current reader.
- Supplementary records join through explicit `ActivityId` fields. `UiResource`
  tags join through `ActivitySplitPak.ActivityTypeString`; spine keys join by exact
  UiResource ID. Matching filenames or coincidentally equal row IDs is insufficient.
- DropPackage.DropPreview supplies preview item IDs/counts. Item, role and weapon
  localization supplies reward names and featured entity names. A preview is not
  a guarantee of drop probability.

Client event identities are `activity:<id>` and `gacha:<id>` under source
`wuwa-game-client`. Existing exact historical matches keep their numeric event ID
and dated occurrences. The former source/identity are retained as
`source_data.legacy_source` and `legacy_source_id`. New events need no timetable
entry to be discovered. Reimports preserve derived prefab artwork for the same
client download, reuse event identities, and retry incomplete children.

## Media pipeline

1. `prepare` selects the downloaded client using the existing media workflow.
2. `event_catalog` exports ConfigDB and its generated readers, enumerates the full
   catalog, checks image package presence, and commits localized events.
3. Direct image jobs run in batches of 20. UI dependency jobs run one event at a
   time, following typed prefab/material/spine references to images. This finds
   artwork that is not in Activity.BgResource, including matrix backgrounds.
4. New image references discovered in prefabs enqueue further bounded image jobs.
   Successful branches continue when a different dependency is missing.
5. Published PNGs retain exact source paths and original package variants. The API
   exposes all artwork references with an available URL or an explicit missing
   package status; the event detail offers a gallery of published images.

The pinned FModel CLI accepts an OR-list of path filters so each image batch
mounts the client once. Sprites export their atlas directory and match normalized
sprite references; sprite output filenames include a source-path hash. Texture
conversion remains limited to eight packages per subprocess. Shared decode paths
use nonblocking locks, and crop files are replaced atomically.

Do not interpret a completed catalog as completed media publication. Inspect its
child tasks. Missing input packages are `not_in_client`; extraction/decode failures
have separate task diagnostics. They are not described as unpublished future art.

## Dates and version meaning

The client includes past, current, future and hidden configuration rows. Presence
in a 3.7 client is not proof that an event is running in 3.7. Opening windows and
limit-to-permanent phase changes are supplied by the game server. Preserve known
historical dates, leave unknown dates null, and show the client catalog version
separately. Do not fabricate current schedules from asset names or IDs. Activity
OpenType 1/2 identifies permanent/transition content; known cyclic tower/matrix
activity types are grouped as recurring. Runtime phase/availability remains
server-controlled.

## Verification against downloaded client 3.7.0 (2026-10-19)

- 378 Activity rows and 254 GachaViewInfo rows; all 632 resolve English names.
- 349 limited, 25 permanent/transition, 4 recurring and 254 convene configuration
  rows under the grouping above. These are configuration counts, not active-event
  counts.
- 4,711 distinct directly linked image/sprite packages; 4,451 are present in the
  mounted archives and 260 are referenced but absent. Additional prefab images
  are discovered in the dependency stage; 557 events reference UI prefabs.
- The matrix entry prefab walk resolved 53 images, including its actual main
  background, with no failed dependencies in that walk.
- Lynae, matrix and Chord Cleansing backgrounds were exported and decoded to
  3200x1440 PNGs in the Linux worker container.
- An isolated PostgreSQL 17 import created 632 events and 1,896 server occurrences;
  a repeated import preserved counts, event IDs and existing historical dates.

These checks used game files and a disposable test database. They did not replace
production event records or publish the full media catalog to production storage.
Missing historical packages require a client build that actually contains them;
a reimport of the same build cannot recover bytes absent from its archives.

## Deployment and rollback

Rebuild the worker image: the FModel filter change is part of the bundled binary,
not just Python. Deploy snapshot/media workers first (they accept old payloads),
then API and web, and run **Import client events** once. Old worker binaries cannot
process the new catalog payload. Updated extractor binaries get a new immutable
export directory; old export receipts are retained.

No schema migration is required. Back up event tables before the first production
catalog conversion. Rolling back to the old API also requires restoring migrated
legacy source/identity values from the retained provenance, checking uniqueness
first. Newly discovered client-only rows can remain stored and are ignored by the
old API. Do not run the legacy community importer during this transition.
