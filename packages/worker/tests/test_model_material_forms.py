import json

import pytest

from wuwa_story_worker.model_material_forms import (
    discover_material_replacement_forms_from_cooked,
    expand_material_replacement_forms,
)


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _da_cache(materials, *, body_type="EKuroCharBodySpecifiedType::Body", apply_type="EKuroCharacterControllerApplyType::ReplaceBaseMaterials"):
    return [
        {
            "Type": "KuroMaterialContainerDataCache",
            "Name": "KuroMaterialContainerDataCache_0",
            "Properties": {
                "MaterialApplyType": apply_type,
                "Filter": {"BodySpecifiedType": body_type},
                "BaseMaterialReplaceParam": {
                    "bMatchMaterialIndex": True,
                    "ReplaceBaseMaterials": [
                        {"ObjectPath": f"/Game/{pkg}.0"} if pkg else {}
                        for pkg in materials
                    ],
                },
            },
        }
    ]


def test_discovers_deduplicated_replace_base_materials_per_appearance(tmp_path):
    cooked = tmp_path / "source-json"
    base_model = "Aki/Character/Role/FemaleXL/TaiDaoNv/R2T1TaidaonvMd10011/Model/R2T1TaidaonvMd10011"
    skin_model = "Aki/Character/Role/FemaleXL/TaiDaoNv/R2T1TaidaonvMd20011/Model/R2T1TaidaonvMd20011"

    base_mats = [
        "Aki/Character/Role/FemaleXL/TaiDaoNv/R2T1TaidaonvMd10011/Model/MI_R2T1TaidaonvMd10011Up01",
        "Aki/Character/Role/FemaleXL/TaiDaoNv/R2T1TaidaonvMd10011/Model/MI_R2T1TaidaonvMd10011Face_FS",
    ]
    base_form2_mats = [
        "Aki/Character/Role/FemaleXL/TaiDaoNv/R2T1TaidaonvMd10011/Model/MI_R2T1TaidaonvMd10011Up02",
        "Aki/Character/Role/FemaleXL/TaiDaoNv/R2T1TaidaonvMd10011/Model/MI_R2T1TaidaonvMd10011Face_FS",
    ]
    skin_mats = [
        "Aki/Character/Role/FemaleXL/TaiDaoNv/R2T1TaidaonvMd20011/Model/MI_R2T1TaidaonvMd20011Up01",
        "Aki/Character/Role/FemaleXL/TaiDaoNv/R2T1TaidaonvMd20011/Model/MI_R2T1TaidaonvMd20011Face_FS",
    ]
    skin_form2_mats = [
        "Aki/Character/Role/FemaleXL/TaiDaoNv/R2T1TaidaonvMd20011/Model/MI_R2T1TaidaonvMd20011Up02",
        "Aki/Character/Role/FemaleXL/TaiDaoNv/R2T1TaidaonvMd20011/Model/MI_R2T1TaidaonvMd20011Face_FS",
    ]

    for model_pkg, mats in ((base_model, base_mats), (skin_model, skin_mats)):
        _write_json(
            cooked / "Client/Content" / (model_pkg + ".json"),
            [
                {
                    "Type": "SkeletalMesh",
                    "Name": model_pkg.rsplit("/", 1)[-1],
                    "Properties": {
                        "Materials": [{"MaterialInterface": {"ObjectPath": f"/Game/{pkg}.0"}} for pkg in mats]
                    },
                }
            ],
        )

    da_root = cooked / "Client/Content/Aki/Effect/MaterialController/DA_Character/Taidaonv"
    _write_json(da_root / "DA_Fx_Taidaonv_Change_DownNew1.json",
                _da_cache(base_form2_mats))
    _write_json(da_root / "DA_Fx_Taidaonv_Change_DownNew2.json",
                _da_cache(base_form2_mats))
    _write_json(da_root / "DA_Fx_Taidaonv_Revert.json", _da_cache(base_mats))
    _write_json(
        da_root / "DA_Fx_Taidaonv_WeaponOnly.json",
        _da_cache(base_form2_mats,
                  body_type="EKuroCharBodySpecifiedType::Weapon"),
    )
    _write_json(da_root / "Pifu/DA_Fx_Taidaonv_Change_DownNew1.json",
                _da_cache(skin_form2_mats))
    _write_json(da_root / "Pifu/DA_Fx_Taidaonv_Change_DownNew2.json",
                _da_cache(skin_form2_mats))

    forms = [
        {"id": "mesh-100066", "label": "Default", "order": 0,
            "model": base_model, "blueprints": []},
        {"id": "mesh-1000661", "label": "Skin 81011108 · Default",
            "order": 1, "model": skin_model, "blueprints": []},
    ]
    expanded = discover_material_replacement_forms_from_cooked(cooked, forms)

    assert [f["id"] for f in expanded] == [
        "mesh-100066",
        "mesh-100066-form-2",
        "mesh-1000661",
        "mesh-1000661-form-2",
    ]
    assert [f["label"] for f in expanded] == [
        "Default",
        "Default · Form 2",
        "Skin 81011108 · Default",
        "Skin 81011108 · Default · Form 2",
    ]
    assert [f["order"] for f in expanded] == [0, 1, 2, 3]
    assert expanded[1]["part_material_overrides"] == {
        base_model: {"0": base_form2_mats[0]}}
    assert expanded[3]["part_material_overrides"] == {
        skin_model: {"0": skin_form2_mats[0]}}


@pytest.mark.asyncio
async def test_expand_material_replacement_forms_noops_without_fmodel(tmp_path, monkeypatch):
    monkeypatch.delenv("WUWA_FMODEL_PATH", raising=False)
    forms = [{"id": "mesh-1", "label": "Default", "order": 0,
              "model": "Aki/Character/Role/FemaleXL/TaiDaoNv/R2T1TaidaonvMd10011/Model/R2T1TaidaonvMd10011"}]
    result = await expand_material_replacement_forms(tmp_path, forms)
    assert result == forms
