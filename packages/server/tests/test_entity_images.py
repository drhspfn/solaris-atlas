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
        return (owner, path,
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
        (1, "/Game/UiQuest/Image/ChuanShuo/Default", "wrong", "character:1"),
        (2, "/Game/Common/IconRup/Material", "item", "item:2"),
        (3, "/Game/Common/T_ActivityRoleYangyang", "foreign", "character:1409"),
        (4, "/Game/Common/T_ActivityRoleYangyang", "own", "character:1402"),
    ]
    assert await entity_image_urls(session, [1, 2, 3, 4]) == {2: "item", 4: "own"}
