# Compiler coverage — Arikatsu Game 3.6.0 / Resource 3.6.6

Source commit: `353f2eaed119bc9f680eab92807d20ac75a79b40`. The build completed with `--strict --strict-coverage`. Validation found zero errors, duplicate IDs, dangling edges, edges without provenance, or raw-evidence hash mismatches. The generated dataset contains 400,297 entities and 615,328 global edges.

## Inventory and coverage

`coverage.json` inventories 2,721 JSON files: 2,022 BinData and 699 Textmaps. Strength tiers are field-based first: Tier 1 strong graph references, Tier 2 strong narrative content/metadata, Tier 3 filename-only candidates, and out of scope. `coverage-summary.json` reports Tier 1 as 9 normalized, 15 partially normalized and 49 raw-evidence-only; strict coverage passes with no new/unclassified Tier 1 schemas or unknown strong fields. All raw tables are preserved with hashes.

107,591 raw top-level TalkItems are accounted for (100%, zero unclassified). There are 40,597 PlotAudio records, with 40,701 direct TalkItem reference occurrences, 40,182 distinct referenced IDs and 415 IDs lacking a direct TalkItem reference. Cutscene action references resolve 146 names and leave 7 unresolved, including one empty value. The build adds 24,332 source-reference records, including exact BubbleData, interaction, LevelPlay, PhantomBattle, map-block and instance-dungeon references.

## Localization

There are 324,306 localization identities in each of 13 locales. Resolution is recorded as nonempty, empty, missing key or broken redirect. English has 267,899 nonempty and 56,407 empty identities; Russian and Vietnamese each have 0 nonempty and 324,306 empty identities. Coverage is not inferred from key presence. The English source has 706 referenced missing keys; per-locale detailed counts are in `dist/3.6.0/localization/coverage.json`.

## Benchmarks and diagnostics

Quest graph files are generated for all quests. The Denia benchmark remains `119000000`; its graph file contains the closure reachable from the quest root in the global reference graph. The graph currently contains 2,137 node IDs and 3,464 edges because it follows outgoing explicit relations through shared source entities; use entity-level raw evidence and edge type when interpreting that closure, not as a guaranteed player traversal. `168800009` and `139000039` are also preserved as benchmark quest graph outputs.

The build has 3,587 diagnostics and zero errors. Leading categories include 1,149 unknown tables, 814 conflicting supplemental localization records, 706 unresolved localization keys, 462 unresolved external quest references, 198 unresolved TalkSequence pointers, 179 unresolved external FlowState references, 21 partial-table notices, 17 unresolved FlowStates, 10 item refs, 7 area refs, 7 cutscenes and 5 sequence transitions. Diagnostics preserve source context; they are not silently dropped.

`--strict` checks schema/action/TalkItem drift. `--strict-coverage` separately checks newly added Tier 1 tables, required tables, Tier 1 classification, unknown strong fields and TalkItem accounting. Tier 3 filename-only candidates do not fail strict coverage. AppleDouble (`._*`) and `.DS_Store` files are ignored by filesystem walkers.

Run `SOURCE_DATE_EPOCH=1787220000 ./wuwa-narrative build --version 3.6.0 --strict --strict-coverage` to reproduce the build, then `./wuwa-narrative validate --version 3.6.0`. Run `python3 -m unittest discover -s tests -v`; install `.[test]` to run pytest. See [PARSER_REFINEMENT_REPORT.md](findings/PARSER_REFINEMENT_REPORT.md) and [INVESTIGATION.md](INVESTIGATION.md) for evidence and unresolved limitations.
