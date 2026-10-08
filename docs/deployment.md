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
- `AGENT_VISION_ENABLED`, `AGENT_VISION_FRAME_INTERVAL`, `AGENT_VISION_MAX_FRAMES`, `AGENT_VISION_BATCH_FRAMES`, `AGENT_VISION_OUTPUT_TOKENS`

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

## Local development Compose

`make dev` combines `infrastructure/local/compose.yml` and `infrastructure/dev/compose.yml`. The dev overlay retains Vite and API hot reload. `scripts/dev-up.sh` prepares local env examples, starts the database and object store, and migrates the schema.

## Rollback and backups

Redeploy a previous commit SHA to roll application images back. Database migrations run forward at API deployment; back up PostgreSQL before schema changes that are not backwards-compatible. Keep backups of PostgreSQL and object storage separate from the server.

### Patch media imports

Snapshot import now queues `release_media` jobs for images (including skills),
quest voices, character voices and cutscenes. Shared client preparation, voice
packages and cutscene exports are prerequisites; dependent jobs wait through a
persistent RabbitMQ delay queue. Production snapshot-worker consumes both
`release_media` and `entity_media`; locally start the `assets` Compose profile.

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
