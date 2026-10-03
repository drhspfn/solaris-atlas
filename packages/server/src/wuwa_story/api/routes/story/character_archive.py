"""Source-backed character dossier from an imported game snapshot."""

from collections.abc import Iterable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.api.routes.story.shared import _release_id, _version_key
from wuwa_story.config.settings import get_settings
from wuwa_story.db.models.core import Character, Item
from wuwa_story.db.models.graph import Node
from wuwa_story.db.models.i18n import Locale, LocalizationKey, LocalizationValue
from wuwa_story.db.models.ops import GameRelease
from wuwa_story.db.models.raw import SourceFile, SourceRecord
from wuwa_story.db.models.storage import FileLocation, FileObject, FileReference
from wuwa_story.db.session import get_session
from wuwa_story.storage.s3 import S3Storage

router = APIRouter(tags=["story browsing"])


async def _records(
    session: AsyncSession, release_id: int, path: str, field: str, value: int
) -> list[SourceRecord]:
    return list(await session.scalars(
        select(SourceRecord)
        .join(SourceFile, SourceFile.id == SourceRecord.source_file_id)
        .where(
            SourceRecord.release_id == release_id,
            SourceFile.logical_source_path == path,
            SourceRecord.data[field].as_integer() == value,
        )
        .order_by(SourceRecord.row_index)
    ))


async def _localized_strings(
    session: AsyncSession, release_id: int, locale_code: str, keys: Iterable[str | None]
) -> dict[str, str]:
    requested = {key for key in keys if isinstance(key, str) and key}
    if not requested:
        return {}
    locale_id = await session.scalar(select(Locale.id).where(Locale.code == locale_code))
    if locale_id is None:
        return {}
    requested_release = await session.get(GameRelease, release_id)
    if requested_release is None:
        return {}
    target_version = _version_key(requested_release.game_version)
    rows = await session.execute(
        select(LocalizationKey.key, LocalizationValue.content, GameRelease.game_version, GameRelease.sequence)
        .join(LocalizationValue, LocalizationValue.key_id == LocalizationKey.id)
        .join(GameRelease, GameRelease.id == LocalizationValue.release_id)
        .where(
            LocalizationKey.key.in_(requested),
            LocalizationValue.locale_id == locale_id,
            LocalizationValue.status == "resolved_nonempty",
        )
    )
    best: dict[str, tuple[tuple[int, ...], int, str]] = {}
    for key, content, version, sequence in rows:
        version_key = _version_key(version)
        if not content or version_key > target_version:
            continue
        candidate = (version_key, sequence, content)
        if key not in best or candidate[:2] > best[key][:2]:
            best[key] = candidate
    return {key: entry[2] for key, entry in best.items()}


def _source(record: SourceRecord, path: str) -> dict[str, Any]:
    return {"file": path, "row": record.row_index, "raw_path": f"$[{record.row_index}]"}


@router.get("/characters/{canonical_key:path}/archive")
async def character_archive(
    canonical_key: str,
    locale: str = "en",
    game_version: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    node = await session.scalar(select(Node).where(Node.canonical_key == canonical_key))
    if node is None or await session.get(Character, node.id) is None:
        raise HTTPException(status_code=404, detail="character not found")
    release_id = await _release_id(session, game_version)
    if release_id is None:
        raise HTTPException(status_code=404, detail="game version not imported")
    role_text = canonical_key.partition(":")[2]
    if not role_text.isdigit():
        raise HTTPException(status_code=404, detail="character has no game role ID")
    role_id = int(role_text)
    paths = {
        "role": "BinData/role/roleinfo.json",
        "favor": "BinData/favor/favorroleinfo.json",
        "skills": "BinData/skill/skill.json",
        "tree": "BinData/skillTree/skilltree.json",
        "chain": "BinData/resonate_chain/resonantchain.json",
        "voices": "BinData/favor/favorword.json",
        "element": "BinData/element_info/elementinfo.json",
        "quality": "BinData/role/rolequalityinfo.json",
        "role_audio": "BinData/audio/roleaudio.json",
    }
    role_rows = await _records(session, release_id, paths["role"], "Id", role_id)
    if not role_rows:
        raise HTTPException(status_code=404, detail="character source record not found")
    role = role_rows[0].data
    element_id = role.get("ElementId")
    element_rows = await _records(session, release_id, paths["element"], "Id", element_id if isinstance(element_id, int) else -1)
    quality_rows = await _records(session, release_id, paths["quality"], "Id", role.get("QualityId") or -1)
    element = element_rows[0].data if element_rows else {}
    quality = quality_rows[0].data if quality_rows else {}
    favor_rows = await _records(session, release_id, paths["favor"], "RoleId", role_id)
    skill_rows = await _records(session, release_id, paths["skills"], "SkillGroupId", role.get("SkillId") or role_id)
    tree_rows = await _records(session, release_id, paths["tree"], "NodeGroup", role.get("SkillTreeGroupId") or role_id)
    chain_rows = await _records(session, release_id, paths["chain"], "GroupId", role.get("ResonantChainGroupId") or role_id)
    voice_rows = await _records(session, release_id, paths["voices"], "RoleId", role_id)
    role_audio_rows = await _records(session, release_id, paths["role_audio"], "Id", role_id)
    cost_ids = {
        cost.get("Key") for row in tree_rows for cost in (row.data.get("Consume") or [])
        if isinstance(cost, dict) and isinstance(cost.get("Key"), int)
    }
    item_rows = await session.execute(
        select(Item.game_item_id, Item.canonical_name, Node.canonical_key, LocalizationKey.key)
        .join(Node, Node.id == Item.node_id)
        .outerjoin(LocalizationKey, LocalizationKey.id == Item.name_key_id)
        .where(Item.game_item_id.in_(cost_ids or {-1}))
    )
    cost_items = {item_id: {"fallback": name, "canonical_key": key, "name_key": name_key}
                  for item_id, name, key, name_key in item_rows}
    favor = favor_rows[0].data if favor_rows else {}
    text_keys = [role.get("Introduction"), element.get("Name"), quality.get("Name"), *(favor.get(field) for field in (
        "Info", "Country", "Birthday", "Influence", "TalentDoc", "TalentName",
        "CVNameCn", "CVNameEn", "CVNameJp", "CVNameKo",
    ))]
    for record in skill_rows:
        text_keys.extend((record.data.get("SkillName"), record.data.get("SkillDescribe")))
    for record in tree_rows:
        text_keys.extend((record.data.get("PropertyNodeTitle"), record.data.get("PropertyNodeDescribe")))
    text_keys.extend(item["name_key"] for item in cost_items.values())
    for record in chain_rows:
        text_keys.extend((record.data.get("NodeName"), record.data.get("AttributesDescription")))
    for record in voice_rows:
        text_keys.extend((record.data.get("Title"), record.data.get("Content")))
    localized = await _localized_strings(session, release_id, locale, text_keys)

    settings = get_settings()
    storage = S3Storage(settings)
    file_rows = await session.execute(
        select(FileReference, FileObject, FileLocation)
        .join(FileObject, FileObject.id == FileReference.file_id)
        .join(FileLocation, FileLocation.file_id == FileObject.id)
        .where(
            FileReference.owner_node_id == node.id,
            FileLocation.available.is_(True),
            FileLocation.is_primary.is_(True),
            FileLocation.backend == "s3",
            FileLocation.bucket == settings.s3_bucket,
        ).order_by(FileReference.id)
    )
    files: dict[tuple[str, str | None], tuple[str, str | None]] = {}
    for reference, file, location in file_rows:
        if reference.source_path:
            files[(reference.source_path, reference.source_name)] = (storage.public_url(location.object_key), file.mime_type)

    def media_url(path: str | None, mime_prefix: str, language: str | None = None) -> str | None:
        if not path:
            return None
        # Audio is language-specific. Never use an unlabelled voice file for another locale.
        linked = files.get((path, language)) if language else files.get((path, None))
        if linked is None:
            return None
        supported = {
            "image/": {"image/png", "image/jpeg", "image/webp", "image/avif", "image/gif"},
            "audio/": {"audio/mpeg", "audio/mp4", "audio/ogg", "audio/wav", "audio/webm", "audio/aac"},
        }
        return linked[0] if linked[1] in supported[mime_prefix] else None

    artwork_fields = ("RoleHeadIcon", "RoleHeadIconBig", "RoleHeadIconLarge", "RoleHeadIconCircle", "Card", "FormationRoleCard", "RolePortrait", "RoleStand", "Icon")
    artwork = [
        {"kind": field, "engine_path": role[field], "url": media_url(role[field], "image/")}
        for field in artwork_fields if isinstance(role.get(field), str) and role[field]
    ]
    skills = [
        {
            "id": row.data.get("Id"), "type": row.data.get("SkillType"),
            "order": row.data.get("SortIndex"), "name": localized.get(row.data.get("SkillName")),
            "description": localized.get(row.data.get("SkillDescribe")),
            "max_level": row.data.get("MaxSkillLevel"),
            "icon": {"engine_path": row.data.get("Icon"), "url": media_url(row.data.get("Icon"), "image/")},
            "source": _source(row, paths["skills"]),
        } for row in skill_rows
    ]
    tree = [
        {
            "id": row.data.get("Id"), "skill_id": row.data.get("SkillId"),
            "order": row.data.get("NodeIndex"), "type": row.data.get("NodeType"),
            "parent_nodes": row.data.get("ParentNodes") or [],
            "costs": [
                {
                    "item_id": cost.get("Key"), "count": cost.get("Value"),
                    "item": {
                        "canonical_key": item["canonical_key"],
                        "name": localized.get(item["name_key"]) or item["fallback"],
                    } if (item := cost_items.get(cost.get("Key"))) else None,
                }
                for cost in (row.data.get("Consume") or []) if isinstance(cost, dict)
            ],
            "title": localized.get(row.data.get("PropertyNodeTitle")),
            "description": localized.get(row.data.get("PropertyNodeDescribe")),
            "source": _source(row, paths["tree"]),
        } for row in tree_rows
    ]
    chains = [
        {
            "id": row.data.get("Id"), "order": row.data.get("GroupIndex"),
            "name": localized.get(row.data.get("NodeName")),
            "description": localized.get(row.data.get("AttributesDescription")),
            "description_params": row.data.get("AttributesDescriptionParams") or [],
            "icon": {"engine_path": row.data.get("NodeIcon"), "url": media_url(row.data.get("NodeIcon"), "image/")},
            "source": _source(row, paths["chain"]),
        } for row in chain_rows
    ]
    voices = [
        {
            "id": row.data.get("Id"), "order": row.data.get("Sort"),
            "type": row.data.get("Type"), "title": localized.get(row.data.get("Title")),
            "text": localized.get(row.data.get("Content")),
            "audio_event_path": row.data.get("Voice") or None,
            "audio_url": media_url(row.data.get("Voice"), "audio/", locale),
            "source": _source(row, paths["voices"]),
        } for row in voice_rows
    ]
    other_voice_events = []
    if role_audio_rows:
        for field, value in role_audio_rows[0].data.items():
            if field in {"Id", "Name"}:
                continue
            values = value if isinstance(value, list) else [value]
            for entry in values:
                event = entry.get("Value") if isinstance(entry, dict) else entry
                if isinstance(event, str) and event and event not in {item["event"] for item in other_voice_events}:
                    other_voice_events.append({"category": field, "event": event})
    return {
        "canonical_key": canonical_key, "locale": locale, "release_id": release_id,
        "biography": localized.get(role.get("Introduction")) or localized.get(favor.get("Info")),
        "profile": {
            "country": localized.get(favor.get("Country")),
            "birthday": localized.get(favor.get("Birthday")),
            "affiliation": localized.get(favor.get("Influence")),
            "talent": localized.get(favor.get("TalentName")),
            "talent_description": localized.get(favor.get("TalentDoc")),
            "voice_actors": {
                "zh-Hans": localized.get(favor.get("CVNameCn")),
                "en": localized.get(favor.get("CVNameEn")),
                "ja": localized.get(favor.get("CVNameJp")),
                "ko": localized.get(favor.get("CVNameKo")),
            },
            "max_level": role.get("MaxLevel"), "element_id": role.get("ElementId"),
            "weapon_type_id": role.get("WeaponType"), "quality_id": role.get("QualityId"),
            "element": localized.get(element.get("Name")),
            "quality": localized.get(quality.get("Name")),
        },
        "artwork": artwork, "skills": skills, "skill_tree": tree,
        "resonance_chain": chains, "voice_lines": voices,
        "other_voice_events": other_voice_events,
        "source": _source(role_rows[0], paths["role"]),
    }


@router.get("/media/files/{file_id}")
async def character_media_file(
    file_id: int, session: AsyncSession = Depends(get_session)
) -> RedirectResponse:
    file = await session.get(FileObject, file_id)
    if file is None or not (file.mime_type or "").startswith(("image/", "audio/")):
        raise HTTPException(status_code=404, detail="media file not found")
    public_reference = await session.scalar(
        select(FileReference.id).where(
            FileReference.file_id == file_id,
            FileReference.owner_node_id.is_not(None),
            FileReference.release_id.is_not(None),
            FileReference.source_path.like("/Game/%"),
        ).limit(1)
    )
    if public_reference is None:
        raise HTTPException(status_code=404, detail="media file not found")
    settings = get_settings()
    location = await session.scalar(
        select(FileLocation).where(
            FileLocation.file_id == file_id,
            FileLocation.backend == "s3",
            FileLocation.bucket == settings.s3_bucket,
            FileLocation.available.is_(True),
        ).order_by(FileLocation.is_primary.desc(), FileLocation.id).limit(1)
    )
    if location is None:
        raise HTTPException(status_code=404, detail="media file not available")
    storage = S3Storage(settings)
    # Preserve old links without proxying bytes or breaking media Range requests.
    return RedirectResponse(storage.public_url(location.object_key), status_code=307,
                            headers={"Cache-Control": "no-cache"})
