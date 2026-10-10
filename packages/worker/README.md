# Snapshot worker

The worker owns the deterministic game data compiler. Upstream repositories and compiled snapshots live in its persistent workspace, not in this repository. A snapshot job is pinned to a game version branch and full Git commit SHA, then cloned, compiled, checked against the manifest, imported through the existing database importer, and cleaned up after a successful import.

## Local commands

Start the local stack and worker services:

```bash
docker compose -f infrastructure/local/compose.yml --env-file infrastructure/local/.env --profile worker up --build -d rabbitmq worker snapshot-scheduler
```

Queue one version manually:

```bash
docker compose -f infrastructure/local/compose.yml --env-file infrastructure/local/.env --profile worker run --rm worker wuwa-story-worker enqueue-snapshot --version 1.0
```

The scheduler scans numeric upstream branches from `1.0` every 30 minutes, queues any commit it has not published before, and records published branch heads in the worker volume. To scan once:

```bash
docker compose -f infrastructure/local/compose.yml --env-file infrastructure/local/.env --profile worker run --rm snapshot-scheduler wuwa-story-worker watch-upstream --from-version 1.0 --once
```

The worker consumes durable RabbitMQ messages from `wuwa.snapshot-build.v1`. Each failed message is dead-lettered to `wuwa.snapshot-build.v1.failed`; inspect it in RabbitMQ Management (`http://localhost:15672`) and requeue it with:

```bash
docker compose -f infrastructure/local/compose.yml --env-file infrastructure/local/.env --profile worker run --rm worker wuwa-story-worker replay-failed --limit 10
```

Per-queue concurrency is controlled with `WUWA_QUEUE_CONCURRENCY`, for example `snapshot_build=1`. Keep snapshot concurrency at one unless each job is given a separate Git cache/workspace strategy. Import batch size is `WUWA_IMPORT_BATCH_SIZE`. Queue data and the shallow Git object cache are persisted in the `wuwa_worker_data` volume.

The compiler source and schema baseline are packaged with this service under `src/wuwa_story_worker/compiler/`; its investigation notes and source schema reports are under `docs/game-data/`. They are ordinary version-controlled sources. The worker does not need a mounted investigation checkout.

## FModel and game client assets

Client assets use independent durable queues: `wuwa.asset-download.v1` and `wuwa.asset-extract.v1` (both supported by the Linux worker image). The existing snapshot worker remains snapshot-only. Downloads come directly from the official global PC launcher CDN; Steam and Epic installations and account credentials are not required. This downloads game archives, not a playable installation, and never launches the game or its installer.

### Download and inspect

Create a pinned plan before queueing any large download:

```powershell
uv run --project packages/worker wuwa-story-worker plan-assets --version 3.7 --tier hd --output packages/worker/var/assets-3.7-hd.json
docker compose -f infrastructure/local/compose.yml --env-file infrastructure/local/.env --profile assets up --build -d asset-worker
docker compose -f infrastructure/local/compose.yml --env-file infrastructure/local/.env exec rabbitmq rabbitmqctl set_policy wuwa-assets-long-jobs '^wuwa[.]asset-(download|extract)[.]v1$' '{"consumer-timeout":86400000}' --apply-to queues
uv run --project packages/worker wuwa-story-worker enqueue-assets packages/worker/var/assets-3.7-hd.json
docker compose -f infrastructure/local/compose.yml --env-file infrastructure/local/.env --profile assets logs -f asset-worker
```

The plan reports version, tier, byte size, file count, and the pinned `yarik0chka/wuwa-keys` commit. It includes the exact archive inventory and source manifest SHA-256. Only the live client is available from this endpoint: old-version `patchConfig` entries upgrade an old client to the live release, and must not be used to claim a historical installation.

The current 3.7 manifest offers common Paks plus HD. SD/UHD are accepted only when actually present in the source manifest; requesting an absent tier fails instead of downloading HD under a different label. The new launcher's tier-specific configuration is not integrated yet. Tier differences include cutscene bitrate, so preserve the tier in exported object identities. Sources: [official resource-tier notice](https://wutheringwaves.kurogames.com/en/main/news/detail/5513), [key repository](https://github.com/yarik0chka/wuwa-keys), [FModelCLI](https://github.com/Herselfta/FModelCLI).

The local Compose bind mount defaults to `packages/worker/var/client-assets`, so the container can read downloaded archives. Set `WUWA_ASSET_HOST_PATH` in the Compose environment to place it on another drive. Jobs live under `assets/<version>-<tier>-<job-id-prefix>/`, with `plan.json`, `status.json`, `game/`, and `keys.txt`. Generated assets and tools are Git-ignored and excluded from Docker builds.

Downloads use four concurrent transfers by default, stream to `.part` files, resume with HTTP Range, retry with CDN fallback, and verify size and MD5 before atomic replacement. Repeat deliveries verify existing files and retain completed downloads. An OS workspace lock prevents two downloader processes from changing the same workspace. Disk preflight reserves the outstanding bytes plus one maximum-sized archive per transfer and 5 GiB for repairs. Archives are retained; there is no automatic eviction or deletion of installed game files. This is resumable within the same plan; cross-version archive deduplication is not implemented.

The RabbitMQ policy above gives asset jobs a 24-hour acknowledgement timeout, rather than timing out a large download after the broker's usual 30 minutes. The policy is persisted in the RabbitMQ volume. Configure the equivalent policy on other deployments before queueing assets.

Failed downloads go to the failed queue and can be replayed:

```powershell
uv run --project packages/worker wuwa-story-worker replay-failed --queue asset_download --limit 1
```

### Linux extraction and publication

The worker image contains FModelCLI, CUE4Parse.CLI, vgmstream and their Linux native dependencies. Build it from the repository root:

```bash
docker build -f packages/worker/Dockerfile -t solaris-worker .
docker run --rm --network none solaris-worker /app/tools/fmodelcli/FModelCLI --check-native-libs
docker run --rm --network none solaris-worker /app/tools/cue-cli/cue4parse --check-native-libs
```

The final image runs these same checks during its build as the non-root worker user with networking disabled. Checks perform an Oodle compression/decompression roundtrip, Zlib decompression, BC1 texture decoding through Detex, and PNG encoding through SkiaSharp. Sources are pinned by Git commit; downloaded Oodle/vgmstream archives are checked by SHA-256. `docker/patch_tools.py` checks the expected source before applying Linux path and initialization fixes. A changed upstream layout, absent library, or incompatible ABI fails the build. `/app/tools/SHA256SUMS` records the published tool files.

Each CLI initializes libraries from its own `.data` directory beside the executable. Runtime code does not download native libraries, copy them into `/tmp`, or depend on an `/opt/wuwa-tools` host mount. No tool path variables are required in GitHub or service env files. The worker resolves the bundled executable paths automatically. Optional `WUWA_FMODEL_PATH`, `WUWA_TEXTURE_CONVERTER_PATH`, and `WUWA_VGMSTREAM_PATH` overrides remain supported for a separately verified local installation.

The local `asset-worker` consumes both download and extraction queues. Rebuild and start it with:

```bash
docker compose -f infrastructure/local/compose.yml --env-file infrastructure/local/.env --profile assets up --build -d asset-worker
```

The worker uses the existing S3 settings and registers published files in PostgreSQL. Exports retain `fmodel.log`, an inventory of file hashes, and a publication receipt. Failed exports, partial mounts, empty output and mismatching file counts reject publication even if the CLI exits zero. Successful raw files are published under version/tier/job/filter-specific keys with the manifest written last.

### Map extraction and database publication

Map extraction uses the bundled CUE4Parse CLI. Readers and coordinate transforms currently accept client `3.7.0` only. To queue maps for a completed asset plan, run the existing command inside the worker (the plan path must be in its mounted workspace):

```bash
wuwa-story-worker enqueue-maps <plan.json>
```

Set `WUWA_ASSET_BUILD_MAPS=1` on the downloader to enqueue map extraction automatically after a verified download. The same `asset_extract` consumer handles raw exports and maps. Failed jobs go to `wuwa.asset-extract.v1.failed`; replay only the required jobs after deploying the repaired image:

```bash
wuwa-story-worker replay-failed --queue asset_extract --limit 1
```

Map publication validates raw exports, resolves ConfigDB tile resources, decodes every requested texture, assembles bounded overview PNGs, and extracts chest/collectible placements. Full-resolution tiles, floor layers and gravity variants remain separate. Migration `0006_tile_maps` is required. Files use `FileRegistrationService`; map and marker records preserve client-build identity. Publication is one database transaction protected by a per-build advisory lock. Failed publication does not expose a partial map. Retries reuse verified outputs; receipts from a different extractor binary require a new export workspace.

API: `GET /maps?game_version=3.7.0`, `GET /maps/{id}`, `GET /maps/{id}/markers`. Local reverse-proxy URLs start with `/api/maps`. Tile manifests include `file_id`, SHA-256 and signed MinIO URLs valid for one hour. Set `MEDIA_PUBLIC_BASE_URL` to the browser-reachable MinIO public bucket root in local development; production public media uses the CDN URL derived from `DOMAIN`.

World coordinates and original tile indices are retained. Marker categories initially come from blueprint names and do not prove that a placement is active in a particular playthrough. Hidden/sleep flags, component overrides and Z height are preserved. Floor assignment is unresolved where there is no reliable source link; marker responses explicitly report this. See [map source notes](../../docs/game-data/maps.md) for transforms and limitations.
### Media lookup diagnostics

Media tasks retain `media_report` in their processing result. Admin task history
opens it lazily, with filename search, missing-only filtering and pagination.
Older tasks expose their recorded missing names; retry a partial media import to
collect detailed lookup evidence using its existing pinned client packages.

Voice preparation records the mounted audio archive index. A voice lookup checks
exact names, case differences and explicit Rover `_F`/`_M` variants. Files present
in that index but absent on disk are re-exported before decoding. Nearby dialogue
names are diagnostic candidates only and are never substituted automatically.
The report is saved before decoding/publication, so a later failure retains the
lookup evidence. Entity image/voice tasks and cutscenes also retain expected and
resolved paths. The admin download contains JSONL observations with `origin`
`archive` or `export`, relative media paths and exported byte sizes. It deliberately
excludes raw CLI logs, AES keys, environment files and other configuration bodies.
Inventory generation streams paths rather than loading the entire client into RAM.

The inspected 1.0 story snapshot uses client assets 3.7.0. On 2026-10-10, these
11 historical voice names were absent from both the export and the mounted voice
archive index (44 language entries):

```text
vo_Huanglong_main_1_2EX_7_17
vo_Huanglong_main_1_2_128_1
vo_Huanglong_main_1_7_102_1
vo_Huanglong_main_1_7_104_3
vo_Huanglong_main_1_7_105_1
vo_Huanglong_main_1_7_105_2
vo_Huanglong_main_1_7_110_1
vo_Huanglong_main_1_7_110_2
vo_Huanglong_main_1_7_161_1
vo_Huanglong_main_1_7_161_2
vo_Huanglong_main_1_7_162_1
```

The first and final three IDs were also absent from the current PlotAudio config.
The other seven IDs still referenced the same unavailable filenames. They remain
partial imports until compatible historical resources are available; repeated
extraction of the current packages cannot recover files absent from their index.
