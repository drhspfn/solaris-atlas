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

FModel, Epic Games downloads, asset extraction, and map-coordinate production are not part of the snapshot job yet. They need their own asset queue, storage lifecycle, and independently bounded concurrency. The snapshot worker currently handles GitHub datamine branches only.
