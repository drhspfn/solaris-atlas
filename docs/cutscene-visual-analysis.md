# Cutscene visual evidence

The supplied Colab notebook is an experiment, not an import specification. Keep
its chronological frame sampling and separate visual observations from dialogue.
Do not copy its unlimited merge request or model pricing assumptions.

## Delivery slices

1. Bounded, versioned observations and durable jobs keyed by published video hash,
   asset version, model and sampling settings. Reuse processing runs, file
   references, provider adapters and the shared daily spending ledger.
2. Worker sampling and checkpointed vision calls. Each small batch must be
   complete and validated; split output-limited batches rather than accepting
   partial JSON. Assemble the chronological observations without a paid global
   merge. Imports enqueue work transactionally; queue recovery reads pending jobs.
3. Quest tools expose cutscene inventory and paginated visual observations with
   provenance and variant identity. The story agent writes reader descriptions
   and optional timed chapters from these observations and exact dialogue.
4. Keep primary narrative role independent of secondary functions. Source-tagged
   main quests require full analysis. Consolidate unknown branch occurrence in
   one disclosure; do not infer mandatory flow from the quest's main-quest label.

## Verification and operations

Use mock providers for implementation checks. Do not start paid backfills or
change the existing daily allowance. Enable automatic vision through worker
configuration after selecting a vision-capable model and its rates. Budget,
unknown remote outcome and source changes stop work without publishing a partial
description. Old explanations remain readable. Sampled frames do not prove the
absence of events between frames. Variants retain their own observations; they
are never concatenated into one supposedly canonical playthrough.

### Enable and run

Set `AGENT_VISION_ENABLED=true` in `packages/worker/.env` and, for administrator
controls, `packages/server/.env`. Keep the model, rates and shared budget settings
consistent on both services. This uses the existing
`AGENT_PROVIDER`, `AGENT_MODEL`, endpoint and prices, and the same $1 daily ledger
as quest analysis. The configured model must accept images. Defaults: one frame
every approximately 3 seconds, at most 600 frames, 48 frames per request and
8192 output tokens. The capped sampling plan always includes the ending; it is
not exhaustive coverage. Each supplied frame needs its own observation.

### Dense frame atlases (visual protocol v2)

The default request contains up to four JPEG contact sheets of twelve frames,
instead of twelve requests containing four separate images. Each sheet uses four
columns with 384-pixel-wide, letterboxed thumbnails and a separate timestamp strip.
Cells run left-to-right, then top-to-bottom. Labels and the accompanying manifest
give original video seconds; every sampled timestamp still needs an observation.
Chat/Responses image inputs use `detail=high`; JPEG quality is 85. A higher image
detail is needed to read individual cells. Fewer requests do not guarantee lower
token usage; the existing spending ledger still reserves each request before sending.

Tune `AGENT_VISION_BATCH_FRAMES` (1..64), `AGENT_VISION_ATLAS_FRAMES` (1..24),
`AGENT_VISION_ATLAS_TILE_WIDTH` (160..640), and `AGENT_VISION_ATLAS_QUALITY` (60..95).
Input-bound batches split locally; output-limited batches keep the existing
checkpointed split behavior. Density changes grouping, not the sampling interval.

Atlases are uploaded through the existing content-addressed S3/R2 registration
service before any paid call. `video_frame_atlas` file variants link each sheet to
the exact source video file. File references include the source SHA-256, sheet
dimensions, cell rectangles and timestamps; checkpoints and completed reports keep
their reference/file IDs. They survive removal of the worker's temporary directory.
Publication is idempotent under a transaction lock. Old v1 jobs resume with their
original single-frame input format; completed reports are not backfilled automatically.

At enqueue time, source English captions are frozen as a bounded context snapshot
(up to 100 rows / 12,000 JSON characters) and included in the job identity. Graph
links, source records and localization must match the video's recording build;
there is no fallback to an unrelated quest or historical snapshot. Missing captions
leave explicit empty context. Text is reference data, never visual evidence or
instructions. Corrected text creates a new job identity; retries retain the old snapshot.
The source-independent atlas builder also handles portrait/landscape videos, and
the enqueue helper accepts optional plain-text context for generic published videos.

### Future source: official YouTube videos (not implemented)

Potential catalog: [Wuthering Waves official channel](https://www.youtube.com/@WutheringWaves).
Keep [lore/trailer material](https://www.youtube.com/watch?v=LXyEJPjiMsU) distinct from
[meme/promotional shorts](https://www.youtube.com/watch?v=EqRYtREFQvY), gameplay,
concerts and long streams. Do not treat every official upload as canon lore or
concatenate unrelated videos. A future importer should preserve URL/video ID,
title, publication date, source captions/language and exact downloaded content hash;
publish the source through existing storage, then reuse atlases and bounded analysis.
Two-hour videos need an explicit sampling/segmentation policy and cost preview.
No channel crawler, downloader, scheduled ingestion or paid YouTube analysis is
enabled by this change.

From the repository root:

```powershell
docker compose -f infrastructure/local/compose.yml --profile cutscene-vision up -d --build cutscene-vision
docker compose -f infrastructure/local/compose.yml exec cutscene-vision wuwa-story-worker enqueue-cutscene-vision --version 3.7.0 --limit 20 --offset 0
docker compose -f infrastructure/local/compose.yml exec cutscene-vision wuwa-story-worker cutscene-vision-status --limit 20
```

Administrators can also open `/admin/cutscene-analysis`, search imported video
names, queue individual variants, inspect paginated tasks and continue known
paused jobs. The quest and cutscene header action menus offer the same operations:
cutscene analysis queues every unique imported variant in its player, while quest
analysis uses the existing English story pipeline. All write endpoints enforce
administrator access and CSRF verification. Matching visual jobs are reused;
completed frame analysis is not repeated. Missing titles can require a separate
bounded text-only request. Uncertain charges cannot be resumed.
The visual consumer must be running to process pending jobs. After visual evidence
completes, analyze the quest to publish its reader-facing description.

New cutscene imports automatically persist jobs when enabled. For existing videos,
use the backfill command, advancing `--offset`; `--asset-node-id` targets one exact
variant. Backfill deduplicates jobs and does not re-charge completed work.
The dispatcher polls every minute and leases delivery for ten minutes. A session
advisory lock prevents simultaneous paid processing; recorded complete responses
are replayed locally after crashes. Known 429 rejections settle with zero usage.

Paused budget/provider/input/validation jobs need explicit resumption after fixing
the cause; the daily limit is never raised. For an output-limited single frame,
increase its output allowance (up to 16000):

```powershell
docker compose -f infrastructure/local/compose.yml exec cutscene-vision wuwa-story-worker resume-cutscene-vision RUN_ID --output-tokens 8192
```

An uncertain remote outcome cannot be resumed until billing is reconciled; its
reservation remains held. Status output contains IDs, progress and safe error text,
never prompts, images or credentials. No migration is needed: processing runs,
the shared call ledger and content-addressed file references already exist.

After reports complete, queue a new quest analysis in the admin panel. Story V6
reads every available variant through paginated visual tools before publishing.
Explicit quest links and conservatively inferred scenario links are included in
the inventory; `context_inferred` distinguishes the latter and does not establish
runtime traversal. Quest video packages also identify related cutscenes by their
unambiguous `CgId`, without claiming that a package proves playback order. Existing
snapshots need the targeted `wuwa_story.ingestion.dungeon_backfill` command after
the relation migration; select the desired version range explicitly. Existing
quest-level titles take precedence over visual titles.

Protocol v4 can also generate a short English title from the sampled visual
evidence, even when no quest is linked. A scene with insufficient evidence keeps
its resource name. Requeueing an older complete visual report without a title
creates a bounded text-only title job from its stored observations: it does not
download the video or resend frames. This separate paid request uses the shared
ledger and keeps the original report immutable; a title is hidden if its source
report or video changes. The admin can queue it using the existing cutscene
analysis action. Existing reports are not automatically reprocessed.
Readable descriptions and timed chapters are stored in the explanation document
and exposed in the corresponding player's AI disclosure. Visual report changes
invalidate the analysis fingerprint; changed video bytes hide outdated reports.
Recording version is stated separately from the quest source snapshot: current
video does not establish what was visible in a historical game build.

To disable paid visual work, stop the dedicated consumer and set the flag false.
Imported video/dialogue and previous valid descriptions continue to work.

## Source failures and variant alignment

Preparation normalizes authored Windows separators and records rejected movie
wrappers under `media_report.invalid_movie_sources`. It continues preparing other
sources; the affected leaf remains partial with exact source diagnostics. Ambiguous
references and traversal paths are never replaced with a guessed movie.

Valid variants with unequal frame counts/rates retain their independent originals
and authored selection flow. They must decode fully before this fallback is allowed;
corrupt/truncated decoding still fails. Preparation retains exact wrapper exports
until its final video-package identity is known, so a preliminary reuse match cannot
leave a leaf without the sources needed for its stricter publication check.
