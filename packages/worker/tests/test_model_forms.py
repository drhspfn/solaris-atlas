from copy import deepcopy

import pytest

from wuwa_story_worker.model_forms import resolve_model_forms

# Only identity fields from scratch/{roleinfo,preload,rolemorph}-3.7.json.
# Keep these independent of the local scratch files and database.
ROLES = [
    {"Id": 1409, "MeshId": 100038, "UiMeshId": 100038},
    {"Id": 1507, "MeshId": 100043, "UiMeshId": 100039},
    {"Id": 1210, "MeshId": 100057, "UiMeshId": 100057},
    {"Id": 1311, "MeshId": 100071, "UiMeshId": 100071},
    {"Id": 1211, "MeshId": 100062, "UiMeshId": 100062},
]
MORPHS = [
    {"Id": 1, "RoleId": 1409, "Morph": 1, "MeshId": 100041, "UiMeshId": 100041},
    {"Id": 2, "RoleId": 1210, "Morph": 1, "MeshId": 100058, "UiMeshId": 100058},
    {"Id": 3, "RoleId": 1311, "Morph": 1, "MeshId": 100072, "UiMeshId": 100072},
]
# mesh ID, authored character directory, mesh object, animation blueprint object.
IDENTITIES = [
    (
        100038,
        "FemaleMS/Katixiya/R2T1KatixiyaMd10011",
        "R2T1KatixiyaMd10011",
        "ABP_R2T1KatixiyaMd10011_PC",
    ),
    (100039, "FemaleXL/Zanni/R2T1ZanniMd10011", "R2T1ZanniMd10011", "ABP_R2T1ZanniMd10011_PC"),
    (
        100041,
        "FemaleZ/Fuludelisi/R2T1FuludelisiMd10011",
        "R2T1FuludelisiMd10011",
        "ABP_R2T1FuludelisiMd10011_PC",
    ),
    (100043, "FemaleXL/Zanni/R2T1ZanniMd10011", "R2T1ZanniMd10011_Up1", "ABP_R2T1ZanniMd10011_PC"),
    (100057, "FemaleM/Aimisi/R2T1AimisiMd10011", "R2T1AimisiMd10011", "ABP_R2T1AimisiMd10011_PC"),
    (
        100058,
        "FemaleZ2/AimisiGD/R2T1AimisiGDMd10011",
        "R2T1AimisiGDMd10011",
        "ABP_R2T1AimisiGDMd10011_PC",
    ),
    (
        100062,
        "FemaleMS/Daniya/R2T1DaniyaMd10011",
        "R2T1DaniyaMd10011_Body",
        "ABP_R2T1DaniyaMd10011_PC",
    ),
    (100071, "FemaleXL/Xinyuehu/R2T1XinMd10011", "R2T1XinMd10011", "ABP_R2T1XinyuehuMd10011_PC"),
    (
        100072,
        "FemaleXL/XinyuehuShen/R2T1ShenXinMd10011",
        "R2T1ShenXinMd10011",
        "ABP_R2T1XinyuehuMd10011_PC",
    ),
]


@pytest.fixture
def preloads():
    return [
        {
            "Id": mesh_id,
            "Meshes": [f"/Game/Aki/Character/Role/{root}/Model/{mesh}.{mesh}"],
            "AnimationBlueprints": [f"/Game/Aki/Character/Role/{root}/{abp}.{abp}_C"],
        }
        for mesh_id, root, mesh, abp in IDENTITIES
    ]


@pytest.mark.parametrize(
    ("role", "mesh_ids", "labels", "sources"),
    [
        (
            ROLES[0],
            [100038, 100041],
            ["Default", "Fleurdelys"],
            [("MeshId", 1409), ("rolemorph", 1)],
        ),
        (
            ROLES[1],
            [100039, 100043],
            ["Default", "Battle form"],
            [("UiMeshId", 1507), ("MeshId", 1507)],
        ),
        (
            ROLES[2],
            [100057, 100058],
            ["Default", "Mecha form"],
            [("MeshId", 1210), ("rolemorph", 2)],
        ),
        (
            ROLES[3],
            [100071, 100072],
            ["Default", "Transformed form 1"],
            [("MeshId", 1311), ("rolemorph", 3)],
        ),
        (ROLES[4], [100062], ["Default"], [("MeshId", 1211)]),
    ],
    ids=["Cartethyia", "Zani", "Aemeath", "Hsin", "Denia-no-morph"],
)
def test_exact_37_identities_and_complete_form_contract(preloads, role, mesh_ids, labels, sources):
    # Portraits and names must never participate in model lookup.
    role = {**role, "RolePortrait": preloads[-1]["Meshes"][0], "Name": "Unrelated"}
    before = deepcopy((role, preloads, MORPHS))
    forms = resolve_model_forms(role, list(reversed(preloads)), list(reversed(MORPHS)))
    expected = []
    for order, (mesh_id, label, (field, source_id)) in enumerate(
        zip(mesh_ids, labels, sources, strict=True)
    ):
        _, root, mesh, abp = next(row for row in IDENTITIES if row[0] == mesh_id)
        source = (
            {"table": "rolemorph", "id": source_id, "role_id": role["Id"]}
            if field == "rolemorph"
            else {"table": "roleinfo", "id": source_id, "field": field}
        )
        expected.append(
            {
                "id": f"mesh-{mesh_id}",
                "label": label,
                "order": order,
                "model": f"Aki/Character/Role/{root}/Model/{mesh}",
                "blueprints": [f"Aki/Character/Role/{root}/{abp}"],
                "source": source,
            }
        )
    assert forms == expected
    assert (role, preloads, MORPHS) == before


@pytest.mark.parametrize("ui_id", [None, 0, -1, True, "100039", 100039.0, 999999, 100043])
def test_optional_ui_id_never_uses_coercion_or_nearby_preloads(preloads, ui_id):
    forms = resolve_model_forms({**ROLES[1], "UiMeshId": ui_id}, preloads, [])
    assert [(form["id"], form["label"]) for form in forms] == [("mesh-100043", "Default")]
    assert forms[0]["source"]["field"] == "MeshId"


@pytest.mark.parametrize(
    "ui_mesh",
    [
        # Same body category is insufficient: this belongs to Hsin, not Zani.
        "/Game/Aki/Character/Role/FemaleXL/Xinyuehu/R2T1XinMd10011/Model/R2T1XinMd10011.R2T1XinMd10011",
        "/Game/Aki/Character/Npc/Zanni/Model/Zanni.Zanni",
        "/Game/Aki/Scene/Zanni/Model/Zanni.Zanni",
    ],
)
def test_borrowed_ui_mesh_is_skipped(preloads, ui_mesh):
    next(row for row in preloads if row["Id"] == 100039)["Meshes"] = [ui_mesh]
    forms = resolve_model_forms(ROLES[1], preloads, MORPHS)
    assert [(form["id"], form["label"]) for form in forms] == [("mesh-100043", "Default")]


@pytest.mark.parametrize("mesh_id", [None, 0, -1, True, "100038", 100038.0, 999999])
def test_primary_mesh_must_resolve_by_exact_positive_integer_id(preloads, mesh_id):
    with pytest.raises(ValueError):
        resolve_model_forms({**ROLES[0], "MeshId": mesh_id}, preloads, MORPHS)


@pytest.mark.parametrize("mesh_id", [None, 0, -1, True, "100041", 100041.0, 999999])
def test_declared_morph_must_resolve_even_when_optional_ui_is_borrowed(preloads, mesh_id):
    role = {**ROLES[0], "UiMeshId": 100039}
    with pytest.raises(ValueError, match="MeshId|missing"):
        resolve_model_forms(role, preloads, [{**MORPHS[0], "MeshId": mesh_id}])


@pytest.mark.parametrize("mesh_id", [100038, 100041], ids=["primary", "declared-morph"])
@pytest.mark.parametrize(
    "changes",
    [
        {"Meshes": []},
        {"Meshes": ["/Game/Aki/Character/Npc/Borrowed/Model/Rig.Rig"]},
        {"Meshes": ["/Game/Aki/Character/Role/Female/Test/Model/../Rig.Rig"]},
        {"Meshes": ["/Game/Aki/Character/Role/Female/Test/Model/A.A"] * 2},
        {"AnimationBlueprints": ["/Engine/Untrusted.Untrusted"]},
    ],
)
def test_invalid_required_sources_fail_instead_of_returning_partial_forms(
    preloads, mesh_id, changes
):
    next(row for row in preloads if row["Id"] == mesh_id).update(changes)
    with pytest.raises(ValueError):
        resolve_model_forms(ROLES[0], preloads, MORPHS)


@pytest.mark.parametrize("field", ["MeshId", "Id", "Morph"])
def test_missing_declared_morph_fields_fail(preloads, field):
    morph = {key: value for key, value in MORPHS[0].items() if key != field}
    with pytest.raises(ValueError):
        resolve_model_forms(ROLES[0], preloads, [morph])


@pytest.mark.parametrize("field", ["Id", "Morph"])
@pytest.mark.parametrize("value", [None, 0, -1, True, "1", 1.0])
def test_malformed_declared_morph_identity_fails(preloads, field, value):
    with pytest.raises(ValueError, match="RoleMorph identity"):
        resolve_model_forms(ROLES[0], preloads, [{**MORPHS[0], field: value}])


def test_unrelated_malformed_morph_does_not_affect_role(preloads):
    forms = resolve_model_forms(ROLES[4], preloads, [{"RoleId": 1409}, {"MeshId": 100041}])
    assert [form["id"] for form in forms] == ["mesh-100062"]


@pytest.mark.parametrize("same_payload", [True, False])
@pytest.mark.parametrize("mesh_id", [100038, 100039], ids=["referenced", "unreferenced"])
def test_duplicate_preload_ids_fail_regardless_of_payload_or_reference(
    preloads, same_payload, mesh_id
):
    duplicate = deepcopy(next(row for row in preloads if row["Id"] == mesh_id))
    if not same_payload:
        duplicate["Meshes"] = preloads[-1]["Meshes"]
    with pytest.raises(ValueError, match=f"Duplicate preload Id: {mesh_id}"):
        resolve_model_forms(ROLES[0], [*preloads, duplicate], MORPHS)


@pytest.mark.parametrize("value", [None, 0, -1, True, "100038", 100038.0])
def test_preload_ids_cannot_alias_integer_identities(preloads, value):
    with pytest.raises(ValueError, match="preload Id"):
        resolve_model_forms(ROLES[0], [*preloads, {"Id": value}], MORPHS)


def test_same_mesh_is_deduplicated_after_package_normalization(preloads):
    ui = next(row for row in preloads if row["Id"] == 100039)
    battle = next(row for row in preloads if row["Id"] == 100043)
    battle["Meshes"] = [ui["Meshes"][0].split(".")[0]]
    morph = {"Id": 10, "RoleId": 1507, "Morph": 1, "MeshId": 100043}
    forms = resolve_model_forms(ROLES[1], preloads, [morph])
    assert len(forms) == 1
    assert forms[0]["id"] == "mesh-100039"
    assert forms[0]["order"] == 0
    assert forms[0]["source"] == {"table": "roleinfo", "id": 1507, "field": "UiMeshId"}


def test_duplicate_mesh_does_not_hide_invalid_morph_blueprints(preloads):
    primary = next(row for row in preloads if row["Id"] == 100038)
    morph = next(row for row in preloads if row["Id"] == 100041)
    morph.update(Meshes=primary["Meshes"], AnimationBlueprints=["/Engine/Invalid.Invalid"])
    with pytest.raises(ValueError, match="authored"):
        resolve_model_forms(ROLES[0], preloads, MORPHS)


def test_morph_order_is_numeric_then_row_id_and_ignores_morph_ui_mesh(preloads):
    morphs = [
        {"Id": 8, "RoleId": 1211, "Morph": 10, "MeshId": 100041},
        {"Id": 7, "RoleId": 1211, "Morph": 2, "MeshId": 100058, "UiMeshId": 100039},
        {"Id": 6, "RoleId": 1211, "Morph": 2, "MeshId": 100072},
    ]
    forms = resolve_model_forms(ROLES[4], preloads, morphs)
    assert [form["id"] for form in forms] == [
        "mesh-100062",
        "mesh-100072",
        "mesh-100058",
        "mesh-100041",
    ]
    assert [form["order"] for form in forms] == [0, 1, 2, 3]
    assert [form["label"] for form in forms] == [
        "Default",
        "Transformed form 2",
        "Transformed form 2",
        "Transformed form 10",
    ]
    assert [form["source"]["id"] for form in forms] == [1211, 6, 7, 8]
