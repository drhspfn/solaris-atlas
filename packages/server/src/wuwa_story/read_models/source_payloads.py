"""Assemble localized cards and compact page payloads for background publication."""

import re
from html import unescape

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select

from wuwa_story.api.routes import cutscenes, nodes, notices
from wuwa_story.api.routes.story import (
    character_archive,
    profiles,
    shared,
)
from wuwa_story.api.routes.story import (
    map as story_map_routes,
)
from wuwa_story.db.models.core import (
    Character,
    Cutscene,
)
from wuwa_story.db.models.graph import Node, NodeType
from wuwa_story.db.models.notices import GameNotice
from wuwa_story.db.models.search import EntityAlias
from wuwa_story.read_models import source_relations as relations
from wuwa_story.read_models import source_sections as sections
from wuwa_story.story import service
from wuwa_story.urls import node_address


def _searchable_text(kind, title, card, payload, version=None):
    """Index displayed localized words, never recursively index raw/config JSON."""
    words = []

    def add(value):
        if isinstance(value, dict):
            text_version = value.get("source_game_version")
            if (
                version
                and isinstance(text_version, str)
                and shared._version_key(text_version) > shared._version_key(version)
            ):
                raise ValueError("Searchable text comes from a future localization snapshot")
            value = value.get("content") or value.get("inline_text")
        if not isinstance(value, str) or value.startswith(
            ("/Game/", "BinData/", "http://", "https://", "/api/")
        ):
            return
        text = " ".join(re.sub(r"<[^>]*>", " ", unescape(value)).split())
        if text:
            words.append(text)

    add(title)
    for alias in card.get("aliases", []):
        add(alias)
    profile = payload.get("profile", {})
    entity = profile.get(kind, {})
    add(entity.get("description"))
    if kind == "character":
        archive = payload.get("archive", {})
        add(archive.get("biography"))
        for key in ("country", "affiliation", "talent", "talent_description"):
            add(archive.get("profile", {}).get(key))
        for group, fields in (
            ("skills", ("name", "description")),
            ("skill_tree", ("title", "description")),
            ("resonance_chain", ("name", "description")),
            ("voice_lines", ("title", "text")),
        ):
            for row in archive.get(group, []):
                for field in fields:
                    add(row.get(field))
    elif kind == "quest":
        for line in payload.get("transcript", {}).get("lines", []):
            add(line.get("text"))
    elif kind == "dialogue":
        add(payload.get("dialogue", {}).get("text"))
    elif kind == "game_notice":
        for block in payload.get("blocks", []):
            if block.get("type") == "text":
                add(block.get("text"))
    elif kind == "game_event":
        for row in [payload.get("event", {}), *payload.get("occurrences", [])]:
            add(row.get("title"))
            add(row.get("description"))
    elif kind == "cutscene":
        add(payload.get("title"))
        add(payload.get("description"))
    return "\n".join(dict.fromkeys(words))


async def _notice_payload(session, entity_id):
    """Materialize revision field overrides without copying unchanged notice bodies."""
    current = sections._public_json(
        jsonable_encoder(
            await notices.notice_detail(str(entity_id), revision=None, session=session)
        )
    )
    snapshots = {}
    for revision in dict.fromkeys(current.get("revisions", [])):
        if revision == current.get("revision"):
            continue
        snapshot = sections._public_json(
            jsonable_encoder(
                await notices.notice_detail(str(entity_id), revision=revision, session=session)
            )
        )
        # The public service emits the same fields for current and revision
        # responses. An overlay cannot represent a removed field safely.
        if snapshot.keys() != current.keys():
            raise ValueError("Notice revision response fields differ from current response")
        snapshots[revision] = {
            key: value for key, value in snapshot.items() if value != current[key]
        }
    return {
        **current,
        "revision_snapshot_format": "overrides-v1",
        "revision_snapshots": snapshots,
    }


async def _build_entity(session, kind, entity_id, locale, snapshot_key):
    version = None if snapshot_key == "current" else snapshot_key
    artwork = []
    canonical_key = None
    card = {"node_type": kind, "aliases": []}
    if kind == "story_map":
        if type(entity_id) is not int or entity_id < 1:
            raise ValueError("Story-map entity IDs must be quest_type_id + 1")
        payload = await story_map_routes.story_map(
            locale=locale.code, game_version=version, quest_type_id=entity_id - 1, session=session
        )
        entity = {"slug": None, "href": "/story-map"}
        title, primary_url = "Story map", None
    elif kind == "game_event":
        payload = await sections._event_payload(session, entity_id, locale.code, snapshot_key)
        card["calendar"] = sections._calendar_payload(payload)
        entity = payload["event"]
        for run in payload["occurrences"]:
            for art in run.get("artwork", []):
                artwork.append(
                    {**art, "server": run["server"], "occurrence_id": run["occurrence_id"]}
                )
        primary_url = next(
            (
                run.get("preview_url") or run.get("banner_url")
                for run in payload["occurrences"]
                if run.get("preview_url") or run.get("banner_url")
            ),
            entity.get("preview_url") or entity.get("banner_url"),
        )
        if not payload["occurrences"]:
            artwork = entity.get("artwork", [])
        title = entity["title"]
    elif kind == "game_notice":
        record = await session.get(GameNotice, entity_id)
        if record is None:
            raise HTTPException(404, "Announcement not found")
        if record.locale != locale.code:
            raise ValueError("A notice cannot be projected as a different locale")
        payload = await _notice_payload(session, entity_id)
        card["notice"] = {
            field: payload.get(field)
            for field in (
                "id",
                "slug",
                "href",
                "source_id",
                "locale",
                "category",
                "title",
                "revision",
                "banner_url",
                "source_url",
                "available_from",
                "observed_at",
                "related_event_ids",
            )
        }
        entity = payload
        primary_url = payload.get("banner_url")
        artwork = ([{"url": primary_url, "kind": "banner"}] if primary_url else []) + [
            block for block in payload.get("blocks", []) if block.get("type") == "image"
        ]
        title = entity["title"]
    else:
        node = await session.get(Node, entity_id)
        node_type = await session.get(NodeType, node.type_id) if node else None
        if node is None or node_type is None or node_type.key not in relations._NODE_KINDS[kind]:
            raise HTTPException(404, f"{kind} not found")
        key = canonical_key = node.canonical_key
        release_id = await service._release_id(session, version)
        if release_id is None:
            raise ValueError(f"Snapshot not imported: {snapshot_key}")
        if kind == "character":
            payload = {
                "profile": await profiles.character_profile(
                    key, locale=locale.code, game_version=version, session=session
                ),
                "archive": await character_archive.character_archive(
                    key, locale=locale.code, game_version=version, session=session
                ),
            }
        elif kind == "item":
            payload = {
                "profile": await profiles.item_profile(
                    key, locale=locale.code, game_version=version, session=session
                )
            }
        elif kind == "location":
            payload = {
                "profile": await profiles.location_profile(key, locale=locale.code, session=session)
            }
        elif kind == "quest":
            payload = await sections._quest_payload(session, key, locale.code, version)
        elif kind == "cutscene":
            scene = await session.get(Cutscene, entity_id)
            if scene is None or not scene.cg_name:
                raise HTTPException(404, "cutscene has no public name")
            catalog = await sections._cutscene_catalog(session, entity_id, locale.code, version)
            card["catalog"] = catalog
            payload = {
                **await cutscenes.detail(
                    scene.cg_name,
                    game_version=catalog["item"]["game_version"],
                    locale=locale.code,
                    session=session,
                ),
                "catalog": catalog,
            }
        elif kind == "dialogue":
            payload = await nodes.node_narrative_context(
                key, locale=locale.code, game_version=version, session=session
            )
        else:
            payload = await sections._node_payload(session, key, locale.code, entity_id)
        profile = payload.get("profile", payload)
        source_version = (profile.get("source") or {}).get("version")
        if (
            version
            and isinstance(source_version, str)
            and shared._version_key(source_version) > shared._version_key(version)
        ):
            raise ValueError(
                "Public source profile selected a future revision for a historical snapshot"
            )
        entity = profile.get(kind) or {**node_address(node, node_type.key), "slug": node.slug}
        card.update(
            node_type=node_type.key,
            href=node_address(node, node_type.key)["href"],
            aliases=entity.get("aliases", []),
            status=getattr(node, "status", "active"),
        )
        if snapshot_key == "current":
            aliases = await session.scalars(
                select(EntityAlias.alias)
                .where(
                    EntityAlias.node_id == node.id,
                    EntityAlias.locale_id == locale.id,
                )
                .order_by(EntityAlias.id)
            )
            card["aliases"] = list(
                dict.fromkeys(
                    alias
                    for alias in [*card["aliases"], *aliases]
                    if isinstance(alias, str) and alias
                )
            )
        if kind == "character":
            character = await session.get(Character, node.id)
            card["role_type"] = (
                (character.metadata_json or {}).get("role_type") if character else None
            )
        elif kind == "quest":
            quest_type = entity.get("quest_type")
            card["quest_type"] = str(quest_type) if quest_type is not None else None
        label = await service._node_label(session, node.id, locale.code)
        title = sections._label(
            entity.get("name"), sections._label(entity.get("canonical_name"), label["label"] or key)
        )
        artwork = await sections._artwork(session, node, kind)
        primary_url = profile.get("media", {}).get("image_url")
        if kind == "cutscene":
            title = sections._label(payload.get("title"), title)
            primary_url = payload.get("preview_url")
            artwork = payload.get("previews", []) + artwork
        elif kind == "dialogue":
            line = payload.get("dialogue", {})
            title = sections._label(line.get("text"), title)
            card.update(
                flow_state=line.get("flow_state"),
                action_index=(line.get("action") or {}).get("index"),
                source_index=line.get("source_index"),
                **await relations._dialogue_filters(session, entity_id, release_id),
                raw_refs=(await sections._raw_refs(session, [key], release_id)).get(
                    key, sections._empty_raw_refs()
                ),
            )
            if "dialogue" in payload:
                payload = {**payload, "dialogue": {**line, "raw_refs": card["raw_refs"]}}
        if kind == "character":
            files = {
                (art.get("engine_path"), art.get("url")): art.get("file_id") for art in artwork
            }
            artwork = [
                {**art, "file_id": files.get((art.get("engine_path"), art.get("url")))}
                for art in payload["archive"].get("artwork", [])
            ]
            primary_url = next(
                (
                    art["url"]
                    for art in artwork
                    if art.get("kind") == "RoleHeadIconLarge"
                    and art.get("url")
                    and art.get("scope") != "shared_resource"
                ),
                None,
            )
        primary_url = primary_url or next(
            (
                art.get("url")
                for art in artwork
                if art.get("url") and art.get("scope") != "shared_resource"
            ),
            None,
        )
    if kind in relations._NODE_KINDS and snapshot_key == "current" and kind != "speaker":
        payload = {**payload, **await sections._node_payload(session, key, locale.code, entity_id)}
    card.setdefault("href", entity.get("href") or f"/notices/{entity_id}")
    card["alias_keys"] = list(
        dict.fromkeys(
            normalized
            for alias in card["aliases"]
            if isinstance(alias, str) and (normalized := " ".join(alias.casefold().split()))
        )
    )
    payload = {**payload, "card": card}
    entry = {
        "entity_id": entity_id,
        "locale_id": locale.id,
        "snapshot_key": snapshot_key,
        "title": title,
        "sort_title": title.casefold(),
        "searchable_text": _searchable_text(kind, title, card, payload, version),
        "canonical_key": canonical_key,
        "slug": entity.get("slug"),
        "primary_image_url": primary_url,
        "primary_image_file_id": next(
            (
                art.get("file_id")
                for art in artwork
                if primary_url and art.get("url") == primary_url
            ),
            None,
        ),
        "artwork": artwork,
        "card": card,
        "role_type": card.get("role_type"),
        "quest_type": card.get("quest_type"),
        "payload": payload,
    }
    return sections._public_json(jsonable_encoder(sections._compact_storage(entry)))
