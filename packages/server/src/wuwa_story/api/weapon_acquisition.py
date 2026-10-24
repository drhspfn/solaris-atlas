"""Exact banner references, not full loot tables, probabilities, or availability.

Each result describes one GachaViewInfo row. ``roles``/``evidence`` describe
only exact list membership; ``pool_links`` contain only fully resolved joins.
Unknown banner types stay unknown. Titles use only the requested release/locale.
"""

from collections.abc import Iterable
from typing import Any

from sqlalchemy import Select, or_, select
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.db.models.i18n import Locale, LocalizationKey, LocalizationValue
from wuwa_story.db.models.ops import GameRelease
from wuwa_story.db.models.raw import SourceFile, SourceRecord

VIEW = "BinData/gacha/gachaviewinfo.json"
POOL = "BinData/gacha/gachapool.json"
GACHA = "BinData/gacha/gacha.json"
MEMBERSHIP_FIELDS = (
    ("UpList", "featured"),
    ("PreviewIdList", "preview"),
    ("ShowIdList", "display"),
)
# Observed in release 5 (2.0.0), view 32: explicit permanent weapon artwork.
# Type=5 alone is not an enum definition and is deliberately not mapped.
PERMANENT_WEAPON_TEXTURE = (
    "/Game/Aki/UI/UIResources/Common/Image/Luckdraw/"
    "T_LuckdrawTextPermanent_W.T_LuckdrawTextPermanent_W"
)
SourceRow = (
    tuple[SourceRecord, SourceFile, GameRelease] | Row[tuple[SourceRecord, SourceFile, GameRelease]]
)


def _source(row: SourceRow) -> dict[str, Any]:
    record, file, release = row
    return {
        "record_id": record.id,
        "file": file.logical_source_path,
        "row": record.row_index,
        "source_key": record.source_key,
        "content_hash": record.content_hash.hex(),
        "release_id": record.release_id,
        "game_version": release.game_version,
        "resource_version": release.resource_version,
    }


def classify_weapon_banner_sources(
    rows: Iterable[SourceRow], item_id: int, release_id: int
) -> list[dict[str, Any]]:
    """Classify exact raw references without SQL or inferred loot membership.

    Rows are (SourceRecord, SourceFile, GameRelease) triples. Only numeric JSON
    IDs and the three allowlisted source paths participate; source_key, nested
    fields, filenames, and numeric prefixes are never ID evidence.
    """
    if type(item_id) is not int:
        return []
    tables: dict[str, list[SourceRow]] = {VIEW: [], POOL: [], GACHA: []}
    for row in rows:
        record, file, release = row
        if (
            record.release_id == file.release_id == release.id == release_id
            and record.source_file_id == file.id
            and file.logical_source_path in tables
            and type(record.data.get("Id")) is int
        ):
            tables[file.logical_source_path].append(row)

    gachas: dict[int, list[SourceRow]] = {}
    pools: dict[int, list[SourceRow]] = {}
    for path, index in ((GACHA, gachas), (POOL, pools)):
        for row in tables[path]:
            index.setdefault(row[0].data["Id"], []).append(row)

    results = []
    for row in sorted(tables[VIEW], key=lambda row: (row[0].data["Id"], row[0].id)):
        raw = row[0].data
        evidence = [
            {"field": field, "role": role, "value": item_id}
            for field, role in MEMBERSHIP_FIELDS
            if isinstance(raw.get(field), list)
            and any(type(value) is int and value == item_id for value in raw[field])
        ]
        if not evidence:
            continue
        links = []
        for pool_row in sorted(pools.get(raw["Id"], []), key=lambda row: row[0].id):
            gacha_id = pool_row[0].data.get("GachaId")
            if type(gacha_id) is not int:
                continue
            for gacha_row in sorted(gachas.get(gacha_id, []), key=lambda row: row[0].id):
                links.append(
                    {
                        "pool_id": pool_row[0].data["Id"],
                        "gacha_id": gacha_id,
                        "pool_source": _source(pool_row),
                        "gacha_source": _source(gacha_row),
                        "basis": "GachaViewInfo.Id = GachaPool.Id; GachaPool.GachaId = Gacha.Id",
                    }
                )
        permanent = raw.get("TextTexture") == PERMANENT_WEAPON_TEXTURE
        results.append(
            {
                "kind": "weapon_banner",
                "item_id": item_id,
                "view_id": raw["Id"],
                "type_id": raw.get("Type"),
                "banner_type": "permanent_weapon" if permanent else "unknown",
                "banner_type_evidence": (
                    {
                        "field": "TextTexture",
                        "value": PERMANENT_WEAPON_TEXTURE,
                        "basis": "explicit permanent weapon artwork",
                    }
                    if permanent
                    else None
                ),
                "roles": [entry["role"] for entry in evidence],
                "evidence": evidence,
                "source": _source(row),
                "pool_links": links,
                "title_key": raw.get("SummaryTitle"),
                "title": None,
            }
        )
    return results


def _records(release_id: int, path: str) -> Select[tuple[SourceRecord, SourceFile, GameRelease]]:
    return (
        select(SourceRecord, SourceFile, GameRelease)
        .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
        .join(GameRelease, GameRelease.id == SourceRecord.release_id)
        .where(
            SourceRecord.release_id == release_id,
            SourceFile.release_id == release_id,
            SourceFile.logical_source_path == path,
        )
        .order_by(SourceRecord.row_index, SourceRecord.id)
    )


async def weapon_banner_sources(
    session: AsyncSession, item_id: int, release_id: int, locale: str
) -> list[dict[str, Any]]:
    """Fetch matching views, exact pool/gacha links, and optional titles in batches.

    At most four SELECTs, independent of the number of matched banners. No
    fallback across releases or locales, and no external banner URL requests.
    """
    if type(item_id) is not int:
        return []
    views = list(
        await session.execute(
            _records(release_id, VIEW).where(
                or_(
                    *(
                        SourceRecord.data.contains({field: [item_id]})
                        for field, _ in MEMBERSHIP_FIELDS
                    )
                )
            )
        )
    )
    view_ids = [row[0].data["Id"] for row in views if type(row[0].data.get("Id")) is int]
    if not view_ids:
        return []
    pools = list(
        await session.execute(
            _records(release_id, POOL).where(
                or_(*(SourceRecord.data.contains({"Id": identity}) for identity in set(view_ids)))
            )
        )
    )
    gacha_ids = [row[0].data["GachaId"] for row in pools if type(row[0].data.get("GachaId")) is int]
    gachas = (
        list(
            await session.execute(
                _records(release_id, GACHA).where(
                    or_(
                        *(
                            SourceRecord.data.contains({"Id": identity})
                            for identity in set(gacha_ids)
                        )
                    )
                )
            )
        )
        if gacha_ids
        else []
    )
    results = classify_weapon_banner_sources([*views, *pools, *gachas], item_id, release_id)
    keys = {result["title_key"] for result in results if isinstance(result["title_key"], str)}
    if keys:
        titles = await session.execute(
            select(
                LocalizationKey.key, LocalizationValue.content, LocalizationValue.source_record_id
            )
            .join(LocalizationValue, LocalizationValue.key_id == LocalizationKey.id)
            .join(Locale, Locale.id == LocalizationValue.locale_id)
            .where(
                LocalizationKey.key.in_(keys),
                LocalizationValue.release_id == release_id,
                Locale.code == locale,
            )
        )
        by_key = {
            key: {
                "content": content,
                "locale": locale,
                "release_id": release_id,
                "source_record_id": source_record_id,
            }
            for key, content, source_record_id in titles
            if content is not None
        }
        for result in results:
            if isinstance(result["title_key"], str):
                result["title"] = by_key.get(result["title_key"])
    return results
