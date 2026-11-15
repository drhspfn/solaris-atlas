from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story.api.routes.story import profiles
from wuwa_story.search.indexer import MAX_UPSERT_CHUNK_ROWS, _document, _upsert_batch


@pytest.mark.asyncio
async def test_upsert_batch_splits_chunks_under_asyncpg_param_limit():
    assert MAX_UPSERT_CHUNK_ROWS * 9 < 32767
    executed_statements = []

    async def fake_execute(statement):
        executed_statements.append(statement)
        return SimpleNamespace(rowcount=1)

    session = SimpleNamespace(
        execute=AsyncMock(side_effect=fake_execute),
        commit=AsyncMock(),
    )
    batch = [_document(i, 1, f"text {i}", "dialogue") for i in range(MAX_UPSERT_CHUNK_ROWS + 50)]
    written = await _upsert_batch(session, batch)

    assert len(executed_statements) == 2
    assert written == 2
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_character_profile_does_not_raise_unbound_edge_for_non_speaker(monkeypatch):
    character = SimpleNamespace(
        node_id=1,
        canonical_name="Name",
        name_key_id=None,
        description=None,
        playable=True,
        metadata_json={},
    )
    session = SimpleNamespace(
        get=AsyncMock(return_value=character),
        execute=AsyncMock(side_effect=[[], [], []]),
        scalars=AsyncMock(side_effect=[[], [], []]),
        scalar=AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        profiles,
        "get_node",
        AsyncMock(return_value=SimpleNamespace(id=1, canonical_key="character:1", slug=None)),
    )
    monkeypatch.setattr(profiles, "_release_id", AsyncMock(return_value=5))
    monkeypatch.setattr(profiles, "prefetch_node_labels", AsyncMock())
    monkeypatch.setattr(profiles, "_localized", AsyncMock(return_value=None))
    monkeypatch.setattr(profiles, "_latest_source_record", AsyncMock(return_value=None))
    monkeypatch.setattr(profiles, "_character_progression_materials", AsyncMock(return_value=[]))

    result = await profiles.character_profile.__wrapped__("character:1", session=session)
    assert isinstance(result, dict)
    assert "character" in result
