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

## Assertion chronology and evidence

New jobs use `story-v3`. Each explanation block contains atomic `assertions`,
with exact citations and a status: `confirmed`, `observed_anomaly`, `inferred`,
or `unresolved`. An observation is not proof of its apparent cause. For example,
dry clothing can be observed while apparent drowning remains an inference.

Each assertion records two independent time axes:

- `chronology_in_quest`: authored encounter order, a cited passage anchor and a
  readable label. The server checks the order against the imported transcript.
  It does not establish a single playthrough through mutually exclusive branches.
- `world_chronology`: before/during/after this quest or unknown, with an
  explanation. Unknown dates must remain unknown.
- `knowledge_state`: what is established at that encounter, without hindsight.
- `later_resolution`: separately cited resolved/partial/contradicted revelations,
  each anchored to a read source. A later passage in the same quest must follow
  the original encounter; sources from other quests must have been read too.

Later revelations are hidden behind an explicit spoiler disclosure on the site.
Publishing a new analysis creates an immutable document revision and moves its
head; previous knowledge and open clues remain in earlier revisions. Sources are
limited to the selected imported snapshot, so missing future content cannot be
treated as an established explanation. No automatic cross-version research is
performed.

`related_records` adds human-readable contextual labels beside record links.
Graph edges show readable endpoints, a relation label, an explanation, confidence
and citations. They remain agent interpretations: only existing discovered nodes
and allowed ontology relations can be used, not arbitrary new relations or nodes.

Legacy analyses and checkpoints remain readable/resumable under their original
contract. To obtain chronology for an existing analysis, enqueue a new job; the
new prompt version changes its identity. This does not launch paid work by itself.
There is no schema migration: assertion structures live in document JSON and
their searchable text includes knowledge and later explanations.

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
usage. Cache reads and writes use their configured rates, and input tokens outside
those groups use the standard input rate. Set `AGENT_CACHED_INPUT_USD_PER_MILLION`
and `AGENT_CACHE_WRITE_USD_PER_MILLION` for the selected provider/model; unset rates
retain the standard input estimate. Before a response is available, reserves use
the highest configured input rate, covering a cache miss/write.
The ledger counts settled usage and outstanding reservations. A second
token ceiling is optional: `AGENT_DAILY_TOKEN_LIMIT=0` disables it (the default),
while positive values enforce an independent guard including cached input.
The USD limit always remains enforced. Application estimates depend on
correct configured rates and provider limits; configure the provider's external
billing controls as well.

Each research request reserves its own serialized byte bound plus output allowance,
rather than the maximum permitted input context. Responses jobs verify a tight
budget or oversized window with `/responses/input_tokens` before any paid request.
Invalid counts do not authorize a request. Embeddings without reliable usage retain
the full context reservation. Compaction retains its configured output reserve.

To correct older recorded estimates, first back up the operational tables. Run
`uv run python scripts/reprice_agent_usage.py --run-id ID --cached-rate 0.01
--write-rate 0.125` from `packages/server` for a dry run, then add `--apply` after
checking the model's actual rates. Repeat `--run-id` for multiple jobs. The command
locks inactive Responses jobs, pins the explicit cache rates, adjusts only completed
call costs and applies matching deltas to their original daily ledgers and run totals.
It preserves the previous cost in private call metadata and is idempotent. Unknown,
reconciled or excessive charges and all outstanding reservations remain untouched.
No schema migration is needed. For rollback, restore the backed-up accounting and
pinned configurations while the consumer is stopped; do not erase unknown charges.

Budget reads refresh locked rows even in long-lived worker sessions, so parallel
settlements and maintenance cannot be overwritten by stale in-memory balances.
If older workers already caused ledger drift, add `--repair-ledger` to the same
maintenance command. It rebuilds daily totals from recorded calls under budget locks,
including all unsettled reservations; a dry run rolls back all changes. This option
can run without `--run-id` when no repricing is needed.

The worker commits call intent before HTTP, then commits the returned response
and usage before processing tools. A crash can replay that saved response without
paying again. Advisory locks prevent duplicate consumers from processing one run
concurrently. Queue publication uses confirmations; a failed confirmation is
recoverable by submitting the same request. A lost confirmation can already have
delivered a message, so no new run is created for the repeat.

Timeouts, invalid/missing usage and ambiguous outcomes retain their full reserve.
No automatic retry is performed for those uncertain outcomes. Temporary HTTP 429
errors with `rate_limit_exceeded` or `slow_down` are different: the worker records
the safe error code, numeric rate-limit headers and the next retry time, retaining
one reservation for the current step. It retries at most three times, honoring
`Retry-After` (seconds or HTTP date), then reset headers, or exponential backoff
with jitter. Defaults cap the total waiting time at 180 seconds; a longer server
delay defers the job instead of retrying early. Change these bounds through
`AGENT_RATE_LIMIT_RETRIES`, `AGENT_RATE_LIMIT_BACKOFF_SECONDS` and
`AGENT_RATE_LIMIT_WAIT_SECONDS`.

Quota/spend-limit and unclassified 429 responses pause as `paused_provider` without
automatic retry. Temporary throttling that exhausts the retry allowance pauses as
`paused_rate_limit`; resume retains its cooldown and original reservation. Provider
error messages, credentials and arbitrary headers are never logged or returned.
This follows [OpenAI's rate-limit guidance](https://developers.openai.com/api/docs/guides/rate-limits).
Model-native reasoning items are kept for
continuation in private operational state; neither public nor admin endpoints
expose raw provider responses, conversation checkpoints or API keys.

## Operations API

### Admin panel

Open `/admin` from the account menu. The shared sidebar groups Story agent under
Content and Alerts / Usage under Operations. Access requires the existing admin
role; promote a verified account with the server's `wuwa-story-admin promote EMAIL`
CLI, then refresh the browser. The role is checked in the database for each API
request; no frontend credential or role override is introduced.

For `paused_steps`, select the saved run, enter Additional steps and click Resume
analysis. For example, adding 16 to a 16-step run permits 32 total research steps.
The backend rejects a resume without a positive extension and caps research at
100 steps. Completed calls remain recorded and the daily allowance is unchanged.
The panel also supports creating quest jobs, explicit Responses compaction resume,
request history, open alerts and the recent daily ledger. Uncertain billing is
shown without a resume action; verified reconciliation remains an operations API
procedure. Jobs refresh every ten seconds while the page is visible.

Browser scenarios use `packages/web/tests/storyAgent.fixture.mjs` on port 8012,
with a dev frontend on 5174 and `VITE_API_BASE=http://localhost:8012`. This isolated
fixture has no game database, queue or provider access. It covers resume, CSRF,
duplicate job creation, alerts, usage, API failures and access states without
spending money. It must not be used as a deployed API.

All administrative routes require administrator authentication. Mutations also
require the existing session CSRF header/cookie mechanism.

| Route under `/admin/story-agent` | Purpose |
| --- | --- |
| `POST /jobs` | Queue `{quest_id, game_version, locale, generation?}` |
| `GET /jobs` | Cursor-paginated runs and usage |
| `GET /jobs/{id}` | Safe status, steps, call IDs, costs and final result |
| `POST /jobs/{id}/resume` | Resume a pause or replay a validated recorded turn; optional `{extra_steps, context_tokens, tool_calls_per_step, compact_context}` |
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
Context pauses can request `{ "compact_context": true }` for the Responses provider,
or an explicitly larger `context_tokens` value, up to 250,000.
This changes the conservative reservation bound and retains previous tool results;
it does not raise the daily USD/token allowance or repeat completed calls. The
environment examples use this larger bound; the code fallback remains 65,536.

Responses jobs automatically compact at 80% of the conservative serialized-request
bound (`AGENT_CONTEXT_COMPACTION`, `AGENT_COMPACTION_THRESHOLD_RATIO`). The native
`/responses/compact` call receives the full history without pruning opaque reasoning.
Its complete output becomes the next conversation window, as required by the
[OpenAI compaction guide](https://developers.openai.com/api/docs/guides/compaction).
Exact source evidence, discovered nodes and quest coverage stay in the independent
checkpoint and remain subject to publication validation. Compaction is a paid,
durably recorded call (step `2000 + research_step`), using the same daily budget and
bounded throttling retries. A crash replays its stored response without paying again.
The endpoint has no output-length parameter: `AGENT_COMPACTION_OUTPUT_TOKENS=32000`
is a conservative billing reservation, not a provider limit. Excess usage pauses
for review. If serialized history exceeds the bound, the worker verifies its exact
input-token count through `/responses/input_tokens` before reserving a paid compaction
call. This handles multilingual text and opaque reasoning without raising the cap;
invalid/missing counts or counting errors stop safely. If the full history still
cannot fit the input bound, or the returned window
still cannot fit a research request, the job pauses instead of repeatedly compacting
the same step. Chat/Gemini jobs retain the existing context-pause behavior.

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
  patch and a patch overview job are subsequent delivery work.
- Defaults allow 16 tool turns, 65,536 conservatively bounded input tokens and
  4,096 output tokens. Responses jobs use native context compaction; very long
  quests can still pause on step/context limits. Hierarchical splitting across
  jobs is not implemented. No incomplete
  explanation is presented as a finished publication.
- Quotes and node identities are validated, but an inference's meaning still needs
  editorial evaluation. One real GPT-6 Luna quest analysis has been checked for
  publication and source navigation; this is not a corpus-wide quality evaluation.
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

### Throttling recovery and native compaction, 2026-10-04

- Run **12** resumed its existing `story-v2` checkpoint. Recorded 429 responses
  identified the token limit as **200,000 TPM**; bounded waits of 6.9 and 14.2
  seconds recovered successfully. No completed paid research call was repeated.
- At research step 20, the conservative request bound reached **256,767 bytes**.
  The exact input counter reported **67,110 tokens**, within the unchanged
  250,000-token reservation bound. GPT-6 Luna `/responses/compact` succeeded:
  **67,128 input / 2,983 output tokens**, estimated cost **$0.01025538**.
  The next research request shrank to **30,360 bytes**; independent source
  evidence and coverage were retained.
- The job completed at checkpoint step **29**, publishing document **1**:
  **6 explanation blocks, 24 citations, 3 unresolved questions**. Total estimated
  spend for run 12 is **$0.09216682**, including compaction and the configured
  price multiplier. Its daily ledger has no outstanding reservation. Run 11's
  earlier unresolved reservation remains untouched on its original budget day.
- Verified the English quest page shows the published notes; followed a citation
  to its exact imported dialogue and matching transcript passage. A Japanese
  explanation request returns the same document with `requested_locale=ja`.
- **36 targeted agent tests passed** in `solaris_agent_test`, including native
  window preservation, paid-response replay after a crash, explicit compaction
  resume, valid exact counts and refusal of invalid/over-limit counts. Ruff and
  strict mypy passed. API and story-agent images were rebuilt and restarted.

### Admin panel, 2026-10-04

- Added `/admin` with a shared sidebar, run management, alerts and usage. Step
  resume requires a positive extension; progress polling reads only the stored
  step/configuration rather than loading private conversation checkpoints.
- **46 agent tests** and **3 frontend resume-policy tests** passed. Changed
  frontend files passed ESLint and Prettier; TypeScript, the production build,
  Ruff and strict mypy passed. The full frontend lint command still fails on the
  pre-existing import-sort error in `EntityProfilePage.tsx`. The UI audit finds
  seven existing form-ownership violations in other pages, none in the new panel.
- Isolated browser checks covered saved-step resume with CSRF, duplicate request
  reuse, invalid quest feedback, language selection, billing-review protection,
  alerts/usage, connection failure, ordinary-account denial and a 390px viewport
  without horizontal overflow. The working deployment's guest access gate and
  HTTP 401 admin API response were checked separately.
- Built and restarted API, frontend and story consumer. No schema migration was
  needed. Working run 14 remains `paused_steps`; browser mutations used fixtures
  and did not enqueue paid work in the working database.

### Cache-aware budget recovery, 2026-10-04

- Kept the $1 daily USD cap and disabled the independent token guard locally.
  Reserves now use each request's bound; tight/oversized Responses inputs use
  the counter's supported input parameters. A real counter request returned 502
  tokens without generating a model response.
- Backed up operational tables before repricing completed calls in runs 11–14
  from recorded cache reads/writes. Refreshed locked daily balances to prevent
  stale worker sessions overwriting parallel changes. Rebuilt daily totals from
  calls: $0.10513408 for 2026-10-04, including the safety multiplier. SQL verification
  found zero differences between daily spend/reservations and recorded call sums.
  Run 11's unresolved $0.03381000 hold remains on 2026-10-03.
- Run 14 completed at step 56 and published its analysis. Its corrected estimate
  is $0.05709574. No completed research calls were replayed for repricing.
- Full server suite: 135 passed, 2 skipped (Redis infrastructure), with existing
  dependency deprecation warnings. Ruff and strict mypy passed for changed agent
  modules. Frontend resume-policy tests, changed-file ESLint/Prettier, TypeScript
  and production build passed. Browser fixtures verified optional token-cap text
  in both admin pages. Rebuilt/restarted API, frontend and the story consumer.

### Assertion chronology, 2026-10-04

- Added the `story-v3` assertion contract, validated encounter anchors/order,
  separately sourced later revelations and readable graph/record labels.
  Publication retains previous document revisions. No schema migration needed.
- Full server suite: 140 passed, 2 skipped (Redis infrastructure); existing
  dependency deprecation warnings remain. Ruff and strict mypy passed for agent
  modules. Two frontend rendering regression tests passed, as did changed-file
  ESLint/Prettier, TypeScript and the production build.
- Browser checks used explicitly illustrative fixture data, not published story
  facts: separate time axes, readable related records and graph paths, exact
  source disclosure, keyboard spoiler toggle, 390px layout without horizontal
  overflow, missing analysis, API failure and successful retry. No paid analysis
  was launched. The broader static design audit reports 16 existing findings in
  other forms and the vendored launcher, none in the changed components.
