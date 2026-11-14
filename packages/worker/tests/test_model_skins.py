"""Skin/form identities from the 3.7 role, roleskin and preload tables."""

from copy import deepcopy

import pytest

from wuwa_story_worker.model_forms import resolve_model_forms, resolve_recipe_forms


def preload(mesh_id, root, mesh):
    path = f"/Game/Aki/Character/Role/{root}/Model/{mesh}"
    return {"Id": mesh_id, "Meshes": [f"{path}.{mesh}"]}


# Compact source rows from scratch/{roleinfo,roleskin,preload}-3.7.json.
ZANI = {"Id": 1507, "SkinId": 81001507, "MeshId": 100043, "UiMeshId": 100039}
ZANI_SKINS = [
    {"Id": 81001507, "RoleId": 1507, "MeshId": 100043, "UiMeshId": 100039},
    {"Id": 81011507, "RoleId": 1507, "MeshId": 1000431, "UiMeshId": 1000391,
     "TitleName": "RoleSkin_81011507_TitleName"},
]
ZANI_PRELOADS = [
    preload(100039, "FemaleXL/Zanni/R2T1ZanniMd10011", "R2T1ZanniMd10011"),
    preload(100043, "FemaleXL/Zanni/R2T1ZanniMd10011", "R2T1ZanniMd10011_Up1"),
    preload(1000391, "FemaleXL/Zanni/R2T1ZanniMd20011", "R2T1ZanniMd20011"),
    preload(1000431, "FemaleXL/Zanni/R2T1ZanniMd20011", "R2T1ZanniMd20011_Up1"),
]


def test_zani_default_and_alternate_skin_each_have_ui_and_battle_forms():
    recipe = {"role": ZANI, "skins": ZANI_SKINS, "preloads": ZANI_PRELOADS,
              "morphs": [], "skin_labels": {"RoleSkin_81011507_TitleName": "Skin title"}}
    original = deepcopy(recipe)
    forms = resolve_recipe_forms(recipe)

    assert [form["id"] for form in forms] == [
        "mesh-100039", "mesh-100043", "mesh-1000391", "mesh-1000431"
    ]
    assert [form["label"] for form in forms] == [
        "Default", "Battle form", "Skin title · Default", "Skin title · Battle form"
    ]
    assert [form["model"].rsplit("/", 1)[-1] for form in forms] == [
        "R2T1ZanniMd10011", "R2T1ZanniMd10011_Up1",
        "R2T1ZanniMd20011", "R2T1ZanniMd20011_Up1",
    ]
    assert [form["source"]["field"] for form in forms] == [
        "UiMeshId", "MeshId", "UiMeshId", "MeshId"
    ]
    assert all(form["source"]["skin_id"] == 81011507 for form in forms[2:])
    assert recipe == original


# (role ID, element ID, gender, base mesh, UI mesh, alternate skin mesh).
ROVER_ROWS = [
    (1501, 5, "male", 100004, 100004, 1000041),
    (1502, 5, "female", 100006, 100006, 1000061),
    (1604, 6, "female", 100021, 100021, 1000211),
    (1605, 6, "male", 100022, 100022, 1000221),
    (1406, 4, "male", 100036, 100022, 1000361),
    (1408, 4, "female", 100042, 100042, 1000421),
    (1309, 3, "male", 100067, 100067, 1000671),
    (1310, 3, "female", 100068, 100068, 1000681),
]


def rover_recipe():
    roles, skins, preloads = [], [], []
    for role_id, element, gender, base, ui, alternate in ROVER_ROWS:
        default_skin, alternate_skin = 81000000 + role_id, 81010000 + role_id
        roles.append({"Id": role_id, "ElementId": element, "model_gender": gender,
                      "SkinId": default_skin, "MeshId": base, "UiMeshId": ui})
        skins.extend([
            {"Id": default_skin, "RoleId": role_id, "GroupId": 1000,
             "MeshId": base, "UiMeshId": ui},
            {"Id": alternate_skin, "RoleId": role_id, "GroupId": 2000,
             "MeshId": alternate, "UiMeshId": alternate},
        ])
        body, character, player = (("MaleM", "Nanzhu", "Male") if gender == "male"
                                   else ("FemaleM", "Nvzhu", "Female"))
        for mesh_id, variant in ((base, "10011"), (alternate, "20011")):
            mesh = f"R2T1Player{player}Md{variant}"
            preloads.append(preload(mesh_id, f"{body}/{character}/{mesh}", mesh))
    return {"roles": roles, "skins": skins, "preloads": preloads, "morphs": []}


def test_rover_eight_element_roles_deduplicate_to_gender_and_skin_models():
    recipe = rover_recipe()
    forms = resolve_recipe_forms(recipe)
    assert len(forms) == 4
    assert {form["model"].rsplit("/", 1)[-1] for form in forms} == {
        "R2T1PlayerMaleMd10011", "R2T1PlayerMaleMd20011",
        "R2T1PlayerFemaleMd10011", "R2T1PlayerFemaleMd20011",
    }
    assert [form["order"] for form in forms] == list(range(4))
    assert sum(form["label"].startswith("Male Rover") for form in forms) == 2
    assert sum(form["label"].startswith("Female Rover") for form in forms) == 2
    assert resolve_recipe_forms({**recipe, "roles": list(reversed(recipe["roles"]))}) == forms


def test_skin_morph_requires_matching_skin_id():
    # Real Cartethyia morph plus a synthetic alternate skin and its own morph;
    # this does not assert that such a skin exists in the client.
    role = {"Id": 1409, "SkinId": 81001409, "MeshId": 100038, "UiMeshId": 100038}
    base_morph = {"Id": 1, "RoleId": 1409, "SkinId": 81001409, "Morph": 1,
                  "MeshId": 100041, "UiMeshId": 100041}
    skin = {"Id": 999001, "RoleId": 1409, "MeshId": 999002, "UiMeshId": 999002}
    skin_morph = {"Id": 999003, "RoleId": 1409, "SkinId": 999001, "Morph": 1,
                  "MeshId": 999004, "UiMeshId": 999004}
    preloads = [
        preload(100038, "FemaleMS/Katixiya/R2T1KatixiyaMd10011", "R2T1KatixiyaMd10011"),
        preload(100041, "FemaleZ/Fuludelisi/R2T1FuludelisiMd10011", "R2T1FuludelisiMd10011"),
        preload(999002, "FemaleMS/Katixiya/SyntheticSkin", "SyntheticSkin"),
        preload(999004, "FemaleZ/Fuludelisi/SyntheticMorph", "SyntheticMorph"),
    ]
    without_skin_morph = resolve_model_forms(role, preloads, [base_morph], [skin])
    assert [form["id"] for form in without_skin_morph] == [
        "mesh-100038", "mesh-100041", "mesh-999002"
    ]
    forms = resolve_model_forms(role, preloads, [base_morph, skin_morph], [skin])
    assert [form["id"] for form in forms] == [
        "mesh-100038", "mesh-100041", "mesh-999002", "mesh-999004"
    ]
    assert forms[-1]["source"]["table"] == "rolemorph"
    assert forms[-1]["source"]["id"] == 999003
    assert forms[-1]["source"]["skin_id"] == 999001


def test_synthetic_hiyuki_skin_morph_is_not_applied_to_default():
    # Only the role, default skin and base mesh are real 3.7 evidence.
    role = {"Id": 1108, "SkinId": 81001108, "MeshId": 100066, "UiMeshId": 100066}
    skin = {"Id": 999101, "RoleId": 1108, "MeshId": 999102, "UiMeshId": 999102}
    morph = {"Id": 999103, "RoleId": 1108, "SkinId": 999101, "Morph": 1,
             "MeshId": 999104, "UiMeshId": 999104}
    preloads = [
        preload(100066, "FemaleXL/TaiDaoNv/R2T1TaidaonvMd10011", "R2T1TaidaonvMd10011"),
        preload(999102, "FemaleXL/TaiDaoNv/SyntheticSkin", "SyntheticSkin"),
        preload(999104, "FemaleXL/TaiDaoNv/SyntheticMorph", "SyntheticMorph"),
    ]
    forms = resolve_model_forms(role, preloads, [morph], [skin])
    assert [form["id"] for form in forms] == ["mesh-100066", "mesh-999102", "mesh-999104"]
    assert forms[-1]["source"]["skin_id"] == skin["Id"]
    with pytest.raises(ValueError):
        resolve_model_forms(role, preloads[:-1], [morph], [skin])


@pytest.mark.parametrize("missing", [1000431, 100043])
def test_missing_required_skin_or_base_preload_fails(missing):
    with pytest.raises(ValueError):
        resolve_model_forms(ZANI, [p for p in ZANI_PRELOADS if p["Id"] != missing], [], ZANI_SKINS)


@pytest.mark.parametrize("bad_id", [None, 0, -1, True, "81011507"])
def test_invalid_skin_identity_is_rejected(bad_id):
    with pytest.raises(ValueError):
        resolve_model_forms(ZANI, ZANI_PRELOADS, [], [{**ZANI_SKINS[1], "Id": bad_id}])


def test_hostile_skin_mesh_path_is_rejected():
    preloads = deepcopy(ZANI_PRELOADS)
    next(row for row in preloads if row["Id"] == 1000431)["Meshes"] = [
        "/Game/Aki/Character/Role/FemaleXL/Zanni/Model/../Other.Other"
    ]
    with pytest.raises(ValueError):
        resolve_model_forms(ZANI, preloads, [], ZANI_SKINS)
