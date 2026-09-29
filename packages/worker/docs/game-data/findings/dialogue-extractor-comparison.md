# Dialogue extractor comparison — shared quests

## Runs

RealNath consumed Arikatsu 3.6. WutheringDialog consumed only Dimbreath 3.1 FlowState rows for QuestIds `139000039` and `880000034`, selected from Arikatsu's PlotHandBook references. Both outputs are saved under `output/realnath/` and `output/wuthering-dialog/data/dialogs_en.jsonl`.

| QuestId | Extractor | State records | Action records | Gameplay actions | Dialogue lines | Narration | Choice display/records | Missing localization keys |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 139000039 | RealNath | 164 headings in transcript | Not preserved in printed transcript | No | 669 speaker lines | included as formatted lines | 164 markers | 0 raw keys |
| 139000039 | WutheringDialog | 167 unique state rows | 767 | 605 | 651 | 21 plot records | 1 option record | 0 missing speaker/text values in emitted records |
| 880000034 | RealNath | 6 headings | Not preserved | No | 67 speaker lines | included | 14 markers | 0 |
| 880000034 | WutheringDialog | 7 state rows | 57 | 51 | 67 | 0 | 1 option record | 0 missing speaker/text values in emitted records |

These counters use different output schemas; line counts are not interchangeable source-row totals. WutheringDialog output loses TextId/TidTalk keys, so a trustworthy exact line-by-line diff cannot be computed after extraction. A future comparison should key both outputs back to raw TalkItem IDs/localization keys.

## Findings

- **Ordering:** RealNath traverses `TalkSequence`, `SequenceTransitions`, and `JumpTalk`; it renders branching paths and their join behavior. WutheringDialog retains raw TalkItems order and action order but does not encode TalkSequence/SequenceTransitions in its output. RealNath is stronger for a human-readable ordered transcript; WutheringDialog is stronger for preserving action IDs.
- **Branches:** In RealNath source, choices attached to a line are processed. WutheringDialog tests `if "TidTalk" in talk` before `elif "Options" in talk`, so a TalkItem containing both a line and options is handled only as a line. The measured output is 164 choice markers versus one standalone option record for QuestId 139000039.
- **Gameplay actions:** WutheringDialog preserves `ActionId`/`Name` for non-dialogue actions but labels everything `gameplay`, losing action-specific semantics. RealNath produces a transcript, not an action graph.
- **Action ID coverage:** The unfiltered WutheringDialog run on the 3.1 source fails on a raw action with no `ActionId` (`KeyError`); it also prints unknown TalkItem type warnings. On the two selected subsets, all encountered action rows were emitted.
- **Source tables:** RealNath joins PlotHandBook -> flow -> FlowState -> split MultiText and accepts explicit QuestId. WutheringDialog scans FlowState rows and MultiText directly; it emits each state as a record but has no QuestId resolver.
- **One-sided lines:** Exact source-line-only differences remain unmeasured because WutheringDialog's output discards source TextId/TidTalk identifiers and uses localized text strings. We can compare those lines after extending a copy of its exporter to retain source keys; it is not necessary to establish the option-loss bug.

## Current 3.6 compatibility

RealNath successfully generated 3.6 transcripts for 3 main-story IDs, 3 side/activity IDs, and a separate cutscene quest. WutheringDialog's own code is from 2024, and the Dimbreath dataset commit is 3.1; Denia QuestId `119000000` has no matching states in that snapshot, so no honest same-quest WutheringDialog comparison is possible for that 3.3 story.
