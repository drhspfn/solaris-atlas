"""Cutscene browsing without triggering exports or analysis."""

from collections import defaultdict

from sqlalchemy import BigInteger, and_, or_, select
from sqlalchemy.orm import aliased

from wuwa_story.agents.cutscene_vision import visual_reference
from wuwa_story.agents.evidence import quest_fingerprint
from wuwa_story.config.settings import get_settings
from wuwa_story.db.models.content import Document, DocumentHead
from wuwa_story.db.models.graph import Edge, EdgeEvidence
from wuwa_story.db.models.i18n import Locale
from wuwa_story.db.models.ontology import RelationType
from wuwa_story.db.models.storage import FileLocation, FileReference
from wuwa_story.db.repositories import cutscenes as repository
from wuwa_story.storage.s3 import S3Storage
from wuwa_story.story.service import _localized, prefetch_localizations


async def previews(session, scene_ids, release_id):
    """Resolve only previews tied to the currently published video and snapshot edges."""
    if not scene_ids:
        return {}
    variants, assets = aliased(Edge), aliased(Edge)
    variant_type, asset_type = aliased(RelationType), aliased(RelationType)
    video = aliased(FileReference)
    latest_video = (select(video.file_id).where(
        video.owner_node_id == assets.to_node_id, video.reference_type == "cutscene_video")
        .order_by(video.id.desc()).limit(1).correlate(assets).scalar_subquery())
    settings = get_settings()
    rows = await session.execute(
        select(variants.from_node_id, FileReference.file_id,
               FileLocation.object_key)
        .join(variant_type, variant_type.id == variants.relation_type_id)
        .join(assets, assets.from_node_id == variants.to_node_id)
        .join(asset_type, asset_type.id == assets.relation_type_id)
        .join(FileReference, FileReference.owner_node_id == assets.to_node_id)
        .join(FileLocation, FileLocation.file_id == FileReference.file_id)
        .where(variants.from_node_id.in_(scene_ids), variants.layer == "source", assets.layer == "source",
               variant_type.key == "has_variant", asset_type.key == "references_asset",
               select(EdgeEvidence.id).where(EdgeEvidence.edge_id == variants.id,
                                             EdgeEvidence.release_id == release_id).exists(),
               select(EdgeEvidence.id).where(EdgeEvidence.edge_id == assets.id,
                                             EdgeEvidence.release_id == release_id).exists(),
               FileReference.reference_type == "cutscene_preview",
               FileReference.metadata_json["source_file_id"].as_integer(
        ) == latest_video,
            FileLocation.backend == "s3", FileLocation.bucket == settings.s3_bucket,
            FileLocation.available.is_(True), FileLocation.is_primary.is_(True))
        .order_by(FileReference.metadata_json["score"].as_integer().desc(), FileReference.id.desc())
    )
    storage = S3Storage(settings)
    result = {}
    for scene_id, file_id, key in rows:
        entry = result.setdefault(scene_id, {"preview_file_id": file_id, "preview_url": storage.public_url(key),
                                             "previews": []})
        if len(entry["previews"]) < 5 and not any(item["file_id"] == file_id for item in entry["previews"]):
            entry["previews"].append(
                {"file_id": file_id, "url": storage.public_url(key)})
    return result


async def descriptions(session, owners, release_id, locale):
    """Only current analyses backed by the current visual observation may label a scene."""
    quests = {quest.node_id: quest for _, quest, _, _ in owners}
    if not quests:
        return {}
    documents = await session.scalars(
        select(Document).join(DocumentHead,
                              DocumentHead.document_id == Document.id)
        .join(Locale, Locale.id == Document.locale_id)
        .where(Document.node_id.in_(quests), Locale.code == locale,
               Document.document_type == f"story-explanation:{release_id}")
    )
    valid = {}
    for document in documents:
        entries = document.metadata_json.get("cutscene_descriptions", [])
        if not entries or document.source_hash != await quest_fingerprint(
            session, quests[document.node_id], release_id, include_visual=True
        ):
            continue
        for entry in entries:
            # A matching current report proves which exported video was observed.
            report = await session.get(FileReference, entry["visual_reference_id"])
            if report is None:
                continue
            version = report.metadata_json.get("asset_version")
            current = await visual_reference(session, entry["asset_node_id"], version) if version else None
            if current and current.id == report.id:
                valid[entry["asset_node_id"]] = entry
    return valid


async def visual_titles(session, scene_ids, release_id, locale):
    """Resolve English visual titles even when no quest association exists."""
    if not scene_ids or locale != "en":
        return {}
    variants, assets = aliased(Edge), aliased(Edge)
    variant_type, asset_type = aliased(RelationType), aliased(RelationType)
    video = aliased(FileReference)
    latest_video = (select(video.file_id).where(
        video.owner_node_id == assets.to_node_id, video.reference_type == "cutscene_video")
        .order_by(video.id.desc()).limit(1).correlate(assets).scalar_subquery())
    report = aliased(FileReference)
    latest_report = (select(report.id).where(
        report.owner_node_id == assets.to_node_id, report.reference_type == "cutscene_visual_description",
        report.file_id == latest_video, report.metadata_json["complete"].as_boolean().is_(True))
        .order_by(report.id.desc()).limit(1).correlate(assets).scalar_subquery())
    rows = await session.execute(
        select(variants.from_node_id, FileReference.metadata_json["title"].as_string())
        .join(variant_type, variant_type.id == variants.relation_type_id)
        .join(assets, assets.from_node_id == variants.to_node_id)
        .join(asset_type, asset_type.id == assets.relation_type_id)
        .join(FileReference, FileReference.owner_node_id == assets.to_node_id)
        .where(variants.from_node_id.in_(scene_ids), variants.layer == "source", assets.layer == "source",
               variant_type.key == "has_variant", asset_type.key == "references_asset",
               select(EdgeEvidence.id).where(EdgeEvidence.edge_id == variants.id,
                                             EdgeEvidence.release_id == release_id).exists(),
               select(EdgeEvidence.id).where(EdgeEvidence.edge_id == assets.id,
                                             EdgeEvidence.release_id == release_id).exists(),
               FileReference.file_id == latest_video,
               or_(and_(FileReference.reference_type == "cutscene_visual_description",
                        FileReference.id == latest_report),
                   and_(FileReference.reference_type == "cutscene_visual_title",
                        FileReference.metadata_json["source_report_id"].as_string().cast(BigInteger) == latest_report)),
               FileReference.metadata_json["title_language"].as_string() == locale)
        .order_by(FileReference.id.desc())
    )
    result = {}
    for scene_id, title in rows:
        if isinstance(title, str) and title.strip():
            result.setdefault(scene_id, title.strip())
    return result


async def summaries(session, rows, release_id, locale):
    owner_rows = await repository.owners(session, [row.node_id for row in rows], release_id)
    await prefetch_localizations(session, [quest.name_key_id for _, quest, _, _ in owner_rows
                                           if quest.name_key_id], locale, release_id)
    owners = defaultdict(list)
    for scene_id, quest, slug, inferred in owner_rows:
        name = await _localized(session, quest.name_key_id, locale, release_id)
        owners[scene_id].append({"game_quest_id": quest.game_quest_id, "slug": slug,
                                 "context_inferred": inferred,
                                 "name": (name or {}).get("text") or (name or {}).get("content")
                                 or f"Quest {quest.game_quest_id}"})
    titles = await descriptions(session, owner_rows, release_id, locale)
    images = await previews(session, [row.node_id for row in rows], release_id)
    visual = await visual_titles(session, [row.node_id for row in rows], release_id, locale)
    scene_titles = {}
    if titles:
        variants, assets = Edge.__table__.alias(
            "variants"), Edge.__table__.alias("assets")
        variant_type, asset_type = RelationType.__table__.alias(
            "vt"), RelationType.__table__.alias("at")
        for scene_id, asset_id in await session.execute(
            select(variants.c.from_node_id, assets.c.to_node_id)
            .join(variant_type, variant_type.c.id == variants.c.relation_type_id)
            .join(assets, assets.c.from_node_id == variants.c.to_node_id)
            .join(asset_type, asset_type.c.id == assets.c.relation_type_id)
            .where(variants.c.from_node_id.in_([row.node_id for row in rows]),
                   variant_type.c.key == "has_variant", asset_type.c.key == "references_asset",
                   variants.c.layer == "source", assets.c.layer == "source",
                   assets.c.to_node_id.in_(titles))
        ):
            scene_titles.setdefault(scene_id, titles[asset_id]["title"])
    return [{"node_id": row.node_id, "name": row.cg_name,
             "title": scene_titles.get(row.node_id) or visual.get(row.node_id) or (
                 owners[row.node_id][0]["name"] if owners[row.node_id] else f"Cutscene {row.cg_name}"),
             "title_kind": "analysis" if row.node_id in scene_titles or row.node_id in visual else "quest" if owners[row.node_id] else "resource",
             "quests": owners[row.node_id], **images.get(row.node_id, {})} for row in rows]
