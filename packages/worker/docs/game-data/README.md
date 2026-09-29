# WuWa deterministic narrative compiler

This is the investigation and compiler source carried forward from the earlier Phase 0 workspace. The compiler now lives at `packages/worker/src/wuwa_story_worker/compiler`; the notes here record the actual schemas and findings from the inspected Arikatsu 3.6 snapshot. Large upstream repositories, generated distributions, and intermediate outputs are not stored in this repository. The runtime snapshot pipeline is documented in [the worker guide](../../README.md).

## Build the complete version

```sh
cd packages/worker
uv run python -m wuwa_story_worker.compiler --data /path/to/WutheringWaves_Data --dist ./var/dist build --strict --strict-coverage
uv run python -m wuwa_story_worker.compiler --data /path/to/WutheringWaves_Data --dist ./var/dist validate --version 3.6.0
uv run python -m wuwa_story_worker.compiler --data /path/to/WutheringWaves_Data --dist ./var/dist coverage --version 3.6.0
uv run python -m wuwa_story_worker.compiler --data /path/to/WutheringWaves_Data --dist ./var/dist quest 119000000
```

`build` writes a versioned manifest, coverage, diagnostics, canonical entities, localization, a global reference graph, per-quest graphs, indexes, and indexed raw evidence. `--strict` fails on schema drift, new action/TalkItem types, or error diagnostics; `--strict-coverage` separately enforces narrative coverage checks. The automatic worker pins source commits and sets `SOURCE_DATE_EPOCH` from that commit for reproducible builds.

Other commands:

```sh
uv run python -m wuwa_story_worker.compiler --data /path/to/WutheringWaves_Data --dist ./var/dist scan
uv run python -m wuwa_story_worker.compiler --data /path/to/WutheringWaves_Data --dist ./var/dist diff 3.6.0 3.7.0
uv run python -m wuwa_story_worker.compiler --data /path/to/WutheringWaves_Data --dist ./var/dist quest 168800009
```

Install the optional pytest runner and editable CLI entry point with:

```sh
python3 -m pip install -e '.[test]'
pytest
python -m pytest
```

`--strict` checks schema/action/TalkItem drift and error diagnostics. `--strict-coverage` separately fails for newly added Tier 1 tables, missing required narrative tables, unclassified Tier 1 tables, unknown strong reference field names, TalkItem coverage gaps, or unclassified TalkItem types. Tier 3 filename-only candidates remain visible in `coverage.json` without making this mode fail.

Each build also writes `coverage-summary.json`. Localization identities carry one of `resolved_nonempty`, `resolved_empty`, `missing_key`, or `broken_redirect`; per-locale counts are in `localization/coverage.json`. These states keep a present key with an empty string distinct from a missing key and a broken redirect.

See [INVESTIGATION.md](INVESTIGATION.md) for raw schema evidence, [CANONICAL_SCHEMA.md](CANONICAL_SCHEMA.md) for output format, [COVERAGE_REPORT.md](COVERAGE_REPORT.md) for measured coverage and diagnostics, [MIGRATION_PLAN.md](MIGRATION_PLAN.md) for the Phase 0 transition, and [findings/FINAL_REPORT.md](findings/FINAL_REPORT.md) for the original investigation. The former workspace's generated outputs are intentionally excluded; the compiler produces them from pinned upstream data when a worker job runs.

## Preserved Phase 0 quest extraction

The earlier single-quest prototype and captured output were part of the investigation workspace; only the current general compiler source and its notes are maintained here.

The original investigator scripts are retained in `tools/` as source reference. They expect local upstream input paths and their own output directories; the long-term build/import path is the worker queue.

## Headline findings

- Arikatsu 3.6 includes `flow.json` (9,729 rows), `flowstate.json` (20,198 rows), `plothandbookconfig.json` (94 rows), Quest/QuestData/QuestNodeData/QuestRefVideo tables, and multi-part Textmaps.
- `PlotHandBook.Data` supplies ordered flow references; `FlowState.Actions` supplies source action order; `ShowTalk.Params` has TalkItems, TalkSequence and SequenceTransitions. `TidTalk` / `TidTalkOption` keys resolve through localized MultiText; speaker IDs resolve as `Speaker_<id>_Name`.
- Main-story QuestId `168800009` produces 132 scenes, 1,712 nodes and 2,894 edges: 984 dialogue, 612 game-action, 108 player-choice, 6 narration and 2 cutscene nodes. It includes 12 explicit choice-branch edges, caption metadata and Wwise event references. Two narration localization keys are missing in the current English snapshot.
- Denia story QuestId `119000000` resolves 38 scenes; the transcript contains Denia/Nivora dialogue and references to Fractsidus/Aleph-1. One localization key is absent. The 3.1 Dimbreath snapshot predates it.
- RealNath ran against 3.6. WutheringDialog ran on the shared 3.1-era quest subsets and comparison exposed substantial option loss. The unfiltered WutheringDialog run crashes on a current FlowState action missing `ActionId`.

See [findings/FINAL_REPORT.md](findings/FINAL_REPORT.md) for details, limitations and proposed Phase 1. Local game extraction tooling and requirements were investigated but not run because this machine has no installed game files or AES keys, and FModelCLI's published binary targets Windows x64. Public datamined data was sufficient for the story extraction PoC.


Parser refinement findings: [localization](findings/localization-coverage.md), [Tier 1 tables](findings/tier1-table-investigation.md), [ambient/battle dialogue](findings/ambient-dialogue-investigation.md), [unused PlotAudio](findings/unused-plotaudio.md), [speaker crosswalk](findings/speaker-crosswalk.md), [unresolved media](findings/unresolved-media.md), and [refinement report](findings/PARSER_REFINEMENT_REPORT.md).
