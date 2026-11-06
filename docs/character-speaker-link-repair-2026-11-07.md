# Character speaker identity repair — 2026-11-07

## Cause and evidence

Personal portrait references alone miss story speakers using placeholder art.
PlotGuest contains explicit SpeakerID arrays and RoleInfo localization keys, but
also stale/copied mappings. In snapshot 3.7.0, GuestID 1005 names Jianxin and
GuestID 1010 names Sigrika while both list speaker 150088. Its resolved speaker
name is Sigrika. Sample dialogue includes her self-introduction, and independent
transcripts attribute the classroom and New Solar Celebration lines to Sigrika:

- https://wuwa.wiki/en/codex/stories/100035
- https://wuthering.th.gl/db/stories/story_100037

The historical cause of the inconsistent guest row is not established. The fix
requires the explicit guest reference, a unique playable RoleInfo name key, and
the same snapshot's resolved English speaker name. Names alone cannot create a
link. Conflicts with personal portrait proof and ambiguous target roles are
rejected. Original source rows and rejected legacy edges remain archived.

## Validation and bounded repair

- 42 targeted compiler, identity-reader, audit and backfill tests passed; one
  private-input test skipped.
- 135 additional profile, identity and projection-control tests passed.
- Ruff and diff whitespace checks passed.
- Read-only production dry-runs covered all 21 imported releases. They found
  76 missing evidence records across 13 snapshots (2.4.0–3.7.0), no absent
  endpoints. The latest snapshot has 16 records for 14 character/speaker pairs;
  duplicate guest presentations remain evidence for one graph edge.

After deploying the immutable API/worker image, repeat the dry-run per release:

```
python -m wuwa_story.ingestion.speaker_crosswalk_backfill --release-id RELEASE
```

Apply only its reviewed role IDs, then repeat the dry-run to confirm zero missing
links. The importer upserts existing identity edges and evidence; it does not
repeat source/media extraction. Refresh affected character projections for each
snapshot and `current`, including already published characters sharing these
speakers' states, so reverse shared-scene links are refreshed too. Preserve
published generations while replacements build.

API/web delivery uses the existing safe app workflow. Snapshot and projection
workers need a separate captured-ID graceful rollout; do not restart active
media workers or use the broad worker deployment target. No schema migration is
required. Rollback: restore prior immutable app/worker images; retained source
and evidence remain available, and old readers ignore the new proof prefix.
