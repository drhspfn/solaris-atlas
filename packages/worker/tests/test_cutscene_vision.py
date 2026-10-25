import hashlib
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from wuwa_story.agents.providers import Provider, ProviderRejected
from wuwa_story.agents.settings import AgentSettings

from test_video_atlas import jpeg
from wuwa_story_worker import cutscene_vision as vision


@pytest.mark.parametrize("provider", ["responses", "chat", "gemini"])
def test_image_requests_keep_model_adapter_and_bounded_output(provider):
    settings = AgentSettings(_env_file=None, provider=provider)
    _, request = vision.vision_request(Provider(settings, None), [0, 3], [b"jpeg1", b"jpeg2"])
    wire = json.dumps(request)
    assert "anBlZzE=" in wire and "anBlZzI=" in wire
    assert "Frame timestamps: [0, 3]" in wire
    assert str(settings.max_output_tokens) in wire


def response(times, *, truncated=False, malformed=False):
    return {
        "status": "incomplete" if truncated else "completed",
        "incomplete_details": {"reason": "max_output_tokens"} if truncated else None,
        "usage": {"input_tokens": 100, "output_tokens": 50},
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": "{"
                        if malformed or truncated
                        else json.dumps(
                            {
                                "summary": "Visible actions",
                                "events": [
                                    {
                                        "time": time,
                                        "observation": "A person moves",
                                        "confidence": "observed",
                                    }
                                    for time in times
                                ],
                            }
                        ),
                    }
                ],
            }
        ],
    }


@pytest.fixture
def pipeline(monkeypatch):
    class Storage:
        async def get(self, key):
            yield b"movie"

    class DB:
        def __init__(self):
            self.calls = {}
            self.reports = []
            self.commit = AsyncMock()

        async def scalar(self, query):
            if "file_location" in str(query):
                return SimpleNamespace(object_key="movie")
            return self.calls.get(query.compile().params["step_1"])

        def add(self, value):
            self.reports.append(value)

    db = DB()

    async def reserve(_, settings, *, step, **kwargs):
        call = SimpleNamespace(
            id=step + 1,
            status="reserved",
            response=None,
            input_tokens=None,
            output_tokens=None,
            cost_usd=None,
        )
        db.calls[step] = call
        return call

    async def settle(_, call_id, settings, source, output, raw):
        call = db.calls[call_id - 1]
        call.status, call.response = "completed", raw
        call.input_tokens, call.output_tokens, call.cost_usd = source, output, Decimal("0.001")
        return True

    monkeypatch.setattr(vision, "S3Storage", lambda _: Storage())
    monkeypatch.setattr(vision, "get_settings", lambda: SimpleNamespace(s3_bucket="test"))
    monkeypatch.setattr(vision.shutil, "which", lambda _: "ffmpeg")
    monkeypatch.setattr(vision, "video_duration", lambda *_: (10, False))
    monkeypatch.setattr(vision, "sample_frame", AsyncMock(return_value=b"jpeg"))
    async def atlases(db, run, storage, ffmpeg, movie, times, root, settings):
        groups = [times[i:i + settings.vision_atlas_frames]
                  for i in range(0, len(times), settings.vision_atlas_frames)]
        return ([b"jpeg"] * len(groups), [{"tiles": group, "width": 1536, "height": 732} for group in groups],
                [{"reference_id": int(group[0] * 10000) + len(group), "file_id": 3,
                  "sample_times": group} for group in groups])
    monkeypatch.setattr(vision, "prepare_atlases", atlases)
    monkeypatch.setattr(vision, "reserve", reserve)
    monkeypatch.setattr(vision, "settle", settle)
    run = SimpleNamespace(
        id=1,
        target_node_id=5,
        tokens_input=0,
        tokens_output=0,
        cost=0,
        raw_output={"step": 0, "batches": []},
        metadata_json={
            "file_id": 2,
            "source_sha256": hashlib.sha256(b"movie").hexdigest(),
            "asset_version": "3.7.0",
        },
    )
    settings = AgentSettings(_env_file=None, vision_max_frames=4, vision_batch_frames=4)
    return db, run, settings


@pytest.mark.asyncio
async def test_truncated_batches_split_and_publish_complete_ending(monkeypatch, pipeline):
    db, run, settings = pipeline

    async def post(_, route, request):
        label = request["input"][0]["content"][0]["text"]
        times = json.loads(label.split("Frame timestamps: ")[1])
        return response(times, truncated=len(times) > 2)

    monkeypatch.setattr(vision.Provider, "post", post)
    await vision.analyze_run(db, run, settings)
    assert run.status == "completed"
    assert len(db.calls) == 3  # one charged truncated call plus two complete halves
    assert len(db.reports) == 1
    report = db.reports[0].metadata_json
    assert len(report["events"]) == 4 and report["events"][-1]["time"] == 9.95
    assert run.tokens_input == 300 and len(run.raw_output["batches"]) == 2


@pytest.mark.asyncio
async def test_checkpoint_replays_paid_response_without_second_call(monkeypatch, pipeline):
    db, run, settings = pipeline
    times = vision.sample_times(10, 3, 4)
    db.calls[0] = SimpleNamespace(
        id=1,
        status="completed",
        response=response(times),
        input_tokens=100,
        output_tokens=50,
        cost_usd=Decimal("0.001"),
    )
    post = AsyncMock()
    monkeypatch.setattr(vision.Provider, "post", post)
    await vision.analyze_run(db, run, settings)
    assert run.status == "completed" and len(db.reports) == 1
    post.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["malformed", "single_truncated", "rejected", "uncertain"])
async def test_failures_do_not_publish_partial_report(monkeypatch, pipeline, failure):
    db, run, settings = pipeline
    if failure == "single_truncated":
        run.raw_output["pending"] = [[0]]
    if failure == "uncertain":
        db.calls[0] = SimpleNamespace(status="reserved", response=None)

    async def post(*_):
        if failure == "rejected":
            raise ProviderRejected("rate_limit_exceeded", 10, {})
        return response(
            [0], truncated=failure == "single_truncated", malformed=failure == "malformed"
        )

    monkeypatch.setattr(vision.Provider, "post", post)
    if failure == "uncertain":
        with pytest.raises(ValueError, match="automatic retry prohibited"):
            await vision.analyze_run(db, run, settings)
    else:
        await vision.analyze_run(db, run, settings)
        assert run.status in ("paused_output", "paused_validation", "paused_provider")
        assert run.raw_output["step"] == 1
    assert not db.reports


@pytest.mark.asyncio
async def test_dense_batch_sends_four_atlases_with_pinned_text(monkeypatch, pipeline):
    db, run, settings = pipeline
    settings.vision_max_frames = 44
    settings.vision_batch_frames = 48
    run.metadata_json["text_context"] = {"text": "Source dialogue, not visible evidence"}
    monkeypatch.setattr(vision, "video_duration", lambda *_: (130, False))

    async def post(_, route, request):
        content = request["input"][0]["content"]
        assert len(content) == 5  # prompt + four contact sheets, not 44 images/11 calls
        assert all(image["detail"] == "high" for image in content[1:])
        assert "Source dialogue" in content[0]["text"]
        return response(json.loads(content[0]["text"].split("Frame timestamps: ")[1]))

    monkeypatch.setattr(vision.Provider, "post", post)
    await vision.analyze_run(db, run, settings)
    assert run.status == "completed" and len(db.calls) == 1
    assert len(db.reports[0].metadata_json["events"]) == 44
    assert len(db.reports[0].metadata_json["atlases"]) == 4


@pytest.mark.asyncio
async def test_atlases_are_persisted_and_linked_to_exact_source(monkeypatch, tmp_path):
    # Exercise the actual generation/publication helper, not the pipeline fixture's mock.
    monkeypatch.setattr(vision, "sample_frame", AsyncMock(return_value=jpeg()))
    register = AsyncMock(return_value=SimpleNamespace(id=31))
    reference = AsyncMock(return_value=SimpleNamespace(id=41))
    variant = AsyncMock()
    monkeypatch.setattr(vision, "FileRegistrationService", lambda _: SimpleNamespace(
        register_file=register, register_reference=reference, register_variant=variant))
    db = SimpleNamespace(scalar=AsyncMock(return_value=None), execute=AsyncMock())
    run = SimpleNamespace(id=7, target_node_id=5,
                          metadata_json={"file_id": 2, "source_sha256": "abc"})
    images, manifests, refs = await vision.prepare_atlases(
        db, run, object(), tmp_path / "ffmpeg", tmp_path / "source.mp4",
        list(range(14)), tmp_path, AgentSettings(_env_file=None))
    assert len(images) == len(refs) == 2
    assert [len(m["tiles"]) for m in manifests] == [12, 2]
    assert reference.await_args.kwargs["reference_type"] == "video_frame_atlas"
    assert reference.return_value.metadata_json["source_file_id"] == 2
    variant.assert_awaited_with(db, 2, 31, "video_frame_atlas", 7)
    assert refs[-1]["sample_times"] == [12, 13]


@pytest.mark.asyncio
async def test_legacy_jobs_retain_original_image_requests(monkeypatch, pipeline):
    db, run, settings = pipeline
    run.prompt_version = "cutscene-visual-v1"

    async def post(_, route, request):
        content = request["input"][0]["content"]
        assert len(content) == 5
        assert all(image["detail"] == "low" for image in content[1:])
        return response(json.loads(content[0]["text"].split("Frame timestamps: ")[1]))

    monkeypatch.setattr(vision.Provider, "post", post)
    await vision.analyze_run(db, run, settings)
    assert run.status == "completed"
    assert db.reports[0].metadata_json["protocol"] == "cutscene-visual-v1"
    assert not db.reports[0].metadata_json["atlases"]


@pytest.mark.asyncio
async def test_atlas_storage_failure_happens_before_paid_reservation(monkeypatch, pipeline):
    db, run, settings = pipeline
    monkeypatch.setattr(vision, "prepare_atlases", AsyncMock(side_effect=OSError("Storage unavailable")))
    post = AsyncMock()
    monkeypatch.setattr(vision.Provider, "post", post)
    with pytest.raises(OSError, match="Storage unavailable"):
        await vision.analyze_run(db, run, settings)
    assert not db.calls and not db.reports
    post.assert_not_called()


@pytest.mark.asyncio
async def test_dense_batches_split_locally_when_input_reservation_would_exceed_limit(monkeypatch, pipeline):
    db, run, settings = pipeline
    settings.vision_max_frames = 44
    settings.vision_batch_frames = 48
    settings.max_input_tokens = 22000
    monkeypatch.setattr(vision, "video_duration", lambda *_: (130, False))

    async def post(_, route, request):
        content = request["input"][0]["content"]
        assert len(content) <= 3
        return response(json.loads(content[0]["text"].split("Frame timestamps: ")[1]))

    monkeypatch.setattr(vision.Provider, "post", post)
    await vision.analyze_run(db, run, settings)
    assert run.status == "completed" and len(db.calls) == 2
    assert len(db.reports[0].metadata_json["events"]) == 44


@pytest.mark.asyncio
async def test_empty_frame_extraction_cannot_reuse_previous_timestamp(monkeypatch, tmp_path):
    output = tmp_path / "frame.jpg"
    output.write_bytes(jpeg())
    monkeypatch.setattr(vision, "run_tool", AsyncMock())  # FFmpeg wrote no frame at EOF
    with pytest.raises(FileNotFoundError):
        await vision.sample_frame(tmp_path / "ffmpeg", tmp_path / "movie.mp4", 1.95, output)
