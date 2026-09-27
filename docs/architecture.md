# Architecture

The service is split into API, persistence, ingestion, graph operations, search primitives, and storage adapters. PostgreSQL is the source of truth for metadata and canonical records. Large immutable objects use an interchangeable local or S3-compatible storage backend; a game-source path is provenance and is never treated as an object-store key.

```mermaid
flowchart LR
  Compiler[Deterministic narrative compiler output] --> Importer[Python importer]
  Importer --> Raw[(raw schema)]
  Importer --> I18n[(i18n schema)]
  Importer --> Graph[(graph schema)]
  Importer --> Core[(core and story schemas)]
  API[FastAPI] --> Repositories[Repositories and services]
  Repositories --> PG[(PostgreSQL 17 + pgvector)]
  Repositories --> ObjectService[Storage service]
  ObjectService --> Local[Local filesystem]
  ObjectService --> S3[S3-compatible object store / MinIO]
```

```mermaid
flowchart TB
  subgraph ops[ops]
    Release[game_release]
    Run[import_run / processing_run]
    Processor[processor / ai_model]
  end
  subgraph sources[raw and i18n]
    SourceFile[source_file]
    SourceRecord[source_record]
    LKey[localization_key]
    LValue[localization_value]
  end
  subgraph canonical[graph and core]
    Node[graph.node]
    Revision[node_revision]
    Edge[graph.edge]
    Evidence[edge_evidence]
    Entity[quest / dialogue / speaker / character / item / location ...]
  end
  Release --> SourceFile --> SourceRecord
  Release --> Node
  Node --> Revision
  Node --> Edge
  Edge --> Evidence
  Node --> Entity
  SourceRecord -. source identity .-> Evidence
  LKey --> LValue
  Entity -. references .-> LKey
  Release --> Run --> Processor
```

```mermaid
flowchart LR
  Raw[Immutable source row] -->|explicitly normalized| Node[Canonical node and revision]
  Node -->|source-backed edge| Edge[Directed graph edge]
  Edge --> Evidence[Field path + source record + basis]
  Node --> I18n[Localization key identity]
  I18n --> Value[Locale value and resolution state]
  Node -. later, out of scope .-> Claims[Semantic claims/events]
```

The schema is divided into `ops`, `storage`, `raw`, `i18n`, `graph`, `core`, `ontology`, `story`, `content`, and `search`. `graph.edge.basis` distinguishes explicit references, exact joins, authored ordering, source-array adjacency, runtime conditions, and later semantic/manual relations. No authored order is asserted as actual player traversal.

Semantic claims and event extraction are represented as persistence contracts only; the importer does not populate them. Embedding tables are model-versioned and no vector index is required until an embedding model is selected.
