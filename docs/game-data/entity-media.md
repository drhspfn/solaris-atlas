# Entity image and character voice jobs

`POST /admin/media-jobs` requires an administrator session and the existing
`X-CSRF-Token` cookie/header verification. It pins source references from the
requested imported story snapshot, records an `ops.processing_run`, and publishes
a persistent RabbitMQ message with publisher confirms. Broker failure returns
503 and `enqueue_failed`; repeating the request safely retries the same run.
`GET /admin/media-jobs/{id}` returns status, counts, missing images and errors.
These endpoints do not expose or accept extraction tool paths or game keys.

Example body (replace the download ID with the full pinned client plan ID):

```json
{
  "download_id": "FULL_64_CHARACTER_PLAN_ID",
  "asset_version": "3.7.0",
  "tier": "hd",
  "game_version": "1.1.0",
  "entities": ["character:1205", "item:50000171"],
  "kinds": ["image", "voice"],
  "voice_ids": [120501]
}
```

Omit `voice_ids` to request all favor/profile voice events for these characters.
Images include role artwork, skills, skill-tree/chain assets and item icons.
Requests support up to 100 entities; split larger catalogs into bounded batches.
Use separate image and voice requests when independent completion is preferred.

Run the Windows extraction consumer with the existing worker environment:

```powershell
uv run --project packages/worker --env-file packages/worker/.env wuwa-story-worker run --queue entity_media
```

Configure absolute `WUWA_ASSET_WORKSPACE`, `WUWA_FMODEL_PATH`,
`WUWA_TEXTURE_CONVERTER_PATH`, `WUWA_VOICE_ROOT`, `WUWA_VGMSTREAM_PATH`.
The client workspace must contain the completed matching plan, archives and keys.
The voice workspace is a completed `download-voices` result for the same asset
version, including all four languages and character packs. Game downloads remain
the separate existing asset/voice download workflows, not a side effect of browsing.

The worker consumes `wuwa.entity-media.v1`, verifies the pinned backend payload
and client plan, decodes Texture2D/LGUI atlas sprites, and resolves voice media
using cooked Wwise event `MediaId`, `MediaPathName` and exact localized debug names.
It never maps languages by list order. Events with multiple clips per language
fail explicitly pending authored sequencing support.

Original assets, images, WEMs and decoded WAVs enter content-hashed storage.
Variants preserve the source relationship; references identify owner, source
asset path, language, story release and independently recorded asset version.
Database advisory locks serialize duplicate processing across workers and
reference publication per owner. Completed/partial jobs skip redelivery.
Failures are visible in the run and the existing dead-letter queue; repeat the
same admin request or replay the failed queue after correcting the cause.

Missing historical textures do not discard available images. A `partial` result
lists exact unresolved image paths; converter failures still fail the job.
Image decoding caches verify output and raw-file hashes and include both tool
hashes in their cache identity. No database migration is needed. Uploads before
a failed database transaction can leave unreferenced objects for storage cleanup.

## Verified local run

Snapshot 1.1.0, client 3.7.0 HD: Changli plus the first 24 item catalog entries
produced 93 published image references, including eight role artworks and nine
skill icons. The old `T_IconRup_1402_UI` source was not found and is reported as
missing. All 24 catalog cards resolve icons. Changli's `Thoughts: I` event
`play_favor_word_changli_sys_to_player01` produced four independent language
tracks (English, Japanese, Korean, Chinese), served through existing public media
routes. Historical story snapshots use exact matching asset paths; imported
3.7 recordings are not presented as verified recordings from 1.1.


The full catalog was additionally queued as seven image batches for 656 items,
plus one batch for all 22 catalog characters. These are separate background
imports; queuing does not mean all their assets have been published. The first
five item batches completed with 1,357 published image variants; historical
`T_IconA*_shlb_02/03/04_UI` references were unavailable in the current client.
Use the job status endpoint to see the remaining batches and exact missing paths.
A first conversion of a shared texture folder can involve thousands of textures;
subsequent batches reuse its verified decode cache. Local API and web containers
were rebuilt, and the Windows `entity_media` consumer was configured and started.

Verification covered server/worker tests, frontend build and targeted lint,
public image responses, all four WAV responses and repeated request identity.
The authenticated admin POST was not exercised through a signed-in browser;
its authorization/CSRF dependencies and broker-failure response are tested.

## Multilingual import recovery

Absolute CDN URLs pass through the frontend unchanged. Dialogue filenames that
now have explicit `_F` / `_M` variants retain both files and Rover metadata;
there is no fuzzy filename match or implicit gender fallback. Character event
media use the base filename shared across all four languages when a localized
dialect alternative is also present. Ambiguous events are reported as missing
voices without discarding the rest of the character batch.

Cutscene import waits for the same client's voice package task. Localized event
banks publish aligned, independent voice stems using the authored VideoSound
start/end times. A bank with unverified layered sequencing leaves its video
available as a partial import and reports the missing audio; it never mixes all
branches together. Original embedded audio is retained as an unseparated mix.

After deploying API, web and snapshot worker, retry the patch's media import in
Data operations. Existing completed tasks are reused; failed and partial tasks
are retried using the cached client exports. No game database migration is needed.

Manual checks after deployment:

1. Open a character and item with published art; images should load from the CDN.
2. Retry partial voice tasks and verify female/male Rover dialogue in each language.
3. Open a previously failed cutscene and switch between its imported voice languages.
4. Click a sidebar scene; it should scroll to that exact player without starting playback.
5. Check remaining partial tasks: missing assets remain visible rather than reported as complete.
