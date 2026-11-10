"""Published model bundles; never project private source metadata into public responses."""

from sqlalchemy import func, select

from wuwa_story.db.models.core import Character
from wuwa_story.db.models.i18n import Locale, LocalizationKey, LocalizationValue
from wuwa_story.db.models.ops import GameRelease
from wuwa_story.db.models.raw import SourceFile, SourceRecord
from wuwa_story.db.models.storage import FileLocation, FileObject, FileReference
from wuwa_story.ingestion.character_identity import MAIN_ROLE_PATH, main_role_identities
from wuwa_story.story.domain import version_key

REFERENCE_TYPES = {
    "character_model_preview": "preview",
    "character_model_download": "download",
    "character_model_physics": "physics",
    "character_model_animations": "animations",
    "character_model_facial": "facial",
    "character_model_animation_manifest": "animation_manifest",
}


async def model_recipe(session, release_id, character_id):
    async def rows(path, field=None, values=()):
        query = select(SourceRecord.data).join(SourceFile).where(
            SourceRecord.release_id == release_id, SourceFile.logical_source_path == path)
        if field is not None:
            query = query.where(SourceRecord.data[field].astext.in_(
                [str(value) for value in values if value]))
        return list(await session.scalars(query.order_by(SourceRecord.row_index)))

    roles = await rows("BinData/role/roleinfo.json", "Id", [character_id])
    if len(roles) != 1:
        raise ValueError("Character model source is missing or ambiguous in this snapshot")
    role = roles[0]
    # The authored main-role table, not name/ID heuristics, defines Rover's
    # genders and elements. Keep both genders; identical element meshes are
    # deduplicated by the worker after resolving all source records.
    main_roles = await rows(MAIN_ROLE_PATH)
    identities = {}
    if any(row.get("Id") == character_id for row in main_roles):
        roles = await rows("BinData/role/roleinfo.json", "Id", [row.get("Id") for row in main_roles])
        identities = main_role_identities(main_roles, roles)
        identity = identities.get(character_id)
        roles = [item for item in roles if identity and identities.get(item["Id"], {}).get(
            "canonical_key") == identity["canonical_key"]] or [role]
        if identity is None:
            identities = {}
    role_ids = [item["Id"] for item in roles]
    morphs = await rows("BinData/role/rolemorph.json", "RoleId", role_ids)
    skins = await rows("BinData/role/roleskin.json", "RoleId", role_ids)
    preloads = await rows("BinData/model_config_preload/modelconfigpreload.json", "Id", {
        mesh for row in [*roles, *morphs, *skins] for mesh in (row.get("MeshId"), row.get("UiMeshId"))
    })
    keys = {row.get("TitleName") for row in skins if row.get("TitleName")}
    labels = dict((await session.execute(select(LocalizationKey.key, LocalizationValue.content)
        .join(LocalizationValue, LocalizationValue.key_id == LocalizationKey.id)
        .join(Locale, Locale.id == LocalizationValue.locale_id)
        .where(LocalizationValue.release_id == release_id, Locale.code == "en",
               LocalizationKey.key.in_(keys)))).all()) if keys else {}
    return {"role": role, "preloads": preloads, "morphs": morphs, "skins": skins,
            "skin_labels": labels,
            **({"roles": [{**item, "model_gender": identities[item["Id"]]["gender"]}
                          for item in roles]} if identities else {})}


async def npc_model_recipe(session, release_id, owner_node_id, blueprint_id):
    # Character metadata is current, not a release-versioned source record.
    # Refuse historical recipes rather than silently assigning current assets to them.
    latest = await model_release(session)
    if latest is None or latest.id != release_id:
        raise ValueError("NPC model metadata is only available for the latest snapshot")
    character = await session.get(Character, owner_node_id)
    npc = (character.metadata_json or {}).get("npc") if character else None
    if not isinstance(npc, dict) or npc.get("blueprint_id") != blueprint_id:
        raise ValueError("NPC model source is missing or does not match this character")
    return {"npc": npc}


async def model_release(session, game_version=None):
    query = select(GameRelease)
    if game_version is not None:
        query = query.where(GameRelease.game_version == game_version)
    return max(
        await session.scalars(query),
        key=lambda release: (version_key(release.game_version), release.sequence),
        default=None,
    )


async def published_model_releases(session, owner_node_id):
    """Find snapshots with model publications in publication order."""
    bundle = FileReference.metadata_json["model_bundle_id"].astext
    return list(await session.scalars(
        select(GameRelease).join(FileReference, FileReference.release_id == GameRelease.id)
        .where(FileReference.owner_node_id == owner_node_id,
               FileReference.reference_type.in_(("character_model_preview", "character_model_download")),
               bundle.is_not(None))
        .group_by(GameRelease.id, bundle)
        .having(func.bool_or(FileReference.reference_type == "character_model_preview"),
                func.bool_or(FileReference.reference_type == "character_model_download"))
        .order_by(func.max(FileReference.id).desc())
    ))


async def published_model_files(session, owner_node_id, release_id, bucket):
    return list(
        await session.execute(
            select(
                FileReference.reference_type,
                FileReference.metadata_json["model_bundle_id"].astext.label("model_bundle_id"),
                FileLocation.object_key,
                FileObject.size_bytes,
                FileReference.metadata_json["viewer_object_key"].astext.label("viewer_object_key"),
                FileReference.metadata_json["form_id"].label("form_id"),
                FileReference.metadata_json["form_label"].label("form_label"),
                FileReference.metadata_json["form_order"].label("form_order"),
                FileReference.metadata_json["component"].label("component"),
                FileReference.metadata_json["scene"].label("scene"),
                FileReference.metadata_json["animation_pack"].label("animation_pack"),
            )
            .join(FileObject, FileObject.id == FileReference.file_id)
            .join(FileLocation, FileLocation.file_id == FileObject.id)
            .where(
                FileReference.owner_node_id == owner_node_id,
                FileReference.release_id == release_id,
                FileReference.reference_type.in_((*REFERENCE_TYPES, "character_model_animation_pack")),
                FileLocation.backend == "s3",
                FileLocation.bucket == bucket,
                FileLocation.available.is_(True),
                FileLocation.is_primary.is_(True),
            )
            .order_by(FileReference.id.desc())
        )
    )
