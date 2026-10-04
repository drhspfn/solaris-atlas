# Adaptive lore analysis

The supplied outline is a product brief. Example lore statements are illustrative,
never seed facts or a keyword classifier. Existing source provenance, budget
reservations, checkpoint replay and old published documents remain authoritative.

## Delivery

1. Add a versioned assessment and evidence-backed hooks. Separate epistemic
   certainty from branch occurrence. Select depth and output bounds on the server;
   preserve old contracts. Cover escalation, verbosity and false-link rejection.
2. Require a cheap pre-scan before research, persist its decision, allow only cited
   depth upgrades, and require structured review before publication. Reuse the
   existing provider loop, cost ledger and exact-source validation.
3. Publish readable classification, scene importance, hooks and source inventory.
   Preserve the knowledge available at each encounter. New imports mark a review
   need without removing still-valid old explanations.
4. Persist import-triggered revisit work, find bounded cross-quest candidates from
   distinctive evidence, and execute focused recontextualization under the same
   daily budget. Preserve the original document and add separately cited findings.
   Include administrative visibility and deterministic recovery from queue errors.

## Decisions

- Mentioning a name or generic word alone cannot establish importance or a link.
  High/critical signals require exact citations and an explanation of their role.
- Pre-scan is provisional. Missing source coverage cannot justify confidently
  skipping a quest; all target dialogue is read before publishing an analysis.
- Short output is still sourced. Word ceilings cover generated visible prose,
  excluding exact quotations and machine identifiers. Bounds and tool budgets
  are enforced, with a repairable validation error on overflow.
- Player choices and conditional paths are occurrence metadata, separate from
  whether a claim is confirmed, suggested, inferred or character speculation.
- Cross-quest claims require a direct reference or multiple distinct sourced
  signals. Candidate matches are not graph facts. Theory never becomes a strong
  semantic edge automatically.
- Recontextualization supplements an immutable previous explanation. Its new
  sources and confidence remain separate from original knowledge at encounter.
- No paid model calls during implementation checks. Deployment retains the
  configured $1 daily allowance. Test providers and isolated PostgreSQL exercise
  the complete flow; migrations are backed up before local application.

## Acceptance

Cheap activity takes a short route; a cited anomaly upgrades it; unsupported
escalation and oversized output fail validation. Classification survives resume
and compaction. Branch uncertainty, loaded-corpus limits and hook priority reach
the reader. Duplicate imports/messages do not duplicate jobs or charges. Revisit
candidate matching rejects generic-word coincidence and preserves prior claims.
The old v1-v4 pipelines and publications remain readable and resumable.

## Delivery and verification · 2026-10-04

Implemented on `codex/feat/adaptive-lore-analysis` in independently committed
policy, runner, import outbox, reader/admin and retrieval slices. The local API,
web and story-agent images run the new code; the protocol is `story-v5`.

- Server suite: 155 passed, 2 skipped. After the final retrieval change, all 51
  targeted contract, pipeline and lore tests passed again, including a term found
  only in a reviewed hook. Providers were mocked throughout.
- Worker story-agent tests: 2 passed with the current server source on PYTHONPATH.
- Web tests: 22 passed; the 5 rendering regressions passed again after the final
  spoiler-title and hook-status changes. TypeScript/Vite and Docker builds passed.
- Changed-file Ruff, ESLint and formatter checks passed. Whole-project gates still
  report existing import-order errors in `api/routes/story/__init__.py` and
  `EntityProfilePage.tsx`, plus unrelated frontend warnings/formatting findings.
  These are not changed by this feature.
- Browser checks used illustrative fixtures: disclosures, keyboard interaction,
  390px layout without horizontal overflow, API error/retry, and review pagination.
  No fixture lore was inserted into the working database.
- Migration 0010 upgrade/downgrade was tested on an isolated database. Before
  local application, schema and affected agent/document data were backed up under
  `E:/Backups/solaris-atlas/adaptive-lore-before-*`. The working database now reports
  `0010_agent_revisit`; the previous prologue explanation still returns HTTP 200.
- The daily allowance remains $1. The working model-call ledger remained at
  176 rows (max ID 176): no paid analysis was started for verification.

### Operational limits

New jobs use the new protocol. Existing publications retain their original
classification and need explicit reanalysis to acquire v5 hooks. New imports can
then schedule focused reviews under the same budget; budget or uncertain-call
pauses still need normal administrator handling.

Deterministic tests validate provenance, budgets, recovery, publication and UI
wiring. They cannot establish the literary quality or semantic correctness of a
real model's analysis. Review representative real outputs before bulk rollout.
Candidate retrieval is deliberately bounded and lexical, so an empty result does
not prove a mystery has no later explanation. Migration rollback instructions and
runtime configuration are in `story-agent-pipeline.md`.
