# Recurring activity runtime evidence (client 3.8.0)

This records client behavior, not a live server schedule. The examined HD download
has ID `fc3c8e67db4fa81e12e4c53be909abc963231aefe4fbf5d071004e818215b009`.
Protocol member names below are generated/obfuscated and are specific to this
client build. Wire field numbers come from its generated `Protocol.js`.

## Common activity data

`ActivityController.RequestActivityData` calls game message 16076 using the
empty `v$n` request after function 10053 is open. Response `p$n` has repeated
`Yps` activity records (field 6) and result `Q4n` (field 10). It passes `Yps`
to `ActivityModel.OnReceiveMessageData`. Registered game notifications 17864
and 18605 update and disable activities respectively. This is the game's `Net`
transport, not a verified public HTTP endpoint; the source connects it to the
normal login/session state. No session data was collected.

Each activity record `fks` supplies `s5n` activity ID (field 1), `h5n` type
(2), show start/end `wps`/`xps` (3/4), open start/end `Pps`/`Ups` (5/6),
unlock `K6n` (7), open type `OS_` (33), pre-open `lk_` (42), limit start/end
`CPs`/`gPs` (67/68), and reward start/end `umd`/`cmd` (69/70).
`ActivityBaseData.Init` and `Phrase` copy these fields. Time comparisons use
server time in seconds; zero endpoints and the forced-close pair `(-1, -1)` have
special behavior. `ActivityModel.GetTimeVisibleAndRemainTime` also consults
open type, `TimeIsDisplay`, show/open/limit status, and localization. A parser
must preserve distinct windows, flags, server and observation provenance; it
must not turn missing, zero, or forced-close values into ordinary timestamps.

## Endstate Matrix: activity 100770002, type 77

`ActivityManager` maps `Proto_NewTowerClimbing` to `WheelTowerController`.
The controller selects `UiView_MowingTowerActivityMain32` for the new view and
`UiItem_WheelTowerActivity` for its old-view branch. The 3.8 `UiResource` rows
explicitly tag both keys `NewTowerClimbing`. Their normal entry prefabs are:

- `/Game/Aki/UI/UIResources/UiActivity/Prefabs/Activity32/MowingTower/ActivityMain/UiView_MowingTowerActivityMain32`
- `/Game/Aki/UI/UIResources/UiActivity/Prefabs/ActivityMowingTower/MowingTower30/UiItem_MowingTowerActivityMain30`

The activity's `fks.Vif` nested message (field 83) is parsed by
`WheelTowerData.PhraseEx`. `Vif.bN_` (field 1) is cycle ID; `RN_` (5) and
`XDg` (6) are cycle begin/end; `UHn` (7) is season ID; `HnC` (8) and `jnC`
(9) are season begin/end. `WheelTowerNewSubView.RefreshUnlockTimeText` uses
`WheelTowerModel.GetNextCycleRemainTime`, based on cycle begin. Its timer asks
for activity data again when its refresh condition holds. The common activity
display time is separately computed through `ActivityModel`.

`NewTowerSeason.Id` is the exact season-key lookup boundary. For example, the
3.8 catalog's season 2 row has version range 3.5–3.8 and provides
`BossBgPrefab=/Game/Aki/UI/UIResources/UiActivity/Prefabs/Activity35/MowingTower/BossBg/UiItem_BossBgDaniya`
and `BossPortrait=/Game/Aki/UI/UIResources/UiActivity/Image/Activity35/MowingTower/AllSeason/BossTex/T_AllSeasonBossTex_S2`.
The version range alone does not prove which season the server currently sent,
nor that these assets are the activity-card background. Decode the view and
its dynamic bindings before assigning a card image.

## Whimpering Wastes: activity 100390001, type 39

`Proto_SlashAndTowerLevelPlay` maps to `ActivityShipTowerController`, whose
entry key is `UiItem_ActivityMowingTower2`; its row is explicitly tagged
`SlashAndTowerLevelPlay`. The normal prefab is
`/Game/Aki/UI/UIResources/UiActivity/Prefabs/ActivityMowingTower/UiItem_ActivityMowingTower`.
Its references include both `T_ActivityMowingTowerMainBg1` and `...Bg2` under
`/Game/Aki/UI/UIResources/Common/Image/BgCgBig/`; reference presence alone
does not establish which layer the view displays.

`ShipTowerController.SlashAndTowerInfoRequest` sends message 21662 (`Cy_`)
and processes `gy_`. Its `dG_` field 12 is the season-end time used by
`ShipTowerModel.GetRemainTime`. The model derives current season from the
returned `BL_` stage records and stage configuration. Notifications 18154 and
17309 update level and cycle state. These are game-session messages.

## Hazard Revisited: activity 100300002, type 21

`Proto_TowerGuideNew` maps to `ActivityLoopTowerController`, not the similarly
named older `ActivityTowerGuideController`. Its entry key is
`UiItem_ActivityLoopTower`, whose normal prefab is
`/Game/Aki/UI/UIResources/UiActivity/Prefabs/ActivityLoopTower/UiItem_ActivityLoopTower`.
That prefab references `/Game/Aki/UI/UIResources/Common/Image/BgCgBig/T_ActivityLoopTowerBg`;
that reference alone is not proof of a complete composed card.

`TowerController` requests tower info with message 26406 (`XCs`), whose `YCs`
response contains `UGs` at field 2. `TowerModel.RefreshTowerInfo` reads
`UGs.EGs` current season (field 1), `yGs` data season (2), `cps` start (4),
and `dps` end (5). `ActivitySubViewLoopTower` renders its countdown from
`TowerEndTime`, populated by `dps`. Notifications 27219, 24752, 17128 and
28183 are registered by the controller; the specific update path must be
traced before treating every notification as a schedule change.

## Integration boundary

A useful first implementation is a version-pinned parser for an **authorized,
already-decoded** activity response and the two tower-info responses. It should
validate activity ID/type and required payload variants; retain raw field values,
server, download/version, observed time, and response/notification provenance;
represent absence and sentinel states explicitly; and select static season rows
by the received season ID, never by latest catalog version. UI should show an
unknown or stale state when live data or a verified view binding is unavailable.
This repository does not yet have a verified authorized producer for these
messages. Do not silently replace them with notice windows or estimated cycles.
