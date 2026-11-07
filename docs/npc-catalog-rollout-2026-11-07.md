# NPC catalog, graph and model rollout

The installed client supplies NPC identities (`character:npc:<BlueprintConfig.Id>`),
world instances, scoped display names, explicit interaction flows and shops.
The native supplement updates the latest imported story snapshot only; it retains
pinned raw source evidence and uses existing idempotent graph upserts. Replaying
it must not produce duplicate nodes, revisions or graph evidence.

Manual NPC model imports use the existing character import/upload workflow.
The catalog job registers model recipes but does not queue all NPC model builds.
Authored mask bones, umbrella/socket attachments and independent linked rigs are
retained; unsupported material compositions fail without publishing partial files.

## Release validation (2026-11-07)

- 567 changed Python tests passed, including PostgreSQL NPC graph replay.
- 91 event backend/worker tests passed, including PostgreSQL integration cases.
- The three event request-harness failures are fixed; full frontend suite: 321 passed.
- TypeScript checking, changed-file Ruff and diff checks passed.
- Fresh CLI rig-only exports, archive verification and rendered previews:
  Rosemary (`15267`), Cristoforo (`15697`), and NPC `17521`.
- A raw MaterialInstance parent crash found by actual exports is fixed centrally.

## Production order

1. Preserve active media worker IDs and the existing scoped dialogue rebuild.
2. Deploy API/web and only the necessary worker services with immutable images.
   No new migration is required for this branch.
3. Start the restartable native NPC catalog job from the installed 3.7.1 client,
   targeting the latest imported story snapshot. Keep its logs outside temporary
   extraction directories. Do not replay completed story/media imports.
4. Build representative NPC character projections first and verify the public
   NPC category, named search, dialogue, shop and model-import availability.
5. Leave the remaining catalog/projection work running in the background; verify
   its processing job and worker are actually active before handing it off.

## NPC profile browsing

- `GET /npcs/{key}/scenes` pages conversations with evidenced dialogue, human
  titles, participants, previews and quest addresses (`limit`/`offset`). It does
  not equate everyone in a referenced conversation with the NPC itself.
- `GET /npcs/{key}/dialogue?state_id=...` reads one of those conversations in
  authored action/line order. States outside the NPC's source set return 404.
- `GET /npcs/{key}/locations` exposes only placements matched to a published
  map marker, with localized area/world labels and a working map URL. Raw
  placements remain available in `/instances` when no public map is available.
- The public profile uses compact cards and pagination; source keys stay in the
  API or closed source details. Metadata distinguishes NPCs, shared templates
  and playable characters using structured profile data.

No schema migration or reimport is required. Deploy API and web together through
selective deployment; active media workers remain untouched. Rollback uses the
previous API/web image pair.

## Map-scoped NPC actor repair

NPC entity IDs are unique within a map, not across the whole game. Flow actor
bindings now resolve their map through an authored LevelPlay owner and its exact
PlayFlow reference before joining a native `(MapId, EntityId)` placement. Shared
or conflicting map owners remain unresolved. This adds scene context; it never
turns a local actor binding into a global speaker/person identity.

For existing data, run `python -m wuwa_story.ingestion.npc_scenario` with
`--release-id`, the retained native `--entity-source` path, and optionally
`--source-record-id` for a canary. The default plans without writing; `--apply`
upserts the graph and queues affected character projections. Only the latest
snapshot can use current-client NPC placements. Repeating the repair must retain
stable edge/evidence identities. No media extraction or schema migration is
required. Keep an independent log/receipt for the background repair and preserve
active media workers during deployment.
