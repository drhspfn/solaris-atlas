from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image
from sqlalchemy import select

from test_visual_memory_service import memory  # noqa: F401
from wuwa_story.api.routes import visual_memory
from wuwa_story.auth.dependencies import require_admin, require_csrf
from wuwa_story.db.models import Character, Node, NodeType
from wuwa_story.db.session import get_session
from wuwa_story.visual_memory.game_identity import is_rover, rover_identity
from wuwa_story.visual_memory.images import encode_png


def test_rover_requires_game_family_not_a_similar_name():
    assert not is_rover(SimpleNamespace(canonical_name="Rover", metadata_json={"role_type": 6}))


@pytest.mark.asyncio
async def test_rover_reuses_existing_identity_and_preserves_approved_references(memory, monkeypatch):  # noqa: F811
    service, session, actor = memory
    node_type = await session.scalar(select(NodeType.id).where(NodeType.key == "character"))
    key = "character:visual-rover-" + uuid4().hex

    async def member(suffix, gender, element, role_type=1):
        node = Node(type_id=node_type, canonical_key=key + suffix)
        session.add(node)
        await session.flush()
        character = Character(node_id=node.id, canonical_name="Rover: " + element,
                              metadata_json={"role_type": role_type, "public_identity": {
                                  "family": "rover", "canonical_key": key,
                                  "gender": gender, "element_id": element,
                              }})
        session.add(character)
        await session.flush()
        return character

    male = await member("", "male", "spectro")
    female = await member("-female", "female", "aero")
    story = await member("-story", "male", "spectro", 5)
    legacy = await service.create_identity("Rover: Spectro", "playable", node_ids=[male.node_id])
    ref = await service.add_reference(encode_png(Image.new("RGB", (24, 32), "blue")),
                                      links=[{"identity_id": legacy["id"]}], actor_id=actor)
    await service.review_reference(ref["id"], "approved", actor_id=actor)
    api = FastAPI()
    api.include_router(visual_memory.router)
    api.dependency_overrides[get_session] = lambda: session
    api.dependency_overrides[require_admin] = lambda: SimpleNamespace(id=actor)
    api.dependency_overrides[require_csrf] = lambda: None
    monkeypatch.setattr(visual_memory, "service_for", lambda _: service)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
        endpoint = "/admin/visual-memory/identities/from-game"
        first = await client.post(endpoint, json={"node_id": female.node_id})
        assert first.status_code == 200, first.text
        result = first.json()
        assert result["id"] == legacy["id"] and result["name"] == "Rover"
        assert set(result["node_ids"]) == {male.node_id, female.node_id, story.node_id}
        assert {a["name"] for a in result["appearances"]} == {"Male Rover", "Female Rover"}
        for character in (male, female, story):
            response = await client.post(endpoint, json={"node_id": character.node_id})
            assert response.status_code == 200
            assert response.json() == result
    kept = await service.get_reference(ref["id"])
    assert kept["status"] == "approved" and kept["enabled_for_search"]
    assert kept["links"][0]["identity_id"] == legacy["id"]
    assert kept["links"][0]["appearance_id"] is None
    # A new element joins the same identity and keeps both existing appearance IDs.
    future = await member("-future", "female", "future-element")
    updated = await rover_identity(session, future, service)
    assert updated["id"] == result["id"] and future.node_id in updated["node_ids"]
    assert updated["appearances"] == result["appearances"]


@pytest.mark.asyncio
async def test_game_picker_offers_two_appearances_for_one_rover(memory, monkeypatch):  # noqa: F811
    _, session, _ = memory
    from unittest.mock import AsyncMock

    from wuwa_story.api.routes import search

    node_type = await session.scalar(select(NodeType.id).where(NodeType.key == "character"))
    node = Node(type_id=node_type, canonical_key="character:visual-picker-" + uuid4().hex)
    session.add(node)
    await session.flush()
    session.add(Character(node_id=node.id, canonical_name="Rover: Spectro", metadata_json={
        "role_type": 1, "public_identity": {"family": "rover", "canonical_key": node.canonical_key}}))
    await session.flush()
    monkeypatch.setattr(search, "search", AsyncMock(return_value={"results": [
        {"id": node.id, "title": "Rover: Spectro", "character_kind": "playable"}]}))
    result = await visual_memory.game_entities(q="Rover", locale="en", limit=20, offset=0, session=session)
    assert result["items"][0]["name"] == "Rover"
    assert result["items"][0]["appearance_choices"] == [
        {"key": "male", "name": "Male Rover"}, {"key": "female", "name": "Female Rover"}]
