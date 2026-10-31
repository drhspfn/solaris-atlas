# Public read projection validation

Local exploratory measurements, 2026-10-30. These are not production endpoint latency claims.

## Workload

PostgreSQL 17.6 on Windows, loopback connection, 50,000 synthetic entity rows. Each row contains a small card, a profile section and an approximately 130 KB archive before PostgreSQL compression. No source graph or production data is copied. The scripts and raw fixture remain in `scratch/read-projection-validation`.

With the same new-table access path and nine repeated reads:

| Query, 24 rows | Median |
| --- | ---: |
| Dedicated compact card column | 0.300 ms |
| Card extracted from full page JSON | 0.523 ms |
| Full page payload | 19.575 ms |

A separate initial seven-read probe without an ordered access path took 496.349 ms when extracting cards from large JSON before sorting, versus 111.802 ms selecting full rows. It exposed why moving the current response into a single JSON column is insufficient: PostgreSQL can decompress every candidate before applying LIMIT. Store compact card fields separately, and give the new read tables access paths matching locale, snapshot and sort order.

The profile-only JSON section read by entity primary identity took 0.201 ms in that initial probe. Counts over all 50,000 scoped rows took 23.565 ms; count is separate from page retrieval and must not be confused with a total endpoint measurement.

## Live baseline

Before the read model rollout, individual requests during active production imports measured: character catalog 503 after 23.925 s; subsequent success 11.640 s; Denia profile 10.030 s; Denia archive 9.069 s; locations 7.216 s; quests 3.119 s; events exceeded a 25 s client deadline; notices 0.313 s, with a later attempt at 1.552 s. Different hardware, cache state and load prevent treating the local results as a production speedup factor.

Production rollout measurements, full endpoint parity and browser timings remain required before enabling the read projection flag.

## Search probe

The checked-in `packages/server/scripts/benchmark_read_projection_search.py` uses 50,000 temporary rows on a disposable loopback PostgreSQL instance, with identical result counts before and after adding trigram access paths. Five repeated queries per term measured:

| Term | Full-text path only | Full-text plus trigram paths |
| --- | ---: | ---: |
| Absent term | 225.482 ms | 0.445 ms |
| Numeric alias | 195.976 ms | 0.301 ms |
| Long exact token | 239.005 ms | 0.319 ms |
| Accented term | 244.917 ms | 0.748 ms |
| Common term matching all rows | 47.514 ms | 65.961 ms |
| Japanese/Chinese samples | 194–198 ms | 202–246 ms |

These results do not establish production search latency. In particular, the current substring fallback for CJK queries still scans this fixture; adding a full-text index alone does not solve it. Broad matches also remain proportional to the matching result set.

## Verification boundaries

Real PostgreSQL integration tests create disposable databases on loopback. They cover leased refresh recovery, simultaneous invalidation/publication, retained publications, deletion proof, bounded bulk expansion, historical scope matching and migration triggers. Public-reader integration tests intentionally create no source graph, raw archive or legacy search tables: character catalog, profile/archive and localized historical search must succeed from projections alone.

The frontend validation includes production build/typechecking, 108 Node tests and 27 SSR tests. Representative public routes were checked in a browser at a narrow viewport using fixtures, including retry, modal focus and selector keyboard behavior. Fixture checks do not establish real CDN/media performance, authenticated administration, every locale/theme or production projection coverage.

The full server suite has two independently reproduced baseline MCP-test failures: an outdated expected tool list and a fake session missing the method used by the existing quest resolver. They also fail on the unchanged main baseline; they are not counted as passing checks.

Final checks (2026-10-31): server plus deployment scripts **853 passed, 120 skipped, two baseline failures**; worker plus scripts **1,002 passed, five skipped**. The disposable PostgreSQL refresh suite passed **44 tests**, and the projection-only API/list-reader suite passed **five tests**, including executed cutscene keyset pagination and notice filtering without article bodies. Changed Python files passed Ruff; whitespace checks passed. Frontend results above were rerun after the final shared-selector changes.

Before reader activation, record production endpoint cold/warm latency under ongoing import load, sampled payload storage, initial scope readiness and refresh lag. A successful local SQL benchmark is not evidence that the production site has already become faster.

## Production activation, 2026-10-31

PRs 98–100 deployed the additive schema, staged readers, refresh workers and deployment fixes. English/current character and story-map scopes completed with no failed work before activation: 157 character rows and 14 story-path rows. Their compressed stored payload sizes were approximately 5.4 MiB and 166 KiB respectively. These are payload-column sizes, not total database sizes.

Public HTTPS requests during ongoing imports, with `X-API-Cache: MISS` after activation:

| API | Before this rollout | After activation |
| --- | ---: | ---: |
| Character catalog | 503 after 23.013 s | 200, 1.489 s |
| Denia profile | 200, 7.128 s | 200, 0.356 s |
| Denia archive | 200, 10.920 s | 200, 0.898 s |
| Story map | 200, 28.131 s | 200, 1.325 s |

An earlier public story-map miss after activation took 0.916 s. These are individual observations under changing load, not percentile or controlled cold-database benchmarks. HTML responses subsequently measured 0.179 s for the character catalog, 0.478 s for Denia and 1.592 s for the story map; upstream caches may already have been warm. Browser inspection confirmed the live map and Denia page rendered without the archive error; model and voice-line sections remained present.

The same probe still measured an 11.018 s location-catalog API miss and a 45 s client deadline for events, which remained source-backed. Those slices are not claimed as fixed by the first activation. New scopes and locales must finish backfill and pass canaries before expanding reader allowlists.

A separate read-only production comparison of two 500-neighbor pages for location node 9906 returned identical serialized output (747,222 bytes; SHA-256 `1b809f9c490f96ef374efad6f69230e7c8d7c0f7d11fd868e03840e7e9e2d90d`): bounded incident-edge reads plus one builder evidence aggregation took 17.570 s; the original path took 65.777 s. The candidate ran first in the same repeatable-read transaction, so this is evidence of output parity and a useful observed improvement, not a general latency guarantee. Repeated evidence aggregation was removed from subsequent builder pages without capping incident edges.

## Bounded story-map rendering

With the same captured 674,406-byte production API payload (1,132 source nodes), rendering the first 60 nodes reduced local React static markup from 1,385,081 to 82,568 bytes and HTML elements from 19,344 to 1,227. Local built Next HTML was approximately 866 KB; the hydrated first-page DOM contained 1,463 elements. The earlier public page contained approximately 19,600 DOM elements and 2.21 MB decoded HTML. These observations do not establish a production hydration-CPU speedup or compressed transfer size.

URL pagination preserves complete-selection search before slicing, chapter/act identities, snapshot-specific quest links and chapter-hash navigation. Search changes update native history without a new server-component request per keystroke. Node tests cover the search/clear/Next transition and hash recovery after conflicting search, in addition to 344-node chapter boundaries. The full API JSON and serialized initial map remain unchanged; pagination bounds rendered markup rather than data transfer.

After PR 102 deployment, a public HTML response measured 868,247 decoded bytes. The live browser showed 1,483 DOM elements and 60 of 1,132 source nodes on page one; search/clear/Next navigation reached page two correctly. A single browser navigation observed DOMContentLoaded at approximately 1.15 s versus 1.65 s in the earlier browser observation. Browser cache/load differed, so these are not percentile or controlled navigation benchmarks.

## Legacy media coordinator recovery

Production inspection found 134 active audio/video children under six completed package coordinators whose legacy outputs had no workspace ID. Their three root preparations already had workspace IDs. Renewal only checked the root, leaving these intermediate prerequisites completed and repeatedly triggering full-release discovery on deferred child deliveries.

Renew expired completed voice/cutscene coordinators independently while retaining valid completed leaf outputs and active coordinators. Existing per-release lock 701 and refreshed ORM state prevent repeated enqueue from resetting work already queued or running. This corrects the loop; it does not require importing the source graph again.

## Complete neighbor reads during refresh

Background builders now fetch an entity's complete ordered neighbor set through a shared source service, rather than sorting and querying the same adjacency set once per 500-row HTTP page. HTTP validation retains its 500-row cap. A production read-only probe fetched all 19,748 neighbors of location 9906 in 24.418 s. Comparing its first 1,000 rows against two current source API pages in the same repeatable-read transaction matched all row/order/evidence fields. The response filter echo intentionally differs: the previous handler overwrote `source_file_filter` with a neighbor's path; the extracted service preserves the requested filter.

Projected neighbor responses derive summaries and evidence counts from the requested direction/relation and page, with private source-file metadata retained for filtering but excluded from public results.

## Event refresh media reuse

An isolated read-only production comparison built three recorded runs of event 1574 in the same repeatable-read transaction. Complete output matched (1,472,837 bytes, SHA-256 `b264a907d509db30c4485e781e0fd15a68f19144b1b0dbc39cf860cd4d42b408`). Original assembly took 18.925 s across 12 queries; the source-build-only media cache took 5.243 s across 10 queries. Its successive runs took 4.104, 0.685 and 0.150 s. This is an observed per-event rebuild improvement, not a public-calendar latency claim.

Exact and suffix lookups, including negative results, are reused only within one entity/locale build and separated by bucket/path. Capture-specific fallback is still evaluated for every run. Scope exit clears the cache, and ordinary public reads retain their existing uncached repository behavior. A separate candidate that narrowed full-detail JSON transfer was not merged because timings did not demonstrate a consistent improvement.
