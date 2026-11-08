import io
import math
import shutil
import subprocess
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image

from test_visual_memory_service import memory  # noqa: F401
from wuwa_story.visual_memory.video import extract_frame, probe_duration, sample_times


class VideoEngine:
    def model_identity(self):
        return {"embedding": "video-test-model", "detector": "video-test-detector"}

    def embed(self, image):
        return SimpleNamespace(
            model_key="video-test-model",
            vector=[1.0] + [0.0] * 383,
            config={"weights_sha256": "video-test"},
        )

    def detect(self, image):
        return [{"bbox": [0, 0, image.width, image.height], "score": 0.9}]


def test_sampling_covers_full_duration_without_global_frame_cap():
    short = sample_times(10, 3)
    long = sample_times(360, 3)
    assert short == [0, 3, 6, 9, 9.8]
    assert len(long) == 121 and long[-1] == 359.8
    assert all(b - a <= 3 for a, b in zip(long, long[1:], strict=False))
    for args in [(0, 5), (1, math.nan), (1, 0), (1, 61)]:
        with pytest.raises(ValueError):
            sample_times(*args)


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg unavailable"
)
def test_extract_synthetic_video(tmp_path):
    video = tmp_path / "sample.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=red:s=64x48:r=10:d=2",
            "-c:v",
            "mpeg4",
            "-y",
            str(video),
        ],
        check=True,
        timeout=30,
    )
    duration = probe_duration(video)
    assert duration == pytest.approx(2, abs=0.2)
    for timestamp in sample_times(duration, interval=1):
        with Image.open(io.BytesIO(extract_frame(video, timestamp))) as image:
            assert image.format == "JPEG"
            assert image.width <= 1280 and image.height <= 720
    with pytest.raises(ValueError):
        extract_frame(video, math.inf)


@pytest.mark.asyncio
async def test_published_graph_asset_without_media_asset_row(memory, tmp_path, monkeypatch):  # noqa: F811
    from sqlalchemy import select

    from wuwa_story.api.routes import cutscenes
    from wuwa_story.db.models import MediaAsset, Node, NodeType
    from wuwa_story.storage.service import FileRegistrationService
    from wuwa_story.visual_memory.video import VideoReviewService

    base, session, _ = memory
    registration = FileRegistrationService(base.storage)
    path = tmp_path / "published.mp4"
    path.write_bytes(b"registered video bytes")
    file = await registration.register_file(session, path, "video_mp4", mime_type="video/mp4")
    asset_type = await session.scalar(select(NodeType.id).where(NodeType.key == "asset_reference"))
    scene_type = await session.scalar(select(NodeType.id).where(NodeType.key == "cutscene"))
    other_type = await session.scalar(select(NodeType.id).where(NodeType.key == "character"))
    asset = Node(type_id=asset_type, canonical_key="asset:ue:/Game/visual-test-" + uuid4().hex)
    scene = Node(type_id=scene_type, canonical_key="cutscene:visual-test-" + uuid4().hex)
    other = Node(type_id=other_type, canonical_key="character:visual-test-" + uuid4().hex)
    session.add_all([asset, scene, other])
    await session.flush()
    assert await session.get(MediaAsset, asset.id) is None
    reference = await registration.register_reference(
        session, file_id=file.id, reference_type="cutscene_video_v2", owner_node_id=asset.id
    )
    reference.metadata_json = {"asset_version": "3.7.1", "cutscene_key": scene.canonical_key}
    foreign = await registration.register_reference(
        session, file_id=file.id, reference_type="cutscene_video_v2", owner_node_id=other.id
    )
    foreign.metadata_json = {"asset_version": "3.7.1", "cutscene_key": scene.canonical_key}
    await session.flush()

    async def detail(name, version, locale, db):
        return {"node_id": scene.id, "game_version": "3.7.0", "event": {"playback": {
            "asset_version": "3.7.1", "media": {"full": {"asset_node_id": asset.id}},
        }}}

    monkeypatch.setattr(cutscenes, "detail", detail)
    service = VideoReviewService(session, base.storage, VideoEngine())
    source = (await service.sources(scene.canonical_key.removeprefix("cutscene:"), "3.7.0"))["sources"][0]
    assert source["source_reference_id"] == reference.id
    assert source["source_sha256"] == file.sha256.hex()
    assert source["game_version"] == "3.7.0" and source["asset_version"] == "3.7.1"
    preview = await service.source_by_reference(reference.id, "3.7.0", "3.7.1", scene.canonical_key)
    assert preview["file_id"] == file.id
    scan = await service.enqueue(asset.id, game_version="3.7.0", asset_version="3.7.1",
                                 cutscene_key=scene.canonical_key, source_reference_id=reference.id,
                                 source_sha256=file.sha256.hex())
    assert scan["source"]["source_reference_id"] == reference.id
    with pytest.raises(ValueError, match="registered game media asset"):
        await service._published(other.id, "3.7.1", scene.canonical_key)
    with pytest.raises(ValueError, match="bytes changed"):
        await service.enqueue(asset.id, asset_version="3.7.1", source_sha256="00" * 32)


@pytest.mark.asyncio
@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg unavailable"
)
async def test_durable_video_sampling_review_resume_and_dedup(memory, tmp_path, monkeypatch):  # noqa: F811
    from sqlalchemy import func, select

    from wuwa_story.db.models import (
        MediaAsset,
        Node,
        NodeType,
        ProcessingRun,
        VisualReference,
        VisualVideoFrame,
    )
    from wuwa_story.storage.service import FileRegistrationService
    from wuwa_story.visual_memory import video as module
    from wuwa_story.visual_memory.video import VideoReviewService

    base, session, actor = memory
    path = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=red:s=64x48:r=10:d=6",
            "-c:v",
            "mpeg4",
            "-y",
            str(path),
        ],
        check=True,
        timeout=30,
    )
    registration = FileRegistrationService(base.storage)
    file = await registration.register_file(session, path, "video_mp4", mime_type="video/mp4")
    node_type = await session.scalar(select(NodeType.id).where(NodeType.key == "media_asset"))
    node = Node(type_id=node_type, canonical_key="visual-video-test:" + uuid4().hex)
    session.add(node)
    await session.flush()
    session.add(
        MediaAsset(
            node_id=node.id, media_kind="video", original_file_id=file.id, preferred_file_id=file.id
        )
    )
    reference = await registration.register_reference(
        session, file_id=file.id, reference_type="cutscene_video_v2", owner_node_id=node.id
    )
    reference.metadata_json = {"asset_version": "3.7.0", "duration": 6}
    await session.flush()
    service = VideoReviewService(session, base.storage, VideoEngine())
    scan = await service.enqueue(node.id, asset_version="3.7.0", interval_seconds=1)
    again = await service.enqueue(
        node.id, asset_version="3.7.0", interval_seconds=1, frame_batch_size=1
    )
    assert again["id"] == scan["id"] and again["duplicate"]
    original = module.extract_frame
    calls = []

    def fail_after_checkpoint(video, timestamp):
        calls.append(timestamp)
        if len(calls) == 3:
            raise ValueError("Simulated extraction interruption")
        return original(video, timestamp)

    monkeypatch.setattr(module, "extract_frame", fail_after_checkpoint)
    with pytest.raises(ValueError, match="interruption"):
        await service.process(scan["id"])
    partial = await service.get_scan(scan["id"])
    assert partial["status"] == "running" and partial["progress"]["completed"] == 2
    monkeypatch.setattr(module, "extract_frame", original)
    done = await service.process(scan["id"])
    assert done["status"] == "succeeded" and done["frame_count"] == 7
    assert done["unresolved_count"] == 7
    assert (
        await session.scalar(
            select(func.count())
            .select_from(VisualVideoFrame)
            .where(VisualVideoFrame.scan_id == scan["id"])
        )
        == 7
    )
    page = await service.get_scan(scan["id"], frame_offset=3, frame_limit=2)
    assert [x["index"] for x in page["frames"]] == [3, 4] and page["next_frame_offset"] == 5
    identity = await base.create_identity("Video NPC", "npc")
    with pytest.raises(ValueError, match="every proposal"):
        await service.mark_reviewed(scan["id"], actor_id=actor)
    approved = await service.resolve(
        scan["id"], 0, 0, "approved", links=[{"identity_id": identity["id"]}], actor_id=actor
    )
    visual = await session.get(
        VisualReference, approved["frames"][0]["detections"][0]["reference_id"]
    )
    assert visual.status == "approved" and visual.source_group == "video:" + file.sha256.hex()
    await service.reject_all(scan["id"], actor_id=actor)
    reviewed = await service.mark_reviewed(scan["id"], actor_id=actor)
    assert reviewed["reviewed"] and reviewed["unresolved_count"] == 0
    assert (await service.enqueue(node.id, asset_version="3.7.0", interval_seconds=1))["reviewed"]
    assert (await session.get(ProcessingRun, scan["id"])).raw_output.get("frames") is None

    # A shared Unreal asset must not select B's newer publication for cutscene A.
    reference.metadata_json = {**reference.metadata_json, "cutscene_key": "cutscene:B"}
    path.write_bytes(path.read_bytes() + b"legacy-cutscene-A")
    legacy_file = await registration.register_file(
        session, path, "video_mp4", mime_type="video/mp4"
    )
    legacy = await registration.register_reference(
        session, file_id=legacy_file.id, reference_type="cutscene_video", owner_node_id=node.id
    )
    legacy.metadata_json = {"asset_version": "3.7.0"}
    scene = Node(type_id=node_type, canonical_key="cutscene:A")
    session.add(scene)
    await session.flush()

    async def detail(name, version, locale, db):
        return {
            "node_id": scene.id,
            "game_version": "3.3.0",
            "event": {
                "playback": {
                    "asset_version": "3.7.0",
                    "media": {"full": {"asset_node_id": node.id}},
                }
            },
        }

    from wuwa_story.api.routes import cutscenes

    monkeypatch.setattr(cutscenes, "detail", detail)
    sources = await service.sources("A", game_version="3.3.0")
    source = sources["sources"][0]
    assert source["source_reference_id"] == legacy.id
    assert source["game_version"] == "3.3.0" and source["asset_version"] == "3.7.0"
    preview = await service.source_by_reference(
        legacy.id, game_version="3.3.0", asset_version="3.7.0", cutscene_key="cutscene:A"
    )
    assert preview["source_sha256"] == legacy_file.sha256.hex()
    bound = await service.enqueue(
        node.id,
        game_version="3.3.0",
        asset_version="3.7.0",
        cutscene_key="cutscene:A",
        source_reference_id=legacy.id,
        source_sha256=legacy_file.sha256.hex(),
    )
    assert bound["source"]["source_reference_id"] == legacy.id
    with pytest.raises(ValueError, match="bytes changed"):
        await service.enqueue(node.id, asset_version="3.7.0", source_sha256="00" * 32)
