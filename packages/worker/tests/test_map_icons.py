import pytest

from wuwa_story_worker.map_icons import sprite_box


def test_atlas_uv_crop_and_bounds():
    assert sprite_box({'uv0X': .1, 'uv3X': .3, 'uv0Y': .4,
                      'uv3Y': .2}, 1000, 500) == (100, 100, 300, 200)
    with pytest.raises(ValueError):
        sprite_box({'uv0X': -.1, 'uv3X': .3,
                   'uv0Y': .4, 'uv3Y': .2}, 1000, 500)


@pytest.mark.asyncio
async def test_icon_conversion_bounds_loaded_texture_batches(tmp_path, monkeypatch):
    import subprocess
    from pathlib import Path
    from unittest.mock import AsyncMock

    from PIL import Image

    from wuwa_story_worker import map_icons
    raw = tmp_path / "exports/group/files"
    folder = raw / "Client/Content/Aki/UI/UIResources/Common/Image/Test"
    folder.mkdir(parents=True)
    sources = []
    for index in range(19):
        (folder / f"T_{index}.uasset").write_bytes(b"source")
        sources.append(f"/Game/Aki/UI/UIResources/Common/Image/Test/T_{index}.T_{index}")
    fmodel, converter = tmp_path / "fmodel", tmp_path / "converter"
    fmodel.write_bytes(b"tool")
    converter.write_bytes(b"tool")
    monkeypatch.setattr(map_icons, "export_assets", AsyncMock(return_value=raw.parent / "manifest.json"))
    batches = []
    def convert(command, **kwargs):
        packages = Path(command[command.index("-c") + 1]).read_text().splitlines()
        assert len(packages) <= 8
        assert kwargs["env"]["DOTNET_PROCESSOR_COUNT"] == "2"
        batches.append(packages)
        destination = Path(command[command.index("-o") + 1])
        for package in packages:
            target = (destination / package).with_suffix(".png")
            target.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (2, 2)).save(target)
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(map_icons.subprocess, "run", convert)
    result = await map_icons.build_icons(tmp_path, fmodel, converter,
        [{"metadata_json": {"icon_source": source}} for source in sources], entity_media=True)
    assert [len(batch) for batch in batches] == [8, 8, 3]
    assert set(result) == set(sources)
