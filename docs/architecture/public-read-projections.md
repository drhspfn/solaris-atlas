# Public read projections

Status: implementation in progress; not deployed.

Public browsing must read durable entity projections, rather than rebuild relationships or resolve artwork from the source graph during HTTP requests. Redis is an optional acceleration layer, not the canonical read store.

## Storage contract

Dedicated character, item, location, quest, event, notice, speaker, dialogue and cutscene read tables contain localized, snapshot-scoped catalog fields and ready public page payloads. Each entry stores its primary file ID and URL, gallery references, stable address, sorting label and generation. `current` is a deliberate snapshot key; it is not a character release date.

A separate relation projection stores owner, related entity, relation kind, snapshot and source evidence. Projected links preserve explicit versus inferred evidence; co-presence must never become an asserted personal relationship. Page payloads may contain denormalized related titles and URLs to avoid relation hydration during browsing.

## Refresh contract

Source writes and projection invalidation commit together. High-volume media changes invalidate affected owners, not the complete character catalog. Bulk source or localization changes coalesce into snapshot/category work. Deletes and unavailable media also invalidate their owners. Projection tables never invalidate themselves.

A persistent dirty-work registry tracks requested and completed generations. Background rebuilds acknowledge only the generation they actually read. New writes during a rebuild remain pending. Refresh errors retain the previous published projection and record retry state. Restarted workers recover pending work; RabbitMQ notification delivery is not the source of truth.

A rebuild prepares data outside a long write transaction, then publishes a coherent entry and relations atomically. Large catalog rebuilds use bounded batches. Existing source records remain the provenance authority.

## Rollout

1. Add projection tables, invalidation and rebuild support without switching requests.
2. Backfill existing imported snapshots and locales, checking coverage, identities, deletion and relation counts.
3. Compare complete public responses against source-backed builders and measure cold and warm latency.
4. Switch each validated endpoint to its projection. Do not silently run the original expensive builder on a cache miss; unready entries have explicit availability state.
5. Keep rollback to the previous reader until coverage and update propagation are verified.

## Measured baseline (2026-10-30)

Live API requests from the production host during active media imports: character catalog returned 503 after 23.925 s; another attempt succeeded in 11.640 s. Denia profile 10.030 s; archive 9.069 s; location catalog 7.216 s; quest catalog 3.119 s; event calendar exceeded a 25 s client deadline; notice list 0.313 s (another attempt 1.552 s). These are individual observations, not percentile benchmarks.

SQL instrumentation found catalog selection itself at 0.773 s and artwork hydration exceeding a 10 s statement deadline. This supports moving artwork identity into the read projection along with the page data.
