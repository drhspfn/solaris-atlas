# Encounter media recovery

## Behaviour

Quest encounters retain authored dungeon/LevelPlay branches, local action order,
asynchronous dialogue, and engine-sequence start/end boundaries. Unknown global
ordering remains explicitly unknown. Referenced MP4 fragments are extracted and
published separately, with their source ranges, clock and offsets; they are not
concatenated into a purported complete recording.

Published fragments require matching owner, release, client download,
presentation hash and bundle identity. Repeated references reuse stored files.
Older sequence packages are invalidated by their schema version without forcing
unrelated voice, image or Spine rebuilds.

## Failure audit (2026-11-05, 18:25 UTC)

- 15 old audio failures have completed replacement voice-package runs. Do not
  replay them solely because their dead-letter messages remain.
- 55 replacement video jobs and four preparation parents are present in the
  ready queue, behind existing work. Their messages are not lost. Seven video
  jobs completed in the preceding hour. Do not enqueue duplicate replacements.
- An export receipt previously accepted an existing directory even if declared
  files had disappeared. Reuse now requires every declared file and size;
  incomplete output is regenerated atomically.
- Cutscene-only release requests also require voice-package preparation.
- Extraction timeouts and killed subprocesses preserve their typed exception
  and persistent log path. Existing bounded broker retries handle timeout and
  exit -9/137; other nonzero exits remain diagnostic failures, not endless retries.

This does not imply that every missing source asset is recoverable or that every
old dead-letter message represents a current failure.

## Validation and rollout

Targeted validation: 144 extraction/retry/release-media/recovery tests and 161
encounter/compiler/sequence/publication/backfill tests passed locally. The M0334
evidence pilot produced four byte-identical MP4 fragments in a portable bundle;
this local evidence test is not proof of production publication.

After deploying matching producer/consumer images, create or reuse one exact
M0334 image-only flow-state media run for 3.1.0. Run
`scripts/recover-story-imports.py` in dry-run mode first. Apply requires the
reviewed pilot run and verifies its bundle, checksums, published objects and API
provenance before queuing additional releases. The host-side journal survives
worker workspace cleanup. Interrupted writes require reconciliation before
resume. No queue purge or blanket dead-letter replay is performed.

Refresh reviewed quest read models after deployment. Media publication marks
affected quests dirty. Confirm that the projection worker consumes the quest
kind; dirty marks alone do not prove the public response has refreshed.

No database schema migration is required. Rolling back code does not delete
published media or evidence. Retain durable logs and recovery checkpoints.

## Production follow-up, 19:55 UTC

PR #147 was squash-merged as `8f4b647099e7ba941d61f28a7db418e2f343f6e7`.
All-image build 42093183198 passed. Selective deployment finished at 19:35 UTC;
API is healthy. Two old image workers retain their active jobs and are draining
naturally. A durable stage-two watcher replaces only those workers after clean
exit; do not replace their containers while imports are still active.

Queue snapshot: video 903 ready / 3 unacknowledged; audio 4091 / 4; image 1943 / 2.
The failed-message counts remain video 83, audio 15, image 21, asset 1. Those are
messages, not unique missing outputs. Twelve release-media and two visual-analysis
runs completed after deployment; the bounded run audit found no new failed,
blocked or enqueue-failed runs in that interval. This does not prove every leaf
or native subprocess succeeded.

A follow-up fixes two confirmed defects: an ambiguous language formerly discarded
otherwise usable character voice languages, and historical quest profiles could
select newer source provenance and fail projection validation. Their combined
51 targeted tests passed. Recovery of 11 unambiguous language mappings is planned;
17 mappings still require selector evidence and must not be guessed.

M0334's full retained quest tree differs from snapshot 3.1. A read-only canary
validated an unchanged quest node and all 37 nodes of its linked LevelPlay tree,
including the M0334 flow. This supports a related encounter with unknown global
position, not an assertion that the newer entire quest tree matches 3.1. The
production fragment pilot and historical recovery remain gated pending this fix.
