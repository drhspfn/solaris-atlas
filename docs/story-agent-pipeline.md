# Story research pipeline

Research and browsing share `db.repositories.quest_scope.quest_state_links`.
The research scope includes direct quest references, quest nodes, plot-step scenes,
and the transcript states of their cutscenes. Every hop must have source evidence
in the pinned release; semantic links and partial paths never establish ownership.
This scope also drives transcript coverage, encounter ordinals, visual inventory
and the input fingerprint. Older analyses made with a narrower scope are stale
and require a new run rather than replaying their incomplete result.
After full reading the agent reconciles early unknowns with subsequent revelations
in the same quest. Encounter knowledge remains local, while the final summary and
knowledge boundary describe what is established by the end of the quest.
After the pre-scan, whole-quest intake requests up to 50 lines per page within
the serialized tool-result bound, including the media inventory. Smaller pages
remain available for pre-scan, translation comparisons and other quests. Traces
retain both requested and executed arguments. Depth budgets reserve reading turns
in addition to research turns, without exceeding the administrator's run ceiling.

## Bounded finalization after the research limit

Quest encounter ordinals are derived from imported dialogue ordering on the server,
using the cited anchor node. `read_node` exposes this ordinal for target dialogue.
Publication normalizes a model-supplied ordinal while rejecting anchors outside the
target quest; exact citations and later-resolution ordering remain mandatory.

After 100 research turns, an administrator can submit `{"finalize": true}` to
`POST /admin/story-agent/jobs/{run_id}/resume`. The existing checkpoint gets one
finalization pass of at most six turns. Only missing transcript/visual pages,
exact source reads and `finish_analysis` are permitted. Further exploration and
depth upgrades are rejected, including unsolicited provider tool calls.
The daily spending cap, citation validation and full source coverage requirements
remain enforced. A failed finalization cannot be extended into another pass.
Budget/context pauses can resume within the original finalization allowance.
No schema migration is required; finalization state lives in the existing checkpoint.

The worker explains one quest transcript from a resolved release and locale. It can
read transcripts, inspect the source graph, search entities, re-read sources, save
working notes and report missing data. Publication creates a cited explanation,
inferred claims/links and generated events. Imported source edges are preserved.

## Readable explanations and connections (V7)

New runs use `story-v7`, so queuing an already analyzed quest can create a new run
instead of returning the completed V6 job. Existing V4–V6 analyses remain readable;
the visual-evidence and source validation rules still apply. Nothing is reanalyzed
automatically on page load.

`blocks[].text` is the reader-facing Markdown narrative. Headings, paragraphs,
emphasis and short lists are supported. Link targets are bounded references to
objects in the same result: `[context](connection:0)`, `[event](event:0)` and
`[source](record:123)`. Connection/event indices are zero-based; record IDs must be
cited or listed as related records in that block. The UI does not render HTML,
external links or remote images. Structured assertions retain chronology,
certainty, branch conditions and source receipts behind closed disclosures.

The public connection URL identifies the published document revision and link
ordinal: `/story-analysis/connections/{document_id}/{index}?locale=en`. Its API
returns a readable description, endpoint records and resolved citations only for
a completed published analysis with still-valid source receipts. Republishing
can refine the description without rewriting old interpretations or source edges.
The existing response cache covers this endpoint and invalidates transactionally.

The admin creation form accepts only a quest ID and always requests English.
When `game_version` is omitted, the server selects the latest imported snapshot
that actually contains the quest, then records that version in the saved request.
Explicit versions remain available for CLI and API callers; the admin API rejects
non-English explanation locales. Source reading remains multilingual.

For `story-v4` and `story-v5`, the resolved game version is the **target transcript snapshot**,
not a research cutoff. Graph neighbors, search and working memory can cover all
imported snapshots. `list_snapshots` exposes the source inventory pinned at enqueue;
`read_quest` and `read_node` accept an explicit `snapshot_id` to compare patches.
Without it, reading prefers the target snapshot when the node exists there,
otherwise the latest available observation. Graph traversal defaults to all pinned
snapshots and can be narrowed to one. Version and import order never establish
world chronology or the order of quests.

Every v4 citation includes its exact snapshot ID and language. Validation requires
the quote to have been read in that snapshot and rechecks it before publication.
Links and source labels retain the source patch, independently of the target
quest version. Older sources can support established background; future reveals
remain separate spoilers rather than rewriting knowledge at the encounter.

New imported snapshots and transactional revisions of imported source tables
participate in job identity, so repeating a request after an import can create a
fresh run. The run's inventory does not expand during research. Public retrieval
checks the inventory and fingerprints of cited sources, including other patches;
changed/deleted source text invalidates the explanation without charging a new
analysis. Full analysis is queued explicitly by an administrator. V5 additionally
schedules focused reviews for flagged hooks after a successful import. Existing
v1–v4 jobs retain their original protocol; start a new job to use v5.

For v5, a growing source inventory no longer hides an otherwise valid explanation.
The page reports its loaded corpus and displays newly cited supplements separately.
Changed original/cited text still invalidates that document.

Entity/text search uses the existing shared lexical index to find candidates,
then exact snapshot reads establish evidence. It is not an exhaustive historical
text index: replaced wording may need an explicit node/quest comparison. Missing
matches do not establish missing lore. The agent can only research patches that
have actually been imported, not every released patch automatically.

## Setup

1. Back up the working database, then apply migrations through `0010_agent_revisit`
   from `packages/server`: `uv run alembic upgrade head`. Migration 0010 adds one
   small outbox table; it does not rewrite imported text. Apply it before starting
   the new API, importer or story worker. Roll back application images first;
   retain this additive table to preserve pending reviews. Downgrading to 0009
   drops only the revisit outbox, so export it first if work must be retained.
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
4. Open `/admin/story-agent`, enter a quest ID and select **Queue analysis**.
   The server selects its latest available transcript; the explanation is English.
   For an explicit historical snapshot, use its actual imported name in the CLI:

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

New jobs use `story-v5`, retaining the assertion structure introduced in v3.
Each explanation block contains atomic `assertions`,
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
head; previous knowledge and open clues remain in earlier revisions. Sources from
other imported patches may establish later context, but missing future content
cannot be treated as an established explanation. The encounter anchor must cite
the target snapshot, regardless of which other patches provide background.

`related_records` adds human-readable contextual labels beside record links.
Graph edges show readable endpoints, a relation label, an explanation, confidence
and citations. They remain agent interpretations: only existing discovered nodes
and allowed ontology relations can be used, not arbitrary new relations or nodes.

Legacy analyses and checkpoints remain readable/resumable under their original
contract. To obtain chronology for an existing analysis, enqueue a new job; the
new prompt version changes its identity. This does not launch paid work by itself.
Assertion structures live in document JSON and
their searchable text includes knowledge and later explanations.

## Adaptive analysis and import reviews (v5)

The first stage only exposes bounded source reads and `assess_quest`. After three
successful pre-scan reads, it must classify before further research. Invalid calls
do not consume the read allowance, but still count against the total step budget.
The full output schema is supplied only after classification. The server stores
the cited assessment, depth policy, coverage and stage in the checkpoint; resume
and compaction retain them. A later upgrade requires new cited reasons and a
strictly larger depth policy. It cannot raise the administrator's overall step
ceiling or the shared daily budget.

| Narrative weight | Normal depth | Maximum visible prose words |
| --- | --- | --- |
| service_repeatable | very_short | 100 |
| tutorial_activity | very_short | 200 |
| side_flavor / worldbuilding | short | 400 |
| side_hook | medium | 800 |
| region_lore | medium | 1200 |
| character_arc | full | 1800 |
| main_plot | full | 3000 |

High-priority or substantive Rover/regional/time-memory signals can upgrade a
small quest to medium. Profiles bound sections and research steps as well as
prose; citations are excluded from prose counts. Output token allowance scales
to 4096/8192/16384/32000, reserved through the existing cost ledger. A paused or
uncertain run is never automatically restarted by the revisit dispatcher.

Certainty is separate from occurrence: a confirmed player option is not an event
that necessarily happened. Conditional/optional/player-choice assertions require
their condition. Medium/full outputs include known/unknown/cannot-conclude notes;
all v5 outputs include narrative function and a structured self-review. Strong
links require a cited direct reference or two distinct signal types. Theory is
stored as a candidate in the explanation and does not create a semantic edge.
These checks validate structure and provenance, not the truth of a model's
interpretation; independent editorial evaluation is still needed for lore quality.

Successful compiled imports atomically insert `ops.agent_revisit` entries for
current v5 explanations with flagged hooks. `(document_id, release_id)` is unique.
The story consumer scans bounded batches every minute, rotating the cursor so
older failed deliveries cannot starve newer work. The existing lexical GIN index
finds phrase candidates observed in the new snapshot. Generic-only terms are
rejected; no candidates means no paid job, **not** a resolved mystery. This is a
bounded candidate search, not exhaustive historical or semantic recall.

A matched review pins its original explanation, hook keys, candidate IDs and new
snapshot. It uses the same agent job/call ledger and budget. Queue confirmation
failures retry the persisted run ID, including after a dispatcher restart. The
review must reread exact original and new evidence; it can skip unrelated pages
of the original quest. Each requested hook gets a status and updated priority.
The original document remains immutable; supplements have separate heads per
original document and imported snapshot. They are hidden behind a spoiler
disclosure. Admin **Reviews after new imports** exposes pending, no-candidate,
queued and paused work with links to the ordinary run controls. Budget/context/
output pauses require an administrator's normal resume action.

Current supplements participate in explanation search while their original
document remains the published head. Reviewed-hook explanations are included in
both lexical text and the first block's embedding input. Search hides supplemental
titles and findings behind a closed spoiler disclosure, just like the quest page.

Existing v1–v4 documents do not acquire invented hooks. Reanalyze selected quests
to publish v5 hooks before expecting import-triggered review. Importing another
snapshot never silently expands a running job's evidence inventory.

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
The budget day follows `AGENT_BUDGET_TIMEZONE`. Environment examples and the code
fallback use Europe/Kyiv. Keep API and worker aligned. USD
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

### Research across patches, 2026-10-04

- Added v4 with a pinned imported-source inventory, explicit per-citation snapshot
  identity, graph traversal across patches and exact source links. Generated
  memory remains secondary evidence. No schema migration needed.
- Regression coverage checks old-to-new and new-to-old reads, graph discovery,
  candidate search, same-node/different-text isolation, full target pagination,
  rejection of unpinned snapshots, later-source invalidation and fresh job identity
  after an import or an external source change. Providers are mocked.
- Full server suite: 143 passed, 2 skipped (Redis infrastructure), with the same
  dependency deprecation warnings. Agent Ruff/strict mypy, three UI rendering
  tests, changed-file ESLint/Prettier, TypeScript and production builds passed.
- Rebuilt and restarted local API, web and story consumer; checked the v4 worker
  contract and a legacy explanation's source-version caption in the browser.
  No paid analysis was queued. The working story inventory currently contains
  1.0.0 and 1.1.0; other story snapshots require import first.

### Quest-only admin submissions, 2026-10-04

- Removed version and language fields. The server resolves and pins the latest
  imported snapshot containing the quest; admin explanations are English.
  Explicit historical versions remain supported for advanced callers.
- Full server suite: 144 passed, 2 skipped (Redis infrastructure), with existing
  dependency deprecation warnings. Agent/route Ruff and strict mypy passed;
  changed-file ESLint/Prettier, TypeScript and production build passed.
- Browser checks against a local fixture verified one form field, retained input
  after an unknown-quest error, the exact quest-only POST body and a 390px layout
  without horizontal overflow. No paid analysis was queued.
- Rebuilt/restarted local API, web and story consumer. Database health, existing
  published explanation retrieval and the anonymous admin 401 gate passed.
  No schema migration is required.

### Output-limit recovery, 2026-10-04

Responses `incomplete` with `max_output_tokens`, Chat Completions `length` and
Gemini `MAX_TOKENS` now pause as `paused_output` before tool arguments are parsed.
Incomplete tool calls are not executed. The admin action raises the response
allowance to at least 16,384 tokens (up to 32,000), adding one research step only
when the current step ceiling requires it. The resume API accepts `output_tokens`
and requires a strictly larger allowance and a recorded settled output-limited
response. It also supports older runs mislabeled as malformed turns.

Resumption advances past that paid attempt, retains the last fully processed
conversation and evidence, and asks for a complete concise response. Completed
research and charges are not replayed; the replacement request consumes the daily
allowance normally. Blocked responses, malformed completed output and uncertain
billing are not treated as output-limit recovery.

- Server suite: 147 passed, 2 skipped (Redis infrastructure), with existing
  dependency deprecation warnings. Ruff and strict mypy passed; frontend recovery
  policy tests, changed-file ESLint/Prettier, TypeScript and production build passed.
- Browser fixture checks covered keyboard recovery, queue failure, successful
  retry, the exact `{output_tokens: 16384, extra_steps: 1}` payload and a 390px
  viewport without horizontal overflow. Static UI audit reports 15 existing
  findings outside the changed components.
- Backed up operational data to
  `E:/Backups/solaris-atlas/story-agent-before-output-recovery-20261004.sql`.
  Reclassified working run 16 as `paused_output` only after verifying its settled
  response was truncated at step 15. Its checkpoint, response and usage are retained;
  no paid request was queued. No schema migration is required.
