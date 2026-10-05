# Cutscene visual evidence

The supplied Colab notebook is an experiment, not an import specification. Keep
its chronological frame sampling and separate visual observations from dialogue.
Do not copy its unlimited merge request or model pricing assumptions.

## Delivery slices

1. Bounded, versioned observations and durable jobs keyed by published video hash,
   asset version, model and sampling settings. Reuse processing runs, file
   references, provider adapters and the shared daily spending ledger.
2. Worker sampling and checkpointed vision calls. Each small batch must be
   complete and validated; split output-limited batches rather than accepting
   partial JSON. Assemble the chronological observations without a paid global
   merge. Imports enqueue work transactionally; queue recovery reads pending jobs.
3. Quest tools expose cutscene inventory and paginated visual observations with
   provenance and variant identity. The story agent writes reader descriptions
   and optional timed chapters from these observations and exact dialogue.
4. Keep primary narrative role independent of secondary functions. Source-tagged
   main quests require full analysis. Consolidate unknown branch occurrence in
   one disclosure; do not infer mandatory flow from the quest's main-quest label.

## Verification and operations

Use mock providers for implementation checks. Do not start paid backfills or
change the existing daily allowance. Enable automatic vision through worker
configuration after selecting a vision-capable model and its rates. Budget,
unknown remote outcome and source changes stop work without publishing a partial
description. Old explanations remain readable. Sampled frames do not prove the
absence of events between frames. Variants retain their own observations; they
are never concatenated into one supposedly canonical playthrough.

### Enable and run

Set `AGENT_VISION_ENABLED=true` in `packages/worker/.env`. This uses the existing
`AGENT_PROVIDER`, `AGENT_MODEL`, endpoint and prices, and the same $1 daily ledger
as quest analysis. The configured model must accept images. Defaults: one frame
every approximately 3 seconds, at most 600 frames, four frames per request and
4096 output tokens. The capped sampling plan always includes the ending; it is
not exhaustive coverage. Each supplied frame needs its own observation.

From the repository root:

```powershell
docker compose -f infrastructure/local/compose.yml --profile cutscene-vision up -d --build cutscene-vision
docker compose -f infrastructure/local/compose.yml exec cutscene-vision wuwa-story-worker enqueue-cutscene-vision --version 3.7.0 --limit 20 --offset 0
docker compose -f infrastructure/local/compose.yml exec cutscene-vision wuwa-story-worker cutscene-vision-status --limit 20
```

New cutscene imports automatically persist jobs when enabled. For existing videos,
use the backfill command, advancing `--offset`; `--asset-node-id` targets one exact
variant. Backfill deduplicates jobs and does not re-charge completed work.
The dispatcher polls every minute and leases delivery for ten minutes. A session
advisory lock prevents simultaneous paid processing; recorded complete responses
are replayed locally after crashes. Known 429 rejections settle with zero usage.

Paused budget/provider/input/validation jobs need explicit resumption after fixing
the cause; the daily limit is never raised. For an output-limited single frame,
increase its output allowance (up to 16000):

```powershell
docker compose -f infrastructure/local/compose.yml exec cutscene-vision wuwa-story-worker resume-cutscene-vision RUN_ID --output-tokens 8192
```

An uncertain remote outcome cannot be resumed until billing is reconciled; its
reservation remains held. Status output contains IDs, progress and safe error text,
never prompts, images or credentials. No migration is needed: processing runs,
the shared call ledger and content-addressed file references already exist.

After reports complete, queue a new quest analysis in the admin panel. Story V6
reads every available variant through paginated visual tools before publishing.
Readable descriptions and timed chapters are stored in the explanation document
and exposed in the corresponding player's AI disclosure. Visual report changes
invalidate the analysis fingerprint; changed video bytes hide outdated reports.
Recording version is stated separately from the quest source snapshot: current
video does not establish what was visible in a historical game build.

To disable paid visual work, stop the dedicated consumer and set the flag false.
Imported video/dialogue and previous valid descriptions continue to work.
