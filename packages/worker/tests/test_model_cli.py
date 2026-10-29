import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from wuwa_story.ingestion.model_archives import read_archive

from wuwa_story_worker import character_models, model_cli, model_forms


@pytest.mark.asyncio
async def test_local_build_packages_final_outputs_and_removes_scratch(tmp_path, monkeypatch):
    recipe = tmp_path / "recipe.json"
    recipe.write_text(json.dumps({"format": "solaris-model-recipe-v1", "character_key": "character:1",
        "game_version": "3.3.0", "role": {}, "preloads": [], "morphs": []}))
    keys = tmp_path / "keys.txt"
    keys.write_text("private local keys must not enter archive")
    args = NS(recipe=recipe, game_dir=tmp_path, keys_file=keys, output=tmp_path / "model.zip",
        asset_version="3.7.0", tier="hd", without_animations=True, workers=3,
        fmodel=None, converter=None, blender=None, ueformat_plugin=None)
    form = {"id": "mesh-1", "label": "Default"}
    monkeypatch.setattr(model_forms, "resolve_model_forms", lambda *_: [form])

    async def build(root, source, **options):
        assert options["local_client"]["keys"] == keys
        assert options["animation_workers"] == 3 and not options["include_animations"]
        assert options["report_progress"] is not None
        output = root / "bundle"
        output.mkdir()
        (output / "preview.glb").write_bytes(b"preview")
        (output / "character.glb").write_bytes(b"model")
        (root / "raw-private-export").write_bytes(b"must not ship")
        return output, {}, {"model": "authored path"}

    builder = AsyncMock(side_effect=build)
    monkeypatch.setattr(character_models, "build_bundle", builder)
    await model_cli.build(args)
    result = read_archive(args.output, tmp_path / "verified")
    assert len(result["objects"]) == 2
    assert result["game_version"] == "3.3.0" and result["asset_version"] == "3.7.0"
    assert not list(tmp_path.glob(".solaris-model-*"))
    with pytest.raises(ValueError, match="already exists"):
        await model_cli.build(args)
    builder.assert_awaited_once()

    args.output = tmp_path / "failed.zip"
    builder.side_effect = RuntimeError("conversion failed")
    with pytest.raises(RuntimeError, match="conversion failed"):
        await model_cli.build(args)
    assert not args.output.exists()
    assert keys.is_file() and not list(tmp_path.glob(".solaris-model-*"))
