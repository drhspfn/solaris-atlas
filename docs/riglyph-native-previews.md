# Native animation previews

Riglyph CLI and its dependencies are pinned to **0.16.1**. The worker image includes
the release's Linux x64 Rust executable, verified against the published SHA-256.
Node evaluates the rig; Rust rasterizes it; FFmpeg encodes transparent VP9 WebM.
The browser loads the video, not the binary skeleton or an atlas bundle.

## Import and storage

The same derivative path runs for supported character trials, event resources and
story Spine archives. It accepts an explicitly rooted single rig with its atlas
and textures, complete dependency export, and loss-free conversion diagnostics.
Unknown UI compositions, multiple rigs, materials and sequences keep their static
artwork. Conversion alone does not prove that a whole event screen is reproduced.

Each source `FileReference.file_id` continues to point to the original portable
archive. It retains the game paths, client version, source packages and controls.
`riglyph_bundle` links a deterministic converted IR/texture archive;
`riglyph_preview` links each rendered clip. Reference metadata carries
`riglyph_file_id`, `animation_import` and `animation_preview` (recipe
`riglyph-native-v1`, source file ID, default animation, clip URLs and dimensions).
File identities and object keys use the existing content-addressed registration.

Named clips are exported independently, including a single frame for a zero-length
pose. Only an explicit authored default, `idle`, or an unambiguous sole clip can
be selected automatically. `PlaySpine` commands select exact names on their
authored dialogue lines; no chronology is inferred from names such as `1to2`.
An unresolved prefab/controller is not turned into a guessed story video.

If native rendering fails after conversion, retain the converted archive and the
original, record `render_failed`, and keep the static image. Render/conversion
logs live separately at `WUWA_ASSET_WORKSPACE/render-logs/<source-sha>/<attempt>`.

## Canvas and quality

There is **no fixed width, height or 16:9 output profile**. Positive authored
skeleton bounds define the canvas and fixed camera. A zero-bounds source uses
Riglyph's evaluated draw-packet envelope across all frames/clips; metadata labels
this `animation_bounds`, not a confirmed game camera. Clips from one source use
the same canvas, so changing animations cannot change zoom.

The source aspect is preserved. `WUWA_RIGLYPH_MAX_EDGE` only caps the longest edge
proportionally, never upscales, and defaults to 2048. VP9 dimensions are rounded to
even pixels with camera padding instead of stretching geometry. Default rate:
30 FPS, two native threads. A package is bounded to 64 clips, 180 seconds per clip,
18,000 total frames and a 256 MiB renderer allocation profile. Unsupported or
oversized sources remain available as static artwork/source archives.

## Enable and backfill

Set `WUWA_RIGLYPH_ENABLED=true` on extraction workers to generate new derivatives.
Set `STORY_SPINE_PLAYBACK_ENABLED=true` on the API to expose previews for story,
character and event views. Rendering and public display are independent; both
defaults remain false. Production deployment forwards these GitHub environment
variables and optional `WUWA_RIGLYPH_MAX_EDGE`, `WUWA_RIGLYPH_FPS`,
`WUWA_RIGLYPH_THREADS`, `WUWA_RIGLYPH_MAX_FRAMES`,
`WUWA_RIGLYPH_TIMEOUT_SECONDS` settings.

Backfill existing archives without downloading/exporting the client again:

```sh
wuwa-riglyph-backfill --limit 20                    # dry run, no writes/downloads
wuwa-riglyph-backfill --apply --limit 20
wuwa-riglyph-backfill --apply --limit 20 --cursor 12345
wuwa-riglyph-backfill --apply --ref-id 606192        # scoped retry
wuwa-riglyph-backfill --apply --ref-id 606192 --rebuild
```

Use a **direct PostgreSQL connection**, not transaction-pooled PgBouncer. The
backfill holds a namespaced session lock, renders outside the publication
transaction, rechecks source identity and atomically publishes metadata with the
existing cache invalidation triggers. Ready/static decisions for the same source
and recipe are skipped. `--rebuild` reevaluates them. Failed references are reported
individually and remain retryable. `next_cursor` advances the bounded scan;
retry failed/locked IDs explicitly before discarding a batch report.

No schema migration or bulk source replacement is needed. Existing read
projections refresh through normal file-reference invalidation. Rollback can
disable API playback immediately while leaving all source files and static
artwork intact. To stop future rendering, disable the worker flag independently.

## Verification

The branch's tests cover rejection, bounds/aspect, pose export, alpha/codec checks,
IR fallback, deterministic archives, publication, provenance, idempotency,
source replacement, backfill concurrency, display flags and frontend selection.
Real uncommitted game samples exercise Chixia, Cubie and Fishing. A Linux
container smoke test using the release ELF, Node 22 and FFmpeg completed a
120-frame Chixia WebM under 2 GiB/2 CPU limits with decoded alpha verification.
An isolated migrated PostgreSQL database verified publication, retained source
identity, repeat registration without duplicates and display-flag behavior.
Browser fixtures use local media and mock API responses, not production data.

These tests do not establish visual equivalence for every game prefab or every
character. Full UI sequences/materials, cross-rig composition, background audio
mixing and automatic nonlinear story traversal remain outside the supported
preview profile and are deliberately not activated by this change.
