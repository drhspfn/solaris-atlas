# Denia query results — Game 3.6.0

All results below come from source-backed canonical records. The character-to-speaker crosswalk uses exact role portrait/resource paths, not name matching.

## Identity and dialogue

- Character: `character:1211`, RoleInfo row 113.
- Resolved speakers: `speaker:200144` and `speaker:250055`, each linked by two exact asset-path matches to RoleInfo 1211. Evidence is in `BinData/speaker/speaker.json` rows 5225 and 5440.
- `speaker:200144` has 435 dialogue lines in the imported release. `speaker:250055` is currently referenced by no dialogue lines.
- English text resolves by the original localization keys. Example: `Main_LahaiRoi_ZRJDYKX_32_5` — “Yeah. It was all made up. To keep her happy. But I didn't realize how much effort it takes to keep a simple lie going.”

## Quest results

The deterministic path is `quest → quest_node → flow_state → action → talk_item → speaker`. It attributes 265 of the 435 dialogue lines to these four quests:

| Quest ID | Quest | Denia lines | States | Actions | Choice records | Cutscenes |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 119000000 | Beneath a Melting Night Sky | 226 | 42 | 262 | 67 | 0 |
| 168800009 | Gold Suspended in Shadows | 33 | 52 | 283 | 81 | 0 |
| 121000040 | Starlights from Yesterdays | 3 | 28 | 112 | 24 | 3 |
| 165800000 | Rabbit Reflected in Shades | 3 | 16 | 90 | 42 | 0 |

For quest `119000000`, 38 scenes have an authored order; 30 contain Denia dialogue. The compiled graph has 63 `next_authored_talk` edges and 7 `sequence_transition` edges directly between her lines. These record authored ordering and transitions, not guaranteed player traversal. The quest also has 2 narration nodes and 17 action-to-audio-asset references. Those are metadata references; this result does not assert that media bytes were extracted.

The 67 choice records belong to the quest states containing the dialogue; they are options, not necessarily 67 separate decision moments. Examples include “You've been doing this all along?”, “That's self-deluding.”, and “Denia wanted to surprise you.”

The three cutscenes found in the connected `121000040` quest are `M0352`, `M3_6_23`, and `M0349`; their `cg_file` fields are empty in this imported canonical projection. No direct `plays_cutscene` edge was found in the main Denia quest `119000000`.

## Remaining coverage gap

The other 170 dialogue lines for speaker 200144 do not reach a Quest entity through this explicit quest-node chain in the current graph. Their dialogue, speaker, flow-state, and source records remain available; they are not assigned to a quest by title or other fuzzy matching.

## Reproduce

Run the read-only SQL in [`../queries/denia.sql`](../queries/denia.sql):

```bash
docker compose exec -T postgres psql -U wuwa -d wuwa_story -f - < queries/denia.sql
```

The API endpoints for identity and direct graph edges are:

```text
GET /search?q=denia&category=character&limit=20
GET /nodes/character%3A1211/edges?direction=both&limit=100
```
