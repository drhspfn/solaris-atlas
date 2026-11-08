import hashlib
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story_worker import riglyph_render as render


def source(tmp_path, monkeypatch, clips=None):
    bundle = tmp_path / "source.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"skeletons": [{"file": "rig.skel"}],
                                                     "variants": [{"animation": "idle", "rover": None}]}))
    monkeypatch.setattr(render, "archive_policy", lambda _: {"conversion_allowed": True})
    monkeypatch.setattr(render, "asset_workspace", lambda: tmp_path / "persistent")

    def convert(_source, destination, _cli):
        destination.mkdir()
        (destination / "skeleton.ir.json").write_text(json.dumps({"skeleton": {"bounds": [0, 0, 640, 360],
            "animations": clips if clips is not None else [{"id": "idle", "durationSec": 2}]}}))
        (destination / "manifest.json").write_text("{}")

    monkeypatch.setattr(render, "convert_archive", convert)
    return bundle


def valid_render(args, log, timeout):
    Path(args[args.index("--out") + 1]).write_bytes(b"validated video fixture")
    width = int(args[args.index("--width") + 1])
    height = int(args[args.index("--height") + 1])
    fps = int(args[args.index("--fps") + 1])
    end = float(args[args.index("--end") + 1])
    return {"format": "webm", "frameCount": round(end * fps),
            "probe": {"codec": "vp9", "alpha": True, "width": width, "height": height}}


def test_native_alpha_render_retains_exact_clips_and_original(tmp_path, monkeypatch):
    bundle = source(tmp_path, monkeypatch, [{"id": "idle", "durationSec": 2},
                                            {"id": "pose", "durationSec": 0}])
    digest = bundle.read_bytes()
    calls = []

    def run(args, log, timeout):
        calls.append((args, log))
        return valid_render(args, log, timeout)

    monkeypatch.setattr(render, "_run", run)
    result = render.build_animation(bundle, cli=["riglyph"])
    assert result.default_animation == "idle"
    assert [v["animation"] for v in result.variants] == ["idle", "pose"]
    assert result.variants[1]["duration_seconds"] == 0
    assert all(args[args.index("--backend") + 1] == "native-cpu" for args, _ in calls)
    assert all("--alpha" in args for args, _ in calls)
    assert all(log.is_relative_to(tmp_path / "persistent") for _, log in calls)
    assert result.decision["display"] == "animated"
    assert bundle.read_bytes() == digest
    with zipfile.ZipFile(result.converted) as archive:
        assert {"skeleton.ir.json", "source-manifest.json"} <= set(archive.namelist())


@pytest.mark.parametrize("change", [{"codec": "h264"}, {"alpha": False}, {"width": 1}])
def test_invalid_video_never_becomes_a_published_preview(tmp_path, monkeypatch, change):
    bundle = source(tmp_path, monkeypatch)

    def run(args, log, timeout):
        report = valid_render(args, log, timeout)
        report["probe"].update(change)
        return report

    monkeypatch.setattr(render, "_run", run)
    result = render.build_animation(bundle, cli=["riglyph"])
    assert result.converted.is_file() and result.variants == []
    assert result.decision["state"] == "render_failed"
    assert bundle.exists()


def test_unsupported_composition_keeps_static_without_running_converter(tmp_path, monkeypatch):
    bundle = source(tmp_path, monkeypatch)
    monkeypatch.setattr(render, "archive_policy", lambda _: {"conversion_allowed": False,
                                                            "reasons": [{"code": "material_semantics_unverified"}]})
    monkeypatch.setattr(render, "convert_archive", lambda *_: pytest.fail("must stay static"))
    result = render.build_animation(bundle)
    assert result.converted is None and result.variants == []
    assert result.decision["display"] == "static"


def test_converted_fallback_archive_is_deterministic(tmp_path, monkeypatch):
    bundle = source(tmp_path, monkeypatch)
    monkeypatch.setattr(render, "_run", valid_render)
    first = render.build_animation(bundle).converted.read_bytes()
    second = render.build_animation(bundle).converted.read_bytes()
    assert first == second


@pytest.mark.parametrize('bounds,size', [
    ([0, 0, 1920, 1080], (1920, 1080)),
    ([-10, -20, 1080, 1920], (1080, 1920)),
    ([0, 0, 3200, 1350], (2048, 864)),
    ([0, 0, 600, 600], (600, 600)),
])
def test_framing_keeps_source_canvas_ratio_and_never_upscales(bounds, size):
    result = render.frame_geometry(bounds, 2048)
    assert (result['width'], result['height']) == size
    assert result['source_bounds'] == bounds
    assert result['camera']['x'] == bounds[0] + bounds[2] / 2
    assert result['camera']['y'] == bounds[1] + bounds[3] / 2


def test_zero_source_bounds_use_one_measured_canvas_for_all_clips(tmp_path, monkeypatch):
    bundle = source(tmp_path, monkeypatch)
    convert = render.convert_archive

    def zero_bounds(*args):
        convert(*args)
        path = args[1] / 'skeleton.ir.json'
        ir = json.loads(path.read_text())
        ir['skeleton']['bounds'] = [0, 0, 0, 0]
        path.write_text(json.dumps(ir))

    def run(args, log, timeout):
        if args[1].endswith('riglyph_frame.mjs'):
            return {'bounds': [-1600, -690, 3200, 1350]}
        config = json.loads(Path(args[args.index('--config') + 1]).read_text())
        assert config['camera']['mode'] == 'fixed'
        return valid_render(args, log, timeout)

    monkeypatch.setattr(render, 'convert_archive', zero_bounds)
    monkeypatch.setattr(render, '_run', run)
    result = render.build_animation(bundle)
    assert result.variants[0]['framing_basis'] == 'animation_bounds'
    assert (result.variants[0]['width'], result.variants[0]['height']) == (2048, 864)


@pytest.mark.parametrize('tampered', [False, True])
@pytest.mark.parametrize('package', ['/Game/Rig', '/game/RIG'])
def test_legacy_archive_policy_requires_verified_decoded_sources(tmp_path, tampered, package):
    data = json.dumps([{'Type': 'SpineSkeletonDataAsset'}, {'Type': 'SpineAtlasAsset'}]).encode()
    entry = 'decoded/files/Client/Content/Rig.json'
    manifest = {'packages': [package], 'source_resources': [package], 'files': {
        entry: {'sha256': hashlib.sha256(data).hexdigest(), 'size_bytes': len(data)}}}
    bundle = tmp_path / 'old-source.zip'
    with zipfile.ZipFile(bundle, 'w') as archive:
        archive.writestr('manifest.json', json.dumps(manifest))
        archive.writestr(entry, data + b' ' if tampered else data)
    if tampered:
        with pytest.raises(ValueError, match='checksum'):
            render.archive_policy(bundle)
    else:
        assert render.archive_policy(bundle)['conversion_allowed'] is True


def test_archive_policy_does_not_match_same_basename_in_another_directory(tmp_path):
    data = json.dumps([{'Type': 'SpineSkeletonDataAsset'}, {'Type': 'SpineAtlasAsset'}]).encode()
    entry = 'decoded/files/Client/Content/Other/Rig.json'
    manifest = {'packages': ['/Game/Rig'], 'source_resources': ['/Game/Rig'], 'files': {
        entry: {'sha256': hashlib.sha256(data).hexdigest(), 'size_bytes': len(data)}}}
    bundle = tmp_path / 'source.zip'
    with zipfile.ZipFile(bundle, 'w') as archive:
        archive.writestr('manifest.json', json.dumps(manifest))
        archive.writestr(entry, data)
    assert render.archive_policy(bundle)['conversion_allowed'] is False


def test_archive_policy_rejects_ambiguous_package_casing(tmp_path):
    bundle = tmp_path / 'source.zip'
    with zipfile.ZipFile(bundle, 'w') as archive:
        archive.writestr('manifest.json', json.dumps({'packages': ['/Game/Rig', '/game/RIG']}))
    with pytest.raises(ValueError, match='Ambiguous'):
        render.archive_policy(bundle)


@pytest.mark.parametrize("clips", [[{"id": "idle", "durationSec": float("nan")}],
                                   [{"id": "idle", "durationSec": -1}],
                                   [{"id": "idle", "durationSec": 181}]])
def test_bad_clip_bounds_are_rejected_before_render(tmp_path, monkeypatch, clips):
    bundle = source(tmp_path, monkeypatch, clips)
    monkeypatch.setattr(render, "_run", lambda *_: pytest.fail("must not render"))
    with pytest.raises(ValueError, match="unbounded"):
        render.build_animation(bundle)


@pytest.mark.asyncio
async def test_publication_links_derivatives_without_replacing_source(tmp_path):
    source_file = SimpleNamespace(id=1)
    archive, video = tmp_path / "riglyph.zip", tmp_path / "idle.webm"
    archive.write_bytes(b"archive")
    video.write_bytes(b"video")
    files = [SimpleNamespace(id=i, sha256=hashlib.sha256(data).digest(), size_bytes=len(data))
             for i, data in [(2, b"archive"), (3, b"video")]]
    service = SimpleNamespace(register_file=AsyncMock(side_effect=files),
                              register_variant=AsyncMock(),
                              storage=SimpleNamespace(public_url=lambda key: "https://cdn.test/" + key))
    artifacts = render.AnimationArtifacts(archive, [{"path": video, "animation": "idle",
                                                    "duration_seconds": 2, "mime_type": "video/webm"}],
                                         {"display": "animated"}, "idle")
    result = await render.publish_animation(None, tmp_path / "source.zip", source_file,
                                            service, None, artifacts=artifacts)
    assert result["riglyph_file_id"] == 2
    assert result["animation_preview"]["source_file_id"] == source_file.id == 1
    assert result["animation_preview"]["variants"][0]["file_id"] == 3
    assert "path" not in result["animation_preview"]["variants"][0]
    assert [call.args[3] for call in service.register_variant.await_args_list] == ["riglyph_bundle", "riglyph_preview"]


def test_legacy_prefab_uses_authored_clip_canvas_and_blend_mapping(tmp_path, monkeypatch):
    from wuwa_story_worker import legacy_spine_composition

    bundle = source(tmp_path, monkeypatch)
    monkeypatch.setattr(render, 'archive_policy', lambda _: {'conversion_allowed': False, 'reasons': []})
    plan = {'clip': 'idle', 'clips': {'idle': True}, 'skeleton': 'rig.skel', 'atlas': 'rig.atlas',
            'size': [2560, 1440], 'layer': {'scale': .8, 'origin': [0, 0]},
            'background': True,
            'blend': {'normal': 'normal', 'additive': 'additive', 'multiply': 'normal', 'screen': 'normal'}}
    monkeypatch.setattr(legacy_spine_composition, 'legacy_plan', lambda _: plan)
    monkeypatch.setenv('WUWA_RIGLYPH_MAX_EDGE', '640')
    original = bundle.read_bytes()
    def convert(_source, output, _cli, **kwargs):
        assert kwargs == {'skeleton_file': 'rig.skel', 'atlas_file': 'rig.atlas'}
        output.mkdir()
        (output / 'manifest.json').write_text('{}')
        (output / 'skeleton.ir.json').write_text(json.dumps({'skeleton': {
            'bounds': [0, 0, 1, 1], 'slots': [{'blend': 'multiply'}],
            'animations': [{'id': 'idle', 'durationSec': 2}, {'id': 'unrequested', 'durationSec': 1}]}}))
    def run(args, log, timeout):
        ir = json.loads(Path(args[2]).read_text())
        assert ir['skeleton']['slots'][0]['blend'] == 'normal'
        result = valid_render(args, log, timeout)
        result['probe']['decodedAlphaVerified'] = True
        return result
    monkeypatch.setattr(render, 'convert_archive', convert)
    monkeypatch.setattr(render, '_run', run)
    backgrounds = []
    monkeypatch.setattr(render, '_compose_black', lambda *args: backgrounds.append(args))
    result = render.build_animation(bundle, cli=['riglyph'])
    assert result.decision['state'] == 'rendered'
    assert [(v['animation'], v['loop'], v['framing_basis']) for v in result.variants] == [('idle', True, 'prefab_canvas')]
    assert (result.variants[0]['width'], result.variants[0]['height']) == (640, 360)
    assert len(backgrounds) == 1 and result.variants[0]['background'] == 'black'
    assert bundle.read_bytes() == original


def test_legacy_missing_authored_transition_never_renders_partial_selection(tmp_path, monkeypatch):
    from wuwa_story_worker import legacy_spine_composition

    bundle = source(tmp_path, monkeypatch)
    monkeypatch.setattr(render, 'archive_policy', lambda _: {'conversion_allowed': False, 'reasons': []})
    monkeypatch.setattr(legacy_spine_composition, 'legacy_plan', lambda _: {
        'clip': 'idle', 'clips': {'idle': True, 'missing': False},
        'skeleton': 'rig.skel', 'atlas': 'rig.atlas',
    })
    convert = render.convert_archive
    monkeypatch.setattr(render, 'convert_archive', lambda source, output, cli, **_: convert(source, output, cli))
    monkeypatch.setattr(render, '_run', lambda *_: pytest.fail('missing transition must fail before rendering'))
    with pytest.raises(ValueError, match='dialogue animation is absent'):
        render.build_animation(bundle, cli=['riglyph'])


def test_legacy_background_failure_keeps_static_fallback(tmp_path, monkeypatch):
    from wuwa_story_worker import legacy_spine_composition

    bundle = source(tmp_path, monkeypatch)
    monkeypatch.setattr(render, 'archive_policy', lambda _: {'conversion_allowed': False, 'reasons': []})
    monkeypatch.setattr(legacy_spine_composition, 'legacy_plan', lambda _: {
        'clip': 'idle', 'clips': {'idle': True}, 'skeleton': 'rig.skel', 'atlas': 'rig.atlas',
        'size': [2560, 1440], 'layer': {'scale': .8, 'origin': [0, 0]}, 'background': True,
        'blend': {'normal': 'normal', 'additive': 'additive', 'multiply': 'normal', 'screen': 'normal'},
    })
    convert = render.convert_archive
    monkeypatch.setattr(render, 'convert_archive', lambda source, output, cli, **_: convert(source, output, cli))
    def run(args, log, timeout):
        result = valid_render(args, log, timeout)
        result['probe']['decodedAlphaVerified'] = True
        return result
    monkeypatch.setattr(render, '_run', run)
    def fail(*_):
        raise ValueError('background failure')
    monkeypatch.setattr(render, '_compose_black', fail)
    result = render.build_animation(bundle, cli=['riglyph'])
    assert result.decision['state'] == 'render_failed'
    assert result.variants == [] and bundle.exists()
