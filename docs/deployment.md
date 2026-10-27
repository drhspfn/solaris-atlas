# Production deployment

GitHub Actions builds API, web and worker images and deploys selected components over SSH. All production configuration lives as individual entries in the GitHub Actions `production` environment: non-sensitive settings under **Variables**, credentials under **Secrets**. The workflow assembles these entries into separate Compose, shared, API and worker env files, transfers them over SSH, and applies owner-only permissions. There is no JSON env bundle and no API key in a repository config file.

The single source of truth for how entries map into service env files is [`scripts/render-production-env.py`](../scripts/render-production-env.py). Update a value in GitHub's `production` environment, then rerun the corresponding deploy. The generated files on the host are outputs and are replaced on every deploy.

## GitHub Actions environment

Create an Actions environment named `production`.

Required Variables:

- `DEPLOY_HOST`, `DEPLOY_USER`, `DOMAIN`
- `POSTGRES_USER`, `RABBITMQ_DEFAULT_USER`, `USE_MINIO` (defaults to `true`; `MINIO_ROOT_USER` is only used with MinIO)
- `AGENT_PROVIDER`, `AGENT_MODEL`, `AGENT_BASE_URL`, `AGENT_DAILY_BUDGET_USD`, `AGENT_BUDGET_TIMEZONE`
- `AGENT_DAILY_TOKEN_LIMIT`, `AGENT_MAX_INPUT_TOKENS`, `AGENT_MAX_TOOL_CALLS_PER_STEP`
- `AGENT_CONTEXT_COMPACTION`, `AGENT_COMPACTION_THRESHOLD_RATIO`, `AGENT_COMPACTION_OUTPUT_TOKENS`
- `AGENT_RATE_LIMIT_RETRIES`, `AGENT_RATE_LIMIT_BACKOFF_SECONDS`, `AGENT_RATE_LIMIT_WAIT_SECONDS`
- `AGENT_INPUT_USD_PER_MILLION`, `AGENT_CACHED_INPUT_USD_PER_MILLION`, `AGENT_CACHE_WRITE_USD_PER_MILLION`, `AGENT_OUTPUT_USD_PER_MILLION`, `AGENT_PRICE_SAFETY_MULTIPLIER`
- `AGENT_EMBEDDING_MODEL`, `AGENT_EMBEDDING_DIMENSIONS`, `AGENT_EMBEDDING_INPUT_USD_PER_MILLION`, `AGENT_PUBLIC_QUERY_EMBEDDINGS`, `AGENT_QUERY_REQUESTS_PER_HOUR`
- `S3_BUCKET`, `S3_REGION`, `S3_USE_SSL`; for R2 set `USE_MINIO=false`, `S3_ACCESS_KEY_ID`, `S3_ENDPOINT_URL` and the optional public endpoint URL
- `APP_NAME`, `MEDIA_CACHE_CONTROL`, `API_CACHE_NAMESPACE`, `API_CACHE_TTL_SECONDS`, `API_CACHE_MAX_BODY_BYTES`, `API_CACHE_TIMEOUT_SECONDS`, `LOG_LEVEL`
- `AUTH_SESSION_TTL_DAYS`, `AUTH_PENDING_REGISTRATION_TTL_MINUTES`, `AUTH_PENDING_LINK_TTL_MINUTES`, `AUTH_COOKIE_NAME`, `AUTH_COOKIE_SECURE`, `AUTH_COOKIE_SAMESITE`, `AUTH_CSRF_COOKIE_NAME`
- `GOOGLE_CLIENT_ID`
- `WUWA_QUEUE_CONCURRENCY`, `WUWA_WORKER_WORKSPACE`, `WUWA_IMPORT_BATCH_SIZE`, `WUWA_ASSET_WORKSPACE`, `WUWA_ASSET_DOWNLOAD_CONCURRENCY`, `WUWA_ASSET_EXPORT_FILTERS`, `WUWA_ASSET_BUILD_MAPS`
- `AGENT_VISION_ENABLED`, `AGENT_VISION_FRAME_INTERVAL`, `AGENT_VISION_MAX_FRAMES`, `AGENT_VISION_BATCH_FRAMES`, `AGENT_VISION_ATLAS_FRAMES`, `AGENT_VISION_ATLAS_TILE_WIDTH`, `AGENT_VISION_ATLAS_QUALITY`, `AGENT_VISION_OUTPUT_TOKENS`

Required Secrets:

- `DEPLOY_SSH_PRIVATE_KEY`, `DEPLOY_KNOWN_HOSTS`
- `POSTGRES_PASSWORD`, `RABBITMQ_DEFAULT_PASS`; `MINIO_ROOT_PASSWORD` only when `USE_MINIO=true`
- `AUTH_CSRF_SECRET`

Additional Secrets:

- `AGENT_API_KEY` — provider credential for story/cutscene analysis.
- `GOOGLE_CLIENT_SECRET` — only if Google sign-in is enabled.
- `S3_SECRET_ACCESS_KEY` — only when supplying external S3/R2 credentials.

GitHub Variables have defaults in the renderer where appropriate; set the entries you want to override. For your configured agent, set `AGENT_BUDGET_TIMEZONE` to `Europe/Kyiv` and store the OpenAI key only in `production` → **Secrets** → `AGENT_API_KEY`. Passwords and API credentials must be Secrets, not Variables. A fill-in checklist is at [`infrastructure/prod/.env.production.local.example`](../infrastructure/prod/.env.production.local.example); the local `.env.production.local` copy is ignored by Git and is only a scratchpad for entering values in GitHub.

`DOMAIN` is the only URL source of truth. Each deployment derives `API_URL=https://api.<DOMAIN>`, `FRONTEND_URL=https://<DOMAIN>`, `MEDIA_PUBLIC_BASE_URL=https://cdn.<DOMAIN>`, `CORS_ALLOWED_ORIGINS=https://<DOMAIN>`, `AUTH_COOKIE_DOMAIN=.<DOMAIN>`, and `GOOGLE_REDIRECT_URI=https://api.<DOMAIN>/auth/google/callback`. Point the root and `api` DNS names at this server; attach `cdn.<DOMAIN>` to the R2 bucket as its custom domain. Register the derived Google callback URI in Google Cloud Console. R2's S3 API endpoint and credentials remain separate storage settings.

## Deployment

1. Point the root and `api` subdomain A/AAAA records at the server, attach `cdn` to the R2 bucket custom domain, and allow inbound TCP 22, 80 and 443 (optionally UDP 443 for HTTP/3).
2. Run **Provision and start infrastructure**. It installs Docker on Debian/Ubuntu and starts PostgreSQL, RabbitMQ, Redis, plus MinIO only when `USE_MINIO=true`. SSH requires passwordless sudo.
3. Run **Build and publish images** on `main` and wait for it to finish. It publishes full and short commit SHA tags plus the `main` tag.
4. In **Deploy selected services**, keep the default image tag `main` to deploy the latest successfully built `main` images. Enter a full or short commit SHA only when you want a specific build. The workflow checks image tags in GHCR before opening an SSH connection.

Later, update one Variable or Secret in the GitHub `production` environment and deploy only the affected service. Components are `infrastructure`, `api`, `web`, `caddy`, `snapshot-worker`, `story-agent`, `cutscene-vision`, and `all`. Set `USE_MINIO=false` to disable MinIO and its bucket initializer; the renderer requires the R2 endpoint and access credentials in separate GitHub entries and passes them into API/worker S3 settings. With `USE_MINIO=true`, Compose enables MinIO's opt-in profile. Switching it off stops/removes its containers but keeps its named data volume. Infrastructure credentials are written into generated server env files; changing an initialized database password also requires rotating it in that service. API deployments run Alembic migrations before replacing the API container. Named volumes are never deleted by deployment.

## Host and generated files

The host is Ubuntu or Debian. Docker Compose v2.30 or newer is installed by the infrastructure workflow. Generated files are stored under `~/solaris-atlas/env/` with mode `0600`; the directory is mode `0700`. They are rebuilt from the GitHub entries on every deployment. Do not make manual edits to generated host files; change the corresponding GitHub entry so later deploys retain it.

## API connection pooling

Production API `DATABASE_URL` targets internal `pgbouncer:6432` in transaction
mode. The API uses SQLAlchemy `NullPool` and UUID prepared statement names;
PgBouncer tracks up to 200 prepared statements per server connection. It admits
500 clients and caps API backend connections at 25 (20 normal plus 5 reserve).
Clients wait up to 10 seconds for a backend. These are admission limits, not a
replacement for fixing expensive queries. Neither PostgreSQL nor PgBouncer
publishes a host port.

The API has two Uvicorn processes, each admitting at most 8 origin requests
(`API_MAX_CONCURRENT_REQUESTS`). Cached responses and `/health` bypass this gate;
excess origin traffic gets HTTP 503 with `Retry-After: 2` rather than an unbounded
connection backlog. Keep the combined process limits below the normal PgBouncer
pool size, leaving room for cache revisions and health checks. Each API transaction
sets LOCAL statement and lock timeouts (20 seconds, configurable with
`API_DB_STATEMENT_TIMEOUT_MS`, and 3 seconds respectively). Worker sessions are
unaffected. Retryable connection/timeout failures produce a sanitized 503.
PgBouncer exposes read-only `SHOW POOLS` / `SHOW STATS` to the existing `wuwa` role
through its internal `pgbouncer` database; no administrative commands are granted.
Rollback uses the preceding main image/configuration; no schema migration is needed.

Workers keep `postgres:5432`, their existing pool limits, and physical session
affinity for workspace, broker and agent advisory locks. `agents.runner.execute_job`
is called by the story worker only; API endpoints enqueue jobs. Keep any future
session-lock, LISTEN or session-state workload off the transaction pool.
Migrations override `DATABASE_URL` with `MIGRATION_DATABASE_URL` in the API's
one-off container, with dependencies disabled, and therefore always connect
directly. After migrations deployment recreates PgBouncer before starting API
services, clearing cached plans after DDL.

Infrastructure, API and full deployments export the existing login role's SCRAM
verifier over the PostgreSQL container's local socket to
`env/pgbouncer/userlist.txt`. The verifier is never printed or passed as a command
argument. Its file is mode `0600`, owned by image UID/GID `70:70`, inside the
protected env directory, and mounted read-only. No database password is changed.
The deployment script needs root permissions to assign UID/GID 70 ownership
(the production SSH account is root).
An absent, expired or non-SCRAM verifier fails deployment. PgBouncer uses
`scram-sha-256`, with no forced backend username, to reuse the identical server
verifier. After rotating a PostgreSQL password and its GitHub Secret, redeploy
infrastructure/API to refresh the verifier and pooler.

The image is
`docker.io/pgbouncer/pgbouncer:1.25.2-20260920@sha256:89f145c1f003340e9b38a37a8734013ec43d931abb9bd7ea739640f2348d4a67`.
It is the Canary9-maintained PgBouncer Docker Hub namespace, **not** a Docker
Official Image. The OCI index covers amd64 and arm64 and includes provenance
attestation manifests. The upstream image entrypoint is bypassed so it cannot
render credentials into config or logs; the binary runs as its default UID 70
with a read-only root filesystem. See the [publisher's build/source documentation](https://hub.docker.com/r/pgbouncer/pgbouncer),
[PgBouncer authentication and pooling reference](https://www.pgbouncer.org/config.html),
[transaction prepared statement support](https://www.pgbouncer.org/faq.html), and
[SQLAlchemy asyncpg guidance](https://docs.sqlalchemy.org/en/21/dialects/postgresql.html#prepared-statement-name-with-pgbouncer).

Before deployment, run the disposable local Docker smoke with the server Python
environment: `RUN_PGBOUNCER_SMOKE=1 packages/server/.venv/bin/python -m unittest discover -s scripts/tests -p test_pgbouncer_live.py`.
On Windows use `packages/server/.venv/Scripts/python.exe` and set the environment
variable in PowerShell. It exercises actual SCRAM pass-through, 60 concurrent
clients, prepared statement reuse across transactions, rollback, transaction-lock
release and direct worker session-lock retention. It removes its containers,
anonymous volumes and network afterward. Container health checks only test the
pooler's listening socket; they do not replace this authenticated smoke.

For rollout, copy `pgbouncer/` alongside the Compose file and use the updated
deployment script (the production workflow does both). Deploy infrastructure
before the new API image when introducing pooling. Confirm PgBouncer is healthy,
the API health endpoint succeeds, public quest/event browsing works, and workers
still use direct PostgreSQL connections. Keep generated env/auth files and
container environment output out of logs. To roll back pooling, redeploy the
previous infrastructure/script revision together with the API image; its env
renderer restores the direct API URL and removes the pooling flag. Preserve
PostgreSQL data and passwords throughout rollback.

## Local development Compose

`make dev` combines `infrastructure/local/compose.yml` and `infrastructure/dev/compose.yml`. The dev overlay retains Vite and API hot reload. `scripts/dev-up.sh` prepares local env examples, starts the database and object store, and migrates the schema.

## Rollback and backups

Web and full deployments validate and reload the running Caddy configuration.
The Next.js frontend listens on port 3000; replacing the web image alone does
not make Caddy reread its mounted configuration. A reload applies updated
upstream ports without restarting the proxy.

Redeploy a previous commit SHA to roll application images back. Database migrations run forward at API deployment; back up PostgreSQL before schema changes that are not backwards-compatible. Keep backups of PostgreSQL and object storage separate from the server.

### Patch media imports

Snapshot import now queues `release_media` jobs for images (including skills),
quest voices, character voices and cutscenes. Shared client preparation, voice
packages and cutscene exports are prerequisites; dependent jobs wait through a
persistent RabbitMQ delay queue. Production media-worker routes `release_media`
and `entity_media` into the execution pools; locally start the `assets` Compose profile.

For an already imported patch, use **Import / retry media** in Data operations.
Completed tasks are reused; failed/partial tasks can be queued again. A completed
snapshot does not mean its separately queued media tasks have finished. Inspect
**Queues & workers** and **All processing tasks** for progress and missing media.
RabbitMQ monitoring requires its management listener and read permissions for the
configured broker user. Unavailable monitoring is displayed as unknown counts.

Voice bootstrap exports KuroPublicConfig.ini from game archives and uses pinned,
public Shorekeeper game-format cipher constants. No user-provided crypto file is
required. Official voice/video manifests and archive checksums are verified.
Historical story snapshots use the available client media; removed content may
be missing. Cutscene configuration decoding currently supports client 3.7.0.

Cutscene visual analysis remains manual and shares the configured daily budget.
Its admin preview uses the site's existing player. Contact sheets are not enabled:
high-detail mosaics have no verified token-saving guarantee for the configured model.

Media recovery: rebuild/redeploy snapshot-worker after extraction fixes, then use
Import / retry media for the affected release. Failed child extraction is now
reported to its parent instead of being automatically resubmitted indefinitely.
Busy asset workspaces defer jobs without marking them failed. Texture conversion
uses eight-package subprocess batches with two .NET processors to bound retained
objects. Voice extraction searches all audio paths rather than only PlotAudio;
its errors include the first extractor failure lines. Linux export lookup matches
file casing explicitly. Shared video references publish one source asset while
keeping authored choices. Missing sound banks produce partial video publication
with missing_assets, preserving visibility of incomplete audio.

### Media worker capacity and retries

API/full deployments stop the currently running API and worker containers before
Alembic runs, releasing import read locks before schema changes. The same container
IDs (including media replicas) restart on both success and failure; subsequent
Compose updates install the selected images. This introduces a maintenance window
during migration. Queued messages and persistent exports are retained. A failed
migration stops the rollout without replacing the running application images.

Production uses independent worker pools. `snapshot-worker` deployments update all
six worker services; `WORKER_IMAGE` pins their shared image. Existing public v1
queue names and payloads remain unchanged. `media-worker --pool coordinator`
forwards public deliveries with publisher confirms before ACK; private execution
queues are `wuwa.pool-{coordinator,image,audio,video}.v1`. Pure entity voice tasks
use audio; mixed legacy image/voice requests use image. Preparation/notice work
uses coordinator, catalog extraction and image/event resource tasks use image, voice tasks use audio,
and cutscene extraction/conversion uses video. Parent dependencies still defer
through durable 60-second waiting queues; they do not hold execution slots.

Default capacity for a 6 CPU / 12 GiB host:

| Service | Replicas | Tasks per replica | CPU per container | Memory per container |
| --- | ---: | ---: | ---: | ---: |
| snapshot-worker | 1 | 1 fetch/compile, 1 global DB import total | 1 | 2 GiB |
| asset-worker | 1 | 1 download + 1 extraction | 0.5 | 1536 MiB |
| media-worker (coordinator) | 1 | 3 executions + public queue routing | 0.25 | 768 MiB |
| image-worker | 1 | 1 | 0.75 | 2 GiB |
| audio-worker | 1 | 1 | 0.75 | 2 GiB |
| video-worker | 1 | 1 | 2 | 2560 MiB |

This provides 9 logical task slots in 6 containers: 1 snapshot fetch/compile,
2 asset (download/extraction), 3 lightweight coordinator and 3 media executions.
These are conservative canary defaults, not the final production parallelism
target. They provide fewer entity execution slots than the previous two media
workers with three entity slots each. Increase useful parallelism after measuring
the new export path rather than keeping six containers as a fixed target.
Each image/audio/video container runs one task at a time. A live operator probe
exported the known CamellyaActivity texture correctly: one PNG in 14.51 seconds
at 2 CPU, maximum child RSS 1188.2 MiB, and temporary scratch removal confirmed.
Adding approximately 90 MiB of Python overhead leaves almost no margin under
1280 MiB, before other container memory is counted. Asset memory remains
1536 MiB. A second cold probe resolved two Shorekeeper events into eight valid
WAVs across English/Japanese/Korean/Chinese with no missing tracks: 131.05 seconds
at 2 CPU, maximum child RSS 1370.9 MiB, scratch removal confirmed. It included
building the main archive index (about 234 MB log / 515 MB SQLite); patch workers
share that index across character jobs. A subsequent image-pool FModel `--list`
aborted under its 1.5 GiB cap, and a native probe in the 2 GiB video container
recorded a cgroup OOM kill while heavy work overlapped. Image memory now defaults
to 2 GiB; audio remains at 2 GiB; video defaults to 2560 MiB and 2 CPU. The new
limits are guardrails following those failures, not proof that every overlapping
export/transcode fits. These checks do not establish full-patch throughput or
performance at the image/audio defaults of 0.75 CPU.
Product/queue canaries remain required before opening the full backlog.

Summed default worker ceilings are 5.25 CPU and 10.75 GiB including snapshot/assets.
These are caps, not reserved RAM, guaranteed host headroom or a throughput promise.
PostgreSQL, RabbitMQ, API, OS and optional AI work also consume memory/CPU. Start
heavy preparation with one image and one audio replica; keep snapshot replicas
at one and pause snapshot/asset work when it is unnecessary during canaries.
Do not overlap FModel indexing with a high-resolution FFmpeg/native probe in the
same container. Coordinate those probes and check actual container peaks, host
MemAvailable, memory.events and restart/OOM counters before adding replicas.
If screenshot work continues to saturate CPU, pause compilations while measuring
media. Memory caps
include child .NET/FFmpeg processes. Reduce concurrency or adjust memory from
canary measurements if a single export approaches its cap. Scaling snapshots to
four requires freeing capacity from other pools or lowering measured per-process
limits; do not start four 2 GiB snapshot containers alongside the default pools.

A production override with image=2, audio=2, coordinator=1, snapshot=1, video=1
and asset=1 has eight worker containers and eleven logical task slots. Set
`WUWA_IMAGE_REPLICAS=2` and `WUWA_AUDIO_REPLICAS=2` only after initial preparation
and live RSS checks; keep one heavy task per container. At the configured limits
this layout sums to 6.75 CPU and 14.75 GiB in caps. Those are not memory
reservations, but simultaneous peaks exceed the 6 CPU / 12 GiB host budget.
The renderer accepts measured deployment overrides rather than declaring that a
sum of caps guarantees safety. Choose per-pool limits from measured
container peaks including Python and child processes, retain DB/API/OS headroom,
and stage compilation or video work when CPU is saturated. Compare completed
media operations per minute and end-to-end patch progress before/after each
change. Replica count alone is not the success criterion.

Set `WUWA_QUEUE_CONCURRENCY` in worker.env (or the GitHub production Variable):
`snapshot_build=1,pool_coordinator=3,pool_image=1,pool_audio=1,pool_video=1`. Values apply **per process and per queue**, and
prefetch equals execution concurrency. Old overrides such as `release_media=4`
now control forwarding only; tune `pool_audio`, `pool_image`, etc. for execution.
Do not run additional legacy public-queue consumers during a controlled rollout:
they can execute tasks outside these resource pools.

Snapshot capacity is capped at four fetch/compile tasks across replicas:
`WUWA_SNAPSHOT_REPLICAS * snapshot_build <= 4`. Default is one replica with one
task; use four replicas at one task, or two replicas at two tasks only after
measuring capacity. Increasing it requires the ordered global import guard in
snapshot_jobs: release sequence allocation alone is insufficient because typed
entity/search tables are global projections and imports commit in batches.
Different compile completion order must never let an older patch overwrite a
newer projection. DB ingestion remains globally serial and version gated. Keep
snapshot concurrency at one when rolling back to an image without that guard.
The shared OS Git cache lock serializes checkout preparation across replicas; compilation
runs in subprocesses. Import JSON/file work can still block the event loop and
is addressed by the importer performance work separately.

`WUWA_{SNAPSHOT,COORDINATOR,IMAGE,AUDIO,VIDEO}_REPLICAS` are Compose variables. All six pools
have `WUWA_<POOL>_CPUS` and `WUWA_<POOL>_MEMORY` controls (`SNAPSHOT` and `ASSET`
are the other pool names). These go into compose.env; runtime concurrency goes
into worker.env. The renderer, example env files and production workflow forward
these settings. Local Compose uses infrastructure/local/.env for interpolation
and packages/worker/.env for runtime settings; `--profile assets` enables the media
and asset pools, `--profile worker` enables snapshot consumption/scheduling. The
dev overlay mounts worker/server sources into every pool.

Each worker process bounds the existing shared SQLAlchemy engine pool with
`WUWA_WORKER_DB_POOL_SIZE=4` and `WUWA_WORKER_DB_MAX_OVERFLOW=4`; imports create
separate engines and must be counted separately. A running task holds an
execution lease and a job-lock connection, and needs additional connections for
status writes/cleanup. Nested direct-handler/export leases must be reentrant or
included in connection capacity. Budget at least two connections per active task plus two spare
connections, and keep summed process budgets plus API/import connections
below PostgreSQL max_connections. More prefetch cannot fix an exhausted pool.

SIGTERM cancels consumers before waiting up to
`WUWA_SHUTDOWN_TIMEOUT_SECONDS=90`; Compose allows 120 seconds. Remaining broker
deliveries are requeued on cancellation/connection close. Media leases stay held
until `finish_writes` finishes owned subprocess/thread work, even after task
cancellation. A container killed after the grace period loses its DB leases;
unacknowledged work is redelivered. Handlers must remain idempotent. Publish/ACK
crashes can produce duplicates. PostgreSQL deadlocks/serialization failures use
three durable retries with preserved headers; exhausted failures go to each
queue's `.failed` queue. Long-task acknowledgement timeout policy (24 hours)
is applied before worker startup and includes private queues and snapshot jobs.

Broker execution resolves `run_workspace_id`, holds `workspace_lease` through
writes, then calls `cleanup_workspace` after releasing the lease. Skipped
cancelled/completed deliveries also trigger cleanup. The helper checks all
siblings under an exclusive DB lease, retaining unknown/nonterminal trees and
original client inputs. Coordinators retry crash cleanup every
`WUWA_WORKSPACE_RECOVERY_SECONDS=300`; its nonblocking lock excludes duplicate
cleanup. No raw workspace is deleted by routing or while owned writes continue.

Tuning and rollout (operator actions; these were not run against production):

1. Deploy the complete worker image, helper and snapshot-ordering changes together.
   Stop/drain old media consumers, retain volumes and apply the acknowledgement
   policy. Keep bulk consumption stopped until product smoke tests and bounded
   image/voice export canaries pass; then start the chosen execution pools.
2. Verify RabbitMQ consumers, ready/unacknowledged counts and waiting/failed
   queues with `rabbitmqctl list_queues name messages_ready messages_unacknowledged consumers`.
   Record `docker stats --no-stream`, OOM/restart counts, DB connections/waits,
   scratch disk usage and completed media operations for at least 15–30 minutes.
3. Compare completed operations and `job.delivery ... elapsed_seconds` logs per
   kind. Delayed dependencies, busy workspace locks, synchronous file/JSON work,
   subprocess CPU and DB pool waits are distinct limits. Separate pools prevent
   long media jobs from monopolizing coordinator/event-loop capacity; they do
   not make synchronous code nonblocking within a process.
4. Increase only one pool's replicas or concurrency at a time. Stay within the
   measured CPU/RSS/DB/disk budget. Prefer additional lightweight replicas;
   keep video/extraction low while screenshot CPU is high. A 10–20 operation
   target is a tuning goal, not permission for 20 .NET exports. No two-hour
   completion estimate is supported without measuring representative patches.
5. On failure, inspect the relevant public **and private** `.failed` queues.
   `wuwa-story-worker replay-failed --queue pool_audio --limit 10` replays private
   failures while retaining dispatch type and resetting DB retry count.

Remaining live acceptance checks before opening bulk queues:

- Smoke-test the product against existing imported data: patch/entity pages,
  images, voice playback and cutscene playback, including actual object fetches.
- Complete a representative voice export and verify the successful image probe
  through the deployed publication path; record peak container memory, CPU,
  duration and the resulting published media.
- Verify queue routing, dependency deferral, ordered DB ingestion, graceful
  shutdown/redelivery and final workspace cleanup with bounded jobs before
  resuming the backlog. Confirm completed operations and patch progress increase
  under the selected production replica counts.

Local unit/configuration checks and isolated PostgreSQL lifecycle checks do not
replace these product and deployment checks. No live throughput estimate is
established until the canaries and a representative queue run are measured.

Rollback: first stop routing new work, drain private queues using the current
image, then restore previous image/Compose/concurrency. If private tasks cannot
be drained, keep compatible private consumers until they can be replayed;
older images do not know private queue names. Never delete broker queues or
persistent volumes as part of rollback. Verify no active leases before cleanup.

Character voice extraction resolves cooked Wwise IDs from the pinned main-client
exports as well as multilingual voice packages. The numeric main-client Media
export is prepared once; verified cached bytes and decoded WAVs are reused.
Missing languages produce a partial media report rather than discarding other
languages. Character audio is queued one character per task so completed
characters become available independently. Historical 1.0/1.1 metadata currently
uses the installed 3.7 client: references removed from that client remain reported
as missing instead of being replaced by guessed audio.

### Telegram import error topic

Set `TELEGRAM_BOT_TOKEN` as a GitHub production Secret, and set production
Variables `TELEGRAM_CHAT_ID` and `TELEGRAM_ERROR_THREAD_ID`. The selected target
is chat `-1004484145903`, topic `2`. Add the bot to that forum with permission
to send messages. Missing bot/chat settings disable delivery without affecting
imports. Credentials are rendered only into worker configuration.

Failed import jobs and partial media reports send the task number, version,
error and a bounded list of missing paths/languages. SQL parameters, connection
URLs and configured secrets are removed. AI conversation payloads are excluded.
Telegram requests have a five-second timeout; delivery failures never retry or
fail the processing task. Complete diagnostics remain in the admin media report.

Main-client Wwise event metadata is cached alongside numeric media. Public API
cache revisions refresh within five seconds of committed data changes, rather
than retaining empty audio links for hours. The Chinese interface locale
`zh-Hans` resolves stored voice tracks labelled `zh`.

### Game-client event catalog

The **Import client events** admin action now discovers the complete catalog from
the downloaded client instead of fetching a community timetable. It queues direct
image batches and UI prefab dependency jobs, and the event page exposes the
published artwork gallery. See [event sources and verification](game-data/events.md).

Rebuild and deploy workers first because the bundled FModel CLI adds batch path
filters. Deploy API and web afterwards, then reimport events once. No schema
migration is needed; take an event-table backup before converting existing source
identities. A catalog marked partial can still have successfully imported names
and image children: inspect `not_in_client` package reports separately from decode
failures. Missing historical bytes are not recovered by repeating the same build.

### Automatic official client updates

The worker consuming `asset_download` checks installed resource tiers hourly and queues
updates through the same idempotent service as **Check & update client** in Data operations.
`WUWA_CLIENT_UPDATE_INTERVAL_SECONDS` defaults to `3600`; set it to `0` to disable polling
or to at least `60` to change the interval. An empty installation is bootstrapped by a
manual download. `client-update-status.json` records the last check and failures.

Each client manifest remains pinned. Verified unchanged archives use hard links across
client directories on the shared worker volume; changed files are downloaded to temporary
files and atomically replaced. Existing import paths remain valid, and disk checks count
only missing/changed archives. The active client is the newest fully downloaded version.
This does not fetch unavailable historical clients or delete old client/extraction caches.
Rollback: deploy the preceding image; already shared archives remain ordinary readable files.

Image extraction retains separate public `release_images` and `entity_images`
queues, which the coordinator routes to the image execution pool. Long
character/quest audio and cutscenes route to audio/video pools independently.
Existing queued tasks keep their original queue; newly created image jobs use the
separate queues. Deploy API and worker together; queue declarations remain durable.

Successful deployments remove only untagged, unused Docker images older than 24 hours.
Tagged rollback images, running/stopped container references and all volumes are retained.

Weapons are projected from `BinData/weapon/weaponconf.json` into the existing item
catalog using `item:<ItemId>`; ItemInfo identities are never overwritten. For an
already imported release, restore missing weapon nodes and item search documents:
`python -m wuwa_story.ingestion.weapon_backfill --release-id 4 --dry-run`, then the
same command without `--dry-run`. It reuses durable raw records/localization,
processes 100 records per batch, and is safe to repeat. New snapshot compilation
includes weapons automatically.
