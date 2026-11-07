"""Backfill is a metadata-only transaction and does not create media tasks."""
from types import SimpleNamespace as Row
from unittest.mock import AsyncMock

import pytest

from wuwa_story.ingestion import character_identity_backfill as module
from wuwa_story.ingestion.character_identity import MAIN_ROLE_PATH


@pytest.mark.asyncio
async def test_backfill_is_idempotent_and_requests_only_current_character_pages(monkeypatch):
    character = Row(metadata_json={"game_character_id": 1501, "role_type": 1})
    node = Row(id=12, status="active")
    absent = Row(metadata_json={"game_character_id": 8888, "public_identity": {"family": "rover"}, "shared_artwork_paths": ["old-foreign-image"]})
    absent_metadata = dict(absent.metadata_json)
    npc = Row(metadata_json={"game_character_id": 1501, "role_type": 6, "npc": {}})
    npc_metadata = dict(npc.metadata_json)
    role = {"Id": 1501, "ElementId": 5, "RoleType": 1}
    rows = [(MAIN_ROLE_PATH, {"Id": 1501, "Gender": 1, "UnlockCondition": 0}),
            ("BinData/role/roleinfo.json", role)]
    session = Row(scalar=AsyncMock(return_value=21),
                  execute=AsyncMock(), commit=AsyncMock())

    async def execute(statement):
        query = str(statement)
        if query.startswith("SET LOCAL"):
            return []
        if "raw.source_record" in query:
            return rows
        if "core.character" in query:
            return [(character, node), (absent, Row(id=99, status="inactive")),
                    (npc, Row(id=77, status="active"))]
        raise AssertionError(query)
    session.execute.side_effect = execute

    class Context:
        async def __aenter__(self): return session
        async def __aexit__(self, *_): return None
    monkeypatch.setattr(module, "SessionFactory", Context)
    dirty = AsyncMock()
    monkeypatch.setattr(module, "mark_dirty", dirty)
    preview = await module.backfill()
    assert preview["changed"] == 1
    assert "public_identity" not in character.metadata_json
    session.commit.assert_not_awaited()
    applied = await module.backfill(apply=True)
    assert applied["changed_ids"] == [12]
    assert absent.metadata_json == absent_metadata
    assert npc.metadata_json == npc_metadata
    dirty.assert_awaited_once_with(session, "character", {12})
    dirty.reset_mock()
    assert (await module.backfill(apply=True))["changed"] == 0
    dirty.assert_not_awaited()
    await module.backfill(apply=True, refresh_all=True)
    dirty.assert_awaited_once_with(session, "character", {12})


@pytest.mark.asyncio
async def test_artwork_repair_preserves_non_rover_public_family(monkeypatch):
    family = {"family": "character:1603", "canonical_key": "character:1603"}
    character = Row(metadata_json={"game_character_id": 5038, "public_identity": family})
    rows = [(MAIN_ROLE_PATH, {}), ("BinData/role/roleinfo.json", {"Id": 5038, "RoleType": 2})]
    session = Row(scalar=AsyncMock(return_value=21), execute=AsyncMock(), commit=AsyncMock())

    async def execute(statement):
        query = str(statement)
        if query.startswith("SET LOCAL"):
            return []
        if "raw.source_record" in query:
            return rows
        return [(character, Row(id=12, status="active"))]

    session.execute.side_effect = execute
    class Context:
        async def __aenter__(self): return session
        async def __aexit__(self, *_): return None
    monkeypatch.setattr(module, "SessionFactory", Context)
    monkeypatch.setattr(module, "main_role_identities", lambda *_: {1501: {"family": "rover"}})
    monkeypatch.setattr(module, "mark_dirty", AsyncMock())
    result = await module.backfill(apply=True, refresh_all=True)
    assert result["changed"] == 0
    assert character.metadata_json["public_identity"] == family
