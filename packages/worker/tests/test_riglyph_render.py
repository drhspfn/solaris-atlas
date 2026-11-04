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
        (destination / "skeleton.ir.json").write_text(json.dumps({"skeleton": {
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
    with pytest.raises(ValueError, match="complete alpha WebM"):
        render.build_animation(bundle, cli=["riglyph"])
    assert bundle.exists()


def test_unsupported_composition_keeps_static_without_running_converter(tmp_path, monkeypatch):
    bundle = source(tmp_path, monkeypatch)
    monkeypatch.setattr(render, "archive_policy", lambda _: {"conversion_allowed": False,
                                                            "reasons": [{"code": "material_semantics_unverified"}]})
    monkeypatch.setattr(render, "convert_archive", lambda *_: pytest.fail("must stay static"))
    result = render.build_animation(bundle)
    assert result.converted is None and result.variants == []
    assert result.decision["display"] == "static"


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
