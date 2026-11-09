import pytest

from wuwa_story_worker.model_forms import resolve_model_forms
from wuwa_story_worker.model_sources import model_sources


def test_carlotta_display_retains_authored_hair_instead_of_deduplicating_body():
    root = "/Game/Aki/Character/Role/FemaleMS/Kmola/R2T1KamolaMd20011/Model/"
    body, hair = root + "R2T1KamolaMd20011_Body", root + "R2T1KamolaMd20011_Hair1"
    preloads = [{"Id": 1000331, "Meshes": [body]},
                {"Id": 1001331, "Meshes": [hair, body]}]
    forms = resolve_model_forms({"Id": 1107, "MeshId": 1000331, "UiMeshId": 1001331}, preloads, [])
    assert len(forms) == 2
    assert [form["label"] for form in forms] == ["Display", "Gameplay"]
    assert forms[0]["extra_models"] == [hair.removeprefix("/Game/")]
    assert "extra_models" not in forms[1]


@pytest.mark.parametrize("extra", ["/Game/Unrelated/Model/Rig_Hair1", "/Game/Aki/Character/Role/Test/Model/Rig_Weapon"])
def test_preload_list_does_not_authorize_merging_arbitrary_rigs(extra):
    with pytest.raises(ValueError, match="composite"):
        model_sources({"MeshId": 1}, {"Id": 1, "Meshes": [
            "/Game/Aki/Character/Role/Test/Model/Rig_Body", extra]})


def test_morph_display_mesh_is_resolved_from_its_own_root_not_base_root():
    def preload(identity, name):
        return {"Id": identity, "Meshes": [f"/Game/Aki/Character/Role/Female/{name}/Model/Rig{identity}"]}
    preloads = [preload(1, "Base"), preload(2, "Transformation"), preload(3, "Transformation")]
    forms = resolve_model_forms({"Id": 99, "SkinId": 10, "MeshId": 1}, preloads,
        [{"Id": 7, "RoleId": 99, "SkinId": 10, "Morph": 1, "MeshId": 2, "UiMeshId": 3}])
    assert [form["id"] for form in forms] == ["mesh-1", "mesh-2", "mesh-3"]
    assert forms[-1]["source"]["field"] == "UiMeshId"
