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

1. Create a visual identity and optionally link existing game node IDs. A selected kind
   such as `location` needs no game character record.
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
No API accepts a local file path. Query tools can temporarily download public HTTPS
images through the guarded resolver described below; they never persist queries.

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
Images accept the source forms below, at most 5 MiB decoded, at most 20 boxes and 20 million
pixels. Bounding boxes are `[left, top, right, bottom]` in EXIF-oriented image pixels.

### Query image sources (MCP and administrator API)

`visual_search` and `visual_identify` accept exactly one source. Existing clients may
continue sending `{"image_base64":"..."}`. New clients can use:

```json
{"file":{"download_url":"https://example.org/temporary-image.png","file_id":"file_...","mime_type":"image/png","file_name":"attachment.png"},"include_images":true}
```

Both MCP descriptors declare `_meta["openai/fileParams"]: ["file"]` following
the [OpenAI File APIs contract](https://developers.openai.com/plugins/reference#file-apis).
ChatGPT can supply this top-level file object for an authorized attachment.
`download_url` and `file_id` are required; `mime_type` and `file_name` are optional
strings. All four fields are declared in the file schema. The top-level `file`
is an inline `type: object` in MCP `tools/list`, without `$ref` or a nullable union,
to avoid requiring clients to resolve definitions when discovering file params. It
is optional to keep the other source forms available, but cannot be combined
with `image` or `image_base64` in one request. Metadata does not bypass actual
image decoding. Temporary URLs use the same guarded HTTPS downloader below and
are redacted from audits/traces; no file is persisted or approved by searching.

```json
{"image":{"url":"https://example.org/character.png"},"include_images":true}
```

```json
{"image":{"asset_id":15499},"limit":5}
```

```json
{"image":{"reference_id":1},"detect":false}
```

```json
{"image":{"base64":"..."},"boxes":[[0,0,100,100]]}
```

The last two examples are `visual_identify` inputs (`visual_search` uses singular
`bbox`). `asset_id` is a **numeric graph node ID owning published `entity_image`
artwork**, not a storage file ID. Its newest available published image is used;
use a URL for a specific artwork variant. `reference_id` selects the retained crop
of an approved, search-enabled visual reference. Pending, rejected, disabled and
vector-only references cannot be used as query images through public MCP.

URLs must return a JPEG, PNG, WebP, GIF or BMP image over HTTPS port 443, without
credentials. HTML pages, local addresses, private/mixed DNS answers, local paths
and arbitrary MCP resource URIs are rejected. DNS is checked at each redirect,
and the connection is pinned to the checked IP while preserving TLS hostname
verification. Downloads allow up to three redirects, 20 seconds total and 5 MiB
of response bytes. HTTP content compression is rejected to prevent decompression
bombs. Image dimensions are checked before inference.
Query images are not ingested or approved automatically.

MCP callers may set `include_images: true` on either query tool or
`visual_get_reference`. Alongside JSON, MCP returns up to three labelled JPEG
reference thumbnails (maximum 512×512), rechecking their approval and visibility.
Administrator JSON endpoints still return ordinary JSON and existing preview URLs.

File forwarding depends on the host: reconnect/rescan the MCP tools after a schema
update and test an attached PNG through `visual_identify`. A local path or bare
`file_id` is insufficient; the server needs the temporary `download_url` delivered
by the client. An expired URL needs a fresh file parameter from ChatGPT. The server
cannot obtain an arbitrary user's attachment from a file ID alone. For clients
without file-param support, use image bytes, an HTTPS image URL, or an archive ID.
There is no anonymous upload/ingest tool and no external image-search scraper.

Public MCP tools: `visual_search`, `visual_identify`, `visual_list_identities`,
`visual_get_reference`. Reference lookup exposes approved, search-enabled references only.
The public transport never advertises curation tools. The native chat read-tool registry
uses the same adapters. Privileged adapters separate write capability from moderation;
AI-created references must remain pending until human review. Image base64 is redacted
from agent tool audit arguments.
The internal agent advertises `visual_create_identity` and `visual_add_reference` for
explicit new identities and pending observations; these are not public MCP tools.

## Small bootstrap

Identity kinds are a shared server enum: `playable`, `story`, `npc`, `technical`,
`character`, `location`, `item`, `creature`, `object`, `other`. Admin creation and
filter controls use the same choices. Creation/update APIs, agent tools and direct
service calls reject arbitrary kind strings; use `other` for unclassified subjects.
The existing text database column is retained; no database migration is required.

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

## Cutscene review and video tools

The Cutscenes tab in `/console/visual-memory` uses published media sources only.
Choose the cutscene and its variant, then scan at a fixed interval (default three
seconds). Sampling covers the entire duration, including the final frame: there
is no global frame-count or video-byte cap. Batch size bounds each review page,
not coverage. Before downloading, the worker checks registered size and temporary
disk space. Downloads are streamed and verified against the registered SHA-256.

`ops.visual_video_frame` checkpoints each frame and its detection proposals.
A stopped worker resumes the same scan from persisted frames. Source hash, model
fingerprints and sampling interval identify a scan; repeated requests reuse it.
Each proposal can be approved, retained pending, or rejected by an administrator.
A source can be marked reviewed only after every proposal has a decision.
Frames from one video count as one independent retrieval source.

The identity picker searches existing visual identities and the game character/NPC
catalog by name. Selecting a game record explicitly creates or reuses its visual
identity. Manual IDs remain optional. Appearances and lore groupings remain separate.

`VideoReviewService` is reusable by application code. Admin endpoints under
`/admin/visual-memory/videos` provide source selection, scan creation/status,
paginated frames and moderation. Public tools `visual_inspect_cutscene` and
`visual_get_video_scan` inspect published sources and human-reviewed frames;
MCP supplies native JPEG image content alongside the structured response.
Internal agents may enqueue through `visual_scan_cutscene` and inspect pending
proposals through `visual_read_video_scan`; they cannot approve their own labels.

## Production video worker

The API image installs the `visual-memory` extra with CPU-only PyTorch on Linux,
plus FFmpeg. Place model files beneath `models/visual-memory` beside production
Compose: DINOv2 config/preprocessor/model.safetensors in `dinov2-small`, and
`anime-character-detector/character.onnx`. This directory is mounted read-only.
The model identity includes the actual weight/configuration/library fingerprints.

Apply database migrations through 0033 using a direct PostgreSQL connection.
Start only the `visual-memory-worker` service with the `visual-memory` Compose
profile. Pin `VISUAL_MEMORY_IMAGE_TAG` to the deployed immutable image SHA and
persist it in production configuration. The single CPU scanner uses a direct
PostgreSQL connection for its advisory lease, 2 GiB RAM and two DB connections.
Existing audio/video/import workers need no restart. App-only deployments preserve
this scanner's captured container identity, like the other media workers.

Validation additionally covers real FFmpeg extraction, full-duration sampling,
checkpoint recovery, paginated review, publication/variant fencing and game-name
lookup. Recognition proposals remain untrusted until human review; these tests
establish pipeline correctness rather than recognition accuracy.

## Rover and manual video annotation

The visual picker resolves the source-backed Rover family as one `Rover` identity.
Male and female appearances have separate reference labels; elements are linked
source game records, not separate visual identities. Existing approved reference
labels are preserved and new elements join through the imported family metadata.
The picker never merges unrelated people merely because their names match.

Each completed, unreviewed video frame has an Edit boxes action using the same
annotation component as the image laboratory. Draw a new box, resize an unresolved
box or change its coordinates. Removing an automatic proposal rejects it; saved
or approved references remain unchanged. Manual boxes get similarity suggestions
without another detector pass, then require explicit identity selection and review.
A detector proposal is not proof that the object is a character: non-character
false positives should be rejected rather than labelled as the nearest candidate.

Video scans support `sampling_mode=full` (default), `segment` with
`start_seconds`/`end_seconds`, or `frame` with `timestamp_seconds` and an optional
zero-based `frame_offset`. For example, frame time 91 seconds plus offset 10 selects
the eleventh decoded frame at or after 1:31. The exported frame records its actual
source presentation timestamp; the offset is not converted using an assumed FPS.
Segment jobs process only their selected time window. Full jobs retain their previous
fingerprint and cover the whole recording. These modes have distinct persisted scan
identities, so a short segment never marks the entire source as reviewed.
`MAX_FRAME_PAGE_SIZE` bounds one response page only and does not limit scan length.
