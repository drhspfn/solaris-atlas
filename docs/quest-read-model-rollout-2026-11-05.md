# Quest projection rollout — 2026-11-05

The API was still serving quests through source graph queries although the current
English quest projection scope had a published generation (11, 1,927 quest rows).

A bounded pilot rebuilt only Denia's quest, entity 5389571 / game quest 119000000.
The refresh finished without an error and published generation 2. No global rebuild,
index replacement, or source graph rewrite was run.

## Parity and server-side measurements under active imports

| Section | Projection seconds | Source HTTP seconds | Parity |
| --- | ---: | ---: | --- |
| Profile | 0.771 | 6.984 | Exact after the existing documented source-body compaction |
| Transcript, first 50 lines, no raw records | 0.487 | 16.016 | Exact |
| Media | 0.081 | 5.983 | Exact |
| Continuity | 0.050 | 8.781 | Exact |

The profile comparison applies `_compact_storage({'profile': _public_json(source)})`
before comparison: read models intentionally omit raw source bodies, retaining
runtime parameters and provenance references. Both paths return the same 84 flow
states; all compact profile fields match. Public technical-source bodies remain
available through their provenance readers.

Production `READ_PROJECTION_KINDS` now also selects `quest`, preserving current/en
selectors and existing character, story-map, notice, location and event readers.
API deployment: GitHub Actions 41934129063. Verify final deployment, external page
latency, and the projection worker selector before considering rollout complete.
Historical explicitly selected patches retain their previous source route.

Rollback: remove only `quest` from the GitHub production variable and redeploy API.
No destructive migrations are involved. Keep published payloads and dirty rows.

Remaining measured limits: PostgreSQL still has 128 MiB shared buffers and shares
host I/O with archive extraction. Do not restart it blindly during active imports.
The public Denia page previously exceeded a 12-second smoke-test deadline; that is
a lower bound, not its full measured load duration. Recheck after routing rollout.

## Static archive regression found during continued imports

A later smoke test returned HTTP 500. The Next.js stack pointed to
`QuestIllustration`: newly archived Spine entries can have playback metadata but
no playable `variants` or `unsupported` array. Optional chaining guarded the
playback object but then called `filter`/`map` on absent fields. Guarding both
arrays preserves the existing poster/unavailable fallback and valid animations.
Seven React server-render regression cases cover absent/null playback, unsupported
metadata, absent/null/empty variants, posterless archives and playable animation.
Related quest/preview tests and TypeScript validation pass. Recheck the production
page after this frontend correction; the earlier 1.98-second HTTP 200 sample alone
is not sufficient evidence of sustained success as new archive shapes arrive.
