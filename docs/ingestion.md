# Deterministic source ingestion

`CompilerDatasetImporter` is the deterministic boundary from a compiled filesystem dataset to the database. It records the release snapshot, raw files and records, localization identities/locale values, canonical nodes and typed records, graph edges, and edge evidence. It does not use an LLM, embeddings, fuzzy matching, or inferred semantic relationships.

## Preconditions

Dataset root must include a versioned `manifest.json`, `coverage.json`, `raw-evidence/index.json`, entity JSONL files, and `graphs/global.jsonl`. Every indexed raw source file is SHA-256 and byte-size checked before reading. Canonical edge records must carry endpoints, type, basis, source, raw path, and version. The adapter fails on malformed rows or missing provenance.

Raw source rows remain release-scoped and are tied to SHA-256-verified source files. Decimal tokens are normalized to JSON numeric values for JSONB. Unpaired UTF-16 surrogate escapes and NUL escapes, which PostgreSQL JSONB rejects, are stored as reversible literal `\\uXXXX` sequences; the exact original bytes remain available in the indexed source file. Localization `resolved_empty` values are retained; absent keys remain absent rather than receiving synthetic text. Graph edge evidence points to the original source file/record and raw field path. Source identity and version are carried into canonical records.

## Entrypoint

Import a compiled snapshot into the configured PostgreSQL database with:

```bash
docker compose run --rm --build --no-deps \
  -v /absolute/path/to/dist/3.6.0:/dataset:ro \
  api wuwa-story-import /dataset --batch-size 500
```

The importer registers the release, then loads raw evidence, localization, canonical entities, typed records, global edges, and edge evidence. Completed raw files are skipped on resume when their stored SHA-256 and row count match the indexed snapshot. JSONB-incompatible NUL and unpaired surrogate escapes are stored as reversible literal escapes, and Git LFS pointer files are retained as explicit raw records.

If a previous importer version populated generic graph nodes but left typed action/dialogue projections incomplete, rebuild those two projections from the same snapshot with:

```bash
.venv/bin/python scripts/refresh_action_dialogue_projection.py \
  /absolute/path/to/dist/3.6.0 --batch-size 500
```

This targeted repair only replaces `core.quest_action` and `core.dialogue_line` rows whose node IDs are present in those two source files. It does not modify raw evidence, localization, graph nodes, or graph edges.

For a compiled snapshot that adds only exact-resource speaker/character crosswalks, apply those graph edges and typed links without replaying the multi-million-row localization import:

```bash
.venv/bin/python scripts/import_exact_crosswalks.py \
  /absolute/path/to/dist/3.6.0
```

This imports only `exact_join` edges and records their source row, raw path, matching asset evidence, and release version.

For an already imported release, refresh searchable entity fields and explicit
entity-reference edges without replaying dialogue/localization projections:

```bash
.venv/bin/python scripts/refresh_searchable_entities.py \
  /absolute/path/to/dist/3.6.0
.venv/bin/python scripts/import_entity_reference_edges.py \
  /absolute/path/to/dist/3.6.0
.venv/bin/python scripts/build_lexical_index.py 3.6.0 \
  --category area --category item_description --category character_nickname
```

The focused import applies to an existing release whose canonical node
identities and raw evidence are already loaded. A fresh release should use the
full `wuwa-story-import` command above. Exact ID references preserve source
file, raw path, version, and resolution basis; unsupported or unresolved
identities remain diagnostics instead of guessed links.
