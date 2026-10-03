# Database schema

PostgreSQL 17 with `vector`, `pg_trgm`, and `pgcrypto`. Alembic owns schema lifecycle. The initial revision creates ten domain schemas and lookup seeds. SQLAlchemy models are grouped under `src/wuwa_story/db/models/`.

| Schema | Purpose | Main tables |
| --- | --- | --- |
| `ops` | Release and processing lifecycle | `game_release`, `import_run`, `processor`, `processing_run`, `ai_model`, `dependency` |
| `storage` | Content-addressed files and their locations | `file_object`, `file_location`, `file_variant`, `file_reference`, `file_type` |
| `raw` | Immutable source table/record snapshots | `source_file`, `source_record` |
| `i18n` | Locale-independent text identity and values | `locale`, `localization_key`, `localization_content`, `localization_value` |
| `graph` | Versioned canonical nodes and source-backed edges | `node_type`, `node`, `node_revision`, `edge`, `edge_evidence` |
| `core` | Typed game and platform entities | quests, quest nodes/states/actions, dialogue, choice, narration, speaker, character, NPC, item, location, cutscene, audio and conversation entities |
| `ontology` | Controlled types, tags and candidate mappings | event/relation types, tags, aliases and candidates |
| `story` | Events, claims, scenes and groupings | `scene`, `event`, `claim`, evidence and memberships |
| `content` | Documents and explicit references | `document`, `document_head`, `document_reference` |
| `search` | Search projections and optional embeddings | search docs, aliases, chunks and model-keyed embedding tables |

Identity rule: source identifiers are preserved in `node.source_identity` and typed fields. Speaker IDs do not imply character IDs. Dialogue rows reference localization identities instead of copying a chosen-language string. Locale text includes empty content and a resolution state. Claims and semantic events are not inferred by this foundation.

## Shared localization content

`i18n.localization_content` stores each exact UTF-8 text once, addressed by a unique
SHA-256 hash and a numeric ID. `localization_value` retains one association for each
release/key/locale, but stores only `content_id`, resolution status, redirect,
source-record reference, and timestamp. Identical text can be shared across both
languages and releases. Whitespace, case, Unicode, and line breaks are preserved;
the empty string is a real dictionary entry, while absent content has a null ID.

The importer inserts dictionary entries in bounded, hash-ordered batches with
`ON CONFLICT DO NOTHING`, then resolves their IDs and checks exact text equality.
Dictionary entries and snapshot associations use the existing import transaction.
Retries reuse existing entries and associations. A conflicting hash with different
text aborts the import rather than silently linking incorrect content.

ORM reads still expose `LocalizationValue.content` and `content_hash`; responses,
fallback rules, and source evidence remain unchanged. Bulk lexical indexing joins
the dictionary directly. Queries selecting only the computed `content` expression
must explicitly use `.select_from(LocalizationValue)`.

### Upgrade and rollback

Migration `0007_localization_content` backfills existing text, verifies hashes and
complete coverage, and replaces the old text/hash columns with dictionary links.
It preserves all snapshot associations and provenance. Fresh databases already
receive this schema from foundation metadata.

This is an atomic maintenance migration, **not a rolling upgrade**:

1. Stop API processes and import workers, including host-run workers.
2. Take and verify a PostgreSQL backup. Reserve space for the dictionary, rewritten
   associations, and WAL; peak disk usage can increase during conversion.
3. Deploy matching server/worker code and run `uv run alembic upgrade head` from
   `packages/server` with the target database configuration.
4. Check row counts and sample text/provenance, then restart services.

The migration holds an exclusive localization-value table lock and fails after
10 seconds if it cannot acquire the lock. Runtime depends on snapshot size. Failure
rolls back the transactional DDL and backfill. To roll back a successful deployment,
stop services, run `uv run alembic downgrade 0006_tile_maps`, then restore the old
application version. Downgrade expands dictionary links back into per-snapshot
text/hash columns and needs additional disk space.

Dropping columns and updating rows does **not** immediately return their disk space
to the filesystem. Schedule `VACUUM (FULL, ANALYZE) i18n.localization_value` in a
separate maintenance window if physical reclamation is required; it locks and
rewrites the table and needs temporary disk space. Ordinary vacuum lets PostgreSQL
reuse dead-row space but does not shrink the file. The migration never runs a
table rewrite automatically.

Raw source JSON snapshots, graph evidence, documents, and search projections are
outside this deduplication change. The database will therefore still grow with
each release; only repeated localization text and hashes are shared.

### Measured on the local 1.0 / 1.1 snapshots

Verified on an isolated full database backup restored to PostgreSQL 17:

- 1,678,392 associations became references to 338,764 unique exact texts.
- A checksum including ordered release/key/locale, text, status, redirect,
  source-record ID, and timestamp matched before and after migration.
- The original localization-value table and indexes occupied 352,403,456 bytes.
- After a separately tested `VACUUM FULL`, associations occupied 237,920,256 bytes
  and the dictionary 88,244,224 bytes: 326,164,480 bytes combined, about 7.4% less.
- Before reclamation the association table temporarily reached 682 MiB, plus the
  84 MiB dictionary. Budget for this conversion peak and WAL, not just the result.
- Repeating an existing text in another snapshot now adds only its association;
  the dictionary text/hash is reused. Fivefold fewer text copies does not imply
  fivefold less database space: association rows and their primary index remain.

API smoke checks passed for an item profile, a quest transcript, filtered dialogue
search, and the catalog. The active development database was not migrated during
this verification; apply the maintenance procedure after deploying matching code.

## Migration caveat

The initial revision currently creates tables from SQLAlchemy metadata at migration execution time. This is convenient during foundation development but is not a frozen migration contract: before a production deployment, replace it with a generated/static Alembic revision and validate it against an empty PostgreSQL 17 database. The migration includes idempotent seed inserts only in the currently single-install path; repeated seed conflict handling should be hardened before repeated fresh schema provisioning.
