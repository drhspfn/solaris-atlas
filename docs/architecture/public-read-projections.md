# Public read projections

Status: implementation under verification; not deployed or enabled in production.

Public browsing must read durable entity projections, rather than rebuild relationships or resolve artwork from the source graph during HTTP requests. Redis is an optional acceleration layer, not the canonical read store.

## Storage contract

Dedicated character, item, location, quest, event, notice, speaker, dialogue and cutscene read tables contain localized, snapshot-scoped catalog fields and ready public page payloads. A separate story-map projection stores the assembled story paths. Each entry stores its primary file ID and URL, gallery references, stable address, sorting label and generation. `current` is a deliberate snapshot key; it is not a character release date.

Compact catalog cards and searchable localized text are physical columns, separate from full page JSON. Listing 24 entities must not load 24 complete archives or decompress every candidate archive before pagination. Detail readers select the requested section. Explicit raw-source inspection resolves stored provenance IDs in bounded batches; complete source records are not copied into every locale and snapshot.

NodeExplorer's explicit entity/dialogue namespaces use projections. Technical namespaces such as actions and flow states, and unqualified numeric node addresses, retain the existing bounded provenance-inspection reader. Routing is decided before lookup, never used as a fallback for an unready projected entity; cache revision selection follows the same boundary.

A separate relation projection stores owner, related entity, relation kind, snapshot and source evidence. Projected links preserve explicit versus inferred evidence; co-presence must never become an asserted personal relationship. Page payloads may contain denormalized related titles and URLs to avoid relation hydration during browsing.

## Refresh contract

Source writes and projection invalidation commit together. High-volume media changes invalidate affected owners, not the complete character catalog. Bulk source or localization changes coalesce into snapshot/category work. Deletes and unavailable media also invalidate their owners. Projection tables never invalidate themselves.

A persistent dirty-work registry tracks requested and completed generations. Background rebuilds acknowledge only the generation they actually read. New writes during a rebuild remain pending. Refresh errors retain the previous published projection and record retry state. Restarted workers recover pending work; RabbitMQ notification delivery is not the source of truth.

A rebuild prepares data outside a long write transaction, then publishes a coherent entry in all available locales and its relations atomically. Claims use expiring leases and generation checks; publication cannot acknowledge later source changes. Large catalog rebuilds expand in bounded keyset batches, including previously published IDs so deleted entities are removed. A failed dependent lookup does not prove the owning entity was deleted. Existing source records remain the provenance authority.

Once a scope has been published, its last coherent generation remains readable while refresh work is pending. Initial incomplete scopes return an explicit preparation response rather than a misleading empty catalog or an expensive source-graph fallback. Publication, rather than dirty-work scheduling, invalidates the public projection cache.

## Rollout

1. Add projection tables, invalidation and rebuild support without switching requests.
2. Backfill current entities and locales, then explicitly requested imported historical snapshots, checking coverage, identities, deletion and relation counts. Initial migration schedules current scopes only; it does not multiply every source body across all historical patches.
3. Compare complete public responses against source-backed builders and measure cold and warm latency.
4. Switch each validated endpoint to its projection. Do not silently run the original expensive builder on a cache miss; unready entries have explicit availability state.
5. Keep rollback to the previous reader until coverage and update propagation are verified.

### Operator controls

`READ_PROJECTIONS_ENABLED=false` preserves the existing public readers during backfill. `WUWA_PROJECTION_REPLICAS=0` leaves refresh workers disabled until capacity is checked. Set replicas to a small positive count to process durable refresh work independently of media and import workers. No import replay is needed to populate these projections.

For staged activation, set `READ_PROJECTION_KINDS=character,story_map` and `READ_PROJECTION_SNAPSHOTS=current`, then enable the reader flag only after those scopes are ready. Other kinds and historical selectors retain their source readers by configuration; selected projection misses still return explicit readiness errors. Empty allowlists mean unrestricted routing and should be used only after coverage is verified. A watcher without explicit `--kind` arguments consumes the configured kinds, allowing backfill while the reader flag remains disabled. Cache revision selection follows the same route decisions so source-backed slices continue invalidating when their source changes.

`READ_PROJECTION_LOCALES=en` limits both the first backfill and reader activation to English. Other locales retain source readers by explicit configuration. Partial-language publication preserves existing rows for other locales; a complete rebuild still removes obsolete locale rows, and a proven entity deletion removes all its locale rows. Before adding another public locale, backfill it with the worker's locale selection overridden to include that locale, verify coverage, then expand the API allowlist. Legacy numeric `locale_id` search overrides retain source routing when a locale allowlist is configured.

From the API image, queue all current scopes with:

```sh
python -m wuwa_story.read_models.worker --backfill
```

Queue a specific imported snapshot with `--backfill --snapshot 3.3.0`; add repeatable `--kind` arguments to restrict work. `--watch` continuously claims queued work. The authenticated, CSRF-protected `/admin/read-projections/rebuild` endpoint exposes the same rebuild control, and `GET /admin/read-projections` reports scope and pending/leased/failed work status.

Before a large backfill, run `--estimate --kind dialogue`. Estimates use stored row samples when available and a conservative allowance otherwise. A pilot can be queued with `--backfill --kind character --entity-id 5322879`; this does not mark the complete character scope ready. After the pilot worker publishes rows, repeat the estimate before queueing the full scope. Budgets apply to bulk expansion, and exceeding a budget retains pending work rather than silently omitting entities.

Default per-scope budgets are `READ_PROJECTION_MAX_ROWS=250000` and `READ_PROJECTION_MAX_BYTES=2147483648`. A complete dialogue scope can exceed these defaults: approve a larger measured budget explicitly after checking database storage. When only the unsampled byte estimate exceeds its budget, bounded pilot sampling can populate at most three entities before a later re-estimate; it does not waive the row budget or publish incomplete coverage.

`READ_PROJECTION_MIN_FREE_GIB=20` pauses the worker when its host filesystem lacks headroom. This protects the shared Compose host volume; deployments with an external database need separate database-volume monitoring. The dirty registry retains work during a pause.

Projection CLI connections have local 60-second statement and one-second lock deadlines, and each claimed build has a ten-minute runtime deadline. They use a dedicated small connection pool, leaving importer/API sessions unchanged. A one-shot `--entity-id` pilot filters both queueing and claiming to the requested kind, entity and snapshot; it cannot accidentally start an earlier bulk scope. Normal watchers remain free to process explicitly queued historical scopes unless a historical snapshot or pilot filter is supplied.

Rollback first disables `READ_PROJECTIONS_ENABLED`, then scales projection workers to zero. The additive tables can remain while investigating. Do not drop populated projections or roll back a migration while API instances still use them. Historical coverage and production cold/warm timings must be checked before enabling the reader flag.

## Measured baseline (2026-10-30)

Live API requests from the production host during active media imports: character catalog returned 503 after 23.925 s; another attempt succeeded in 11.640 s. Denia profile 10.030 s; archive 9.069 s; location catalog 7.216 s; quest catalog 3.119 s; event calendar exceeded a 25 s client deadline; notice list 0.313 s (another attempt 1.552 s). These are individual observations, not percentile benchmarks.

SQL instrumentation found catalog selection itself at 0.773 s and artwork hydration exceeding a 10 s statement deadline. This supports moving artwork identity into the read projection along with the page data.
