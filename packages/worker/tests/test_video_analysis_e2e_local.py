from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from wuwa_story.agents.budget import BudgetExceeded
from wuwa_story.agents.providers import Provider
from wuwa_story.agents.settings import AgentSettings

from wuwa_story_worker.video_analysis_agent import (
    VideoWorkspace,
    _estimate_input_tokens,
    run_video_analysis_agent,
)

VALID_STORAGE_FILE_TYPES = {
    "audio_mp3",
    "audio_ogg",
    "audio_opus",
    "audio_wav",
    "audio_wem",
    "image",
    "raw_archive",
    "raw_json",
    "raw_jsonl",
    "sequence",
    "subtitle_srt",
    "subtitle_vtt",
    "thumbnail",
    "unknown",
    "video_game",
    "video_mp4",
    "video_webm",
}


def _make_synthetic_video(ffmpeg_path: Path, out_path: Path, duration_sec: int = 6) -> None:
    subprocess.run(
        [
            str(ffmpeg_path),
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=duration={duration_sec}:size=640x360:rate=10",
            "-pix_fmt",
            "yuv420p",
            str(out_path),
        ],
        check=True,
        capture_output=True,
    )


def _make_settings(**overrides) -> AgentSettings:
    defaults = {
        "provider": "responses",
        "model": "gpt-5.4",
        "vision_model": "gpt-5.4",
        "max_input_tokens": 250_000,
        "max_output_tokens": 8192,
        "vision_output_tokens": 8192,
        "vision_atlas_frames": 6,
        "vision_atlas_tile_width": 320,
        "vision_atlas_quality": 75,
        "vision_max_frames": 24,
        "vision_max_atlases": 4,
    }
    defaults.update(overrides)
    return AgentSettings(**defaults)


class _FakeDb:
    def __init__(self, *, scalar_values=None):
        self._scalar_values = list(scalar_values or [])
        self.added = []
        self.executed = []
        self.commits = 0
        self.flushes = 0
        self.refreshes = 0

    async def execute(self, stmt, *_args, **_kwargs):
        self.executed.append(stmt)
        return SimpleNamespace(
            scalar_one_or_none=lambda: None,
            scalars=lambda: SimpleNamespace(all=lambda: []),
            mappings=lambda: SimpleNamespace(all=lambda: []),
            fetchall=lambda: [],
        )

    async def scalar(self, stmt):
        if self._scalar_values:
            return self._scalar_values.pop(0)
        sql = str(stmt)
        if "graph.node" in sql:
            return 42
        if "ontology.relation_type" in sql:
            return 5
        if "i18n.locale" in sql:
            return 1
        return None

    async def scalars(self, _stmt):
        return SimpleNamespace(all=lambda: [])

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushes += 1

    async def commit(self):
        self.commits += 1

    async def refresh(self, _obj):
        self.refreshes += 1


def test_estimate_input_tokens_ignores_base64_and_bounds_atlases(tmp_path: Path):
    settings = _make_settings()
    ws = VideoWorkspace(
        root=tmp_path,
        ffmpeg=Path("ffmpeg"),
        movie=tmp_path / "m.mp4",
        duration=12.0,
        settings=settings,
        limits={"max_frames": 24, "max_atlases": 4, "max_tool_calls": 16},
    )
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        pytest.skip("ffmpeg not installed on PATH")
    _make_synthetic_video(Path(ffmpeg_bin), ws.movie, duration_sec=6)

    import asyncio

    atlases = asyncio.run(ws.build_atlases(
        [0.5, 1.5, 2.5, 3.5, 4.5, 5.5], reason="overview"))
    assert len(atlases) == 1

    # ~1.6MB base64 string that used to estimate as 400k text tokens
    fake_b64 = "A" * 1_600_000
    payload = [
        {"role": "system", "content": "Analyze video"},
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "Inspect the overview atlas."},
                {"type": "input_image",
                    "image_url": f"data:image/jpeg;base64,{fake_b64}", "detail": "high"},
            ],
        },
    ]
    estimated = _estimate_input_tokens(payload, atlases)
    assert estimated < 20_000
    assert estimated > 4_000


@pytest.mark.asyncio
async def test_run_video_analysis_agent_e2e_local(tmp_path: Path, monkeypatch):
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        pytest.skip("ffmpeg not installed on PATH")

    movie_path = tmp_path / "cutscene.mp4"
    _make_synthetic_video(Path(ffmpeg_bin), movie_path, duration_sec=6)

    settings = _make_settings()
    workspace = VideoWorkspace(
        root=tmp_path / "ws",
        ffmpeg=Path(ffmpeg_bin),
        movie=movie_path,
        duration=6.0,
        settings=settings,
        limits={
            "overview_interval": 2.0,
            "max_overview_frames": 24,
            "soft_zoom_frames": 12,
            "hard_zoom_frames": 24,
            "max_frames": 24,
            "max_atlases": 4,
            "max_tool_calls": 16,
        },
    )
    workspace.root.mkdir(parents=True, exist_ok=True)

    # Fake DB: first scalar() is _existing_step_count -> None; remaining queries dispatch by table name.
    db = _FakeDb(scalar_values=[None])

    run = SimpleNamespace(
        id=99001,
        target_node_id=5001,
        status="running",
        cost=0.0,
        tokens_input=0,
        tokens_output=0,
        raw_output={"mode": "video_agent", "step": 0},
        input_hash=b"\xab\xcd" * 16,
        metadata_json={"asset_version": "3.7.1", "file_id": 123, "source_sha256": "deadbeef"},
    )

    registered_files: list[dict] = []

    class _FakeFileRegistrationService:
        def __init__(self, _storage):
            pass

        async def register_file(self, _db, path: Path, file_type: str, **kwargs):
            assert file_type in VALID_STORAGE_FILE_TYPES, f"Invalid storage.file_type: {file_type}"
            assert path.exists(), f"Registered file does not exist: {path}"
            registered_files.append(
                {"path": path, "file_type": file_type, "kwargs": kwargs})
            return SimpleNamespace(id=len(registered_files) + 100)

        async def register_variant(self, _db, parent_file_id: int, variant_file_id: int, variant_type: str, run_id: int):
            return SimpleNamespace(id=variant_file_id)

        async def register_reference(self, _db, file_id: int, **_kwargs):
            return SimpleNamespace(id=file_id)

    monkeypatch.setattr(
        "wuwa_story_worker.video_analysis_agent.FileRegistrationService", _FakeFileRegistrationService)
    monkeypatch.setattr(
        "wuwa_story_worker.video_analysis_agent.lexical_search",
        AsyncMock(return_value=[{"id": 42, "name": "Rover"}]),
    )

    reserved_calls: dict[int, SimpleNamespace] = {}

    async def fake_reserve(_db, _settings, *, input_bound: int, output_bound: int, run_id: int, step: int, **_kwargs):
        if input_bound > settings.max_input_tokens:
            raise BudgetExceeded(
                "Input context exceeds the configured token bound")
        call = SimpleNamespace(
            id=1000 + step,
            run_id=run_id,
            step=step,
            input_bound=input_bound,
            output_bound=output_bound,
            cost_usd=0.0,
            status="reserved",
        )
        reserved_calls[call.id] = call
        return call

    async def fake_settle(_db, call_id: int, _settings, tokens_input: int, tokens_output: int, _raw=None):
        call = reserved_calls[call_id]
        call.tokens_input = tokens_input
        call.tokens_output = tokens_output
        call.cost_usd = round((tokens_input * 0.0000025) +
                              (tokens_output * 0.00001), 6)
        call.status = "settled"

    monkeypatch.setattr(
        "wuwa_story_worker.video_analysis_agent.reserve", fake_reserve)
    monkeypatch.setattr(
        "wuwa_story_worker.video_analysis_agent.settle", fake_settle)

    turn_counter = {"n": 0}

    def _responses_payload(calls: list[tuple[str, str, dict]], input_tokens: int, output_tokens: int) -> dict:
        return {
            "status": "completed",
            "output": [
                {
                    "type": "function_call",
                    "call_id": call_id,
                    "name": name,
                    "arguments": json.dumps(args),
                }
                for call_id, name, args in calls
            ],
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        }

    async def fake_post(self, route: str, payload: dict) -> dict:
        assert route == "/responses"
        assert "input" in payload
        turn = turn_counter["n"]
        turn_counter["n"] += 1
        if turn == 0:
            return _responses_payload(
                [
                    ("tc_id", "identify_atlas_characters", {"atlas_index": 1, "question": "Who appears?"}),
                    ("tc_ent", "search_entities", {"query": "Rover", "category": "character", "limit": 5}),
                    ("tc_lore", "search_lore", {"query": "Rover arrives", "limit": 5}),
                ],
                12000,
                250,
            )
        if turn == 1:
            return _responses_payload(
                [
                    (
                        "tc_res",
                        "resample_video_window",
                        {"start_seconds": 1.0, "end_seconds": 4.0, "interval_seconds": 1.0, "reason": "close-up"},
                    )
                ],
                15000,
                180,
            )
        if turn == 2:
            # Invalid finish payload must return a tool error to the model, not crash the run
            return _responses_payload(
                [("tc_bad_fin", "finish_video_analysis", {"summary": 123})],
                18000,
                140,
            )
        return _responses_payload(
            [
                (
                    "tc_ok_fin",
                    "finish_video_analysis",
                    {
                        "title": "Test Cutscene",
                        "summary": "Rover stands in a synthetic test scene.",
                        "events": [
                            {
                                "time": 0.5,
                                "global_frame": 1,
                                "observation": "Test pattern appears",
                                "confidence": "observed",
                                "actors": ["Rover"],
                                "items": [],
                                "lore_topics": ["Test"],
                            }
                        ],
                        "story_events": [
                            {
                                "title": "Arrival",
                                "summary": "Rover arrives in the test arena.",
                                "time_start": 0.5,
                                "time_end": 4.5,
                                "participants": ["Rover"],
                                "topics": ["Test arena"],
                            }
                        ],
                        "entity_links": [
                            {
                                "target_node_id": 42,
                                "target_name": "Rover",
                                "kind": "character",
                                "relation": "appears_in",
                                "confidence": "observed",
                                "evidence": "Visible in A01 #001",
                            }
                        ],
                        "lore_topics": ["Test arena"],
                        "preview_candidates": [
                            {"time": 99.0, "global_frame": 1, "score": 90}
                        ],
                    },
                )
            ],
            20000,
            320,
        )

    monkeypatch.setattr(Provider, "post", fake_post)
    monkeypatch.setattr(
        "wuwa_story_worker.video_analysis_agent._identify_atlas_characters",
        AsyncMock(return_value={"characters": ["Rover"]}),
    )

    result = await run_video_analysis_agent(
        db,
        run,
        settings,
        workspace,
        storage=object(),
        context={"asset_path": "/Game/Test/M0206.M0206",
                 "game_version": "3.7.1"},
    )

    assert result.title == "Test Cutscene"
    assert run.status == "completed"
    assert run.tokens_input == 12000 + 15000 + 18000 + 20000
    assert run.tokens_output == 250 + 180 + 140 + 320
    assert run.cost > 0
    assert run.raw_output["mode"] == "video_agent"
    assert run.raw_output["step"] == 4
    assert run.raw_output["atlases"] == 2
    assert len(reserved_calls) == 4
    assert all(c.input_bound < settings.max_input_tokens for c in reserved_calls.values())

    file_types = [entry["file_type"] for entry in registered_files]
    assert "image" in file_types
    assert "raw_json" in file_types
    assert "json" not in file_types
    assert db.flushes >= 4
    assert db.commits >= 5
    edges = [obj for obj in db.added if getattr(obj, "__tablename__", None) == "edge"]
    assert len(edges) == 1
    assert edges[0].layer in {"source", "canonical", "semantic", "manual"}
    assert edges[0].basis in {
        "explicit_reference",
        "exact_join",
        "authored_order",
        "source_array_adjacency",
        "semantic_extraction",
        "inference",
        "manual",
    }
    assert edges[0].processor_run_id == run.id


@pytest.mark.asyncio
async def test_run_video_analysis_agent_replays_paid_steps_without_new_calls(tmp_path: Path, monkeypatch):
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        pytest.skip("ffmpeg not installed on PATH")

    movie_path = tmp_path / "cutscene.mp4"
    _make_synthetic_video(Path(ffmpeg_bin), movie_path, duration_sec=4)

    settings = _make_settings()
    workspace = VideoWorkspace(
        root=tmp_path / "ws",
        ffmpeg=Path(ffmpeg_bin),
        movie=movie_path,
        duration=4.0,
        settings=settings,
        limits={
            "overview_interval": 2.0,
            "max_overview_frames": 24,
            "soft_zoom_frames": 12,
            "hard_zoom_frames": 24,
            "max_frames": 24,
            "max_atlases": 4,
            "max_tool_calls": 16,
        },
    )
    workspace.root.mkdir(parents=True, exist_ok=True)

    db = _FakeDb()
    run = SimpleNamespace(
        id=99002,
        target_node_id=5002,
        status="running",
        cost=0.05,
        tokens_input=1000,
        tokens_output=100,
        raw_output={"mode": "video_agent", "step": 2, "tool_results": {
            "0:0": {"output": {"results": []}, "atlases": []}}},
        input_hash=b"\xef\x01" * 16,
        metadata_json={"asset_version": "3.7.1", "file_id": 123, "source_sha256": "deadbeef"},
    )

    monkeypatch.setattr(
        "wuwa_story_worker.video_analysis_agent.FileRegistrationService",
        lambda _s: SimpleNamespace(
            register_file=AsyncMock(return_value=SimpleNamespace(id=200)),
            register_variant=AsyncMock(return_value=SimpleNamespace(id=200)),
            register_reference=AsyncMock(return_value=SimpleNamespace(id=200)),
        ),
    )
    steps_seen: list[int] = []
    resumed_calls: dict[int, SimpleNamespace] = {}

    async def fake_reserve(_db, _settings, *, step: int, **_kwargs):
        steps_seen.append(step)
        call = SimpleNamespace(id=2000 + step, step=step, cost_usd=0.0, status="reserved")
        resumed_calls[call.id] = call
        return call

    async def fake_settle(_db, call_id: int, _settings, _tokens_input: int, _tokens_output: int, _raw=None):
        call = resumed_calls[call_id]
        call.cost_usd = 0.01
        call.status = "settled"

    monkeypatch.setattr(
        "wuwa_story_worker.video_analysis_agent.reserve", fake_reserve)
    monkeypatch.setattr(
        "wuwa_story_worker.video_analysis_agent.settle", fake_settle)

    async def fake_post_quick(self, route: str, payload: dict) -> dict:
        assert route == "/responses"
        return {
            "status": "completed",
            "output": [
                {
                    "type": "function_call",
                    "call_id": "tc_fin",
                    "name": "finish_video_analysis",
                    "arguments": json.dumps(
                        {
                            "title": "Resumed Cutscene",
                            "summary": "Completed on resumed step.",
                            "events": [
                                {
                                    "time": 0.5,
                                    "global_frame": 1,
                                    "observation": "Scene resumes cleanly.",
                                    "confidence": "observed",
                                    "actors": [],
                                    "items": [],
                                    "lore_topics": [],
                                }
                            ],
                            "story_events": [],
                            "entity_links": [],
                            "lore_topics": [],
                            "preview_candidates": [],
                        }
                    ),
                }
            ],
            "usage": {"input_tokens": 5000, "output_tokens": 120},
        }

    first_response = {
        "status": "completed", "usage": {"input_tokens": 500, "output_tokens": 50},
        "output": [{"type": "function_call", "call_id": "tc_search",
                    "name": "search_entities", "arguments": '{"query":"Rover"}'}],
    }
    final_response = await fake_post_quick(None, "/responses", {})
    db._scalar_values = [
        SimpleNamespace(status="completed", response=first_response, cost_usd=0.02),
        SimpleNamespace(status="completed", response=final_response, cost_usd=0.03),
    ]
    post = AsyncMock(side_effect=AssertionError("Paid response must be replayed"))
    monkeypatch.setattr(Provider, "post", post)

    await run_video_analysis_agent(
        db,
        run,
        settings,
        workspace,
        storage=object(),
        context={"asset_path": "/Game/Test/Resumed.Resumed",
                 "game_version": "3.7.1"},
    )

    assert steps_seen == []
    assert run.raw_output["step"] == 2
    assert run.tokens_input == 1000 and run.tokens_output == 100
    assert pytest.approx(run.cost, rel=1e-6) == 0.05
    post.assert_not_awaited()
