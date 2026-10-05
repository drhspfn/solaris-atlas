import hashlib
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from wuwa_story.agents.providers import Provider, ProviderRejected
from wuwa_story.agents.settings import AgentSettings

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
