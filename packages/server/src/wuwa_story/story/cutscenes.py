"""Cutscene browsing without triggering exports or analysis."""

from collections import defaultdict

from sqlalchemy import select

from wuwa_story.agents.cutscene_vision import visual_reference
from wuwa_story.agents.evidence import quest_fingerprint
from wuwa_story.db.models.content import Document, DocumentHead
from wuwa_story.db.models.graph import Edge
from wuwa_story.db.models.i18n import Locale
from wuwa_story.db.models.ontology import RelationType
from wuwa_story.db.models.storage import FileReference
from wuwa_story.db.repositories import cutscenes as repository
from wuwa_story.story.service import _localized, prefetch_localizations


async def descriptions(session, owners, release_id, locale):
    """Only current analyses backed by the current visual observation may label a scene."""
    quests = {quest.node_id: quest for _, quest, _ in owners}
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


async def summaries(session, rows, release_id, locale):
    owner_rows = await repository.owners(session, [row.node_id for row in rows], release_id)
    await prefetch_localizations(session, [quest.name_key_id for _, quest, _ in owner_rows
                                           if quest.name_key_id], locale, release_id)
    owners = defaultdict(list)
    for scene_id, quest, slug in owner_rows:
        name = await _localized(session, quest.name_key_id, locale, release_id)
        owners[scene_id].append({"game_quest_id": quest.game_quest_id, "slug": slug,
                                 "name": (name or {}).get("text") or (name or {}).get("content")
                                 or f"Quest {quest.game_quest_id}"})
    titles = await descriptions(session, owner_rows, release_id, locale)
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
             "title": scene_titles.get(row.node_id) or (
                 owners[row.node_id][0]["name"] if owners[row.node_id] else f"Cutscene {row.cg_name}"),
             "title_kind": "analysis" if row.node_id in scene_titles else "quest" if owners[row.node_id] else "resource",
             "quests": owners[row.node_id]} for row in rows]
