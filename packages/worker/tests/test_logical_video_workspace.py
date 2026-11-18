import copy
import hashlib
import io
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image
from wuwa_story.agents.budget import BudgetExceeded
from wuwa_story.agents.cutscene_vision import VideoAnalysisResult
from wuwa_story.agents.providers import ProviderFailure, ToolCall
from wuwa_story.agents.settings import AgentSettings

from test_video_atlas import jpeg
from wuwa_story_worker import cutscene_vision as vision
from wuwa_story_worker import logical_video_workspace as logical
from wuwa_story_worker import video_analysis_agent as agent

ORIGINAL_EXTRACT = agent.VideoWorkspace.extract_frame
ORIGINAL_DURATION = logical.video_duration


def scene_plan():
    return {"version": 1, "cutscene_node_id": 15537,
            "selection": {"rover_gender": "female", "ignored_alternative_asset_node_ids": [90]},
            "segments": [
                {"asset_node_id": 10 + i, "file_id": 20 + i,
                 "source_sha256": hashlib.sha256(f"movie{i}".encode()).hexdigest(),
                 "asset_version": "3.8.0", "source_start": local,
                 "source_end": local + length, "scene_start": start, "scene_end": start + length,
                 "provenance": {"flow_node": f"clip-{i}", "gender": "female"}}
                for i, (local, start, length) in enumerate([(2, 0, 4), (0, 6, 3), (5, 9, 2)])]}


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    plan = scene_plan()
    run = SimpleNamespace(id=701, target_node_id=15537, input_hash=b"scene-identity",
                          prompt_version="video-analysis-v5", raw_output={},
                          tokens_input=0, tokens_output=0, cost=0,
                          metadata_json={"analysis_plan": plan, "file_id": 20,
                                         "source_sha256": plan["segments"][0]["source_sha256"],
                                         "asset_version": "3.8.0"})

    class DB:
        def __init__(self):
            self.calls = {}
            self.refs = []
            self.commit = AsyncMock()

        async def scalar(self, query):
            if "file_location" in str(query):
                file_id = query.compile().params["file_id_1"]
                return SimpleNamespace(object_key=f"movie{file_id - 20}")
            if "agent_call" in str(query):
                return self.calls.get(query.compile().params["step_1"])
            if "file_reference" in str(query):
                params = query.compile().params
                return next((ref for ref in self.refs
                             if ref.source_path == params.get("source_path_1")
                             and ref.reference_type == params.get("reference_type_1")
                             and ref.owner_node_id == params.get("owner_node_id_1")), None)
            return None

    class Storage:
        def __init__(self):
            self.downloads = []

        async def get(self, key):
            self.downloads.append(key)
            yield key.encode()

    db, storage = DB(), Storage()
    settings = AgentSettings(_env_file=None, provider="responses", max_input_tokens=200000,
                             vision_max_frames=24, vision_atlas_frames=4)
    monkeypatch.setattr(logical, "video_duration", lambda *_: (10, False))
    monkeypatch.setattr(agent, "_publish_cutscene_knowledge", AsyncMock())

    async def extract(self, timestamp, index):
        # Resolve the actual source even in the fake decoder, so a bad mapping fails.
        self.source_at(timestamp)
        out = self.root / f"frame_{index}.jpg"
        out.write_bytes(jpeg())
        self.frame_paths[index] = out
        self.frame_times[index] = timestamp
        return out.read_bytes()

    monkeypatch.setattr(agent.VideoWorkspace, "extract_frame", extract)

    class Files:
        variants = []

        def __init__(self, _):
            pass

        async def register_file(self, _, path, *args, **kwargs):
            if path.suffix == ".json":
                self.report = json.loads(path.read_text(encoding="utf-8"))
            return SimpleNamespace(id=100 + len(db.refs))

        async def register_reference(self, _, **kwargs):
            ref = SimpleNamespace(id=200 + len(db.refs), **kwargs)
            db.refs.append(ref)
            return ref

        async def register_variant(self, _, parent, child, kind, run_id):
            self.variants.append((parent, kind, run_id))

    monkeypatch.setattr(agent, "FileRegistrationService", Files)

    async def reserve(_, settings, *, step, **kwargs):
        call = SimpleNamespace(id=step + 1, status="reserved", response=None, cost_usd=0)
        db.calls[step] = call
        return call

    async def settle(_, call_id, settings, source, output, raw):
        call = db.calls[call_id - 1]
        call.status, call.response, call.cost_usd = "completed", raw, 0.001
        return True

    monkeypatch.setattr(agent, "reserve", reserve)
    monkeypatch.setattr(agent, "settle", settle)
    return db, run, settings, storage, SimpleNamespace(s3_bucket="test"), tmp_path, Files


async def workspace(runtime):
    db, run, settings, storage, config, root, _ = runtime
    return await logical.load_logical_workspace(db, run, settings, storage, config, root, Path("ffmpeg"))


def response(name, args):
    return {"status": "completed", "usage": {"input_tokens": 100, "output_tokens": 20},
            "output": [{"type": "function_call", "call_id": name,
                        "name": name, "arguments": json.dumps(args)}]}


def result():
    return {"title": "Three Parts", "summary": "One selected scene in three ordered parts.",
            "events": [{"time": t, "observation": "Visible action", "confidence": "observed"}
                       for t in (0, 6, 9)],
            "story_events": [{"title": "Crossing parts", "summary": "An action continues.",
                              "time_start": 3, "time_end": 10}],
            "preview_candidates": [{"time": 7, "score": 80}]}


@pytest.mark.asyncio
async def test_three_parts_one_report_global_pool_exact_source_variants(runtime, monkeypatch):
    db, run, settings, storage, _, _, files = runtime
    ws = await workspace(runtime)
    calls = []

    async def post(_, route, payload):
        calls.append(payload)
        user = next(item for item in payload["input"] if item["role"] == "user")
        intro = json.loads(user["content"][0]["text"].split("\n", 1)[1])
        assert len(intro["analysis_plan"]["segments"]) == 3
        assert {m["source_segment"]["asset_node_id"] for m in intro["atlases"]} == {10, 11, 12}
        return response("finish_video_analysis", result())

    async def preview_tool(args, *_):
        assert args[args.index("-i") + 1].endswith("source_1.mp4")
        assert args[args.index("-ss") + 1] == "1.0"
        Path(args[-1]).write_bytes(jpeg())

    monkeypatch.setattr(agent.Provider, "post", post)
    monkeypatch.setattr(agent, "run_tool", preview_tool)
    await agent.run_video_analysis_agent(db, run, settings, ws, storage, {})
    reports = [r for r in db.refs if r.reference_type == "cutscene_visual_description"]
    assert len(reports) == len(calls) == len(db.calls) == 1
    assert storage.downloads == ["movie0", "movie1", "movie2"]
    assert all(r.owner_node_id == 15537 for r in db.refs)
    report = reports[0].metadata_json
    assert report["analysis_plan"] == scene_plan()
    assert report["gaps"] == [{"scene_start": 4, "scene_end": 6}]
    assert report["cost_usd"] == 0.001 and report["tokens_input"] == 100
    assert [ev["source"]["source_time"] for ev in report["events"]] == [2, 0, 5]
    assert [ev["source"]["scene_offset"] for ev in report["events"]] == [-2, 6, 4]
    assert len(report["story_events"][0]["source_spans"]) == 3
    assert {parent for parent, kind, _ in files.variants if kind == "video_frame_atlas"} == {20, 21, 22}
    assert (21, "cutscene_preview", 701) in files.variants
    numbers = [tile["global_frame"] for atlas in report["atlases"] for tile in atlas["tiles"]]
    assert numbers == list(range(1, len(numbers) + 1))
    assert all(not 4 <= time < 6 for time in ws.frame_times.values())


@pytest.mark.asyncio
async def test_new_runs_review_and_resample_before_publication_and_replay(runtime, monkeypatch):
    db, run, settings, storage, _, _, files = runtime
    run.metadata_json["temporal_review_version"] = 1
    ws = await workspace(runtime)
    requests = []

    async def post(_, route, payload):
        requests.append(payload)
        if len(requests) == 1:
            return response("finish_video_analysis", result())
        if len(requests) == 2:
            assert "Draft received" in json.dumps(payload)
            return response("resample_video_window", {"start_seconds": 6, "end_seconds": 7,
                                                       "interval_seconds": 0.5, "reason": "inspect contact"})
        final = result()
        for event in final["events"]:
            event["global_frame"] = next(i for i, time in ws.frame_times.items() if time == event["time"])
            event["evidence_kind"] = "observed"
        final["story_events"][0].update(time_start=6, time_end=10, evidence_kind="observed",
                                        evidence_frames=[final["events"][1]["global_frame"],
                                                         final["events"][2]["global_frame"]])
        final["identity_tracks"] = [{"identity": "Unknown", "appearance": "Dark coat", "confidence": "uncertain",
                                     "basis": "observed", "time_start": 6, "time_end": 10,
                                     "evidence_frames": [final["events"][1]["global_frame"]]}]
        return response("finish_video_analysis", final)

    async def preview_tool(args, *_):
        Path(args[-1]).write_bytes(jpeg())

    monkeypatch.setattr(agent.Provider, "post", post)
    monkeypatch.setattr(agent, "run_tool", preview_tool)
    await agent.run_video_analysis_agent(db, run, settings, ws, storage, {})
    report = next(ref.metadata_json for ref in db.refs if ref.reference_type == "cutscene_visual_description")
    assert len(requests) == 3
    assert report["temporal_review_version"] == 1 and report["reviewed_atlases"]
    assert all(event["evidence_kind"] == "observed" for event in report["events"])
    assert report["identity_tracks"][0]["confidence"] == "uncertain"
    assert "1:0" in run.raw_output["tool_results"]

    fresh = await workspace(runtime)
    monkeypatch.setattr(agent.Provider, "post", AsyncMock(side_effect=AssertionError("unexpected paid call")))
    await agent.run_video_analysis_agent(db, run, settings, fresh, storage, {})
    assert run.tokens_input == 300


@pytest.mark.asyncio
async def test_temporal_review_rejects_missing_or_mismatched_evidence(runtime):
    ws = await workspace(runtime)
    await ws.build_atlases(ws.overview_times(), reason="overview")
    payload = result()
    payload["events"][0].update(global_frame=999, evidence_kind="contextual")
    payload["events"][1].update(global_frame=1, evidence_kind="observed")
    payload["events"][2].update(global_frame=1, evidence_kind="inferred", evidence_frames=[999])
    errors = agent._temporal_evidence_errors(VideoAnalysisResult.model_validate(payload), ws)
    assert any("unknown global_frame" in error for error in errors)
    assert any("contextual source" in error for error in errors)
    assert any("time does not match" in error for error in errors)
    assert any("evidence_frames" in error for error in errors)


@pytest.mark.asyncio
async def test_observed_action_cannot_claim_continuity_across_scene_gap(runtime):
    ws = await workspace(runtime)
    await ws.build_atlases(ws.overview_times(), reason="overview")
    payload = result()
    payload["story_events"][0].update(evidence_kind="observed", evidence_frames=[1, len(ws.frame_times)])
    errors = agent._temporal_evidence_errors(VideoAnalysisResult.model_validate(payload), ws)
    assert any("crosses a scene gap" in error for error in errors)


@pytest.mark.asyncio
async def test_story_events_must_follow_timeline(runtime):
    ws = await workspace(runtime)
    await ws.build_atlases(ws.overview_times(), reason="overview")
    payload = result()
    payload["story_events"] = [
        {"title": "Later", "summary": "Later action", "time_start": 6, "time_end": 6,
         "evidence_kind": "observed", "evidence_frames": [1]},
        {"title": "Earlier", "summary": "Earlier action", "time_start": 2, "time_end": 2,
         "evidence_kind": "observed", "evidence_frames": [1]},
    ]
    errors = agent._temporal_evidence_errors(VideoAnalysisResult.model_validate(payload), ws)
    assert "story events must be chronological" in errors


@pytest.mark.asyncio
async def test_overview_keeps_short_unique_parts_and_never_samples_gap(runtime):
    runtime[2].vision_max_frames = 6
    ws = await workspace(runtime)
    times = ws.overview_times()
    assert len(times) == 6
    assert [ws.source_at(t)[3]["segment_index"] for t in times] == [0, 0, 1, 1, 2, 2]
    for index in range(3):
        segment = ws.sources[index].segment
        assert times[index * 2] == segment["scene_start"]
        assert times[index * 2 + 1] >= segment["scene_end"] - 0.051
    with pytest.raises(ValueError, match="gap"):
        ws.source_at(5)


@pytest.mark.asyncio
async def test_source_seek_uses_source_local_time(runtime, monkeypatch):
    ws = await workspace(runtime)
    # Exercise the real decoder command builder independently of the fixture decoder.
    async def tool(args, *_):
        assert float(args[args.index("-ss") + 1]) == 6.0
        assert args[args.index("-i") + 1].endswith("source_2.mp4")
        Path(args[-1]).write_bytes(jpeg())
    monkeypatch.setattr(agent, "run_tool", tool)
    await ORIGINAL_EXTRACT(ws, 10, 1)
    assert ws.frame_times[1] == 10


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["overlap", "scaled", "nan", "unknown_version", "wrong_owner", "missing_bound", "source_changed", "too_short"])
async def test_invalid_plans_or_sources_fail_before_paid_call(runtime, monkeypatch, problem):
    plan = runtime[1].metadata_json["analysis_plan"]
    if problem == "overlap":
        plan["segments"][1]["scene_start"] = 3
        plan["segments"][1]["scene_end"] = 6
    elif problem == "scaled":
        plan["segments"][0]["scene_end"] = 3
    elif problem == "nan":
        plan["segments"][0]["source_start"] = float("nan")
    elif problem == "unknown_version":
        plan["version"] = 2
    elif problem == "wrong_owner":
        plan["cutscene_node_id"] = 999
    elif problem == "missing_bound":
        del plan["segments"][0]["source_end"]
    elif problem == "source_changed":
        plan["segments"][0]["source_sha256"] = "a" * 64
    elif problem == "too_short":
        monkeypatch.setattr(logical, "video_duration", lambda *_: (1, False))
    post = AsyncMock()
    monkeypatch.setattr(agent.Provider, "post", post)
    with pytest.raises(ValueError):
        await workspace(runtime)
    post.assert_not_awaited()
    assert not runtime[0].calls


@pytest.mark.asyncio
async def test_resampling_shares_total_allowance_and_rejects_gap(runtime):
    ws = await workspace(runtime)
    await ws.build_atlases([0, 6, 9])
    ws.settings.vision_max_frames = 4
    call = ToolCall("zoom", "resample_video_window",
                    {"start_seconds": 6, "end_seconds": 8, "interval_seconds": 1})
    output, entries = await agent._execute_video_tool(None, ws, call)
    assert not output["ok"] and "allowance" in output["error"] and not entries
    ws.settings.vision_max_frames = 24
    call = ToolCall("gap", "resample_video_window",
                    {"start_seconds": 3, "end_seconds": 7, "interval_seconds": 1})
    output, entries = await agent._execute_video_tool(None, ws, call)
    assert not output["ok"] and "gap" in output["error"]
    assert len(ws.frame_paths) == 3


@pytest.mark.asyncio
async def test_observations_cannot_point_into_gaps_or_other_frames(runtime):
    ws = await workspace(runtime)
    payload = result()
    payload["events"][1]["time"] = 5
    with pytest.raises(ValueError, match="gap"):
        ws.report_provenance(VideoAnalysisResult.model_validate(payload))
    payload = result()
    payload["events"][0]["global_frame"] = 99
    with pytest.raises(ValueError, match="frame"):
        ws.report_provenance(VideoAnalysisResult.model_validate(payload))
    payload = result()
    payload["story_events"][0].update(time_start=4.2, time_end=5.8)
    with pytest.raises(ValueError, match="no selected video evidence"):
        ws.report_provenance(VideoAnalysisResult.model_validate(payload))


@pytest.mark.asyncio
async def test_eof_margin_never_seeks_before_a_short_selected_tail(runtime, monkeypatch):
    segment = runtime[1].metadata_json["analysis_plan"]["segments"][-1]
    segment.update(source_start=9.96, source_end=10, scene_end=9.04)
    ws = await workspace(runtime)
    _, _, _, source = ws.source_at(9.02)
    assert source["decode_source_time"] == 9.96
    async def tool(args, *_):
        assert float(args[args.index("-ss") + 1]) >= segment["source_start"]
        Path(args[-1]).write_bytes(jpeg())
    monkeypatch.setattr(agent, "run_tool", tool)
    await ORIGINAL_EXTRACT(ws, 9.02, 1)


@pytest.mark.asyncio
async def test_recorded_call_replays_after_crash_without_recharge_and_rebuilds_zoom(runtime, monkeypatch):
    db, run, settings, storage, _, _, _ = runtime
    zoom = response("resample_video_window", {"start_seconds": 6, "end_seconds": 8,
                                              "interval_seconds": 1, "reason": "detail"})
    db.calls[0] = SimpleNamespace(id=1, status="completed", response=zoom, cost_usd=0.001)
    post = AsyncMock(side_effect=ProviderFailure("simulated_crash"))
    monkeypatch.setattr(agent.Provider, "post", post)
    ws = await workspace(runtime)
    with pytest.raises(ProviderFailure, match="simulated_crash"):
        await agent.run_video_analysis_agent(db, run, settings, ws, storage, {})
    checkpoint = copy.deepcopy(run.raw_output)
    assert checkpoint["accounted_steps"] == [0]
    assert "0:0" in checkpoint["tool_results"]
    assert db.calls[1].status == "reserved"
    # Unknown remote outcome must block a second POST on redelivery.
    post.reset_mock()
    fresh = await workspace(runtime)
    with pytest.raises(ValueError, match="Uncertain prior call"):
        await agent.run_video_analysis_agent(db, run, settings, fresh, storage, {})
    post.assert_not_awaited()
    assert len(fresh.atlases) == len(ws.atlases)
    assert run.cost == 0.001 and run.tokens_input == 100
    # A response committed before the run checkpoint is accounted once on replay.
    db.calls[1].status = "completed"
    db.calls[1].response = response("finish_video_analysis", {**result(), "preview_candidates": []})
    db.calls[1].cost_usd = 0.002
    fresh = await workspace(runtime)
    await agent.run_video_analysis_agent(db, run, settings, fresh, storage, {})
    assert run.status == "completed" and run.cost == pytest.approx(0.003)
    assert run.tokens_input == 200
    post.assert_not_awaited()
    report_count = len([r for r in db.refs if r.reference_type == "cutscene_visual_description"])
    fresh = await workspace(runtime)
    await agent.run_video_analysis_agent(db, run, settings, fresh, storage, {})
    assert len([r for r in db.refs if r.reference_type == "cutscene_visual_description"]) == report_count == 1
    assert run.cost == pytest.approx(0.003) and run.tokens_input == 200


@pytest.mark.asyncio
async def test_analyze_run_routes_scene_plan_without_single_asset_download(runtime, monkeypatch):
    db, run, settings, storage, config, _, _ = runtime
    monkeypatch.setattr(vision, "get_settings", lambda: config)
    monkeypatch.setattr(vision, "S3Storage", lambda _: storage)
    monkeypatch.setattr(vision.shutil, "which", lambda _: "ffmpeg")
    analyze = AsyncMock()
    monkeypatch.setattr(vision, "run_video_analysis_agent", analyze)
    await vision.analyze_run(db, run, settings)
    assert storage.downloads == ["movie0", "movie1", "movie2"]
    assert isinstance(analyze.await_args.args[3], logical.LogicalVideoWorkspace)


@pytest.mark.asyncio
async def test_three_real_videos_keep_distinct_frames_in_one_scene_pool(runtime, monkeypatch):
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("FFmpeg unavailable")
    db, run, settings, _, config, root, _ = runtime
    movies = {}
    for index, color in enumerate(("red", "green", "blue")):
        path = root / f"fixture_{index}.mp4"
        subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
                        f"color={color}:size=160x90:rate=10:duration=8", "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", str(path)], check=True, capture_output=True)
        movies[f"movie{index}"] = path.read_bytes()
        run.metadata_json["analysis_plan"]["segments"][index]["source_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()

    class Storage:
        async def get(self, key):
            yield movies[key]

    monkeypatch.setattr(logical, "video_duration", ORIGINAL_DURATION)
    monkeypatch.setattr(agent.VideoWorkspace, "extract_frame", ORIGINAL_EXTRACT)
    ws = await logical.load_logical_workspace(db, run, settings, Storage(), config, root, Path(ffmpeg))
    entries = await ws.build_atlases(ws.overview_times())
    assert {entry.layout["source_segment"]["asset_node_id"] for entry in entries} == {10, 11, 12}
    for index, scene_time in enumerate((0, 6, 9)):
        frame_index = next(i for i, time in ws.frame_times.items() if time == scene_time)
        with Image.open(io.BytesIO(ws.frame_paths[frame_index].read_bytes())) as image:
            pixel = image.getpixel((20, 20))
        assert pixel[index] > max(pixel[channel] for channel in range(3) if channel != index) + 80


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["budget", "usage", "output", "turns"])
async def test_limits_never_publish_partial_or_start_unbounded_requests(runtime, monkeypatch, failure):
    db, run, settings, storage, _, _, _ = runtime
    ws = await workspace(runtime)
    post = AsyncMock(return_value=response("identify_atlas_characters", {}))
    monkeypatch.setattr(agent.Provider, "post", post)
    if failure == "budget":
        monkeypatch.setattr(agent, "reserve", AsyncMock(side_effect=BudgetExceeded("bounded")))
    elif failure == "usage":
        monkeypatch.setattr(agent, "settle", AsyncMock(return_value=False))
    elif failure == "output":
        raw = response("finish_video_analysis", result())
        raw.update(status="incomplete", incomplete_details={"reason": "max_output_tokens"})
        post.return_value = raw
    else:
        monkeypatch.setattr(agent, "_identify_atlas_characters", AsyncMock(return_value={"matches": []}))
    with pytest.raises(ValueError):
        await agent.run_video_analysis_agent(db, run, settings, ws, storage, {})
    assert not [r for r in db.refs if r.reference_type == "cutscene_visual_description"]
    assert post.await_count == (0 if failure == "budget" else agent.MAX_AGENT_TURNS if failure == "turns" else 1)
    if failure == "turns":
        post.reset_mock()
        with pytest.raises(ProviderFailure, match="max_turns"):
            await agent.run_video_analysis_agent(db, run, settings, await workspace(runtime), storage, {})
        post.assert_not_awaited()
        assert run.tokens_input == agent.MAX_AGENT_TURNS * 100
