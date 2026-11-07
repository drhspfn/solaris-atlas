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

## Explicit contextual references

Follow-up: preserve every unambiguous PlotGuest.Name -> RoleInfo.Name reference
as `plot_guest -> character`, even when its speaker labels conflict. Structured
edge metadata carries `identity_status` and `warnings`; the release-specific
evidence explanation records the same warning. Existing guest -> speaker edges
retain the original speaker array. Contextual role references never establish a
global speaker identity, so a suspect Jianxin guest row does not assign Sigrika's
lines to Jianxin. Missing or ambiguous role keys remain in raw guest records.

The extended production dry-run found 182 missing evidence records across the
same 13 snapshots: 76 speaker proofs plus 106 contextual role references. All
endpoints exist. Updated targeted suite: 49 passed, one private-input test skipped.


## Follow-up: stale profiles and sequence actors

Source repair and public projection refresh are separate stages. A zero-missing
source dry-run does not prove that character pages are current. The bounded repair
CLI now accepts `--apply --refresh` to queue the selected character pages even
when no additional source edge needs writing. Refreshes retain published pages
while replacements build.

Iuno has a temporary RoleInfo 5036 with explicit ParentId 1410, the same name key
and personal head assets. These source-backed copies resolve to the playable
parent instead of making its portrait ownership ambiguous. Exact big-head assets
also occur in Speaker.HeadIconAsset, not only HeadRoundIconAsset.

Hsin exposes another identity path: the flow state
`剧情_3_6_玄方城_4_1` references `DA_M3_12_02`, whose generated BindingBP contains
`BP_Xinyuehu_Seq_V2`. Its default TalkID is 450001 and its SkeletalMesh exactly
matches RoleInfo 1311 -> model preload 100071. This proves the default talker
without a portrait. Speaker 400002 is independently corroborated by the same
scene cast plus its uniquely resolved RoleInfo name. A name without this resource
chain never establishes the identity. The exported actor chain, hashes and raw
Blueprint properties are retained with graph evidence.

Sequence media imports retain these typed actor Blueprint dependencies and
upsert the resulting speaker links. The focused sequence identity CLI can repair
existing retained flow records without reimporting audio/video. Replays add no
duplicate graph edges; conflicting existing identity proofs are not overwritten.
