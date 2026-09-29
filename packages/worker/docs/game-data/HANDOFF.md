# Game data compiler handoff

The deterministic parser source is part of `packages/worker/src/wuwa_story_worker/compiler/`. Its baseline is packaged under `compiler/schemas/`; regression source and small fixtures are under `packages/worker/tests/compiler/`. This directory contains the investigation, schema, coverage, and migration notes preserved from the earlier Phase 0 work.

The sibling `wuwa-story-investigation` workspace has been removed. Its upstream Git clones, generated `dist/` snapshots, and intermediate parser outputs were not migrated. The large data and generated snapshots should be fetched into the worker's persistent volume at runtime, not committed to the project.

The current pipeline is scheduler → durable RabbitMQ snapshot job → pinned Git checkout → deterministic compiler → manifest checks → existing database importer. See [the worker guide](../../README.md), [the canonical schema](CANONICAL_SCHEMA.md), and [the investigation](INVESTIGATION.md).
