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

Client assets use independent durable queues: `wuwa.asset-download.v1` (Linux or Windows) and `wuwa.asset-extract.v1` (Windows). The existing snapshot worker remains snapshot-only. Downloads come directly from the official global PC launcher CDN; Steam and Epic installations and account credentials are not required. This downloads game archives, not a playable installation, and never launches the game or its installer.

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

The local Compose bind mount defaults to `packages/worker/var/client-assets`, so the Windows extractor can read downloaded archives. Set `WUWA_ASSET_HOST_PATH` in the Compose environment to place it on another drive. Jobs live under `assets/<version>-<tier>-<job-id-prefix>/`, with `plan.json`, `status.json`, `game/`, and `keys.txt`. Generated assets and tools are Git-ignored and excluded from Docker builds.

Downloads use four concurrent transfers by default, stream to `.part` files, resume with HTTP Range, retry with CDN fallback, and verify size and MD5 before atomic replacement. Repeat deliveries verify existing files and retain completed downloads. An OS workspace lock prevents two downloader processes from changing the same workspace. Disk preflight reserves the outstanding bytes plus one maximum-sized archive per transfer and 5 GiB for repairs. Archives are retained; there is no automatic eviction or deletion of installed game files. This is resumable within the same plan; cross-version archive deduplication is not implemented.

The RabbitMQ policy above gives asset jobs a 24-hour acknowledgement timeout, rather than timing out a large download after the broker's usual 30 minutes. The policy is persisted in the RabbitMQ volume. Configure the equivalent policy on other deployments before queueing assets.

Failed downloads go to the failed queue and can be replayed:

```powershell
uv run --project packages/worker wuwa-story-worker replay-failed --queue asset_download --limit 1
```

### Windows extraction and publication

Use the published Windows x64 FModelCLI binary. Keep a pinned tool version in `packages/worker/var/tools/`; this initial implementation was verified with `v1.0.2`. The extractor records the executable SHA-256 and rejects reuse with another binary. The `--fmodel` argument is an operator-controlled local executable path, not a URL supplied by a queue message.

The downloader queues one extraction per `WUWA_ASSET_EXPORT_FILTERS` entry (comma-separated FModel substring filters). Default `ConfigDB` is a small first validation pass; add media filters after inspecting real archive paths. Run a separate Windows worker against the same host directory:

```powershell
$env:WUWA_ASSET_WORKSPACE = 'E:\Projects\solaris-atlas\packages\worker\var\client-assets'
$env:WUWA_FMODEL_PATH = 'E:\Projects\solaris-atlas\packages\worker\var\tools\FModelCLI-v1.0.2.exe'
$env:RABBITMQ_URL = 'amqp://wuwa:wuwa@localhost:5672/'
uv run --project packages/worker wuwa-story-worker run --queue asset_extract
```

S3 settings use the existing server settings (`S3_ENDPOINT_URL`, credentials and bucket); local defaults point to MinIO on `localhost:9000`. To test a completed job directly:

```powershell
uv run --project packages/worker wuwa-story-worker extract-assets <job-directory> --fmodel <FModelCLI.exe> --filter ConfigDB --upload
```

Exports retain `fmodel.log`, a file inventory with SHA-256 hashes, and a publication receipt. Exit zero alone is not success: explicit failures, empty output, missing PAK mounts, or mismatching file counts reject publication. Verified files go to version/tier/job/filter-specific S3 keys with content hashes, then the manifest is uploaded last as the completion marker. Retry reuses a completed local export and republishes the same immutable keys. Failed extraction messages use the same dead-letter/replay workflow with `--queue asset_extract`.

This stage publishes raw archive contents. Audio conversion (Wwise bank extraction/decoding), cutscene conversion, linking bytes to database media references, map coordinates, and application playback are subsequent stages. Successfully opening one archive does not establish that every 3.7 archive is supported; the complete job must pass mount and export checks.
