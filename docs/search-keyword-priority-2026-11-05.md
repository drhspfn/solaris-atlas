# Lexical search timeout: indexed words before fuzzy matching

Reproduced on 2026-11-05 with `I Won't Wait Anymore`, all source categories,
English, limit 30. Public API returned HTTP 503 after 20.88 seconds. PostgreSQL
logged its statement deadline in the title/body trigram OR query, before the
full-text query could execute. Both trigram GIN indexes and the full-text GIN
index already exist; creating another identical index would not fix the order.

The old plan performs a bitmap OR over title/body trigram indexes, then rechecks
potentially large texts and sorts similarity scores before its LIMIT. A title-only
probe also exceeded an eight-second diagnostic deadline. Breaking the same fuzzy
query into more concurrent requests would retain this cost and increase load.

Search now keeps canonical identity and exact alias lookup first, followed by
indexed `plainto_tsquery('simple', query)` full-text matching. If no word matches
exist, the original trigram search remains available for typos and partial names.
Word matches take priority; fuzzy-only suggestions are no longer mixed into a
successful keyword result page. Locale/category filters, sorting, entity
uniqueness and pagination remain in effect. This is not a change to the separate
quest-scoped dialogue API or semantic/story-analysis search.

## Evidence

- Existing full-text index probe: one match, 540ms execution, 62 shared/read buffers.
- Candidate full API handler, read-only against production data: first sample
  2.694s, subsequent samples 0.061s / 0.083s. These have different buffer/cache
  states and are not a controlled cold-cache benchmark.
- Returned source line: node 2747059, `I won't wait anymore.` (2.5 authored flow).
- Seven repository regressions cover apostrophes, Unicode, filters, duplicate
  documents, pagination, canonical/alias fast paths and typo fallback; route
  category and batched-label tests also pass (18 targeted tests total).

No database migration, reindex or mass source rebuild is required. Existing fuzzy
fallback can still be costly for queries with no keyword matches; this focused
fix does not claim to solve all fuzzy or historical-search latency. Rollback is
an API image rollback; no persisted search data changes.
