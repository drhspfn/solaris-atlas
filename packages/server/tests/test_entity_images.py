from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story.storage import entity_media
from wuwa_story.storage.entity_media import entity_image_urls


@pytest.mark.asyncio
async def test_catalog_prefers_large_character_portrait_and_keeps_latest_item_icon(monkeypatch):
    monkeypatch.setattr(entity_media, "S3Storage", lambda _: SimpleNamespace(
        public_url=lambda key: "https://media.example/" + key))
    def row(owner, path, file_id):
        return (SimpleNamespace(owner_node_id=owner, source_path=path), SimpleNamespace(id=file_id),
                f"objects/{file_id}", f"{'character' if owner == 1 else 'item'}:{owner}")
    session = AsyncMock()
    session.execute.return_value = [
        row(1, "/Game/Other/Icon.png", 30),
        row(1, "/Game/IconRoleHead256/Large", 20),
        row(2, "/Game/Items/New", 40),
        row(2, "/Game/Items/Old", 10),
    ]
    assert await entity_image_urls(session, [1, 2]) == {1: "https://media.example/objects/20", 2: "https://media.example/objects/40"}


@pytest.mark.asyncio
async def test_empty_catalog_does_not_query_all_media():
    session = AsyncMock()
    assert await entity_image_urls(session, []) == {}
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_shared_resource_cannot_become_character_avatar_but_remains_valid_for_items(monkeypatch):
    monkeypatch.setattr(entity_media, "S3Storage", lambda _: SimpleNamespace(public_url=lambda key: key))
    session = AsyncMock()
    session.execute.return_value = [
        (SimpleNamespace(owner_node_id=1, source_path="/Game/UiQuest/Image/ChuanShuo/Default"), None, "wrong", "character:1"),
        (SimpleNamespace(owner_node_id=2, source_path="/Game/Common/IconRup/Material"), None, "item", "item:2"),
        (SimpleNamespace(owner_node_id=3, source_path="/Game/Common/T_ActivityRoleYangyang"), None, "foreign", "character:1409"),
        (SimpleNamespace(owner_node_id=4, source_path="/Game/Common/T_ActivityRoleYangyang"), None, "own", "character:1402"),
    ]
    assert await entity_image_urls(session, [1, 2, 3, 4]) == {2: "item", 4: "own"}
