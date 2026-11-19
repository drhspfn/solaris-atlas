from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from wuwa_story.api.routes.story import profiles


@pytest.mark.asyncio
async def test_unlinked_story_speaker_opens_standalone_profile(monkeypatch):
    """A dialogue "Spoken by" speaker without a character crosswalk must still have a profile."""
    speaker = SimpleNamespace(id=7872446, canonical_key="speaker:1696", slug="7872446-cassandra")
    requested_types = []

    async def fake_get_node(session, key, types=()):
        requested_types.append(types)
        return speaker if "speaker" in types else None

    session = MagicMock()
    session.scalars = AsyncMock(return_value=[])
    session.scalar = AsyncMock(return_value=None)  # no references_character edge / entity link
    session.execute = AsyncMock(return_value=[])
    session.get = AsyncMock(side_effect=lambda model, ident: (
        SimpleNamespace(game_speaker_id=1696, name_key_id=None) if model is profiles.Speaker else None))
    monkeypatch.setattr(profiles, "get_node", fake_get_node)
    monkeypatch.setattr(profiles, "_release_id", AsyncMock(return_value=1))
    monkeypatch.setattr(profiles, "_explicit_entity_links", AsyncMock(return_value=[]))

    result = await profiles.character_profile("7872446-cassandra", session=session)

    assert "speaker" in requested_types[0]
    assert result["character"]["canonical_key"] == "speaker:1696"
    assert result["character"]["name"] == "Cassandra"
    assert result["speakers"][0]["game_speaker_id"] == 1696
