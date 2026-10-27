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


@pytest.mark.parametrize(
    "name,owner,borrower",
    [
        ("Yangyang", 1402, 1409),
        ("Bailian", 1103, 1607),
        ("Changli", 1205, 1207),
        ("Xiangliyao", 1305, 1206),
    ],
)
def test_borrowed_portrait_is_hidden_only_for_the_foreign_role(name, owner, borrower):
    path = f"/Game/Aki/UI/UIResources/Common/Image/PixActivity/T_ActivityRole{name}.T_ActivityRole{name}"
    assert character_artwork_scope("RolePortrait", path, borrower) == "shared_resource"
    assert character_artwork_scope("RolePortrait", path, owner) == "character"
    assert character_artwork_scope("RolePortrait", path.lower(), borrower) == "shared_resource"


def test_actual_portraits_with_different_internal_aliases_remain_supported():
    path = "/Game/Common/IconRolePile/T_IconRole_Pile_Kamola_UI.T_IconRole_Pile_Kamola_UI"
    assert character_artwork_scope("RolePortrait", path, 1107) == "character"
    assert (
        character_artwork_scope("RolePortrait", "/Game/Common/T_ActivityRoleJianxin", 1405)
        == "character"
    )
    assert (
        character_artwork_scope("RolePortrait", "/Game/Common/T_ActivityRoleNvzhu", 1604)
        == "character"
    )


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


@pytest.mark.asyncio
async def test_export_preserves_cartethyia_card_but_omits_yangyang_portrait():
    own = "/Game/Common/IconRolePile/T_IconRole_Pile_katixiya_UI"
    foreign = "/Game/Common/PixActivity/T_ActivityRoleYangyang.T_ActivityRoleYangyang"
    session = AsyncMock()
    session.scalar.side_effect = [
        1,
        SimpleNamespace(id=20),
        {
            "FormationRoleCard": own,
            "RolePortrait": foreign,
        },
    ]
    session.scalars.return_value = []
    request = MediaRequest(
        download_id="a" * 64,
        asset_version="3.7.0",
        game_version="2.8.0",
        entities=["character:1409"],
        kinds=["image"],
    )
    targets = await entity_media_targets(session, request)
    assert [target["path"] for target in targets] == [own]
