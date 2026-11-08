# Cutscene descriptions

A cutscene has two distinct kinds of content:

- **System observation:** the full visual report in `cutscene_visual_description`. It remains evidence tied to the observed video hash and recording version. Public-summary editing never modifies it.
- **Public summary:** a short readable title, text and optional chapters. It is stored independently from a quest and returned by `/cutscenes/{name}` as `public_descriptions`, and on the corresponding playback media as `description`.

## Identity and revisions

Use the existing `content.document` and `content.document_head` tables. No schema migration is needed. Heads are keyed by video **asset node**, locale, and document type:

- `cutscene-public:{release_id}` — generated summary;
- `cutscene-public-edit:{release_id}` — manual override.

The release identifies the requested story snapshot; `asset_version` separately records the client recording version. Variant assets retain independent summaries. Source SHA-256 identifies the observed bytes, not the mutable publication reference ID. Changed bytes require review/republication; old revisions remain stored. Replacing a report or quest analysis with the same video bytes does not remove an independent public summary.

Generated quest analysis publishes its validated public summaries to independent heads in the same transaction. Quest metadata remains for compatibility. Standalone producers can call `publish_descriptions` with validated `CutsceneDescription` entries; no quest association is required by this service. Public responses never include the system report's observations.

## Admin integration contract

The current admin catalog/player work is a separate change. Its editor should use these shared services from `wuwa_story.story.cutscene_descriptions`, inside the authenticated admin transaction:

1. Resolve the scene and requested release/locale, and verify the submitted asset is in that scene's playback manifest. Never accept an arbitrary asset ID from the form without this check.
2. Call `edit_public_description(..., asset_id, release_id, locale_id, title, text, expected_revision)`. The service binds the edit to the current video hash. `expected_revision=0` creates the first manual override; subsequent edits must submit the current **manual** revision, not the generated revision. On conflict, reload rather than overwrite.
3. Call `clear_manual_description(..., expected_revision)` to restore generated fallback. This clears only the manual head and retains all documents/history.
4. Commit through the existing session lifecycle. Do not overwrite quest analysis metadata or the visual report. These are public-summary services, not system-report editing endpoints.

Public detail and quest media endpoints overlay current description heads on read-projection data, so manual edits do not wait for a full quest projection rebuild. An absent description produces no empty public section. Locale selection is exact for independent heads; it does not silently apply an English edit to another language.

## Existing summaries

Transfer already generated summaries without exports or model calls:

```sh
python -m wuwa_story.story.cutscene_description_backfill --all-releases
python -m wuwa_story.story.cutscene_description_backfill --all-releases --apply
```

Use `--release-id ID` (repeatable) to restrict the scope. Default limit is 1000 quest documents; `limit_reached` indicates that a larger explicitly bounded `--limit` may be needed, up to 10000. The default run is read-only and reports candidates. Applying also checks current complete visual reports before copying. Invalid/stale inputs are reported by source document ID and left unchanged. Repeated application reuses an identical head; it does not overwrite a manual head. Nothing invokes paid analysis or schedules a media reimport.

Tests cover standalone/no-quest reads, projection overlays, variants, byte identity, hidden system observations, edit conflicts and override removal. The real PostgreSQL analysis test also covers generated publication, manual-edit survival across publication, locale/release separation and idempotent legacy backfill.
