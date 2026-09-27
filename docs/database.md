# Database schema

PostgreSQL 17 with `vector`, `pg_trgm`, and `pgcrypto`. Alembic owns schema lifecycle. The initial revision creates ten domain schemas and lookup seeds. SQLAlchemy models are grouped under `src/wuwa_story/db/models/`.

| Schema | Purpose | Main tables |
| --- | --- | --- |
| `ops` | Release and processing lifecycle | `game_release`, `import_run`, `processor`, `processing_run`, `ai_model`, `dependency` |
| `storage` | Content-addressed files and their locations | `file_object`, `file_location`, `file_variant`, `file_reference`, `file_type` |
| `raw` | Immutable source table/record snapshots | `source_file`, `source_record` |
| `i18n` | Locale-independent text identity and values | `locale`, `localization_key`, `localization_value` |
| `graph` | Versioned canonical nodes and source-backed edges | `node_type`, `node`, `node_revision`, `edge`, `edge_evidence` |
| `core` | Typed game and platform entities | quests, quest nodes/states/actions, dialogue, choice, narration, speaker, character, NPC, item, location, cutscene, audio and conversation entities |
| `ontology` | Controlled types, tags and candidate mappings | event/relation types, tags, aliases and candidates |
| `story` | Events, claims, scenes and groupings | `scene`, `event`, `claim`, evidence and memberships |
| `content` | Documents and explicit references | `document`, `document_head`, `document_reference` |
| `search` | Search projections and optional embeddings | search docs, aliases, chunks and model-keyed embedding tables |

Identity rule: source identifiers are preserved in `node.source_identity` and typed fields. Speaker IDs do not imply character IDs. Dialogue rows reference localization identities instead of copying a chosen-language string. Locale text includes empty content and a resolution state. Claims and semantic events are not inferred by this foundation.

## Migration caveat

The initial revision currently creates tables from SQLAlchemy metadata at migration execution time. This is convenient during foundation development but is not a frozen migration contract: before a production deployment, replace it with a generated/static Alembic revision and validate it against an empty PostgreSQL 17 database. The migration includes idempotent seed inserts only in the currently single-install path; repeated seed conflict handling should be hardened before repeated fresh schema provisioning.
