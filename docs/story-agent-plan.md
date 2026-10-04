# Story agent: implementation plan and audit

## Goal

Explain a quest and a patch using cited imported game evidence. Readers can open
the exact dialogue passage behind an explanation, follow inferred event/faction
links, and search explanations with a natural-language question. An agent can
research bounded story/graph tools, keep persistent notes, and report missing data
without altering imported source facts.

## Existing foundation

- Canonical nodes, source/canonical/semantic/manual graph layers and edge evidence.
- Quest states/actions, dialogue, choices, release-specific localization and raw provenance.
- Events, claims/claim evidence, versioned documents/document heads/references.
- Processing runs/models/token counters and RabbitMQ workers with explicit queues.
- PostgreSQL pgvector and model-keyed embedding metadata; lexical story/entity search.
- Public API caching with transactional invalidation. New public tables need cache triggers.
- Administrative authentication and CSRF protection for task creation/mutations.

## Missing parts

1. Provider adapters with normalized usage, tool calls and embedding requests.
2. Persistent agent job state, scoped memory/alerts and checkpoints; duplicate jobs
   must not repeat paid calls. Uncertain remote calls must not be retried silently.
3. A shared daily USD/token ledger with concurrent reservations before each call.
4. Bounded, source-aware transcript/graph/search tools, including pagination and
   branch/runtime-order semantics. No arbitrary SQL or filesystem tool.
5. Validated output: cited explanation blocks, passage annotations, inferred links
   and events. Existing source nodes must be resolvable and evidence must have been read.
6. Versioned publication and stale-input detection; historical versions/locales must
   not overwrite each other. Generated memory is context, never primary evidence.
7. Explanation embeddings/search, separate from source dialogue and keyed by model,
   dimensions, text hash, release and language. Vectors augment readable documents.
8. Admin queue/status/usage/alerts and public explanation/search API; quest integration.
9. Deterministic mocked-provider and isolated PostgreSQL/queue integration checks.

## Decisions

- Start with **GPT-6 Luna**, API ID `gpt-6-luna`, via Responses API. Its documented
  Chat Completions tool support requires reasoning disabled; Responses is the
  appropriate primary adapter. Retain OpenAI-compatible Chat (DeepSeek/Qwen) and
  native Gemini adapters without imposing a vendor SDK on core orchestration.
- Default to no paid execution until key, prices and a positive daily budget are
  configured. No external billable calls during implementation tests.
- Store structured readable explanations in existing content documents. Embeddings
  are a retrieval index, not the only copy of knowledge.
- Scope artifacts/memory by quest, release and language. Persist citations as node
  identities and verified snippets; build internal URLs in application code.
- Preserve source graph. Agent-created events/links use semantic/inference layers
  and processing provenance, and are identified as generated interpretations.
- A bounded loop may fetch more evidence, save notes/alerts, then publish a validated
  final result. Do not publish partial/unvalidated prose as a completed explanation.
- Queue jobs by content/config/prompt identity. Persist remote-call intent before
  sending; preserve reservations for timeout/crash/unknown usage. Recovery pauses
  uncertain calls rather than automatically spending again.
- Use exact Decimal USD values and a locked daily row. Conservative preflight
  reservations include bounded input/output. Provider usage settles completed calls;
  missing usage or underreported limits stop execution and raise an alert. Provider
  billing settings remain the ultimate external billing ceiling.
- Public question search retrieves cited explanations; it does not launch a paid
  LLM call on every anonymous question. Embeddings can be configured separately.

## Delivery slices (bottom-up)

1. Plan, validated contracts, minimal persistence/migration, budget invariants.
2. Provider adapters and bounded evidence/memory tools, with deterministic tests.
3. Durable queued agent loop, artifact publication and vector indexing/search.
4. Admin/public API and quest/search UI integration; deployment docs and full checks.

Each coherent validated slice is committed locally. Remote push/PR is not part of
this request. Existing working DB migrations are not silently applied.

## Acceptance checks

- Process a seeded quest through mocked research/tool/final calls and publish an
  explanation with working source citations and a semantic graph edge.
- A duplicate completed job does not spend again; replay after an uncertain call
  does not send another remote request.
- Two concurrent reservations cannot overspend the configured daily ceiling.
- Changed source input prevents publishing an outdated result as current.
- Wrong-release/unknown/unread citations, invented node IDs and invalid links fail validation.
- Queries/notes are bounded and historical/future releases do not leak into results.
- Budget/step/context/provider failures have inspectable statuses/alerts and safe logs.
- Public explanation lookup is release/language scoped; question retrieval returns
  scored readable blocks and citations with no remote provider charge.

## Primary references checked

- [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna)
- [OpenAI Responses](https://developers.openai.com/api/reference/resources/responses/methods/create)
- [Gemini function calling](https://ai.google.dev/gemini-api/docs/function-calling)
- [Gemini embeddings](https://ai.google.dev/gemini-api/docs/embeddings)
- [DeepSeek Chat](https://api-docs.deepseek.com/api/create-chat-completion/)
- [Qwen function calling](https://docs.modelstudio.console.alibabacloud.com/en/model-studio/qwen-function-calling)

## Delivered and verified

All four slices are implemented on `codex/feat/story-agent-pipeline`: durable
execution/budget/memory, three provider adapters, source tools and validated
publication, optional vectors, admin/public APIs, quest explanations, event pages
and question retrieval. The local API/worker daily allowance is **$1**; keys are
present locally but have not been checked with a paid request. A working-database
migration is still required for a real run. Detailed setup,
recovery and current bounds are in `docs/story-agent-pipeline.md`.

The first delivery processes explicitly queued quests. Patch-wide crawling,
hierarchical analysis of quests that exceed context limits, retention automation
and editorial evaluation against real model output remain separate work. They
are not claimed as implemented by the current bounded pipeline.
