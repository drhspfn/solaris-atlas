from unittest.mock import AsyncMock, Mock

from sqlalchemy.dialects import postgresql

from wuwa_story.db.repositories.character_models import model_recipe


async def test_skin_recipe_includes_each_skin_and_both_mesh_fields_in_same_snapshot():
    role = {"Id": 1507, "SkinId": 81001507, "MeshId": 100043, "UiMeshId": 100039}
    skin = {"Id": 81011507, "RoleId": 1507, "MeshId": 1000431, "UiMeshId": 1000391,
            "TitleName": "RoleSkin_81011507_TitleName"}
    morph = {"Id": 8, "RoleId": 1507, "SkinId": 81011507, "MeshId": 111, "UiMeshId": 112}
    db = AsyncMock()
    db.scalars.side_effect = [[role], [], [morph], [skin], [{"Id": 1000391}]]
    db.execute.return_value = Mock(all=lambda: [(skin["TitleName"], "Downtown Repose")])
    recipe = await model_recipe(db, 42, 1507)
    assert recipe["role"] == role and recipe["skins"] == [skin]
    assert recipe["skin_labels"] == {skin["TitleName"]: "Downtown Repose"}
    statements = [str(call.args[0].compile(dialect=postgresql.dialect(),
                  compile_kwargs={"literal_binds": True})) for call in db.scalars.await_args_list]
    assert all("raw.source_record.release_id = 42" in sql for sql in statements)
    preload_sql = statements[-1]
    assert "modelconfigpreload.json" in preload_sql
    for mesh_id in (100043, 100039, 1000431, 1000391, 111, 112):
        assert f"'{mesh_id}'" in preload_sql


async def test_rover_recipe_uses_authored_gender_members_and_new_elements():
    roles = [{"Id": identity, "MeshId": identity, "UiMeshId": identity}
             for identity in (1501, 1502, 99901)]
    configs = [{"Id": 1501, "Gender": 1, "UnlockCondition": 0},
               {"Id": 1502, "Gender": 0, "UnlockCondition": 0},
               {"Id": 99901, "Gender": 1, "UnlockCondition": 99}]
    db = AsyncMock()
    db.scalars.side_effect = [[roles[0]], configs, roles, [], [], []]
    recipe = await model_recipe(db, 42, 1501)
    assert [(role["Id"], role["model_gender"]) for role in recipe["roles"]] == [
        (1501, "male"), (1502, "female"), (99901, "male")]
    db.execute.assert_not_awaited()


async def test_historical_snapshot_without_skins_remains_supported():
    role = {"Id": 1402, "MeshId": 100001}
    db = AsyncMock()
    db.scalars.side_effect = [[role], [], [], [], [{"Id": 100001}]]
    recipe = await model_recipe(db, 1, 1402)
    assert recipe["role"] == role and recipe["skins"] == []
    assert "roles" not in recipe
    db.execute.assert_not_awaited()
