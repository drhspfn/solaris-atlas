import pytest

from wuwa_story_worker.archive_index import index_voice_paths
from wuwa_story_worker.map_icons import sprite_box


def package_index(root, sources):
    log = root / "archive-index.log"
    paths = ["Client/Content/" + source.removeprefix("/Game/").split(".", 1)[0] + suffix
             for source in sources for suffix in (".uasset", ".uexp")]
    log.write_text("".join(f"[File] {path}\n" for path in paths) + f"[Done] Listed {len(paths)} files\n",
                   encoding="utf-8")
    index_voice_paths(log)


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
        packages = Path(command[command.index("-c") + 1]).read_text(encoding="utf-8").splitlines()
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
    package_index(tmp_path, sources)
    requested = sources + (["/Game/Aki/UI/UIResources/Common/Image/Test/Missing"] if exact_textures else [])
    result = await map_icons.build_icons(tmp_path, fmodel, converter,
        [{"metadata_json": {"icon_source": source}} for source in requested], entity_media=True,
        exact_textures=exact_textures)
    assert [len(batch) for batch in batches] == [8, 8, 3]
    assert set(result) == set(sources)
    filters = {call.args[2] for call in map_icons.export_assets.await_args_list}
    assert filters == ({"|".join(sorted(f"Common/Image/Test/T_{index}" for index in range(19)))}
                       if exact_textures else {"Common/Image/Test/"})

@pytest.mark.asyncio
@pytest.mark.parametrize("bad_index", [None, -1, 1, "0", .5, True, "missing_map", "missing_textures"])
async def test_exact_sprite_uses_its_atlas_and_matches_qualified_reference(tmp_path, monkeypatch, capsys, bad_index):
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
        (folder / name).with_suffix(".uexp").write_bytes(b"export")
    source = "/Game/Aki/UI/UIResources/Common/Atlas/Test/SP_Icon"
    broken = "/Game/Aki/UI/UIResources/Common/Atlas/Test/SP_Broken"
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
            data = {
                "Sprites": [source + ".SP_Icon"],
                "SpriteAtlasTextureMap": [{"Key": 0, "Value": 0}],
                "SpriteInfoMap": [{"Key": 0, "Value": {"uv0X": 0, "uv3X": .5, "uv0Y": 0, "uv3Y": .5}}],
                "SoftAtlasTextures": [{"AssetPathName": texture + ".T_Atlas"}],
            }
            malformed = {**data, "Sprites": [broken],
                         "SpriteAtlasTextureMap": [{"Key": 0, "Value": bad_index}]}
            if bad_index == "missing_map":
                malformed.pop("SpriteAtlasTextureMap")
            elif bad_index == "missing_textures":
                malformed["SpriteAtlasTextureMap"] = [{"Key": 0, "Value": 0}]
                malformed.pop("SoftAtlasTextures")
            (out / "TPI_Test.json").write_text(json.dumps(
                [{"Properties": data}, {"Properties": malformed}]), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(map_icons.subprocess, "run", convert)
    package_index(tmp_path, [source])
    alias = source.replace("SP_Icon", "SP_icon") + ".SP_icon"
    result = await map_icons.build_icons(tmp_path, tool, tool,
        [{"metadata_json": {"icon_source": value}} for value in [source, alias, broken]],
        entity_media=True, exact_textures=True)
    assert export.await_args.args[2] == "Common/Atlas/Test/"
    assert source in result
    assert result[alias] == result[source]
    assert broken not in result
    assert "WARNING: Missing atlas texture reference: " + broken in capsys.readouterr().out
    assert set(result[source]["raw_paths"]) == {
        str((folder / (name + suffix)).resolve())
        for name in ["SP_Icon", "T_Atlas", "TPI_Test"] for suffix in [".uasset", ".uexp"]
    }
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


@pytest.mark.asyncio
async def test_all_absent_images_do_not_export_a_false_substring_match(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    from wuwa_story_worker import map_icons

    source = "/Game/Aki/UI/UIResources/Common/Image/Test/Removed"
    package_index(tmp_path, [source + "Extra"])
    tool = tmp_path / "tool"
    tool.write_bytes(b"tool")
    export = AsyncMock(side_effect=AssertionError("absent package must never reach FModel"))
    monkeypatch.setattr(map_icons, "export_assets", export)
    result = await map_icons.build_icons(tmp_path, tool, tool,
        [{"metadata_json": {"icon_source": source}}], entity_media=True, exact_textures=True)
    assert result == {}
    export.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("packed", [False, True, "discrete"])
@pytest.mark.parametrize("stale_cache", [False, True])
async def test_sp_named_textures_reach_existing_converter(tmp_path, monkeypatch, packed, stale_cache):
    """SP_ is an asset name, not a reliable Unreal export type discriminator."""
    import hashlib
    import json
    import subprocess
    from pathlib import Path
    from unittest.mock import AsyncMock

    from PIL import Image

    from wuwa_story_worker import map_icons

    directory = "Aki/UI/UIResources/Common/Atlas"
    source = f"/Game/{directory}/SP_CommonDefault"
    texture = f"/Game/{directory}/_SpriteTextures/SP_CommonDefault" if packed else source
    raw = tmp_path / "exports/atlas/files"
    names = [source, texture, f"/Game/{directory}/TPI_Common"] if packed else [source]
    for name in names:
        package = raw / ("Client/Content/" + name[6:] + ".uasset")
        package.parent.mkdir(parents=True, exist_ok=True)
        package.write_bytes(b"asset")
        package.with_suffix(".uexp").write_bytes(b"export")
    tool = tmp_path / "tool"
    tool.write_bytes(b"tool")
    monkeypatch.setattr(map_icons, "export_assets", AsyncMock(return_value=raw.parent / "manifest.json"))
    package_index(tmp_path, [source])

    # The old final cache can contain a valid but empty result. Its decode receipt
    # can also be valid while lacking PNGs for every skipped SP_ package.
    identity_parts = [[source], map_icons._sha256(tool), map_icons._sha256(tool), "exact_textures_v2"]
    if packed == "discrete":
        identity_parts.append("sp_textures_v1")
    identity = json.dumps(identity_parts, sort_keys=True)
    cache = tmp_path / "entity-image-cache" / (hashlib.sha256(identity.encode()).hexdigest() + ".json")
    old_decoded = tmp_path / "maps/icons" / ("entity-atlas-" + map_icons._sha256(tool)[:16])
    if packed == "discrete":
        old_decoded = old_decoded.with_name(old_decoded.name + "-sp-textures-v1")
    if stale_cache:
        cache.parent.mkdir()
        cache.write_text(json.dumps({"icons": {}, "files": {}}), encoding="utf-8")
        old_decoded.mkdir(parents=True)
        (old_decoded / "decode-receipt.json").write_text("{}", encoding="utf-8")
    converted = []

    def convert(command, **kwargs):
        output = Path(command[command.index("-o") + 1])
        packages = Path(command[command.index("-c") + 1]).read_text(encoding="utf-8").splitlines()
        if command[command.index("-f") + 1] == "png":
            converted.extend(packages)
            for package in packages:
                if package == "files/Client/Content/" + texture[6:] + ".uasset":
                    target = (output / package).with_suffix(".png")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    Image.new("RGBA", (8, 8), "red").save(target)
        else:
            target = output / f"files/Client/Content/{directory}/TPI_Common.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps([{"Properties": {
                "Sprites": [source + ".SP_CommonDefault"],
                # Production TPI_Common retains stale page IDs in discrete mode.
                "bPackDiscreteSprites": packed == "discrete",
                "SpriteAtlasTextureMap": [{"Key": "0", "Value": 1 if packed == "discrete" else 0}],
                "SpriteInfoMap": [{"Key": "0", "Value": {
                    "uv0X": 0, "uv3X": .5, "uv0Y": 0, "uv3Y": .5}}],
                "SoftAtlasTextures": [{"AssetPathName": texture + ".SP_CommonDefault"},
                                      {"AssetPathName": "/Game/UnrelatedLegacyPage"}],
            }}]), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(map_icons.subprocess, "run", convert)
    markers = [{"metadata_json": {"icon_source": source}}]
    result = await map_icons.build_icons(tmp_path, tool, tool, markers,
                                        entity_media=True, exact_textures=True)
    assert "files/Client/Content/" + texture[6:] + ".uasset" in converted
    with Image.open(result[source]["path"]) as image:
        assert image.size == ((4, 4) if packed else (8, 8))
    assert str((raw / ("Client/Content/" + texture[6:] + ".uasset")).resolve()) in result[source]["raw_paths"]
    converted.clear()
    assert await map_icons.build_icons(tmp_path, tool, tool, markers,
                                       entity_media=True, exact_textures=True) == result
    assert converted == []


@pytest.mark.asyncio
async def test_exact_texture_matches_archive_case_and_preserves_authored_identity(tmp_path, monkeypatch):
    import json
    from pathlib import Path
    from unittest.mock import AsyncMock

    from PIL import Image

    from wuwa_story_worker import map_icons

    authored = "/Game/Aki/UI/UIResources/Common/Image/IconRolePile/T_IconRole_Pile_luhesi_UI"
    canonical = authored.replace("_luhesi_", "_Luhesi_")
    raw = tmp_path / "exports/role/files"
    package = raw / ("Client/Content/" + canonical[6:] + ".uasset")
    package.parent.mkdir(parents=True)
    package.write_bytes(b"asset")
    package.with_suffix(".uexp").write_bytes(b"export")
    tool = tmp_path / "tool"
    tool.write_bytes(b"tool")
    package_index(tmp_path, [canonical])
    monkeypatch.setattr(map_icons, "export_assets", AsyncMock(return_value=raw.parent / "manifest.json"))

    def convert(command, **kwargs):
        output = Path(command[command.index("-o") + 1])
        target = output / ("files/Client/Content/" + canonical[6:] + ".png")
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGBA", (8, 8), "red").save(target)

    monkeypatch.setattr(map_icons.subprocess, "run", convert)
    result = await map_icons.build_icons(tmp_path, tool, tool,
        [{"metadata_json": {"icon_source": authored}}], entity_media=True, exact_textures=True)
    assert set(result) == {authored}
    assert result[authored]["raw_paths"] == [str(package.resolve()), str(package.with_suffix(".uexp").resolve())]
    assert json.loads(next((tmp_path / "entity-image-cache").glob("*.json")).read_text(encoding="utf-8"))["icons"] == result
