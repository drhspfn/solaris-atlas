# Christoforo conversation text investigation

The NPC `character:npc:15697` scene at state node `2499781`, source state key
`剧情_2_5_隐海试验场_失忘彼岸_2_1`, contains 13 dialogue lines. The production
dialogue response returned `inline_text: null` for all 13, and the scene previews
were empty before frontend rendering.

Retained flow records `18550651` (release 10) and `64651014` (release 21) contain
the same sampled entries in action 6. These entries have text references but no
inline dialogue:

| WhoId | TextId | TidTalk |
| --- | --- | --- |
| 83, inner voice | 437 | `Main_Linaxita_FuLuoLuoCE_70_11` |
| 560, Phrolova | 438 | `Main_Linaxita_FuLuoLuoCE_70_1` |
| 50080, Cristoforo | 440 | `Main_Linaxita_FuLuoLuoCE_70_3` |

The sampled keys have no `i18n.localization_key` rows. Direct checks of retained
English `MultiText` and `FlowText` rows in releases 10, 13, 18, and 21 found no
matching sampled keys or numeric TextIds. This does not rule out text in other
unexamined assets or sources, and the inspected metadata does not establish that
the flow was deleted, unused, or a prototype. A text-unavailable indication is
appropriate; dialogue should not be inferred from speaker names.

The NPC dialogue API now exposes available inline text in `text.content` and
marks absent text as `resolution: "unavailable"` (commit `879425b`). For that
backend change, the targeted NPC profile and scene tests passed (25 tests), as
did Ruff. These checks did not recover the missing conversation text.
