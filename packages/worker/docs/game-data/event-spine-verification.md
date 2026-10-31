# Event source verification, 2026-11-01

Pinned server client: 3.7.0. An isolated 4 GiB diagnostic container used the
branch's archive-index, prefab decoder and dependency walker. A five-root
walk completed with 150 distinct decoded packages and zero reported missing
references among the dependency types supported by that walker. This does not
assert support for ignored dependency types or visual equivalence.

| Event / screen | Decoded packages | Result |
| --- | ---: | --- |
| Cubie Wars guide | 55 | Spine `DangoBrawlGuide`; real portable source archive 13,281,068 bytes; Riglyph archive 9,142,979 bytes; `idle`, `start` |
| Waking Moon Fishing background | 7 | Spine `Waking_Moon_Fishing`; source archive 9,541,612 bytes; Riglyph archive 6,574,064 bytes; `idle` |
| Endstate Matrix entry | 51 | UI sequences, materials, texture layers; no Spine in this entry's dependency closure |
| Echo Erase entry | 37 | UI sequences, materials, texture layers; no Spine in this entry's dependency closure |
| Beyond the Waves info | 95 | UI sequences, materials, texture layers; no Spine in this info screen's dependency closure |

The matrix's main screen is a separate composition: the actual
`UiView_MowingTowerActivityMain32` prefab references `UiItem_BossBgDaniya`.
The latter was independently extracted and archived (4,323,914 bytes). It has
two rigs and two texture pages: `PnlFullBg` and `MowingTower_boss_35`.
Both converted successfully through published `@riglyph/cli` 0.15.1. The
converted archive is 2,735,511 bytes; the background has `idle` and `idle2`,
and the character has `idle`.

All four actual rig conversions (Cubie, Fishing, matrix background, matrix
character) reported Spine 4.1.23, no losses and no unsupported features.
Riglyph still marks numerical/visual parity unverified. The multi-component
container preserves each rig's own manifest and source identity; it explicitly
requires authored composition controls rather than inventing a layer order.

Changes resulting from verification:

* Batch package exports while keeping separate decode caches per package.
* Include the matrix controller's exact untagged entry resource
  `UiItem_ActivityMowingTower2`; module-tag joins alone missed that row.
* Event Spine archives start at their prefabs and retain sequence sources,
  rather than starting only at the discovered skeleton packages.
* Convert multi-rig archives as separately identified Riglyph components.

The diagnostic source and converted archives are retained locally under
`scratch/spine-event-verification`. The source archives also remain in the
server client workspace. This verification did not deploy public playback,
publish these event archives into R2, or demonstrate browser rendering. An
event with UI sequences/materials cannot be reproduced by the Spine runtime
alone; those source controls require their own playback implementation.
