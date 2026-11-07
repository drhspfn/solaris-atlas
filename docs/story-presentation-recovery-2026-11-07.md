# Story presentation recovery

The release-21 source audit found 1,594 missing `ShowTalk.SequenceDataAsset`
edges. The compiler already emits these edges for new imports. Historical
snapshots need the explicit repair; a video re-encode does not restore them.

`review_order_backfill --repair-links` now runs the sequence repair alongside
the quest/dungeon repair, followed by catalog order and projection invalidation:

```sh
python -m wuwa_story.ingestion.review_order_backfill --all-releases
python -m wuwa_story.ingestion.review_order_backfill --all-releases --repair-links --apply
```

Writes use bounded batches to stay below PostgreSQL's bind parameter limit.
Completed batches can survive a later failure; repeat failed release IDs safely.
Source evidence and dirty scopes coalesce, and published videos are untouched.

To queue illustrations and sequence-controlled presentations from an imported
snapshot, pin an existing completed preparation with retained files:

```sh
python -m wuwa_story.ingestion.story_presentation_backfill --release-id 21 --workspace-id 17391
python -m wuwa_story.ingestion.story_presentation_backfill --release-id 21 --workspace-id 17391 --apply
```

Each state is a separate job so an unsupported scene cannot hold an entire
100-entity batch. `--entity` narrows the scope; `--priority-entity` selects a
source-verified state to enqueue first. Run detached with durable stdout/stderr.
The normal worker verifies exact published source provenance and reuses valid
files. Sequence metadata revision 4 revisits obsolete extraction recipes.

After confirmed queue loss, add `--republish-queued` to the same apply command.
This redelivers the stored payload under the same run identity. It leaves running
tasks alone; duplicate delivery is handled by the worker's run lock and terminal
state checks. It is explicit recovery, not a periodic blanket queue replay.

Sequence control discovery follows event-reachable receiver classes rather than
all character/material references in decoded bytecode. Unsupported multi-rig UI
composition remains an archived source with diagnostics, not a claimed animated
preview. The fox scene after M0390 uses `DA_M3_13_41` and is the priority pilot.

No schema migration is required. Roll back application code through the normal
deployment path; keep retained archives and existing published media intact.
Deploy the image worker with the matching code without cancelling its active
predecessor, and keep the source-link rebuild separate from media rendering.
