# Story research pipeline

The worker researches one imported quest in a selected release and locale. It can
read transcripts, inspect the source graph, search entities, re-read sources, save
working notes and report missing data. Publication creates a cited explanation,
inferred claims/links and generated events. Imported source edges are preserved.

## Setup

1. Back up the working database, then apply the new `0009_story_agent` migration
   from `packages/server`: `uv run alembic upgrade head`. The migration adds five
   small tables and a cache revision trigger; it does not rewrite imported text.
2. Set the agent values in **both** `packages/server/.env` and
   `packages/worker/.env`. Examples use `responses`, `gpt-6-luna` and a shared
   **$1/day** cap. Add `AGENT_API_KEY` locally, never to a frontend variable.
   Restart API and worker after changing environment values.
3. Ensure PostgreSQL/pgvector and RabbitMQ are available. Run the explicit consumer
   from `packages/worker`:

   ```powershell
   uv run wuwa-story-worker run --queue story_agent
   ```

   Alternatively, from the repository root:

   ```powershell
   docker compose -f infrastructure/local/compose.yml --profile story-agent up -d --build story-agent
   ```

   This consumer does not download the game or run asset extraction.
4. Queue a quest using its actual imported snapshot name:

   ```powershell
   uv run wuwa-story-worker enqueue-analysis --quest-id 139000025 --version 1.0.0 --locale en
   ```

   From the repository root with the running Docker consumer:

   ```powershell
   docker compose -f infrastructure/local/compose.yml exec -T story-agent wuwa-story-worker enqueue-analysis --quest-id 139000025 --version 1.0.0
   ```

   `--locale` selects the language of the written explanation (English by
   default), independently of source reading. New jobs read sources by priority
   `en → zh-Hans → ja → zh-Hant → other available locales`, falling back per
   passage when a translation is missing. Tools accept a `locale` argument to
   compare the same node in another language; the prompt requests Chinese and
   Japanese checks for ambiguous or inconsistent translations. This is a model
   instruction, not a guarantee that every poor translation will be detected.
   Citations retain their source language and open that transcript translation.
   The source fingerprint includes all quest translations, so changing any of
   them invalidates the shared analysis. Existing `story-v1` jobs and documents
   retain their original fingerprint rules.

   A published explanation is available across site languages: prefer a matching
   written translation, otherwise show an existing explanation by the same
   priority. Browsing another locale does not launch or charge a new analysis.
   Story search includes these fallback explanations without duplicate quests.

   Repeating the same request reuses its job. `--generation review-2` explicitly
   requests a fresh generation with the same source/configuration.
5. Open the quest page. Published notes appear above its authored transcript.
   Search at `/search?mode=story` retrieves readable explanations and citations.

Development checks used a disposable database and mocked providers. The local
working database was subsequently backed up and upgraded through `0009`; see the
deployment receipt below for the real-provider smoke test.

## Models and embeddings

`AGENT_PROVIDER=responses` uses the OpenAI Responses API. `chat` supports an
OpenAI-compatible Chat Completions endpoint; `gemini` uses its native API. For a
provider change, set its base URL, model, API key, output token parameter where
applicable and **that model's prices**. Do not use the OpenAI example prices for
another provider. Queued jobs pin provider, endpoint, model and prices; a worker
with a different provider/endpoint pauses the job instead of sending its key there.

For vector indexing, configure `AGENT_EMBEDDING_MODEL=text-embedding-3-small`,
`AGENT_EMBEDDING_DIMENSIONS=1536` and its input price in both environments **before
creating jobs**. Each explanation block receives its own vector. Dimensions,
provider, endpoint and model identify an index; changing them requires regeneration.
Vectors do not replace readable documents or citations.

Query embeddings require `AGENT_PUBLIC_QUERY_EMBEDDINGS=true` on the API and
working Redis. Requests are cached for a day, guarded by a Redis lock, limited to
30 uncached attempts/hour/client IP and charged to the same daily ledger. Missing
Redis, exhausted budget or provider failures fall back to lexical retrieval.
Search never generates an LLM answer for an anonymous request. Query vectors are
disabled by default; explanation lookup and lexical question search need no key.

Primary references:

- [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna)
- [Vector embeddings](https://developers.openai.com/api/docs/guides/embeddings)
- [Embedding model and pricing](https://developers.openai.com/api/docs/models/text-embedding-3-small)

## Spending and execution

All instances share `ops.agent_daily_usage`, locked before each reservation.
The budget day follows `AGENT_BUDGET_TIMEZONE`. Environment examples select
Europe/Kyiv; the code fallback is Europe/Moscow. Keep API and worker aligned. USD
uses Decimal with upward rounding, a 25% price margin and provider-reported token
usage. The ledger counts settled usage and outstanding reservations. A second
token ceiling defaults to two million/day. Application estimates depend on
correct configured rates and provider limits; configure the provider's external
billing controls as well.

The worker commits call intent before HTTP, then commits the returned response
and usage before processing tools. A crash can replay that saved response without
paying again. Advisory locks prevent duplicate consumers from processing one run
concurrently. Queue publication uses confirmations; a failed confirmation is
recoverable by submitting the same request. A lost confirmation can already have
delivered a message, so no new run is created for the repeat.

Timeouts, invalid/missing usage and ambiguous outcomes retain their full reserve.
No automatic paid retry is performed. Model-native reasoning items are kept for
continuation in private operational state; neither public nor admin endpoints
expose raw provider responses, conversation checkpoints or API keys.

## Operations API

All administrative routes require administrator authentication. Mutations also
require the existing session CSRF header/cookie mechanism.

| Route under `/admin/story-agent` | Purpose |
| --- | --- |
| `POST /jobs` | Queue `{quest_id, game_version, locale, generation?}` |
| `GET /jobs` | Cursor-paginated runs and usage |
| `GET /jobs/{id}` | Safe status, steps, call IDs, costs and final result |
| `POST /jobs/{id}/resume` | Resume a pause or replay a validated recorded turn; optional `{extra_steps, context_tokens, tool_calls_per_step}` |
| `GET /usage` | Recent shared spend/reservations and current cap |
| `GET /alerts` | Missing data, contradictions and worker failures |
| `POST /alerts/{id}/resolve` | Resolve an investigated alert |
| `POST /calls/{id}/reconcile` | Set verified `{input_tokens, output_tokens}` for an unresolved call |

For an uncertain call, check provider billing first. Reconcile only verified usage;
this closes its charge and marks the run failed because its remote result is
unavailable. An explicit new `generation` is needed to spend again. Never clear a
reservation merely because a request timed out. Resume is intentionally refused
for uncertain or stale runs. Budget pauses can be resumed after the next budget
day or an intentional allowance change; step pauses can add bounded extra steps.
Context pauses require an explicitly larger `context_tokens` value, up to 250,000.
This changes the conservative reservation bound and retains previous tool results;
it does not raise the daily USD/token allowance or repeat completed calls. The
environment examples use this larger bound; the code fallback remains 65,536.

A completed, paid response that failed local tool parsing can be replayed with an
explicit `tool_calls_per_step` limit, up to 20. Resume first validates the stored
response with the selected limits without making an HTTP request. It refuses
unknown, incomplete or still-invalid responses. The example environments allow
20 calls per turn; the code fallback is eight.

Public routes:

- `GET /quests/{quest_id}/explanation?game_version=...&locale=...`
- `GET /story-analysis/search?q=...&game_version=...&locale=...`
- `GET /story-analysis/events/{node_id}?game_version=...&locale=...`

Documents retain their release and written language; source reading and public
fallback are multilingual. Only published heads are returned;
changed quest inputs hide stale explanations. Publication invalidates the existing
public cache through transactional content/graph revisions. Source citations
contain validated exact snippets; source URLs are constructed by the application.

## Current bounds

- The worker processes explicitly queued quests. Autonomous crawling of every
  patch, a patch overview job and an admin dashboard are subsequent delivery work.
- Defaults allow 16 tool turns, 65,536 conservatively bounded input tokens and
  4,096 output tokens. Very long quests can pause on step/context limits; automatic
  hierarchical splitting and summarization are not implemented. No incomplete
  explanation is presented as a finished publication.
- Quotes and node identities are validated, but an inference's meaning still needs
  editorial evaluation. Real GPT-6 Luna output quality has not been evaluated yet.
- Entity search uses the current search index, filtered to node IDs observed in
  the chosen snapshot. Release-localized text and raw source reads are scoped;
  existing typed inline/action records follow the importer's canonical identity
  conventions. The agent does not reconstruct missing historical game assets.
- Vector similarity currently scans the small generated corpus; a model-specific
  ANN index should be added when its measured size/latency requires one.
- Operational responses/checkpoints need a retention policy before a large run.
  They can include generated reasoning and source text and must stay private.

## Rollback

Stop the story consumer and disable public query embeddings first. Existing
source/import/media workers remain independent. `alembic downgrade 0008` removes
the five new operational/index tables and their cache revision entry, so back up
agent state first. Generated documents/claims/events use existing tables and are
not destroyed by this migration; retain their provenance rather than attempting
an unscoped graph deletion.

## Verification

Tests use an isolated PostgreSQL database selected by `WUWA_TEST_DATABASE_URL`.
Mocked HTTP adapters exercise Responses, Chat and Gemini formats, usage and vector
validation. Integration tests cover concurrent budget reservations, unknown-call
replay protection, full-quest citation checks, immutable publication, semantic
links/events, source changes, vector/text retrieval, administrator authentication
and bounded transcript focus. Worker tests cover strict queue payloads and CLI
dispatch. Browser checks use a disposable seeded quest and generated explanation.

The repository-wide UI audit currently reports existing native-select/form
ownership findings outside the new flow. The whole frontend lint gate also has
an existing import-sort error in `EntityProfilePage.tsx`; these are not silently
reformatted as part of the agent feature.

### Local results, 2026-10-03

- Full server suite: **99 passed, 2 skipped** (real Redis integration not
  configured), using the disposable PostgreSQL database. One upstream Starlette
  deprecation warning remains.
- Full worker suite: **102 passed, 2 skipped, 1 existing failure**. The unchanged
  localization compiler emits Windows backslashes while
  `test_redirect_numeric_namespaces_and_all_locales` expects POSIX separators.
  The agent queue/CLI tests passed.
- Agent modules/API: Ruff and strict mypy passed. Changed frontend files: ESLint
  has no errors; existing transcript `any` warnings remain. Prettier, production
  build and all **13 frontend tests** passed.
- Fresh migration, downgrade `0009 → 0008` and upgrade back passed in the
  disposable database. Compose configuration validation passed. Confirmed durable
  RabbitMQ publication/delivery passed through a temporary queue that was removed.
- Browser: seeded quest explanation, generated event, exact citation navigation,
  keyboard disclosure, query results, no matches and request failure/retry were
  checked. At 390px the story search had no horizontal overflow.
  Temporary demo data and API/frontend processes were cleaned up afterward.

### Working deployment, 2026-10-03

- Backed up `wuwa_story` to
  `E:/Backups/solaris-atlas/wuwa-before-story-agent-20261003-232659.dump`
  (301,163,674 bytes); verified the custom-format archive with `pg_restore --list`.
- Upgraded the working schema from `0006` through `0009_story_agent`. Built and
  started API, frontend, snapshot worker, asset worker and the dedicated story
  consumer. PostgreSQL, RabbitMQ, Redis and MinIO are running.
- Both local environments use GPT-6 Luna, the shared $1/day cap, a 250,000 input
  reservation bound and 20 tool calls per turn. Local credentials remain ignored.
  Embeddings remain disabled; lexical story search is available.
- Queued run **11**, quest **139000025**, release **1.0.0**, locale **en**. The
  real provider completed 13 calls. The worker read all **229** transcript lines
  and continued graph research. Context and tool-limit recovery reused saved
  checkpoints/responses without paying for completed requests again.
- OpenAI returned **HTTP 429** on step 13. The run is `paused_uncertain`, with no
  publication: estimated settled spend is **$0.04751428**, and **$0.03381000** is
  retained as an unresolved reservation. The current adapter does not retain the
  provider's error body, so the particular rate/quota limit and billing outcome
  cannot be determined from the stored call. No automatic retry was sent. Check
  provider usage and limits before billing reconciliation or a fresh generation.
- Added context-resume and recorded-turn replay regression tests: **16 targeted
  agent tests passed**, including existing budget/provider tests. Ruff and strict
  mypy passed for the changed service and API files.

### Multilingual research, 2026-10-03

- `story-v2` separates source reading from explanation language. No schema
  migration is needed; old documents remain readable under their original rules.
- **17 targeted agent tests passed**, including priority fallback, Chinese and
  Japanese comparison, shared publication/search, source-language links, rejected
  mismatched-language quotes and invalidation on a Japanese source change.
- Ruff and strict mypy passed for agent modules and API routes; frontend ESLint
  and the production build passed. The worker's two queue/CLI tests passed.
- Updating the consumer does not retry paused run 11. Enqueuing this quest under
  `story-v2` creates a new research job; its paid calls remain subject to the
  existing daily ledger and provider limits.
