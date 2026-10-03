# Client map sources (3.7.0)

The map pipeline reads the downloaded client's ConfigDB and Unreal textures. It does not substitute tiles or positions from a community map.

## Source records

| Input | Use |
| --- | --- |
| `db_mapfog.db / fogtextureconfig` | Tile block X/Y, game map ID, gravity variant, texture resource ID |
| `db_ui_resource.db / uiresource` | Resource ID to Unreal asset path |
| `db_map.db / multimap` | Floor layer ID, group, floor number, gravity and tile resources |
| `db_level_entity.db / levelentityconfig` | Entity ID, map ID, blueprint, transform and component overrides |

BinData is decoded with FlatBuffers. Field offsets were checked against the matching client `FogTextureConfig.js`, `MultiMap.js`, `LevelEntityConfig.js`, `UiResource.js`, and `SubType/IntVector.js` readers. Other versions fail explicitly until their schemas and transforms are checked.

## Coordinates

Client `Game/Module/Map/MapDefine.js`: `DETAIL_TILE_REALSIZE=850`, `UNIT=100`. A tile spans **85,000 Unreal world units**. `MapUtil.GetTilePosition` uses `ceil(worldX/85000)`, `ceil(-worldY/85000)`. `MapTileMgr` places its center at `(tileX-.5)*850`, `(tileY-.5)*850` in UI coordinates. UI Y points up, whereas PNG Y points down: raster tile rows run from maximum tile Y to minimum tile Y.

For raster tile size `S`:

```text
originWorldX = (minTileX - 1) * 85000
originWorldY = -maxTileY * 85000
pixelX = (worldX - originWorldX) * S / 85000
pixelY = (worldY - originWorldY) * S / 85000
tilePixelX = (tileX - minTileX) * S
tilePixelY = (maxTileY - tileY) * S
```

`Game/World/Model/CreatureModel.js` divides each ConfigDB transform vector by **100** before using world positions. Keeping the encoded integer without that division would displace markers by two orders of magnitude.

Most selected textures are 1024×1024. Two JH floor textures are 1028×1024; the client renders these in the same square tile item. Previews reproduce that stretch and tile metadata retains the actual texture dimensions. Overview size is bounded to 4096 pixels per dimension; this does not downsample the stored original tile files.

## Limits

- A shared world map can contain geographically separated regions; their source coordinates are preserved. No arbitrary regional offsets are introduced.
- Floors are independent map layers. Original gravity IDs are retained; entities are not assigned to gravity/floor variants without evidence.
- Chest and collectible categories use blueprint names, with Unclaimed Rafter Kite placements additionally identified by BaseInfoComponent.MapIcon = 15 from templates and entity overrides. Their names, descriptions and icons come from the type-only mapmark record; positions and hidden flags come from each level entity. This is a placement inventory, not a confirmed completion checklist. Conditional, dormant, hidden and template placements can appear. Component overrides are retained for later classification.
- Marker endpoints return the game map's placements and explicitly report unresolved floor assignment. They support world bounds, category, hidden flag and cursor pagination.
- Source game map IDs are not conflated with existing story `core.location` identities.
- Database rollback removes only the new spatial tables. Uploaded content is retained; automatic garbage collection is not included.


## Item acquisition links

Published marker metadata includes `drop_item_ids` and `drop_source` when the
merged template/placement RewardComponent has a supported RewardType (0 or 2),
is enabled, and its RewardId resolves to DropPackage.DropPreview. These are
possible rewards; a zero preview quantity does not mean guaranteed zero drops,
and probabilities or final quantities are not inferred. The item profile joins
only the same map asset job and world, choosing the latest published version.
`/map?map=45&item=41100012&source=100564` identifies one authored source type via
a numeric representative marker; filtering still checks each placement's exact
item/reward reference. All matching placements are fitted in the viewport.

For existing published maps, update only source metadata without decoding tiles:

```powershell
uv run --project packages/worker --env-file packages/worker/.env wuwa-story-worker refresh-map-sources CLIENT_ROOT
```

The refresh verifies every source hash in the published manifest, uses the same
asset-job lock as map publication, and preserves unrelated marker metadata. The
local 3.7 refresh updated 28,844 markers. MF Whisperin Core resolves 46 source
groups and its 1.1 shop offer resolves once to Weapon Shop; source-shop tables
are scoped to the requested story snapshot. No exact ShopInfo.Id-to-map-marker
join was found for shop 101, so its position is not inferred from the display name.


Verification: 18 targeted server/worker/frontend tests passed; two existing
infrastructure-gated map tests skipped. Frontend build and targeted lint passed
(existing WorldMapPage hook warnings remain). Browser checks covered region
disclosures, Weapon Shop name/price/limit and an acquisition link selecting 68
Whiff Whaff placements with a matching 68-location menu count. The strict design
auditor reports 16 existing native-select/form/vendor-source findings outside
this acquisition change; it is not a clean repository-wide audit.
