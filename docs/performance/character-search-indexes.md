# Character search index probe

The production English/current character projection contained 14,874 rows on
2026-11-17. A read-only `EXPLAIN (ANALYZE, BUFFERS)` for the unfiltered
character search `aalto` showed a parallel bitmap heap scan removing 14,868
rows; execution took 150 ms under the observed load. The playable catalog count
and first 24 cards used the existing role index and took 1.9 ms and 0.18 ms,
respectively. These are individual observations, not cold-storage benchmarks.

An isolated PGlite PostgreSQL fixture with 15,000 synthetic character rows,
the existing title trigram and vector indexes, and a query matching the search
expression was measured five times before and after adding the two expression
indexes. Median client-observed execution was 46.9 ms before and 1.3 ms after;
the seven returned IDs and their order matched. The plans reported 53.9 ms
versus 0.24 ms and 2,143 versus 21 shared buffer hits. With
`plan_cache_mode=force_generic_plan`, the post-change plan used both new
indexes as well as the existing trigram and canonical indexes. These are warm,
synthetic results, not production speedup or end-to-end latency claims.

Migration 0037 builds the indexes concurrently, repairs invalid interrupted
builds on retry, and drops both indexes concurrently on rollback. Index builds
consume database I/O and require capacity planning. Query semantics, relevance
scores, pagination and existing projection publications are unchanged; the
query still works if the indexes are absent during rollout or rollback.
