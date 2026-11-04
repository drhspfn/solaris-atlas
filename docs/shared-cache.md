# Shared cache and operation leases

`wuwa_story.cache.CacheManager` owns one asynchronous Redis pool per API/worker
event loop. All processes use the same `REDIS_URL` and `CACHE_NAMESPACE` to share
state. The pool is created inside the lifecycle and closed after requests/jobs
and worker background tasks finish, including error/shutdown paths. Nothing
connects to Redis at module import, and there is no process-local fallback.

## API dependency

```python
from fastapi import Depends
from wuwa_story.api.dependencies import get_cache
from wuwa_story.cache import CacheManager

async def endpoint(cache: CacheManager = Depends(get_cache)):
    value = await cache.get_json("catalog:summary")
    if value is None:
        value = {"items": ["one", "two"]}
        await cache.set_json("catalog:summary", value, ttl=60)
    return value
```

This is a reusable service, independent of FastAPI. Domain services can receive
the manager as an ordinary argument. JSON methods support dicts, lists, strings,
numbers and booleans; `get_bytes`/`set_bytes` support already encoded responses.
`None` means a cache miss, so cached JSON null cannot be distinguished from a
miss. Callers own serialization schemas, response eligibility and invalidation.
Cache keys must include everything that changes the result (revision, locale,
scope and, for personalized data, identity). No global conversation-response
sharing is enabled for the public chat.

All writes require a positive TTL. `delete(key)` invalidates a value.
`increment(key, ttl=60)` atomically increments a shared counter, with expiry
starting on its first increment. Values, counters, quotas and locks have separate
key spaces; logical keys are hashed so IP addresses and query text are not stored
as plaintext Redis key names. Namespaces must differ between environments that
share one Redis instance. Never use credentials as a cache key or payload.

Existing HTTP response-cache policies (public route allowlist, revision-aware
keys, ETag, payload cap, single-flight and private-response bypass) remain in
`api/cache.py`. That adapter and the existing query-vector cache reuse the pool
owned by the manager. They retain their existing key formats and behavior; new
consumers use the manager methods rather than constructing another Redis client.

## Worker context

RabbitMQ consumers (`wuwa-story-worker run`) open `worker_cache_lifespan` before
starting delivery and background tasks, and close it after they drain. Task
contexts inherit the manager without changing every existing handler signature:

```python
from wuwa_story_worker.cache import get_cache

async def handle_job(payload):
    cache = get_cache()
    await cache.set_json(f"job-summary:{payload['run_id']}", ["done"], ttl=300)
```

Standalone commands/schedulers can explicitly use
`async with worker_cache_lifespan() as cache:`. Access outside that context raises
a clear error. Do not retain the manager after its lifecycle or share a pool
between event loops.

## Operation leases

```python
async with cache.lock("build-summary:quest:42", ttl=30):
    # Recheck the cache inside the lease, then do bounded work and publish it.
    ...
```

Acquisition is nonblocking (`SET NX PX`); contention raises `CacheLockBusy`.
Release compares the random owner token atomically, so a stale owner cannot
delete a replacement lease. Exceptions and cancellation release an owned lease;
TTL recovers it after a crash. Low-level `acquire_lock` returns a token or `None`,
and `release_lock` returns whether that token still owned the lease.

The TTL is a **lease duration**, not a guarantee that long work remains exclusive.
There is no automatic renewal. Work must finish within that duration; a context
that finishes after losing ownership raises `CacheLockLost`. Redis failure on
normal release raises `CacheUnavailable`; cleanup preserves an original task
error/cancellation. Redis restart or data loss can also remove leases. Durable
imports, payments and database consistency still need idempotency/DB constraints
and their existing PostgreSQL locks; this change does not replace them.

## First consumer: public chat

`POST /search/chat` injects the manager and admits at most **five attempts per
canonical visitor IP per UTC day**, shared by all API processes. IPv4-mapped IPv6
addresses use the same allowance as IPv4. One Lua operation uses Redis server
time, checks/increments the quota and expires it at the next UTC midnight.
Concurrent processes cannot each admit five requests. Rejected attempts do not
extend the window or increase the counter. Accepted attempts consume quota even
if the downstream model fails, matching the previous pre-call accounting.

Quota exhaustion returns `429` with `Retry-After` until reset. Missing client IP
or unavailable Redis returns `503` before any paid model invocation. Forwarding
headers are handled by the trusted Caddy/Uvicorn ingress, never parsed directly
by the endpoint. The existing Cloudflare visitor-IP configuration still applies.

## Configuration and failure policy

- `REDIS_URL`: existing shared backend; Compose supplies it automatically.
  Use `rediss://` and credentials for a remote backend.
- `CACHE_NAMESPACE=solaris-shared-v1`: shared API/worker namespace, configured in
  production `shared.env` through the existing renderer/workflow Variables.
- `CACHE_TIMEOUT_SECONDS=0.3`: finite bound on the complete Redis operation,
  including connect/retry time. Redis-py retries are disabled. HTTP response
  caching keeps its existing `API_CACHE_*` policy and timeout settings.

Failures become `CacheUnavailable` without logging keys, payloads or credentials;
warnings are throttled per manager. Optional response caching can serve origin
data on failure. Quotas and operation locks must fail closed; applications choose
their HTTP/retry/defer policy rather than silently granting access or ownership.

Compose uses `noeviction`: cached responses cannot evict a live quota or lease.
All manager entries expire. A full Redis rejects writes; optional HTTP caching
bypasses storage and public chat returns `503`. Redis remains disposable (no
AOF/snapshots), so restarting it clears cached values and resets quotas/leases.
Monitor memory before the 256 MB limit and size it for payload/TTL volume.

Deploy API and workers plus the Compose/Redis configuration (the **all**
deployment applies all three). Applying only new application images does not
change an existing Redis eviction policy. No database migration is required.
Rollback restores previous images/configuration; shared entries expire naturally.
Changing the namespace also discards the effective quota and lock state, so do
not change it during live operations as a routine cache flush.

## Verification

Set `WUWA_TEST_REDIS_URL` to a disposable local Redis and run server
`tests/test_cache_manager.py`. Tests use random namespaces and delete only their
own entries; they never flush Redis. Coverage includes independent processes,
concurrent API instances, UTC windows, TTLs, ownership after expiry, cancellation,
bounded outages and no paid call when admission is unavailable. Worker
`tests/test_cache_lifecycle.py` verifies inherited context and cleanup on failure.
