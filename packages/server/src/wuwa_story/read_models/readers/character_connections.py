"""Bounded public connection views over an already-published character profile.

The source graph and full evidence remain available through the original profile
API. Browsing a character never rebuilds that graph or sends all transcripts.
"""

from types import SimpleNamespace

from fastapi import HTTPException

from wuwa_story.api.pagination import cursor_scope, decode_cursor, encode_cursor
from wuwa_story.db.models.browse import CharacterRead
from wuwa_story.read_models.context import should_route_to_projection
from wuwa_story.read_models.readers import entities


def _text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return next((value[k] for k in ("content", "label", "inline_text")
                     if isinstance(value.get(k), str)), "")
    return ""


def _target(value):
    if not isinstance(value, dict):
        return {}
    result = {key: value[key] for key in (
        "id", "canonical_key", "game_quest_id", "href", "slug", "node_type",
        "quest_type", "game_version", "first_seen_version",
    ) if key in value}
    for key in ("label", "name", "title"):
        if key in value:
            result[key] = _text(value[key])
    if isinstance(value.get("classification"), dict):
        result["classification"] = {k: value["classification"][k] for k in ("kind", "label")
                                    if k in value["classification"]}
    return result


def _quest(value):
    return _target(value.get("quest", value)) if isinstance(value, dict) else {}


def _scene(value):
    lines = value.get("dialogue") or []
    return {
        "flow_state": value.get("flow_state"),
        "quest_refs": [_quest(q) for q in value.get("quest_refs", [])],
        "dialogue": [{"id": line.get("id"), "speaker": _target(line.get("speaker")),
                      "text": _text(line.get("text"))} for line in lines[:3]],
        "dialogue_truncated": bool(value.get("dialogue_truncated") or len(lines) > 3),
        "basis": value.get("basis"),
    }


def connection_rows(profile, section):
    """Deduplicate source-backed entries without treating co-presence as a relationship."""
    if section == "quests":
        rows = [_quest(row) for row in profile.get("quests_with_dialogue", [])]
        unique = {}
        for row in rows:
            key = row.get("canonical_key") or row.get(
                "game_quest_id") or row.get("href")
            if key is not None:
                unique.setdefault(str(key), row)
        return list(unique.values())
    if section != "characters":
        raise HTTPException(422, "Unknown character connection section")
    own = {profile.get("character", {}).get("canonical_key")}
    own.update(v.get("canonical_key")
               for v in (profile.get("family") or {}).get("variants", []))
    own.update(v.get("canonical_key") for v in profile.get("speakers", []))
    unique = {}
    for row in profile.get("co_present_characters", []):
        target = _target(row.get("character"))
        key = target.get("canonical_key")
        if not key or key in own:
            continue
        scenes = row.get("shared_scenes") or []
        unique.setdefault(("co_present", key), {
            "target": target, "relation": "Shared authored scene",
            "basis": row.get("basis"), "flow_states": (row.get("flow_states") or [])[:2],
            "shared_scenes": [_scene(scene) for scene in scenes[:2]],
            "shared_scenes_total": row.get("shared_scenes_total", len(scenes)),
            "shared_scenes_truncated": bool(row.get("shared_scenes_truncated") or len(scenes) > 2),
            "meaning": row.get("meaning"),
            "_search": " ".join(_text(q.get("name")) for scene in scenes
                                for q in scene.get("quest_refs", [])),
        })
    for row in profile.get("explicit_links", []):
        target = _target(row.get("target"))
        key = target.get("canonical_key")
        if not key or key in own:
            continue
        relation = row.get("relation") or "Source reference"
        unique.setdefault((relation, key), {"target": target, "relation": relation,
                                            "basis": row.get("basis")})
    return list(unique.values())


def _search_text(row):
    values = [_text(row.get(key))
              for key in ("name", "title", "label", "_search")]
    target = row.get("target") or {}
    values.extend(_text(target.get(key)) for key in ("name", "title", "label"))
    for scene in row.get("shared_scenes", []):
        values.extend(_text(q.get("name"))
                      for q in scene.get("quest_refs", []))
    return " ".join(values).casefold()


def page_connections(profile, section, *, identity, generation, locale="en", snapshot="current",
                     limit=12, q="", cursor=None):
    if not 1 <= limit <= 50 or len(q) > 200:
        raise HTTPException(422, "Invalid character connection pagination")
    query = " ".join(q.casefold().split())
    scope = cursor_scope("character_connections", identity,
                         section, locale, snapshot, query)
    after = 0
    if cursor:
        try:
            revision, after = decode_cursor(cursor, scope, (int, int))
        except ValueError as error:
            raise HTTPException(400, "Invalid connection cursor") from error
        if revision != generation:
            raise HTTPException(
                409, "Connections were updated; restart from the first page")
    rows = connection_rows(profile, section)
    if query:
        rows = [row for row in rows if query in _search_text(row)]
    if after < 0 or after > len(rows):
        raise HTTPException(400, "Invalid connection cursor position")
    visible = [{k: v for k, v in row.items() if not k.startswith("_")}
               for row in rows[after:after + limit]]
    more = after + len(visible) < len(rows)
    return {"results": visible, "total": len(rows), "has_more": more,
            "next_cursor": encode_cursor([generation, after + len(visible)], scope) if more else None}


async def _profile(session, identity, locale, game_version):
    snapshot = game_version or "current"
    if should_route_to_projection("character", game_version, locale=locale):
        coverage = await entities._require_scope(session, "character", snapshot)
        statement = entities._identity_statement(
            CharacterRead, identity, await entities._locale_id(session, locale), snapshot,
            (CharacterRead.entity_id, CharacterRead.canonical_key, CharacterRead.generation,
             CharacterRead.payload["profile"].label("payload")),
        )
        rows = (await session.execute(statement)).mappings().all()
        if not rows:
            entities._missing(coverage)
        entry = entities._resolve([SimpleNamespace(**row)
                                  for row in rows], identity)
        return entry.payload, entry.canonical_key, entry.generation
    # Explicit historical/non-projected requests retain the existing source API.
    from wuwa_story.api.routes.story.profiles import character_profile

    profile = await character_profile(identity, locale=locale, game_version=game_version, session=session)
    canonical = profile.get("character", {}).get("canonical_key", identity)
    keys = [[row.get("canonical_key") or row.get("target", {}).get("canonical_key")
             for row in connection_rows(profile, section)] for section in ("quests", "characters")]
    generation = int(cursor_scope(profile.get("source"), keys)[:12], 16)
    return profile, canonical, generation


async def character_connection_page(session, identity, section, *, locale="en", game_version=None,
                                    limit=12, q="", cursor=None):
    profile, canonical, generation = await _profile(session, identity, locale, game_version)
    return entities.ProjectedPayload(page_connections(
        profile, section, identity=canonical, generation=generation, locale=locale,
        snapshot=game_version or "current", limit=limit, q=q, cursor=cursor,
    ))


async def compact_character_profile(session, identity, locale="en", game_version=None, limit=12):
    if not 1 <= limit <= 50:
        raise HTTPException(422, "Invalid character connection pagination")
    profile, canonical, generation = await _profile(session, identity, locale, game_version)
    pages = {section: page_connections(profile, section, identity=canonical,
                                       generation=generation, locale=locale,
                                       snapshot=game_version or "current", limit=limit)
             for section in ("quests", "characters")}
    large_fields = {"quests_with_dialogue", "co_present_characters",
                    "co_present_speakers", "explicit_links"}
    compact = {key: value for key,
               value in profile.items() if key not in large_fields}
    mapped = {row.get("character", {}).get("canonical_key")
              for row in profile.get("co_present_characters", [])}
    unresolved = sum(not any(target.get("canonical_key") in mapped
                             for target in row.get("character_entities", []))
                     for row in profile.get("co_present_speakers", []))
    compact.update({
        "quests_with_dialogue": pages["quests"]["results"],
        "connections_preview": pages,
        "connections_summary": {section: {k: v for k, v in page.items() if k != "results"}
                                for section, page in pages.items()},
        "unresolved_speakers_total": unresolved,
    })
    return entities.ProjectedPayload(compact)
