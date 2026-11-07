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
        character_artwork_scope(
            "RolePortrait", "/Game/Common/T_ActivityRoleYinlin") == "character"
    )
    assert (
        character_artwork_scope("RoleHeadIconLarge",
                                "/Game/Common/IconRoleHead256/Yinlin")
        == "character"
    )
    assert character_artwork_scope(
        "RoleStand", "/Game/Common/Character/Yinlin") == "character"


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
    assert character_artwork_scope(
        "RolePortrait", path, borrower) == "shared_resource"
    assert character_artwork_scope("RolePortrait", path, owner) == "character"
    assert character_artwork_scope(
        "RolePortrait", path.lower(), borrower) == "shared_resource"


def test_actual_portraits_with_different_internal_aliases_remain_supported():
    path = "/Game/Common/IconRolePile/T_IconRole_Pile_Kamola_UI.T_IconRole_Pile_Kamola_UI"
    assert character_artwork_scope("RolePortrait", path, 1107) == "character"
    assert (
        character_artwork_scope(
            "RolePortrait", "/Game/Common/T_ActivityRoleJianxin", 1405)
        == "character"
    )
    assert (
        character_artwork_scope(
            "RolePortrait", "/Game/Common/T_ActivityRoleNvzhu", 1604)
        == "character"
    )


@pytest.mark.asyncio
async def test_native_npc_does_not_borrow_roleinfo_or_speaker_artwork():
    session = AsyncMock()
    session.scalar.side_effect = [1, SimpleNamespace(id=15)]
    with pytest.raises(ValueError, match="No authored media references"):
        await entity_media_targets(session, MediaRequest(
            download_id="a" * 64, asset_version="3.7.1", game_version="3.7.0",
            entities=["character:npc:17288"], kinds=["image"],
        ))
    assert session.scalar.await_count == 2
    session.scalars.assert_not_awaited()


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
        *([None] if kind == "character" else []),  # No stored trial metadata.
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
        None,  # No stored trial metadata.
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


def test_npc_does_not_own_reused_yangyang_formation_card():
    from wuwa_story.ingestion.character_artwork import inherited_character_fields

    template = {"Id": 1402, "RoleHeadIcon": "yangyang.png",
                "FormationRoleCard": "yangyang-card.png"}
    npc = {"Id": 5015, "RoleType": 2, "RoleHeadIcon": "masked-man.png",
           "FormationRoleCard": "yangyang-card.png"}
    assert inherited_character_fields(npc, template) == {"FormationRoleCard"}
    assert inherited_character_fields(
        {**npc, "RoleHeadIcon": "yangyang.png"}, template) == set()
    assert inherited_character_fields(
        {**npc, "RoleType": 1}, template) == set()


def test_named_creature_with_complete_foreign_role_defaults_keeps_no_yangyang_portrait():
    from wuwa_story.ingestion.character_artwork import inherited_character_fields
    template = {"Id": 1402, "Name": "RoleInfo_1402_Name",
                "RoleHeadIcon": "yangyang-head", "FormationRoleCard": "yangyang-card"}
    creature = {**template, "Id": 5002,
                "Name": "RoleInfo_5002_Name", "RoleType": 2}
    assert inherited_character_fields(creature, template) == {
        "RoleHeadIcon", "FormationRoleCard"}
    actual_yangyang = {**creature, "Name": "RoleInfo_1402_Name"}
    assert inherited_character_fields(actual_yangyang, template) == set()


@pytest.mark.asyncio
async def test_npc_export_does_not_schedule_template_skill_or_yangyang_images():
    own = "/Game/MonsterHead976"
    borrowed = "/Game/YangyangCard"
    npc = {"Id": 5015, "RoleType": 2, "Name": "RoleInfo_5015_Name", "SkillId": 1402,
           "RoleHeadIcon": own, "FormationRoleCard": borrowed}
    template = {"Id": 1402, "Name": "RoleInfo_1402_Name", "RoleHeadIcon": "/Game/YangyangHead",
                "FormationRoleCard": borrowed}
    session = AsyncMock()
    session.scalar.side_effect = [1, SimpleNamespace(id=5015), npc, None]
    session.scalars.side_effect = [[template], [], []]
    result = await entity_media_targets(session, MediaRequest(
        download_id="a" * 64, asset_version="3.7.0", game_version="3.7.0",
        entities=["character:5015"], kinds=["image"]))
    assert [target["path"] for target in result] == [own]
    assert all("SkillGroupId" not in str(
        call.args[0]) for call in session.scalars.await_args_list)

@pytest.mark.parametrize("skill_id", [1208, 1603])
def test_story_role_rejects_foreign_head_set_but_keeps_owned_card(skill_id):
    from wuwa_story.ingestion.character_artwork import inherited_character_fields

    heads = {
        field: f"/Game/Common/T_IconRoleHead{size}_53_UI.T_IconRoleHead{size}_53_UI"
        for field, size in [("RoleHeadIcon", "80"), ("RoleHeadIconBig", "150"),
                            ("RoleHeadIconLarge", "256"), ("RoleHeadIconCircle", "Circle256"),
                            ("Card", "175")]
    }
    role = {"Id": 99901, "RoleType": 2, "Name": "person", "Introduction": "person-info",
            "SkillId": skill_id, "FormationRoleCard": "person-card", "RolePortrait": "person-portrait", **heads}
    template = {"Id": skill_id, "Name": "person" if skill_id == 1603 else "other",
                "Introduction": "person-info" if skill_id == 1603 else "other-info",
                "FormationRoleCard": "person-card" if skill_id == 1603 else "other-card",
                "RolePortrait": "person-portrait" if skill_id == 1603 else "other-portrait",
                "RoleHeadIcon": "different-head"}
    assert inherited_character_fields(role, template) == set(heads)
    # An actual copy of the source head owner retains its artwork.
    owner = {**role, "Id": 1210}
    assert inherited_character_fields(role, owner) == set()
    # A matching name alone cannot establish ownership.
    assert "FormationRoleCard" in inherited_character_fields(role, {**template, "Introduction": "other-info", "FormationRoleCard": "person-card"})


def test_only_reused_creature_default_is_rejected():
    from wuwa_story.ingestion.character_artwork import inherited_character_fields

    default = "/Game/Common/IconMonsterHead/T_IconMonsterHead_115_UI.T_IconMonsterHead_115_UI"
    real = "/Game/Common/IconMonsterHead/T_IconMonsterHead_976_UI.T_IconMonsterHead_976_UI"
    role = {"Id": 5057, "RoleType": 2, "RoleHeadIcon": default,
            "RoleHeadIconBig": default, "Card": default, "RolePortrait": real}
    assert inherited_character_fields(role, {}) == {"RoleHeadIcon", "RoleHeadIconBig", "Card"}
    assert inherited_character_fields({**role, "RoleHeadIcon": real}, {}) == {"RoleHeadIconBig", "Card"}
    assert inherited_character_fields({**role, "RoleType": 1}, {}) == set()

@pytest.mark.asyncio
async def test_story_role_export_keeps_corroborated_card_and_omits_foreign_head():
    head = "/Game/Common/T_IconRoleHead80_53_UI.T_IconRoleHead80_53_UI"
    card = "/Game/Common/IconRolePile/owned-card"
    role = {"Id": 99901, "RoleType": 2, "SkillId": 42, "Name": "person",
            "Introduction": "person-info", "RoleHeadIcon": head, "FormationRoleCard": card}
    template = {**role, "Id": 42, "RoleType": 1, "RoleHeadIcon": "/Game/own-head"}
    session = AsyncMock()
    session.scalar.side_effect = [1, SimpleNamespace(id=99901), role, None]
    session.scalars.side_effect = [[template], [], []]
    targets = await entity_media_targets(session, MediaRequest(
        download_id="a" * 64, asset_version="3.7.0", game_version="3.7.0",
        entities=["character:99901"], kinds=["image"]))
    assert [target["path"] for target in targets] == [card]
