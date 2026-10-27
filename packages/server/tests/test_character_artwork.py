from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story.ingestion.character_artwork import character_artwork_scope
from wuwa_story.ingestion.entity_media import MediaRequest, entity_media_targets


def test_roleinfo_shared_defaults_are_not_personal_artwork():
    assert (
        character_artwork_scope(
            "RoleStand", "/Game/Aki/UI/UIResources/UiQuest/Image/ChuanShuo/T_IconChuanShuo1_UI"
        )
        == "shared_resource"
    )
    assert (
        character_artwork_scope(
            "Icon", "/Game/Aki/UI/UIResources/Common/Image/IconRup/T_IconRup_1503_UI"
        )
        == "shared_resource"
    )
    assert (
        character_artwork_scope("RolePortrait", "/Game/Common/T_ActivityRoleYinlin") == "character"
    )
    assert (
        character_artwork_scope("RoleHeadIconLarge", "/Game/Common/IconRoleHead256/Yinlin")
        == "character"
    )
    assert character_artwork_scope("RoleStand", "/Game/Common/Character/Yinlin") == "character"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["character", "item"])
async def test_export_omits_shared_character_resources_but_keeps_item_icons(kind):
    portrait = "/Game/Common/T_ActivityRoleYinlin"
    icon = "/Game/Aki/UI/UIResources/Common/Image/IconRup/T_IconRup_1503_UI"
    stand = "/Game/Aki/UI/UIResources/UiQuest/Image/ChuanShuo/T_IconChuanShuo1_UI"
    session = AsyncMock()
    session.scalar.side_effect = [
        1,
        SimpleNamespace(id=15),
        {
            "RolePortrait": portrait,
            "RoleStand": stand,
            "Icon": icon,
        },
    ]
    session.scalars.return_value = []
    request = MediaRequest(
        download_id="a" * 64,
        asset_version="3.7.0",
        game_version="1.0.0",
        entities=[f"{kind}:1302"],
        kinds=["image"],
    )
    targets = await entity_media_targets(session, request)
    assert {target["path"] for target in targets} == (
        {portrait} if kind == "character" else {portrait, stand, icon}
    )
