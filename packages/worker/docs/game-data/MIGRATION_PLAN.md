# Migration plan from the Phase 0 PoC

## Reuse

- Keep `scripts/extract_quest.py` and existing `output/canonical/` as baseline fixtures. Its FlowState/TalkSequence/SequenceTransitions/JumpTalk handling is useful, but its single-quest view and hardcoded English/version paths are limiting.
- Keep raw upstream checkouts, pinned source commits, Phase 0 reports, and Denia/cutscene benchmark IDs.
- Reuse exact data joins already checked against raw records: PlotHandBook flow references, speaker ID to Speaker table, `TidTalk` to PlotAudio, `PlayMovie.VideoName` to `CgName`.

## Changes

1. Inventory every checked-out BinData table and every locale, record shape/count/field paths and classify narrative relevance before normalization.
2. Add a versioned compiler package and CLI. Emit stable JSONL entities and edges plus manifest, coverage and diagnostics. Store full raw payload for normalized records and copy every inventoried JSON table as raw evidence.
3. Compile the whole version, including QuestNodeData structure and independent phone/random plot entry points. Keep source-array ordering distinct from explicit graph references and runtime conditions.
4. Resolve only exact IDs/keys. Keep speaker, role and NPC namespaces separate unless explicit cross-table fields prove identity.
5. Validate graph integrity, strict schema handling, localization coverage, benchmark non-regression, and deterministic rebuilds.

## Compatibility and scope

The old `./extract-quest` command remains usable. The new compiler writes to `dist/<version>/` and has no database or LLM dependency. Game media extraction is an optional later process; metadata references are included now.
