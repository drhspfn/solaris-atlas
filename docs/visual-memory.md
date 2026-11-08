# Visual Memory — first slice

Visual Memory is available in **Console → Visual Memory** (`/console/visual-memory`).
It uses the existing database, authentication, content-addressed object storage and agent tooling.
It does not replace the character/NPC graph or change existing imports.

## Run locally

From `packages/server`, install `uv sync --extra visual-memory`, then run
`uv run alembic upgrade head`. Migrations `0030`–`0032` affect only the new visual tables;
rollback removes these visual-memory records, not game entities or media.

Set these environment variables before starting the API:

```text
VISUAL_MEMORY_DINO_PATH=/models/dinov2-small
VISUAL_MEMORY_DETECTOR_PATH=/models/anime-character-detector/character.onnx
```

The first directory must contain `config.json`, `preprocessor_config.json` and either
`model.safetensors` or `pytorch_model.bin` for `facebook/dinov2-small`. The ONNX model
is the upstream [anime-character-detector YOLOX model](https://github.com/ksasao/anime-character-detector).
The paths are local, mounted read-only in a container; model files are not downloaded
at request time. Use the same storage/database configuration as the API.

```sh
uv run visual-memory status
uv run visual-memory smoke /path/to/known-wuwa-image.png
uv run uvicorn wuwa_story.api.app:app --host 127.0.0.1 --port 8000
```

`status` checks configuration/dependencies without loading weights; `smoke` actually
loads both models and returns their result. An absent or incompatible model returns
an explicit unavailable result/HTTP 503, never a colour-histogram substitute.
Pillow is a base dependency; CPU inference dependencies are an optional extra.
Inference is lazy, serialised and uses one CPU thread per engine.

Production installation requires rebuilding the API image with the extra and mounting
the model files, then applying the migrations before enabling this console workflow.
No production deployment or model download is performed by this feature itself.

## Working with the library

1. Create a visual identity and optionally link existing game node IDs. A custom kind
   such as `landmark` needs no game character record.
2. Create another identity for a genuinely different visual/game form. Yangyang and
   Yangyang: Xuanling remain separate identities; an explicit lore group can relate them.
   An outfit is an appearance belonging to one identity, not an automatic lore merge.
3. Upload an image in Laboratory. Detect objects, adjust or draw bounding boxes, then
   identify the crops. Coordinate inputs provide an alternative to pointer drawing.
4. Inspect candidates and their reference images. Similarity scores are **not probabilities**.
   An unknown/unresolved result is valid; create an identity explicitly if appropriate.
5. Link one or more identities/appearances and save the crop. Pending is the default;
   only an explicit administrator decision approves it for search.
6. In Library, inspect provenance, model identity and review history. Rejection and
   disabling search immediately remove a reference from retrieval. Relabelling revokes
   approval until reviewed again. Re-uploading the same crop preserves prior moderation.

Query uploads are kept only for request processing; the browser retains its working
image until changed/closed. Chosen image references save the original and crop using
the existing R2 file registration/deduplication path. A `vector_only` import stores no
image bytes. Switching an existing reference to vector-only unlinks its images, preserving
embeddings and source metadata. It does not physically purge shared R2 objects: other
game imports may still use those same content-addressed files.

## Storage and retrieval

`core.visual_identity`, `visual_identity_node`, `visual_lore_identity` and
`visual_appearance` model identity separately from imported graph nodes.
`core.visual_reference` and `visual_reference_link` store crops and multiple labels;
`search.visual_embedding` reuses `search.embedding_model`. `ops.visual_review` retains
moderation/relabel/reindex history. SHA-256 deduplicates exact crops and pHash helps
prevent duplicate images contributing repeated evidence.

DINOv2 CLS vectors have 384 dimensions and are L2-normalized. A composite model/dimension
index bounds the eligible rows, and PostgreSQL validates vector dimensions. The model key fingerprints
weights, configuration, preprocessing and relevant library versions. A new fingerprint
requires explicit reindexing; vectors from different fingerprints are never compared.
Old vectors remain retained. Vector-only references cannot be reindexed without an image.

This slice uses **balanced exact cosine retrieval** in PostgreSQL/pgvector, rather than
an ANN image top-100 that can erase rare identities before grouping. SQL selects the best
reference per source image and at most five independent sources per identity. Final
ranking uses best similarity, mean similarity and consistency, with a reference-quality
penalty; image counts are never summed. This deliberately scans the active model's
approved references. Benchmark before enabling a very large library; ANN acceleration
must retain a balanced fallback/prototype search and must not restore the raw top-10 bias.
The unknown/margin thresholds are conservative **uncalibrated heuristics**, not measured
recognition accuracy or permission to auto-label.

## API, code and agent tools

Reusable service: `wuwa_story.visual_memory.service.VisualMemoryService`.
The caller supplies an async SQLAlchemy session, may inject storage/inference, and owns
the transaction. Search is read-only, including when the current model has no library.
No API accepts a local file path or downloads arbitrary remote image URLs.

Administrator endpoints live under `/admin/visual-memory`:

| Area | Routes |
| --- | --- |
| Model checks | `GET /models` |
| Visual/game identities | `GET/POST /identities`, `PATCH /identities/{id}` |
| Lore and appearances | `GET/POST /lore-identities`, `GET/POST /identities/{id}/appearances` |
| References | `GET/POST /references`, `GET /references/{id}`, `GET /references/{id}/image` |
| Curation | `POST /references/{id}/links`, `/review`, `/remove`; `GET /references/{id}/history` |
| Reindex | `POST /references/reindex` with up to 20 explicit IDs |
| Recognition | `POST /search`, `POST /identify` |

Write/recognition requests require the existing CSRF token and administrator session.
Images use `image_base64` JSON, at most 5 MiB decoded, at most 20 boxes and 20 million
pixels. Bounding boxes are `[left, top, right, bottom]` in EXIF-oriented image pixels.

Public MCP tools: `visual_search`, `visual_identify`, `visual_list_identities`,
`visual_get_reference`. Reference lookup exposes approved, search-enabled references only.
The public transport never advertises curation tools. The native chat read-tool registry
uses the same adapters. Privileged adapters separate write capability from moderation;
AI-created references must remain pending until human review. Image base64 is redacted
from agent tool audit arguments.
The internal agent advertises `visual_create_identity` and `visual_add_reference` for
explicit new identities and pending observations; these are not public MCP tools.

## Small bootstrap

After explicitly linking game nodes to visual identities:

```sh
uv run visual-memory bootstrap 15499 15504
```

This command takes 1–20 explicit IDs, selects one existing image per node from configured
storage and creates pending references. Missing/ambiguous identities are reported, not
guessed. It makes no web search or remote download and does not automatically approve.
Review actual imported images: existing source artwork may itself contain a wrong label.

## Validation

Run the `test_visual_memory_*` and `test_admin_visual_memory.py` tests plus
`node --test tests/test_visual_memory_ui.mjs` from `packages/server`.
Set `WUWA_VISUAL_TEST_DATABASE_URL` to a disposable, fully migrated PostgreSQL database
to enable real storage/vector integration tests. Those tests use rollback/local object
storage and never contact production or external inference services.

Validated on 2026-11-08: 77 targeted Python tests and six JavaScript tests passed,
including PostgreSQL/pgvector retrieval, concurrent duplicate writes, read-only searches,
the 500-popular-references/rare-NPC case, permissions and migration up/down/up.
The desktop/mobile browser flow used real API/authentication/storage/database with a
deterministic inference fixture. Separately, real local DINOv2/YOLOX weights passed
the persisted-reference → moderation → search/identify smoke on an existing WuWa image;
the exact same crop matched at similarity 1.0. This does not measure recognition accuracy.

The first slice does not implement video tracking, visual quest analysis, fine-tuning,
automatic scraping or an end-user photo-search screen. Their code can reuse the service
and curated references without defining another identity or embedding store.
