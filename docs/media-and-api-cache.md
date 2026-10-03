# Public media and API caching

## Media

The game media bucket contains public assets only. File objects remain deduplicated
by SHA-256; `objects/<hash prefixes>/<hash>` URLs do not expire. No per-file access
column is needed for this bucket. Private uploads must use a separate private bucket
and the explicit `S3Storage.signed_url()` method, never this bucket/CDN domain.

Configure `MEDIA_PUBLIC_BASE_URL=https://media.example.com` as the **full bucket
root** for an R2 custom domain. Do not append the bucket name. Without this setting,
the API uses `S3_PUBLIC_ENDPOINT_URL/<bucket>` (local MinIO). Keep the internal S3
endpoint and credentials for uploads; never expose them to the frontend.

New content-addressed uploads have `Cache-Control: public, max-age=31536000, immutable`.
Changed bytes get a new hash/URL, so replacing a portrait needs no CDN purge.
Other upload keys receive no immutable header. Existing objects can be upgraded
without downloading/re-encoding their bytes, from `packages/server`:

```powershell
uv run python scripts/public_media_headers.py          # dry run
uv run python scripts/public_media_headers.py --apply  # idempotent metadata copy
```

This changes object metadata, preserving content type, custom metadata and bytes.
It performs S3 HEAD/COPY operations; a versioned bucket may retain previous object
versions. Missing objects/errors stop the command; fix them and rerun safely.
Local Compose grants anonymous downloads only for the `objects` prefix.
Existing `/api/media/files/<id>` links still validate the public game reference,
then redirect to storage instead of proxying audio/image bytes through Python.

### Cloudflare R2

1. Attach a public custom domain to the game media bucket. A custom domain supports
   Cloudflare caching; `r2.dev` is intended for development.
2. Add a Cache Rule scoped to the media hostname **and `/objects/` path**: eligible
   for cache, respect origin Cache-Control. Hash keys have no filename extension;
   default extension-based rules alone are insufficient. Do not apply this rule to API/auth.
3. Allow the site's origins in R2 CORS for GET/HEAD, request headers `Range`, and expose
   `ETag`, `Content-Length`, `Content-Range`, `Accept-Ranges` for browser media/canvas.
4. Verify anonymous image/audio/video GET and Range requests, then verify
   `CF-Cache-Status: HIT` on a repeated eligible request. Existing cached headers
   may require a purge after the initial CORS/header rollout.

See [R2 custom-domain caching](https://developers.cloudflare.com/cache/interaction-cloudflare-products/r2/)
and [R2 CORS](https://developers.cloudflare.com/r2/buckets/cors/). CDN rules, DNS and
bucket permissions are external deployment settings, not configured by the API.

## Redis API cache

Set `REDIS_URL` to enable shared caching. Compose provides internal Redis with a
256 MiB memory limit, LRU eviction, no persistence and no exposed port. Redis is
disposable: eviction/restart causes misses, not data loss. In production use a
private network and Redis authentication/TLS where appropriate.

- Explicit public GET roots: catalog, categories, items, locations, characters,
  quests, dialogue, story-map, maps, nodes, graph, search, releases.
- Authorization, **any Cookie**, Range requests, other routes and other methods
  bypass shared caching. The frontend omits credentials only for these public game
  GETs, so signed-in users also benefit from shared caching. Account/admin requests
  and mutations retain credentials/CSRF; personalized preferences/progress must
  never enter the shared game-data cache.
- Keys include path, query parameters (including release, filters, pagination and
  locale), Accept-Language, content revision, deployment source fingerprint and
  media configuration. Duplicate query-value order is preserved. Redis keys/logs
  contain hashes, not query text or credentials.
- Only successful JSON responses without Set-Cookie, private/no-store or Vary are
  stored. Bodies larger than `API_CACHE_MAX_BODY_BYTES` (8 MiB) bypass storage.
- TTL defaults to one hour (`API_CACHE_TTL_SECONDS`). Redis commands have a bounded
  300 ms deadline and fail open. Warnings are throttled and omit connection secrets.
- A 30-second token-owned distributed lock coalesces cold reads across API workers;
  waiters wait up to one second, then serve origin. No lock can block users indefinitely.
- Hit responses support ETag/If-None-Match (304), `Vary: Accept-Language`, and
  `Cache-Control: no-cache`. API clients revalidate with the backend. Private/unknown
  responses use `private, no-store`; do not override this with a Cloudflare Cache Everything rule.
- `X-API-Cache: HIT/MISS/BYPASS` supports operational checks. Redis outages need no restart.

### Invalidation

Migration `0008_public_api_cache` installs PostgreSQL **statement** triggers on
public-content tables and a small per-table revision registry. The revision changes
once per touched table/transaction, and becomes visible with the same commit as
the data. Rollbacks preserve the previous revision. Worker imports, media exports,
admin writes and direct SQL updates all invalidate without sending Redis messages.
Writes to auth/session/job bookkeeping do not invalidate public game data.

Each cache lookup performs one small PostgreSQL revision read (~one row per content
table). Hits avoid expensive content queries, but do not eliminate all DB access.
The global revision conservatively invalidates all public responses on any content
commit. Old generations expire naturally; no blocking Redis key scan is required.
An in-flight old response can only fill its old generation. Redis downtime during
an import cannot leave stale responses after recovery.

Per-table revision rows avoid a global writer lock. Concurrent writes to the same
table serialize on its revision row until commit; keep import transactions bounded
and preserve existing retry handling for transaction deadlocks. New public tables
must install the same statement trigger in their migration and seed a registry row.
Auth/private tables must not be watched. Adding personalized logic to an allowlisted
route requires removing it from shared caching or introducing an explicitly reviewed
user-scoped policy; never infer a user from a query parameter.

## Deployment and rollback

1. Apply previous migrations first. In particular `0007_localization_content` needs
   the maintenance procedure in `database.md`; it is not a small cache migration.
2. Run `uv run alembic upgrade head` from `packages/server`. Migration 0008 touches
   metadata/trigger definitions, not existing content rows; a 10-second lock timeout
   aborts instead of waiting indefinitely on busy writers. Schedule around imports.
3. Start the rebuilt API with `REDIS_URL` and public media configuration, apply
   bucket permissions/CORS and backfill existing media headers as needed.
4. Request the same anonymous catalog/quest/map twice: MISS then HIT. Change locale
   or filters: separate MISS. Import/update content: next request is MISS with new data.
5. Stop Redis: API still responds from PostgreSQL. Start Redis: cached reads resume.

To disable response caching, unset `REDIS_URL` and restart API. This leaves harmless
revision triggers in place. To remove them, disable caching first, then
`uv run alembic downgrade 0007_localization_content`; no content is deleted.
Source changes automatically isolate cache namespaces. `API_CACHE_NAMESPACE` can
also be changed to force a manual cold cache without flushing other applications.

Frontend Vite `/assets/` files use immutable year-long caching. HTML/navigation
responses revalidate (`no-cache`) so deployments load the new hashed bundle names.

Integration checks use isolated resources:

```powershell
$env:WUWA_TEST_DATABASE_URL='postgresql+asyncpg://.../isolated_test_database'
$env:WUWA_TEST_REDIS_URL='redis://127.0.0.1:16379/0'
uv run pytest tests/test_api_cache.py tests/test_api_cache_integration.py tests/test_public_media.py
```
