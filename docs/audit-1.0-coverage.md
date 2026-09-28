# 1.0 snapshot coverage spot-check

Date: 2026-09-27. This is a targeted check of the imported 1.0.0 snapshot, not a claim that every game-data family is covered.

## Findings

- The database contains 626 typed items for release 1.0.0. The item search page was empty because `search.document` had not been built after import; item records and non-empty English localization were present. Rebuilding the deterministic lexical index created 175,738 localized search documents overall (4,984 item-category documents across locales).
- `item:41100011` is `LF Whisperin Core`, with a non-empty English name and description. `item:41100012`–`item:41100014` are the corresponding MF/HF/FF Whisperin Cores. The 1.0 raw `rolebreach.json` records contain exact material requirements; group 1604 links to `RoleInfo.Id=1604` (`Rover-Havoc`) for 4 units at the level-40 cap.
- Some breach groups are deliberately shared by multiple `RoleInfo` rows: item 41100011 has 2 exact mappings for group 1501, 2 for 1502, and 4 for 1102. The old resolver displayed these as unresolved because it required exactly one matching role. The profile resolver now emits every exact `BreachGroupId == RoleInfo.BreachId` match and retains the role ID/source row. Groups with no exact `RoleInfo` remain unresolved (for example group 1603).
- Searching the single word `core` exposed a separate query bug: trigram matches were returned early, hiding whole-word matches that only matched PostgreSQL full-text search. Search now merges trigram and full-text matches before de-duplicating entities.
- `character:1604` has a localized name (`Rover-Havoc`) and exact progression-use evidence, but zero story appearances in this release. Its profile has no `SpeakerEntityLink`; the character-to-dialogue association therefore is not proven by currently imported crosswalks. This is a distinct speaker identity coverage gap, not missing character localization or missing item data.
- Character profiles now show the reverse progression view from exact release-scoped `RoleInfo.BreachId` / `RoleDevProsProject` group joins to material records. Aalto (`character:1403`) now has 22 source-backed material entries, including LF Howler Core ×4 at Lv. 40, with item links and raw row paths.
- For speaker resolution, Aalto's `RoleInfo.FormationRoleCard` exactly equals `Speaker.RolePileIconAsset`, and that asset occurs once in each table in 1.0. This unique exact join maps Aalto to speaker 132 and exposes two quest appearances (114000020 and 140000004). The profile only applies this mapping when the exact asset is unique on both sides; shared icons remain unresolved.

## Interpretation and limits

The screenshot comparison is against later-game (3.x) progression requirements, while the inspected snapshot is 1.0.0. The 1.0 snapshot cannot be expected to contain items introduced later. For 1.0 data, the item definitions and at least the sampled ascension links are present. This spot-check does not establish complete coverage of all progression, acquisition, map-spawn, quest, or character-dialogue tables.

New imports now build search documents automatically after canonical entities and localization have been imported. Existing imported snapshots can be repaired with `cd packages/server && uv run python scripts/build_lexical_index.py <game-version>`.
