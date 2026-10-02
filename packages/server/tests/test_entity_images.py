from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story.storage.entity_media import entity_image_urls


@pytest.mark.asyncio
async def test_catalog_prefers_large_character_portrait_and_keeps_latest_item_icon():
    def row(owner, path, file_id):
        return SimpleNamespace(owner_node_id=owner, source_path=path), SimpleNamespace(id=file_id)
    session = AsyncMock()
    session.execute.return_value = [
        row(1, "/Game/Other/Icon.png", 30),
        row(1, "/Game/IconRoleHead256/Large", 20),
        row(2, "/Game/Items/New", 40),
        row(2, "/Game/Items/Old", 10),
    ]
    assert await entity_image_urls(session, [1, 2]) == {1: "/api/media/files/20", 2: "/api/media/files/40"}


@pytest.mark.asyncio
async def test_empty_catalog_does_not_query_all_media():
    session = AsyncMock()
    assert await entity_image_urls(session, []) == {}
    session.execute.assert_not_called()
