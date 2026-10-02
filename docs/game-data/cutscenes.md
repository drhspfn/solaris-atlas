# Cutscene import and playback graphs

## Model

A cutscene recipe contains exact Unreal video asset identities, timed soundtrack
inputs and a versioned playback graph. `clip` nodes reference an asset and its
`start`/`end` seconds; `choice` nodes name options and their next nodes. Clips
can lead to another clip or choice. Multiple branches can target one shared
continuation. A null next target ends playback. Ordinary movies need one clip.

The graph validator rejects dangling targets, cycles, duplicate IDs, unreachable
nodes, invalid ranges, non-finite timing and unpublished asset references.
Publishing also checks each asset is an authored variant of the cutscene and
that clip ranges fit its source duration. Source snapshots and extracted asset
versions remain separate.

The first published example is `Start` (CG 101 / male Rover, CG 102 / female
Rover), current 3.7.0 HD assets: 1080p, 30 fps, 3498 frames / 116.6 seconds.
An initial manual 24-second boundary was incorrect and has been removed.
Localized comparison detects the first visible difference at frame 508
(16.9333 seconds) and the last at frame 3482. With a three-frame guard, the
pipeline exports an intro of 505 frames (16.8333 seconds), two variant clips
of 2981 frames each (99.3667 seconds), and a shared tail of 12 frames (0.4 seconds).
These are separate playable MP4 files including their matching audio portions.
The graph uses these files, not seeks into the full originals. The original
movies remain stored for provenance and reprocessing.

This is a viewer Rover-variant choice, not an authored narrative choice.
`cutscene-start.json` is generated from the game configuration and enables
automatic comparison, without a manually chosen scene boundary.

## Automatic comparison and segmentation

Multi-variant plans enable `compare_variants`. Import prepares complete video
and soundtrack first, then compares every corresponding frame and decoded PCM
window across all variants. RGB differences are measured locally: the detector
counts changed pixels instead of averaging differences across the entire frame.
A small Rover silhouette therefore cannot disappear in a mostly shared background.
Scaling/blur and pixel tolerance suppress encoder noise. Audio differences also
expand the divergent interval, even when the pictures match.

The comparison settings live in `ComparisonSettings` in `cutscene_segments.py`:
960×540 analysis, blur sigma 1.2, RGB tolerance 12, minimum 24 changed pixels,
PCM tolerance 32 / minimum 8 changed samples, three guard frames and minimum
0.1-second shared fragments. These are perceptual classification thresholds,
not proof that differently encoded recordings are identical. Results include
per-frame visual/audio counts, first/last differing frames, input hashes, tool
version and all settings, so decisions can be inspected and reproduced.

The cache key includes video hashes, settings and FFmpeg version. Processing
changes must bump the algorithm version. Completed results are written atomically
under a workspace lock. Aligned constant frame rates and timestamps are verified;
mismatched lengths/rates or variable timing fail rather than inventing an alignment.
Two to eight variants are supported in this comparison pass.

The exporter encodes shared intro, each divergent branch and shared tail from
exact source frame intervals, with matching sound. H.264 CRF18/fast and AAC192k
allow cuts independent of source keyframes. Each new fragment is decoded and its
frame count checked before publication. If the recordings match within the
thresholds throughout, only one shared clip is needed. If differences start at
frame zero, the player starts with the variant choice. Short common spans within
the first-to-last divergent interval remain in their branch; this avoids repeatedly
asking the same variant choice. The shared suffix becomes an explicit graph join.

## Plan from game configuration

With the base archives already exported, build a recipe for a named cutscene:

```powershell
uv run --project packages/worker python -m wuwa_story_worker.cutscene_plan `
  --config-db PATH_TO_EXPORTED_db_cgVedio.db `
  --assets PATH_TO_EXPORTED_ASSETS `
  --name Start --asset-version 3.7.0 `
  --output packages/worker/var/cutscene-plan.json
```

The planner reads VideoData and VideoSound using the verified 3.7 schema. It
uses exact media asset paths, distinguishes gender-specific and shared events,
and reads start/end seconds. A single variant becomes one clip; multiple
variants become a choice followed by complete clips with automatic comparison
enabled. The importer then derives common prefix/suffix boundaries from media
comparison, never from filenames. Authored narrative choices remain explicit. Reviewed source
arrangements can replace this default graph without changing importer/player.
Other schema versions fail explicitly until verified.

Export the referenced assets, event banks, corresponding WEM media and movies
before publishing. The importer resolves each `.uexp` `filepath://./` movie
path, rather than guessing a filename from the CG name. Banks are matched to
actual media IDs. Unsupported or multiple-source Wwise banks fail explicitly.

## Publish

Run from the repository root with local worker environment configured:

```powershell
uv run --project packages/worker --env-file packages/worker/.env python -m wuwa_story_worker.cutscene_import `
  --recipe docs/game-data/cutscene-start.json `
  --assets packages/worker/var/cutscene-sample `
  --movies packages/worker/var/cutscene-video-files `
  --audio packages/worker/var/cutscene-audio `
  --output packages/worker/var/cutscene-playable `
  --decoder PATH_TO_VGMSTREAM `
  --ffmpeg PATH_TO_FFMPEG
```

Inputs retain exported `Client/Content` paths. Tools are explicit paths and are
not installed by this command. Per-run temporary directories prevent colliding
publishers from overwriting each other. Video remains H.264 without re-encoding;
embedded sound is preserved, external sound receives authored delay/end timing
and recipe gain, then AAC encoding. Audio is padded to finite video duration.
This does not reconstruct arbitrary Wwise bus automation or event graphs.

Original MP4s, banks, WEMs and decoded WAVs use existing content-hashed storage.
Playable files have `cutscene_soundtrack_mix` variant links to originals.
`cutscene_video` references attach to exact asset nodes with soundtrack recipe,
source file IDs, duration and asset version. A hashed recipe JSON file and
`cutscene_flow` reference attach the playback arrangement to the cutscene node.
`cutscene_segment` references identify exported fragments; FileVariant links
connect them to complete movies. Segment metadata records original frame ranges,
frame rate and the stored analysis file ID. API URLs resolve those exact fragments.
No database migration is required.

Publication uses a transaction and deterministic owner advisory lock order.
Retries reuse file hashes and references. Uploads preceding a failed database
transaction can leave unreferenced objects for normal storage cleanup.

## API and player

Quest media includes published graph nodes and signed media URLs. A flow is
served only when all its assets are available in the exact recorded asset
version; it never combines an older flow with newer recordings. Unpublished
quests retain `references_only`; published media uses `partial` because other
resource references may still be unexported.

The player pauses at a choice and replaces the source/range after selection.
Branch joins use the same node and asset. Seeking respects clip boundaries;
restart returns to the entry. Native video controls and keyboard choice buttons
remain available. Starting a voice or video pauses other media. Playback does
not autoplay on page load. MinIO HTTP Range responses support seeking.

## Official video packages

Packages are downloaded separately and on demand by the game; an installed
client may lack a particular movie. Official resource configuration provides:

- `{MixUri}/Windows/VideoConfig_hd.json`, with manifest SHA-1;
- `{MixUri}/Windows/3.7.0/Video/VideoManifest_hd.json`;
- `{ResUri}/Windows/3.7.0/Video/{PakName}` and matching signatures.

SD/HD/UHD use their own suffixes. Config/manifest decryption uses private resource
configuration credentials, never committed. This example downloaded hash-verified
`Video_101_1-HD-WindowsNoEditor.pak` and `Video_102_0-HD-WindowsNoEditor.pak`.
Game CDN is an import source; website playback uses our storage.

## Current limits

Automatic video CDN planning/download/extraction is not yet wired into the queue.
The recipe importer accepts other exported cutscenes, but configuration planning
is verified only for 3.7 and soundtrack bank parsing only for single-source Sound
or MusicTrack objects in Wwise v172. Subtitles and subtitle-triggered localized
voices are not included yet. Unsupported cases fail rather than silently publishing
incorrect sound. Quality switching and historical asset matching remain separate
work. The UI states missing subtitle coverage.

## Verification

Tests cover a normal movie, shared intro/choices/convergence, tiny localized
changes, encoder noise tolerance, audio-only differences, frame alignment, invalid graphs,
source timing, gender-specific audio, safe paths, bank layouts, asset build
matching and unavailable branches. The opening was actually imported and replayed
idempotently; both variants returned HTTP 206 video/mp4. Browser playback decoded
1080p frames, paused for Rover choice, continued the selected branch and restarted.
