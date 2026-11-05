# Character identities and source roles

Rover uses one public family anchored at the existing default source role address.
Source nodes, revisions, media ownership and existing links are preserved. No
schema migration, graph-node deletion or game/media reimport is required.

The compiler reads `BinData/main_role_change/mainroleconfig.json`, including its
explicit Gender field (not ID parity). Attribute labels come from elementinfo.
New attributes need no ID/name allowlist. Temporary roles join only when their
name key and both personal portrait references exactly match one main role.
They are not additional selectable variants. Missing/ambiguous evidence does
not invent identity. Old cached canonical bundles preserve an established family.

Catalog/search default to playable roles. Story/event and technical roles are
explicit filters, applied before pagination/counting. The public card family
field is materialized with the existing character projection. Shared source
skill-template artwork is not presented as personal artwork on unrelated roles.

## Rollout

1. Deploy the API and projection builder containing this change; update the
   compiler worker image for future imports without interrupting active jobs.
2. Preview existing-data changes:
   `python -m wuwa_story.ingestion.character_identity_backfill`
3. Apply metadata corrections and republish current character archives (including
   the new talent-value contract) without importing game/media again:
   `python -m wuwa_story.ingestion.character_identity_backfill --apply --refresh-all`
4. Let the existing projection worker publish these pages, then deploy the web
   changes. Confirm one Rover in the playable catalog/search and that gender
   and attribute selectors preserve one another. Source role URLs remain valid.
5. Repeat the preview: changed must be zero. No queue purge or media import is
   part of this backfill. `--refresh` intentionally requests republishing even
   when metadata already matches; omit it for a no-op repeat.

Rollback is code rollback; added public_identity metadata is additive. Earlier
readers ignore it. Do not delete source nodes or media. The default command is
read-only; production application has not been performed by this branch.

## Validation

An isolated source-code probe against server data, entirely rolled back, found
8 selectable variants (male/female × Spectro/Havoc/Aero/Electro), with 392 shared
quest appearances. Batched quest localization/classification reduced the complete
source profile build from exceeding 120 seconds to 62.13 seconds. This is offline
projection construction, not public request latency. Catalog/public profile
reads continue using the existing prepared character read model.

The read-only existing-data preview identified 31 Rover source roles (8 primary
and 23 temporary) before the final additional foreign-creature artwork guard.
NPC 5015 retained its own head/card pictures and excluded Yangyang's formation
card, borrowed skills and biography. No production records were changed.

Local browser checks at 1280×900 and 390×844 verified gender change, preservation
of gender across a previously unknown attribute, a single common quest entry,
category filtering, and no horizontal overflow. This browser test used controlled
API fixtures; the separate server probe validated actual source records.

Targeted backend/compiler and frontend checks pass. The full frontend suite has
three pre-existing eventRequests.test.mjs module-mock failures for
`../ui/RenderedAnimation`; neither that test nor the event component was changed.


## Talent parameters and NPC corrections

Skill attributes join skilldescription by the source SkillLevelGroupId in one
snapshot-scoped batch. Exact authored arrays are retained, including constants,
missing positions, and additional levels. The public level selector is capped by
MaxSkillLevel; ambiguous multi-parameter expressions expose their separate values
without inventing a formula. These records already exist in the database.

Foreign NPC artwork paths are materialized with character metadata so legacy
image lookups also exclude them. The compiler updates this alongside each role;
old cached bundles preserve established exclusions. Public portraits, galleries
and social previews use personal images only, falling back to a placeholder if
none exist. Personal NPC artwork and original media references are retained.
The public character hero no longer shows the snapshot's ARCHIVE SOURCE banner.

The backfill uses a 2-second lock timeout and 30-second statement timeout. A lock
conflict rolls back rather than blocking imports. Before application, save the
old character metadata and take a protected-container ID snapshot. Update only
idle snapshot/media preparation and projection services; leave active image,
audio, video, model workers and database/broker services running. Publishing
current character views is additive and serves existing views during rebuild.
