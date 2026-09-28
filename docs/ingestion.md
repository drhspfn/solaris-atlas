# Deterministic source ingestion

`CompilerDatasetImporter` is the deterministic boundary from a compiled filesystem dataset to the database. It records the release snapshot, raw files and records, localization identities/locale values, canonical nodes and typed records, graph edges, and edge evidence. It does not use an LLM, embeddings, fuzzy matching, or inferred semantic relationships.

## Preconditions

Dataset root must include a versioned `manifest.json`, `coverage.json`, `raw-evidence/index.json`, entity JSONL files, and `graphs/global.jsonl`. Every indexed raw source file is SHA-256 and byte-size checked before reading. Canonical edge records must carry endpoints, type, basis, source, raw path, and version. The adapter fails on malformed rows or missing provenance.

Raw source rows remain release-scoped and are tied to SHA-256-verified source files. Decimal tokens are normalized to JSON numeric values for JSONB. Unpaired UTF-16 surrogate escapes and NUL escapes, which PostgreSQL JSONB rejects, are stored as reversible literal `\\uXXXX` sequences; the exact original bytes remain available in the indexed source file. Localization `resolved_empty` values are retained; absent keys remain absent rather than receiving synthetic text. Graph edge evidence points to the original source file/record and raw field path. Source identity and version are carried into canonical records.

## Entrypoint

Import a compiled snapshot into the configured PostgreSQL database with:

```bash
docker compose -f infrastructure/local/compose.yml --env-file infrastructure/local/.env \
  run --rm --build --no-deps \
  -v /absolute/path/to/dist/3.6.0:/dataset:ro \
  worker wuwa-story-import /dataset --batch-size 500
```

## Import a chronological version series

Place each separately compiled snapshot in a child directory of one root, for example:

```text
/datasets/wuwa/
  3.0.0/manifest.json
  3.1.0/manifest.json
  ...
  3.6.0/manifest.json
```

The series importer reads each manifest, sorts snapshots by the full game version, and imports
them one at a time. It requires at least one snapshot for every major/minor version in the
requested range and fails before connecting to PostgreSQL if any are missing. Multiple hotfix
snapshots in a minor version are all imported in ascending order.

```bash
cd packages/worker && uv run wuwa-story-import-series /datasets --from-version 3.0 --to-version 3.6 --dry-run

cd packages/worker && uv run wuwa-story-import-series /datasets --from-version 3.0 --to-version 3.6
```

`wuwa-story-import-series /datasets --from-version 3.0 --to-version 3.6` is the equivalent
installed CLI command. This importer consumes version-specific snapshots; it does not synthesize
older versions from a newer archive. To obtain the snapshots from upstream and compile them, use
the GitHub sync command below.

## Fetch, compile, and import directly from GitHub

Arikatsu's public `WutheringWaves_Data` repository keeps version branches (`3.0` through `3.6`)
with the source datamine for each release line. `sync-github` discovers those refs using Git,
fetches each branch head as a shallow pinned commit into a persistent cache, compiles it with the
local narrative compiler, imports that compiled snapshot, then records the branch/commit checkpoint.
The checkpoint advances only after a successful compile and DB import, so a failed run retries that
commit next time. With no `--to-version`, newly appearing later release branches are discovered too.

From the worker package directory (`cd packages/worker`):

```bash
uv run wuwa-story-sync-github \
  --compiler-root ../../../wuwa-story-investigation \
  --workspace ./var/upstream-sync \
  --from-version 3.0 --to-version 3.6 --dry-run

uv run wuwa-story-sync-github \
  --compiler-root ../../../wuwa-story-investigation \
  --workspace ./var/upstream-sync \
  --from-version 3.0 --to-version 3.6
```

For ongoing polling, omit `--to-version` and pass `--watch`; it checks every 30 minutes by default:

```bash
uv run wuwa-story-sync-github \
  --compiler-root ../../../wuwa-story-investigation \
  --workspace ./var/upstream-sync \
  --from-version 3.0 --watch --interval-seconds 1800
```

The upstream branch inventory was confirmed on GitHub on 2026-09-27. A one-time historical sync
can therefore create separate 3.0–3.6 release snapshots from the pinned branch heads. This records
the latest retained datamine commit for each release line; it does not reconstruct every hotfix
commit that was overwritten on a branch.

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
