import json
import struct
import subprocess
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story_worker import character_models as worker
from wuwa_story_worker.model_bundle import animation_exclusion
from wuwa_story_worker.model_sources import glb_summary


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [False, True])
async def test_blender_failure_preserves_only_bounded_owned_log(tmp_path, monkeypatch, timeout):
    failure = (
        subprocess.TimeoutExpired(["blender"], 1800, output=b"untrusted captured output")
        if timeout
        else subprocess.CalledProcessError(1, ["blender"], output=b"untrusted captured output")
    )
    (tmp_path / "metadata.log").write_text("private converter secret")
    (tmp_path / "convert.log").write_text("private converter secret")

    async def fail(args, log, seconds):
        assert args == ["blender"]
        assert log == tmp_path / "blender.log"
        assert seconds == 1800
        log.write_bytes(
            b"discarded prefix\n" + b"x" * 20000 + b"\xff\x00\x1b[31m"
            b"Traceback: actual Blender root cause\x1b[0m\n"
            b"\x1b]0;terminal title\x07final diagnostic\n"
        )
        raise failure

    monkeypatch.setattr(worker, "tool", fail)
    with pytest.raises(RuntimeError) as caught:
        await worker.run_blender(["blender"], tmp_path)
    message = str(caught.value)
    assert ("timed out after 1800 seconds" if timeout else "exited with code 1") in message
    assert "Traceback: actual Blender root cause\nfinal diagnostic\n" in message
    assert "discarded prefix" not in message
    assert "secret" not in message
    assert "untrusted captured output" not in message
    assert "terminal title" not in message
    assert "\x1b" not in message
    assert "\x00" not in message
    assert len(message.split("(last 8 KiB):\n", 1)[1].encode("utf-8")) <= 8192
    assert caught.value.__cause__ is failure


@pytest.mark.asyncio
async def test_blender_missing_log_preserves_failure_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(
        worker, "tool", AsyncMock(side_effect=subprocess.CalledProcessError(2, ["blender"]))
    )
    with pytest.raises(RuntimeError, match="exited with code 2.*") as caught:
        await worker.run_blender(["blender"], tmp_path)
    assert "Blender log unavailable" in str(caught.value)


@pytest.mark.parametrize(
    "metadata,tracks,curves,reason",
    [
        (True, 0, 37, "curves_only"),
        (True, 1, 0, None),
        (True, 0, 0, "error"),
        (False, 1, 37, "error"),
    ],
)
def test_empty_decode_fails_but_curves_only_is_explicitly_excluded(
    metadata, tracks, curves, reason
):
    dto = SimpleNamespace(
        metadata=SimpleNamespace(num_frames=121, additive_anim_type=0) if metadata else None,
        tracks=[None] * tracks,
        curves=[None] * curves,
    )
    if reason == "error":
        with pytest.raises(ValueError, match="no decoded"):
            animation_exclusion(dto, require_decoded=True)
    else:
        assert animation_exclusion(dto, require_decoded=True) == reason


def test_additive_and_single_frame_exclusions_preserve_full_export_policy():
    dto = SimpleNamespace(
        metadata=SimpleNamespace(num_frames=2, additive_anim_type=1), tracks=[None], curves=[]
    )
    assert animation_exclusion(dto, require_decoded=True) == "additive"
    dto.metadata.additive_anim_type = 0
    dto.metadata.num_frames = 1
    assert animation_exclusion(dto, require_decoded=True) == "single_frame"
    dto.tracks = []
    assert animation_exclusion(dto) == "no_decoded_tracks"


def animation_glb(path, mutate=lambda data: None):
    binary = struct.pack("<8f", 0, 1, 0, 0, 0, 1, 2, 3)
    data = {
        "asset": {"version": "2.0"},
        "nodes": [{"name": "Root"}],
        "scenes": [{"nodes": [0]}],
        "scene": 0,
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [{"buffer": 0, "byteLength": len(binary)}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "type": "SCALAR", "count": 2},
            {"bufferView": 0, "byteOffset": 8, "componentType": 5126, "type": "VEC3", "count": 2},
        ],
        "animations": [
            {
                "name": "Stand1",
                "channels": [{"sampler": 0, "target": {"node": 0, "path": "translation"}}],
                "samplers": [{"input": 0, "output": 1}],
            }
        ],
    }
    mutate(data)
    payload = json.dumps(data).encode()
    payload += b" " * (-len(payload) % 4)
    path.write_bytes(
        struct.pack("<4sIII4s", b"glTF", 2, 28 + len(payload) + len(binary), len(payload), b"JSON")
        + payload
        + struct.pack("<I4s", len(binary), b"BIN\0")
        + binary
    )


def test_animation_summary_reads_actual_clips_and_rejects_them_in_rig_mode(tmp_path):
    path = tmp_path / "animations.glb"
    animation_glb(path)
    summary = glb_summary(path, allow_animations=True)
    assert summary["animations"] == 1
    assert summary["textures"] == 0
    assert summary["clips"] == [{"name": "Stand1", "channels": 1, "seconds": 1.0}]
    with pytest.raises(ValueError):
        glb_summary(path)
    from wuwa_story_worker.model_animations import animation_summary

    with pytest.raises(ValueError, match="non-rig bone"):
        animation_summary(path, {"DifferentBone"})


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.update(meshes=[{}]),
        lambda d: d.update(images=[{}]),
        lambda d: d.update(textures=[{}]),
        lambda d: d.update(materials=[{}]),
        lambda d: d.update(animations=[]),
        lambda d: d["buffers"][0].update(uri="external.bin"),
        lambda d: d["animations"][0].update(channels=[]),
        lambda d: d["animations"][0]["channels"][0]["target"].update(node=5),
        lambda d: d["animations"][0]["channels"][0]["target"].update(path="weights"),
        lambda d: d["accessors"][1].update(count=100),
        lambda d: d["accessors"][1].update(bufferView=-1),
        lambda d: d["animations"][0]["samplers"][0].update(input=-1),
        lambda d: d["animations"][0]["channels"][0]["target"].update(node=-1),
        lambda d: d["accessors"][1].update(count=1),
        lambda d: d["bufferViews"][0].update(byteLength=1000),
        lambda d: d["nodes"].append({"name": "Root"}),
    ],
)
def test_animation_summary_rejects_invalid_standalone_contract(tmp_path, change):
    path = tmp_path / "animations.glb"
    animation_glb(path, change)
    with pytest.raises(ValueError):
        glb_summary(path, allow_animations=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("include", [True, False])
async def test_extraction_scope_includes_character_siblings_shared_moves_and_acl(
    tmp_path, monkeypatch, include
):
    model = "Aki/Character/Role/FemaleM/Shouanren/R2T1ShouanrenMd10011/Model/Rig"
    receipt = tmp_path / "exports/manifest.json"
    receipt.parent.mkdir()
    receipt.write_text('{"files": []}')
    exported = AsyncMock(return_value=receipt)
    monkeypatch.setattr(worker, "export_assets", exported)
    monkeypatch.setattr(worker, "tool", AsyncMock())
    expected = tmp_path / "converted/Client/Content" / (model + ".uemodel")
    expected.parent.mkdir(parents=True)
    expected.touch()
    # The real caller starts with an empty converted directory.
    original_mkdir = type(tmp_path).mkdir
    monkeypatch.setattr(
        type(tmp_path),
        "mkdir",
        lambda p, *a, **kw: None if p == tmp_path / "converted" else original_mkdir(p, *a, **kw),
    )
    await worker.extract_model(
        tmp_path, {"model": model, "blueprints": ["Aki/AuthoredBP"]}, "fmodel", "cue", include
    )
    filters = exported.call_args.args[2].split("|")
    assert filters == (
        [
            "Aki/Character/Role/FemaleM/Shouanren/",
            "Aki/AuthoredBP",
            "Aki/Character/Role/FemaleM/BaseAnim/",
            "ACLAnim",
            "ACLPlugin.uplugin",
            "Shouanren",
        ]
        if include
        else [model.rsplit("/", 1)[0] + "/", "Aki/AuthoredBP"]
    )


def job_payload():
    return dict(
        run_id=1,
        character_key="character:1505",
        owner_node_id=2,
        release_id=3,
        game_version="3.0",
        asset_version="3.0.0",
        download_id="a" * 64,
        tier="hd",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("include", [None, True, False])
async def test_publication_validates_legacy_payload_and_publishes_actual_count(
    tmp_path, monkeypatch, include
):
    payload = job_payload()
    if include is not None:
        payload["include_animations"] = include
    job = worker.CharacterModelJob.model_validate(payload)
    run = SimpleNamespace(metadata_json={"payload": deepcopy(payload)}, status="running")
    references = []

    async def reference(*args, **kwargs):
        ref = SimpleNamespace(kind=kwargs["reference_type"])
        references.append(ref)
        return ref

    service = SimpleNamespace(
        prepare_file=AsyncMock(return_value=SimpleNamespace(digest=b"a" * 32)),
        register_file=AsyncMock(return_value=SimpleNamespace(id=4)),
        register_reference=reference,
        register_variant=AsyncMock(),
    )
    session = SimpleNamespace(scalar=AsyncMock(return_value=run), commit=AsyncMock())

    @asynccontextmanager
    async def factory():
        yield session

    monkeypatch.setattr(worker, "SessionFactory", factory)
    monkeypatch.setattr(worker, "get_settings", lambda: None)
    monkeypatch.setattr(worker, "S3Storage", lambda settings: None)
    monkeypatch.setattr(worker, "FileRegistrationService", lambda storage: service)
    monkeypatch.setattr(worker, "publish_viewer", AsyncMock(return_value="viewer/index.html"))
    monkeypatch.setattr(worker, "ensure_glb_download", AsyncMock())
    summaries = {}
    if include:
        animation_glb(tmp_path / "animations.glb")
        summaries["animations"] = glb_summary(tmp_path / "animations.glb", allow_animations=True)
    await worker.publish_bundle(job, tmp_path, summaries, {"model": "Aki/Rig"})
    assert run.status == "completed"
    assert run.raw_output["animations"] == int(bool(include))
    assert {ref.kind for ref in references} == {
        "character_model_preview",
        "character_model_download",
    } | ({"character_model_animations"} if include else set())
    assert all(ref.metadata_json["model_bundle_id"] == "1" for ref in references)
    assert all(ref.metadata_json["animation_count"] == int(bool(include)) for ref in references)
    assert worker.ensure_glb_download.await_count == (2 if include else 1)
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_requested_animation_file_never_publishes_partial_success(
    tmp_path, monkeypatch
):
    job = worker.CharacterModelJob.model_validate({**job_payload(), "include_animations": True})
    storage = AsyncMock()
    monkeypatch.setattr(worker, "S3Storage", storage)
    with pytest.raises(FileNotFoundError):
        await worker.publish_bundle(job, tmp_path, {}, {})
    storage.assert_not_called()


@pytest.mark.asyncio
async def test_claim_accepts_legacy_payload_but_rejects_changed_animation_request(monkeypatch):
    payload = job_payload()
    run = SimpleNamespace(metadata_json={"payload": deepcopy(payload)}, status="completed")
    session = SimpleNamespace(get=AsyncMock(return_value=run))

    @asynccontextmanager
    async def context(*args):
        yield session

    monkeypatch.setattr(worker, "engine", SimpleNamespace(connect=context))
    monkeypatch.setattr(worker, "job_lock", context)
    monkeypatch.setattr(worker, "SessionFactory", context)
    await worker.process_character_model(payload)
    with pytest.raises(ValueError, match="payload does not match"):
        await worker.process_character_model({**payload, "include_animations": True})


@pytest.mark.asyncio
@pytest.mark.parametrize("include", [True, False])
async def test_build_uses_actual_skeleton_and_keeps_animation_choice(
    tmp_path, monkeypatch, include
):
    model = "Aki/Character/Role/FemaleM/Test/Variant/Model/Rig"
    cooked = tmp_path / "source-json"
    metadata = cooked / "Client/Content" / (model + ".json")
    metadata.parent.mkdir(parents=True)
    metadata.write_text(
        json.dumps(
            [
                {
                    "Type": "SkeletalMesh",
                    "Name": "Rig",
                    "Properties": {"Skeleton": {"ObjectPath": "/Game/Aki/ActualSkeleton.1"}},
                }
            ]
        )
    )
    plugin = tmp_path / "plugin"
    (plugin / "io_scene_ueformat").mkdir(parents=True)
    monkeypatch.setenv("WUWA_UEFORMAT_PLUGIN", str(plugin))
    executable = tmp_path / "tool"
    executable.touch()
    monkeypatch.setattr(worker, "tool_path", lambda name: executable)
    extraction = AsyncMock(
        return_value=(tmp_path / "model", tmp_path / "converted", cooked, {"files": []})
    )
    monkeypatch.setattr(worker, "extract_model", extraction)

    async def blender(args, log, timeout):
        assert args[args.index("--skeleton") + 1] == "/Game/Aki/ActualSkeleton"
        assert ("--with-animations" in args) is include
        assert ("--rig-only" in args) is not include
        output = tmp_path / "bundle"
        if include:
            animation_glb(output / "animations.glb")
            summary = glb_summary(output / "animations.glb", allow_animations=True)
            (output / "animations.json").write_text(json.dumps(summary))
            (output / "report.json").write_text('{"animation_coverage": {"playable_clips": 1}}')

    real_summary = worker.glb_summary
    monkeypatch.setattr(worker, "tool", blender)
    monkeypatch.setattr(
        worker,
        "glb_summary",
        lambda path, **kwargs: real_summary(path, **kwargs) if kwargs else {"bytes": 10},
    )
    _, summaries, provenance = await worker.build_bundle(
        tmp_path, {"model": model, "blueprints": []}, include_animations=include
    )
    assert extraction.call_args.args[-1] is include
    assert ("animations" in summaries) is include
    assert provenance["include_animations"] is include
    if include:
        assert provenance["animations"]["animations"] == 1
        assert provenance["animation_coverage"]["playable_clips"] == 1
