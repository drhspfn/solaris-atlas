# Deterministic source ingestion

`CompilerDatasetImporter` is the deterministic boundary from a compiled filesystem dataset to the database. It records the release snapshot, raw files and records, localization identities/locale values, canonical nodes and typed records, graph edges, and edge evidence. It does not use an LLM, embeddings, fuzzy matching, or inferred semantic relationships.

## Preconditions

Dataset root must include a versioned `manifest.json`, `coverage.json`, `raw-evidence/index.json`, entity JSONL files, and `graphs/global.jsonl`. Every indexed raw source file is SHA-256 and byte-size checked before reading. Canonical edge records must carry endpoints, type, basis, source, raw path, and version. The adapter fails on malformed rows or missing provenance.

Raw source rows remain release-scoped and are tied to SHA-256-verified source files. Decimal tokens are normalized to JSON numeric values for JSONB. Unpaired UTF-16 surrogate escapes and NUL escapes, which PostgreSQL JSONB rejects, are stored as reversible literal `\\uXXXX` sequences; the exact original bytes remain available in the indexed source file. Localization `resolved_empty` values are retained; absent keys remain absent rather than receiving synthetic text. Graph edge evidence points to the original source file/record and raw field path. Source identity and version are carried into canonical records.

The quest-tree refinement adds `release_id` to each edge-evidence row. Repeated canonical edges may now have independent evidence for every imported snapshot, so a version-filtered graph query uses only edges present in that snapshot. `graph.node_revision` supplies observed quest presence by release. A quest's earliest observed game version means the earliest **imported** snapshot containing it; imported branch heads cannot prove the first live patch in which it appeared. Run `cd packages/server && .venv/bin/alembic upgrade head` before importing compiler output with the new quest-tree relations. Existing imports need to be recompiled and reimported to gain the newly normalized QuestTree nodes and edges; the migration only preserves evidence already in the database.

## Automated worker pipeline

The worker package contains the deterministic compiler, so no investigation checkout or bind mount is required. The scheduler discovers numeric release branches from `1.0`, publishes each unseen branch head to durable RabbitMQ, and checkpoints the commit only after the broker confirms publication. Each message carries both the version branch and immutable full commit SHA. The consumer checks out that commit, compiles and verifies the manifest, imports the dataset through `CompiledDatasetImporter`, and removes the temporary per-job build after success. The shared shallow Git object cache remains in the worker volume.

Start the consumer and scheduler:

```bash
docker compose -f infrastructure/local/compose.yml --env-file infrastructure/local/.env \
  --profile worker up --build -d rabbitmq worker snapshot-scheduler
```

Queue one version manually or scan once:

```bash
docker compose -f infrastructure/local/compose.yml --env-file infrastructure/local/.env \
  --profile worker run --rm worker wuwa-story-worker enqueue-snapshot --version 1.0

docker compose -f infrastructure/local/compose.yml --env-file infrastructure/local/.env \
  --profile worker run --rm snapshot-scheduler wuwa-story-worker watch-upstream --from-version 1.0 --once
```

The scheduler polls every 30 minutes by default and watches new commits on known branches as well as newly appearing version branches. It records the latest published commit per branch in the persistent worker volume. This captures the retained branch heads; it does not reconstruct historical hotfix commits overwritten upstream. Consumers acknowledge successful imports; failures go to the durable `wuwa.snapshot-build.v1.failed` queue and can be requeued with `wuwa-story-worker replay-failed`. See [the worker guide](../packages/worker/README.md) for concurrency and operations.

The previous local compiled-series entrypoint remains available via `wuwa-story-worker import-series /datasets --from-version 1.0 --to-version 3.6`, for importing snapshots compiled outside this automatic pipeline.

The importer registers the release, then loads raw evidence, localization, canonical entities, typed records, global edges, and edge evidence. Completed raw files are skipped on resume when their stored SHA-256 and row count match the indexed snapshot. JSONB-incompatible NUL and unpaired surrogate escapes are stored as reversible literal escapes, and Git LFS pointer files are retained as explicit raw records. If an upstream JSON file is structurally malformed, already readable rows are imported and the complete original byte stream is retained as an explicit `malformed_json_file` raw record with its parser error and source hash; this is visible in source-file ingestion metadata and is not treated as a clean parse.

If a previous importer version populated generic graph nodes but left typed action/dialogue projections incomplete, rebuild those two projections from the same snapshot with:

```bash
cd packages/server && uv run python scripts/refresh_action_dialogue_projection.py \
  /absolute/path/to/dist/3.6.0 --batch-size 500
```

This targeted repair only replaces `core.quest_action` and `core.dialogue_line` rows whose node IDs are present in those two source files. It does not modify raw evidence, localization, graph nodes, or graph edges.

For a compiled snapshot that adds only exact-resource speaker/character crosswalks, apply those graph edges and typed links without replaying the multi-million-row localization import:

```bash
cd packages/server && uv run python scripts/import_exact_crosswalks.py \
  /absolute/path/to/dist/3.6.0
```

This imports only `exact_join` edges and records their source row, raw path, matching asset evidence, and release version.

For an already imported release, refresh searchable entity fields and explicit
entity-reference edges without replaying dialogue/localization projections. A
fresh full import builds all lexical search documents automatically; run the
indexer manually only to repair or refresh an existing release:

```bash
cd packages/server && uv run python scripts/refresh_searchable_entities.py \
  /absolute/path/to/dist/3.6.0
cd packages/server && uv run python scripts/import_entity_reference_edges.py \
  /absolute/path/to/dist/3.6.0
cd packages/server && uv run python scripts/build_lexical_index.py 3.6.0
```

The focused import applies to an existing release whose canonical node
identities and raw evidence are already loaded. A fresh release should use the
full `wuwa-story-import` command above. Exact ID references preserve source
file, raw path, version, and resolution basis; unsupported or unresolved
identities remain diagnostics instead of guessed links.
