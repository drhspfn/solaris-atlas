import pytest

from wuwa_story_worker.map_icons import sprite_box


def test_atlas_uv_crop_and_bounds():
    assert sprite_box({'uv0X': .1, 'uv3X': .3, 'uv0Y': .4,
                      'uv3Y': .2}, 1000, 500) == (100, 100, 300, 200)
    with pytest.raises(ValueError):
        sprite_box({'uv0X': -.1, 'uv3X': .3,
                   'uv0Y': .4, 'uv3Y': .2}, 1000, 500)


@pytest.mark.asyncio
@pytest.mark.parametrize("exact_textures", [False, True])
async def test_icon_conversion_bounds_loaded_texture_batches(tmp_path, monkeypatch, exact_textures):
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
        [{"metadata_json": {"icon_source": source}} for source in sources], entity_media=True,
        exact_textures=exact_textures)
    assert [len(batch) for batch in batches] == [8, 8, 3]
    assert set(result) == set(sources)
    filters = {call.args[2] for call in map_icons.export_assets.await_args_list}
    assert filters == ({"|".join(sorted(f"Common/Image/Test/T_{index}" for index in range(19)))}
                       if exact_textures else {"Common/Image/Test/"})

@pytest.mark.asyncio
async def test_exact_sprite_uses_its_atlas_and_matches_qualified_reference(tmp_path, monkeypatch):
    import json
    import subprocess
    from pathlib import Path
    from unittest.mock import AsyncMock

    from PIL import Image

    from wuwa_story_worker import map_icons
    raw = tmp_path / "exports/atlas/files"
    folder = raw / "Client/Content/Aki/UI/UIResources/Common/Atlas/Test"
    folder.mkdir(parents=True)
    for name in ["SP_Icon.uasset", "T_Atlas.uasset", "TPI_Test.uasset"]:
        (folder / name).write_bytes(b"asset")
    source = "/Game/Aki/UI/UIResources/Common/Atlas/Test/SP_Icon"
    texture = "/Game/Aki/UI/UIResources/Common/Atlas/Test/T_Atlas"
    tool = tmp_path / "tool"
    tool.write_bytes(b"tool")
    export = AsyncMock(return_value=raw.parent / "manifest.json")
    monkeypatch.setattr(map_icons, "export_assets", export)
    def convert(command, **kwargs):
        output = Path(command[command.index("-o") + 1])
        out = output / "files/Client/Content/Aki/UI/UIResources/Common/Atlas/Test"
        out.mkdir(parents=True, exist_ok=True)
        if command[command.index("-f") + 1] == "png":
            Image.new("RGBA", (8, 8), "red").save(out / "T_Atlas.png")
        else:
            (out / "TPI_Test.json").write_text(json.dumps([{"Properties": {
                "Sprites": [source + ".SP_Icon"],
                "SpriteAtlasTextureMap": [{"Key": 0, "Value": 0}],
                "SpriteInfoMap": [{"Key": 0, "Value": {"uv0X": 0, "uv3X": .5, "uv0Y": 0, "uv3Y": .5}}],
                "SoftAtlasTextures": [{"AssetPathName": texture + ".T_Atlas"}],
            }}]), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(map_icons.subprocess, "run", convert)
    result = await map_icons.build_icons(tmp_path, tool, tool,
        [{"metadata_json": {"icon_source": source}}], entity_media=True, exact_textures=True)
    assert export.await_args.args[2] == "Common/Atlas/Test/"
    assert source in result
    with Image.open(result[source]["path"]) as image:
        assert image.size == (4, 4)


@pytest.mark.asyncio
async def test_concurrent_icon_requests_reuse_conversion_after_waiting(tmp_path, monkeypatch):
    import asyncio
    import threading
    from pathlib import Path
    from unittest.mock import AsyncMock

    from PIL import Image

    from wuwa_story_worker import map_icons

    raw = tmp_path / "exports/atlas/files"
    relative = Path("Client/Content/Aki/UI/UIResources/Common/Image/Test/T_One")
    (raw / relative).parent.mkdir(parents=True)
    (raw / relative).with_suffix('.uasset').write_bytes(b'asset')
    source = '/Game/Aki/UI/UIResources/Common/Image/Test/T_One'
    tool = tmp_path / 'tool'
    tool.write_bytes(b'tool')
    monkeypatch.setattr(map_icons, 'export_assets', AsyncMock(return_value=raw.parent / 'manifest.json'))
    entered, release = threading.Event(), threading.Event()
    calls = []

    def convert(command, **kwargs):
        calls.append(command)
        entered.set()
        assert release.wait(5)
        destination = Path(command[command.index('-o') + 1]) / 'files' / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', (2, 2)).save(destination.with_suffix('.png'))

    monkeypatch.setattr(map_icons.subprocess, 'run', convert)
    markers = [{'metadata_json': {'icon_source': source}}]
    first = asyncio.create_task(map_icons.build_icons(tmp_path, tool, tool, markers, entity_media=True))
    assert await asyncio.to_thread(entered.wait, 5)
    second = asyncio.create_task(map_icons.build_icons(tmp_path, tool, tool, markers, entity_media=True))
    await asyncio.sleep(0)  # Let the duplicate reach the per-atlas lock before publishing.
    release.set()
    results = await asyncio.wait_for(asyncio.gather(first, second), timeout=10)
    assert results[0] == results[1]
    assert source in results[0]
    assert len(calls) == 1
