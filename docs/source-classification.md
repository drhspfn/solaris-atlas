# Weapon sources and quest roles

These API annotations are computed from the selected imported snapshot. They
need no schema migration, media export, LLM call or full reimport.

## Weapon banner references

`GET /items/{key}/profile` includes `banner_sources` for weapons. Exact numeric
membership in `GachaViewInfo.UpList`, `PreviewIdList` and `ShowIdList` is reported
as featured, preview and display evidence respectively. Pool links require both
`GachaViewInfo.Id = GachaPool.Id` and `GachaPool.GachaId = Gacha.Id` in the same
release. Permanent weapon artwork identifies that banner category; other
categories stay unknown when no verified mapping exists. Every result retains
file/row, record hash, release and optional localized title provenance.

Preview/artwork is not a complete loot table, a drop probability or a statement
that a banner is currently available. The UI labels the evidence accordingly.

## Quest classification

Shared quest responses include `classification`, its source records and evidence:

- `test`: the authored quest subtype points to a main type using the game's test
  tab icon; a hidden subtype plus an explicit internal test/debug name also qualifies.
  Older snapshots with incomplete type dictionaries can use explicit developer
  namespaces (`test/`, `debug/`) or numeric item/weapon/echo test-fixture names.
- `story_gallery`: internal `Data.Key` explicitly contains `剧情图鉴专用`.
- `deprecated`: an explicit delimited deprecated marker prefixes the internal key.
- `internal`: the quest type explicitly sets `IsShowInQuestPanel` to false.
- `unclassified`: no supported explicit marker, missing source or ambiguous quest row.

Missing text, dialogue, scenes or rewards never establish a test classification.
Do not propagate a test label along prerequisite/reference edges: a normal quest
may reference an internal helper. Existing source edges are preserved.

Item links additionally explain exact `GetItem` requirements and both authored
`HandInItems` layouts. These are requirements, not quest rewards. Multiple allowed
hand-in item IDs remain alternatives. Unrecognized references stay references.
The quest profile lists these requirements separately from its dialogue transcript.

## Verification examples

- Snapshot 2.0.0: Emerald of Genesis (`item:21020015`) appears in view/pool 32,
  gacha 3, with preview/display roles and permanent weapon artwork.
- Quest 139000037 has the source name `测试21020015武器`, a test type, and a
  `GetItem` condition requiring one Emerald of Genesis. No reward is implied.
- Across the 756 source quest rows in that snapshot, the rules yield 323 test
  records, one story-gallery record and 432 unclassified records.

Checks: server tests `test_weapon_acquisition.py`, `test_quest_classification.py`,
`test_item_acquisition.py`, `test_quest_state_links.py`; web `npm run build`,
`npm run test:ssr` and `npm test` in `packages/web_new`.

Manual checks: open the weapon profile and follow its test-quest link; inspect
the classification evidence disclosure, requirement count and item backlink;
repeat at a narrow viewport. Select an older snapshot and verify the evidence
uses that release rather than the newest imported data.

Rollback: revert the API/UI commits; no stored game data is rewritten.
