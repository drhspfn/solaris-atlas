# Local map collection progress

The map's browser store lives in `packages/web/src/state/mapProgress.ts` and is
subscribed to through `useMapProgress`. It persists versioned JSON under the
configured `APP_SETTINGS.storage.mapProgress` key. No API or database change is
needed; clearing this browser key resets personal marks.

Identity is `game_map_id:entity_id`, not an exported marker row ID, asset job or
patch. New exports retain progress when the source placement identity is retained.
The store does not assume that unrelated source identities represent the same
object. Account/device synchronization is not implemented.

Chests and verified `collection_kind` placements are eligible: Sonance Casket
(Huanglong, Ragunna, Septimont), Windchimer, Unclaimed Rafter Kite, Tape of Last
Words, Blobfly and Frostbug. The compact marker API retains this identity so
completion does not depend on English display names. Older imports retain a
strict name fallback for Caskets, Windchimers and Kites. Respawning plants and
permanent collectors must not receive completion controls.

The 3.7 reader identifies these placements using `BaseInfoComponent.Category.
ExploratoryDegree` and an enabled server reward plan. It merges placement
overrides before classification. Shared MapIcon/blueprint names alone cannot
identify a pickup: quest props reuse them. Windchimers can have a disabled
CollectComponent because they are claimed by hitting them. Item names/icons
provide the canonical Casket groups; Blobfly/Frostbug use map type icons.
Server reward plans do not establish client drop-preview evidence.

The source audit found 126 Huanglong Caskets (one hidden), 95 Ragunna Caskets,
85 Septimont Caskets, 61 Windchimers, 130 Tapes, 90 typed Kites, 43 Blobflies
and 10 Frostbugs on published map IDs. These are source placements, not verified
playable completion totals. Hidden placements still require Include hidden game
placements. Published marker IDs and browser progress identities survive upserts.

Cross-check references: [Casket family](https://wutheringwaves.fandom.com/wiki/Casket),
[exploration categories](https://game8.co/games/Wuthering-Waves/archives/490735),
[Soliskins](https://game8.co/games/Wuthering-Waves/archives/572345).
Soliskin / Soliseed of Essehalo needs separate placement evidence: the 3.7
ExploratoryTypeConfig key 61 has no matching published level entities, and the
server reward plan alone does not prove which props award the item. Viewpoints,
quests and puzzles are not made markable through a guessed collectible label.

## Verification

Run domain tests from `packages/web`:

```powershell
node --test tests/mapProgress.test.mjs
```

For isolated browser verification, start two terminals in `packages/web`:

```powershell
node tests/mapProgress.fixture.mjs
```

```powershell
$env:VITE_API_BASE='http://127.0.0.1:8013'
npm run dev -- --port 5174 --strictPort
```

Open `http://localhost:5174/tests/map-progress.html`. The fixture uses illustrative
placements and separate origin storage, with no database writes or game assets.

Manual tests:

1. Show all, open a chest and Mark as found. Its icon disappears; its card stays
   open with an undo button. Reload: the mark remains saved.
2. Show found markers restores the icon at 40% opacity. Mark as not found restores
   normal visibility. Both actions work with keyboard controls.
3. Floramber, the collector, Nexus, boss and Tacet field have no completion action.
   Include hidden game placements reveals the markable Unclaimed Rafter Kite.
4. Open a second fixture tab. A mark/undo in one updates the other's count.
5. Block progress storage, then mark. An inline session-only warning appears and
   undo still works. Restore storage and change the toggle to save session state.
6. Open `?map=1&marker=105` for a found Kite. Its focused card offers undo even
   while the icon is hidden. Sharing never encodes progress.
7. At 390px width the sidebar scrolls above the map and the card's action fits
   within the viewport. Region switching does not transfer marks to the other
   source game-map identity.
8. Switch to Statistics. Every eligible type has an icon, name, found/total count
   and progress bar; plants, services and other ineligible types are absent.
   Mark/undo from the selected card updates its row immediately, including its
   completion indicator. Search and category visibility do not alter totals.
   Switching back to Markers retains filters and the selected region.

The feature was checked through these browser flows, domain tests and the web
build. Broader existing lint/design-audit findings are separate from this change.
