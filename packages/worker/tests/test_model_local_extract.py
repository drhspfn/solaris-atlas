from pathlib import Path

import pytest

from wuwa_story_worker import character_models as models


@pytest.mark.asyncio
@pytest.mark.parametrize("batch_supported", [True, False])
async def test_local_export_mounts_once_or_falls_back_only_when_batch_empty(tmp_path, monkeypatch, batch_supported):
    calls = []
    model = "Aki/Character/NPC/Test/Model/Body"

    def extract(root, fmodel, query, manifest, destination, *_args, **_kwargs):
        calls.append(query)
        output = destination / "files"
        output.mkdir()
        if "|" not in query or batch_supported:
            (output / "body.uasset").write_bytes(b"same authored bytes")

    async def tool(args, _log):
        if "--mesh-format" in args:
            destination = Path(args[args.index("-o") + 1]) / "Client/Content" / (model + ".uemodel")
            destination.parent.mkdir(parents=True)
            destination.touch()

    monkeypatch.setattr(models, "_extract_files", extract)
    monkeypatch.setattr(models, "tool", tool)
    await models.extract_model(tmp_path, {"model": model, "blueprints": ["Aki/BP"]},
        "fmodel", "converter", False, local_client={"game": tmp_path, "keys": tmp_path / "keys", "processors": 1})
    assert calls[0] == "Aki/Character/NPC/Test/Model/|Aki/BP"
    assert len(calls) == (1 if batch_supported else 3)


@pytest.mark.asyncio
@pytest.mark.parametrize("model", [
    "Aki/Character/Weapon/Prop/Role/QianXiaProp/EQianXiaJiandaoMd10011/Model/EQianXiaJiandaoMd10011",
    "Aki/Character/NPC/AlienNPC/Level_S/SS1Bozai/Model/SS1Bozai",
])
async def test_animated_linked_component_uses_its_authored_root(tmp_path, monkeypatch, model):
    queries = []
    root = model.split("/Model/")[0]
    blueprint, montage = root + "/ABP", root + "/BaseAnim/Clip"

    def extract(_root, _fmodel, query, _manifest, destination, *_args, **_kwargs):
        queries.append(query)
        output = destination / "files"
        output.mkdir()
        (output / "body.uasset").touch()

    async def tool(args, _log):
        if "--mesh-format" in args:
            destination = Path(args[args.index("-o") + 1]) / "Client/Content" / (model + ".uemodel")
            destination.parent.mkdir(parents=True)
            destination.touch()

    monkeypatch.setattr(models, "_extract_files", extract)
    monkeypatch.setattr(models, "tool", tool)
    await models.extract_model(tmp_path, {"model": model, "blueprints": [blueprint],
        "montages": [montage], "component": {"id": "child"}}, "fmodel", "converter",
        local_client={"game": tmp_path, "keys": tmp_path / "keys", "processors": 1})
    filters = queries[0].split("|")
    assert filters == [root + "/", blueprint, montage, "Engine/Content/Animation/DefaultAnim",
                       "ACLAnim", "ACLPlugin.uplugin"]
    assert "Role" not in filters and "Level_S" not in filters
