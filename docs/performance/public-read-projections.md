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
