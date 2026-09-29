# Version stability — Dimbreath 3.1 vs Arikatsu 3.6

The available snapshots are different upstream exports, so this is a cross-source comparison pinned to explicit commits rather than a same-repository pair of releases. Dimbreath commit `e9234ffe094b2d944d16b222d31102e8ab32d954` identifies game/resource 3.1.19; Arikatsu commit `353f2eaed119bc9f680eab92807d20ac75a79b40` identifies Game 3.6.0 / Resource 3.6.6.

The comparison script took PlotHandBook state references from Arikatsu 3.6, selected matching `StateKey`s in both FlowState tables, and compared ID sets within the matched states.

| QuestId | Shared state keys | ActionIds | TalkItem Ids | TextIds | TidTalk/TidTalkOption keys | WhoIds |
|---:|---:|---:|---:|---:|---:|---:|
| 139000039 (When the Night Knocks) | 167 / 167 | 317 / 317 stable | 31 / 31 | 461 / 461 | 842 / 842 | 29 / 29 |
| 915700000 (When the Unknown Thrums) | 6 / 6 | 24 / 24 stable | 27 / 27 | 66 / 66 | 66 / 66 | 8 / 8 |

For these sampled shared quests, StateKey, ActionId, TalkItem Id, numeric TextId, localization keys, and speaker IDs are unchanged in the two snapshots. This is positive evidence for identifier stability across these specific records, not a guarantee for every table or patch. The script compares key sets but does not compare all localized text content, `ActionGuid`, or edge semantics.

QuestId `119000000` (Beneath a Melting Night Sky / Denia storyline) is absent from Dimbreath 3.1; it exists in Arikatsu 3.6. This is an unavailable later quest, not an unstable identifier. Dimbreath's observed snapshot reaches 3.1 and cannot provide the 3.3 Denia benchmark.

Matching changed content by text hash, speaker sequence, adjacent actions, graph topology, or asset paths remains a future method. No renumbered IDs were observed in the sampled overlaps; no claims are made about the unobserved range.
