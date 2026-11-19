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
    assert report["analysis_metrics"]["resample_requests"] == 1
    assert report["analysis_metrics"]["finish_attempts"] == 2
    assert len(report["analysis_metrics"]["usage_steps"]) == 3
    assert all(event["evidence_kind"] == "observed" for event in report["events"])
    assert report["identity_tracks"][0]["confidence"] == "uncertain"
    assert "1:0" in run.raw_output["tool_results"]

    fresh = await workspace(runtime)
    monkeypatch.setattr(agent.Provider, "post", AsyncMock(side_effect=AssertionError("unexpected paid call")))
    await agent.run_video_analysis_agent(db, run, settings, fresh, storage, {})
    assert run.tokens_input == 300


@pytest.mark.asyncio
async def test_review_ledger_persists_draft_and_replays_without_provider(runtime, monkeypatch):
    db, run, settings, storage, _, _, _ = runtime
    run.metadata_json.update(temporal_review_version=1, review_evidence_version=1)
    monkeypatch.setattr(agent, "_previous_scene_evidence", AsyncMock(return_value={"available": False}))
    ws = await workspace(runtime)
    calls = []

    async def post(_, route, payload):
        calls.append(payload)
        initial = result()
        if len(calls) == 1:
            return response("finish_video_analysis", initial)
        for event in initial["events"]:
            event["global_frame"] = next(i for i, time in ws.frame_times.items() if time == event["time"])
            event["evidence_kind"] = "observed"
        initial["story_events"][0].update(time_start=6, time_end=10, evidence_kind="observed",
                                          evidence_frames=[initial["events"][1]["global_frame"],
                                                           initial["events"][2]["global_frame"]])
        initial["event_review"] = [{"draft_index": index, "status": "retained", "final_indices": [index]}
                                   for index in range(len(initial["events"]))]
        initial["preview_candidates"] = []
        return response("finish_video_analysis", initial)

    monkeypatch.setattr(agent.Provider, "post", post)
    await agent.run_video_analysis_agent(db, run, settings, ws, storage, {})
    report = next(ref.metadata_json for ref in db.refs if ref.reference_type == "cutscene_visual_description")
    assert len(report["review_draft"]["events"]) == len(report["event_review"]) == 3
    assert run.raw_output["review_draft"]["events"] == report["review_draft"]["events"]
    monkeypatch.setattr(agent.Provider, "post", AsyncMock(side_effect=AssertionError("unexpected paid call")))
    await agent.run_video_analysis_agent(db, run, settings, await workspace(runtime), storage, {})
    assert run.tokens_input == 200


@pytest.mark.asyncio
async def test_previous_evidence_requires_exact_source_plan_and_context(monkeypatch):
    from wuwa_story.agents import scene_analysis_reader

    plan = scene_plan()
    context = {"release": {"id": 22}, "rows": [{"text": "Together?"}]}
    run = SimpleNamespace(id=2, target_node_id=15537, metadata_json={"analysis_plan": plan,
        "asset_version": "3.8.0", "source_sha256": "same", "file_id": 20, "text_context": context})
    report = SimpleNamespace(id=5, metadata_json={"run_id": 1, "source_sha256": "same", "source_file_id": 20,
        "events": [{"time": index} for index in range(40)], "identity_tracks": [{"identity": "Lily"}]})
    previous = SimpleNamespace(status="completed", raw_output={"report_reference_id": 5},
                               metadata_json={"text_context": context})
    db = SimpleNamespace(get=AsyncMock(return_value=previous))
    lookup = AsyncMock(return_value=report)
    monkeypatch.setattr(scene_analysis_reader, "current_scene_report", lookup)
    result = await agent._previous_scene_evidence(db, run)
    assert result["available"] and len(result["events"]) == 30
    assert result["events"][0]["prior_index"] == 0 and result["events"][-1]["prior_index"] == 39
    lookup.assert_awaited_once_with(db, 15537, "3.8.0", plan)
    report.metadata_json["source_sha256"] = "other_variant"
    assert not (await agent._previous_scene_evidence(db, run))["available"]
    report.metadata_json["source_sha256"] = "same"
    previous.metadata_json["text_context"] = {"release": {"id": 21}}
    assert not (await agent._previous_scene_evidence(db, run))["available"]


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


def test_evidence_frames_are_ordered_by_time_not_frame_number():
    ws = SimpleNamespace(frame_times={1: 10, 2: 20, 3: 11})
    original = {"events": [{"evidence_frames": [1, 2, 3]}],
                "story_events": [{"evidence_frames": [2, 3, 1]}],
                "identity_tracks": [{"evidence_frames": [99, 1]}]}
    result = agent._order_evidence_frames(original, ws)
    assert result["events"][0]["evidence_frames"] == [1, 3, 2]
    assert result["story_events"][0]["evidence_frames"] == [1, 3, 2]
    assert result["identity_tracks"][0]["evidence_frames"] == [99, 1]
    assert original["events"][0]["evidence_frames"] == [1, 2, 3]


def test_order_evidence_frames_expands_intervals_to_cover_known_evidence_frames():
    ws = SimpleNamespace(frame_times={76: 149.958, 77: 151.958, 78: 153.957, 79: 155.957, 80: 157.957}, duration=180.0)
    payload = {
        "summary": "scene",
        "events": [{
            "time": 149.958,
            "time_end": 152.978,
            "global_frame": 76,
            "evidence_frames": [76, 77, 78, 79, 80],
            "observation": "Montage sequence",
            "confidence": "observed",
            "evidence_kind": "observed",
        }],
        "story_events": [{
            "title": "Montage",
            "summary": "Sequence",
            "time_start": 150.0,
            "time_end": 152.978,
            "evidence_frames": [76, 80],
            "evidence_kind": "observed",
        }],
        "identity_tracks": [{
            "identity": "Rover",
            "appearance": "dark jacket",
            "confidence": "identified",
            "basis": "observed",
            "time_start": 150.5,
            "time_end": 152.0,
            "evidence_frames": [76, 80],
        }],
    }
    ordered = agent._order_evidence_frames(payload, ws, normalize_intervals=True)
    assert ordered["events"][0]["time_end"] == pytest.approx(157.957)
    assert ordered["story_events"][0]["time_start"] == pytest.approx(149.958)
    assert ordered["story_events"][0]["time_end"] == pytest.approx(157.957)
    assert ordered["identity_tracks"][0]["time_start"] == pytest.approx(149.958)
    assert ordered["identity_tracks"][0]["time_end"] == pytest.approx(157.957)
    assert agent._temporal_evidence_errors(VideoAnalysisResult.model_validate(ordered), ws) == []


def test_chronological_sort_remaps_review_indices():
    ws = SimpleNamespace(frame_times={1: 10, 2: 20})
    original = {"events": [{"time": 20}, {"time": 10}],
                "story_events": [{"time_start": 20}, {"time_start": 10}],
                "event_review": [{"draft_index": 0, "final_indices": [0, 1]}]}
    result = agent._order_evidence_frames(original, ws, order_events=True, order_story_events=True)
    assert [event["time"] for event in result["events"]] == [10, 20]
    assert result["event_review"][0]["final_indices"] == [1, 0]
    assert [event["time_start"] for event in result["story_events"]] == [10, 20]
    assert original["event_review"][0]["final_indices"] == [0, 1]


def test_review_requires_each_draft_event_and_identity_to_be_accounted_for():
    ws = SimpleNamespace(frame_times={1: 1, 2: 2, 3: 30})
    payload = {"summary": "scene", "events": [{"time": 1, "global_frame": 1,
               "observation": "Two people meet", "confidence": "observed"}],
               "identity_tracks": [{"identity": "Lily", "appearance": "coat", "confidence": "identified",
                                    "basis": "observed", "time_start": 1, "time_end": 2, "evidence_frames": [1]}]}
    draft = VideoAnalysisResult.model_validate(payload)
    final = VideoAnalysisResult.model_validate(payload)
    assert len(agent._review_evidence_errors(draft, final, ws)) == 2
    final = VideoAnalysisResult.model_validate({**payload,
        "event_review": [{"draft_index": 0, "status": "retained", "final_indices": [0]}],
        "identity_review": [{"draft_index": 0, "status": "retained", "final_indices": [0]}]})
    assert agent._review_evidence_errors(draft, final, ws) == []
    final.events[0].observation = "One person stands"
    assert any("changed wording" in error for error in agent._review_evidence_errors(draft, final, ws))
    final.event_review[0].status = "revised"
    final.event_review[0].reason = "The second figure was background"
    final.event_review[0].evidence_frames = [2]
    assert agent._review_evidence_errors(draft, final, ws) == []
    final.identity_tracks[0].identity = "Another person"
    assert any("changed identity" in error for error in agent._review_evidence_errors(draft, final, ws))
    final.identity_review[0].status = "disputed"
    final.identity_review[0].final_indices = []
    final.identity_review[0].reason = "Different appearance"
    final.identity_review[0].evidence_frames = [3]
    assert any("nearby frames" in error for error in agent._review_evidence_errors(draft, final, ws))
    final.identity_review[0].evidence_frames = [2]
    assert agent._review_evidence_errors(draft, final, ws) == []


def test_review_revised_falls_back_to_target_evidence_and_allows_distant_confirming_frames():
    ws = SimpleNamespace(frame_times={1: 1, 2: 2, 10: 55}, duration=60)
    draft = VideoAnalysisResult.model_validate({
        "summary": "scene",
        "events": [{"time": 1, "global_frame": 1, "observation": "A dark-haired figure stands near the door",
                    "confidence": "observed", "evidence_kind": "observed"}],
        "identity_tracks": [{"identity": "unknown", "appearance": "dark jacket", "confidence": "uncertain",
                             "basis": "observed", "time_start": 1, "time_end": 2, "evidence_frames": [1]}],
    })
    final = VideoAnalysisResult.model_validate({
        "summary": "scene",
        "events": [{"time": 1, "global_frame": 1, "observation": "Rover stands near the door",
                    "confidence": "observed", "evidence_kind": "observed", "evidence_frames": [1, 10]}],
        "identity_tracks": [{"identity": "Rover", "appearance": "dark jacket", "confidence": "identified",
                             "basis": "observed", "time_start": 1, "time_end": 55, "evidence_frames": [1, 10]}],
        "event_review": [{"draft_index": 0, "status": "revised", "final_indices": [0],
                          "reason": "Identity confirmed as Rover at 00:55"}],
        "identity_review": [{"draft_index": 0, "status": "revised", "final_indices": [0],
                             "reason": "Later close-up at frame 10 confirms Rover", "evidence_frames": [1, 10]}],
    })
    assert agent._review_evidence_errors(draft, final, ws) == []


def test_temporal_evidence_rejects_adjacent_duplicate_observations():
    ws = SimpleNamespace(frame_times={1: 38.0, 2: 39.0, 3: 45.0}, duration=60.0)
    dup = VideoAnalysisResult.model_validate({
        "summary": "scene",
        "events": [
            {"time": 38.0, "global_frame": 1, "observation": "A small female figure is surrounded by pink light",
             "confidence": "observed", "evidence_kind": "observed"},
            {"time": 39.0, "global_frame": 2, "observation": "A small female figure is surrounded by pink light",
             "confidence": "observed", "evidence_kind": "observed"},
        ],
    })
    errors = agent._temporal_evidence_errors(dup, ws)
    assert any("duplicate adjacent observation" in error for error in errors)

    merged = VideoAnalysisResult.model_validate({
        "summary": "scene",
        "events": [
            {"time": 38.0, "time_end": 39.0, "global_frame": 1, "evidence_frames": [1, 2],
             "observation": "A small female figure is surrounded by pink light",
             "confidence": "observed", "evidence_kind": "observed"},
            {"time": 45.0, "global_frame": 3, "observation": "She lowers her gaze and smiles faintly",
             "confidence": "observed", "evidence_kind": "observed"},
        ],
    })
    assert agent._temporal_evidence_errors(merged, ws) == []


def test_temporal_evidence_enforces_ending_coverage_and_rover_dialogue_identity():
    ws = SimpleNamespace(frame_times={1: 153.957, 2: 168.0}, duration=180.0)
    context = {"dialogue_lines": [{"speaker": "{PlayerName}", "text": "Sorry. The Tacet Discords held me up.", "node_id": 7987613}]}
    truncated = VideoAnalysisResult.model_validate({
        "summary": "scene",
        "events": [
            {"time": 153.957, "time_end": 168.0, "global_frame": 1, "evidence_frames": [1, 2],
             "observation": "A white feline appears and energy sweeps across the field",
             "confidence": "observed", "evidence_kind": "observed"},
        ],
        "identity_tracks": [
            {"identity": "Lily", "appearance": "flower crown", "confidence": "identified",
             "basis": "observed", "time_start": 153.957, "time_end": 168.0, "evidence_frames": [1]},
        ],
    })
    errors = agent._temporal_evidence_errors(truncated, ws, context)
    assert any("timeline ending coverage" in error for error in errors)
    assert any("'{PlayerName}' (Rover)" in error for error in errors)

    complete = VideoAnalysisResult.model_validate({
        "summary": "scene",
        "events": [
            {"time": 153.957, "global_frame": 1, "evidence_frames": [1],
             "observation": "A white feline appears and energy sweeps across the field",
             "confidence": "observed", "evidence_kind": "observed"},
            {"time": 168.0, "global_frame": 2, "evidence_frames": [2], "actors": ["Rover", "Lily"],
             "observation": "Rover supports Lily as they walk together across the flower field at sunset",
             "confidence": "observed", "evidence_kind": "observed"},
        ],
        "identity_tracks": [
            {"identity": "Rover", "appearance": "dark jacket and trousers", "confidence": "identified",
             "basis": "contextual", "context_sources": ["7987613"], "time_start": 153.957, "time_end": 168.0,
             "evidence_frames": [1, 2]},
        ],
    })
    assert agent._temporal_evidence_errors(complete, ws, context) == []


def test_order_evidence_frames_backfills_off_by_one_trailing_event_review():
    ws = SimpleNamespace(frame_times={1: 10.0, 2: 20.0}, duration=30.0)
    draft = VideoAnalysisResult.model_validate({
        "summary": "scene",
        "events": [
            {"time": 10.0, "global_frame": 1, "observation": "First action", "confidence": "observed", "evidence_kind": "observed"},
            {"time": 20.0, "global_frame": 2, "observation": "The image fades to black and remains black.", "confidence": "observed", "evidence_kind": "observed"},
        ],
    })
    raw_final = {
        "summary": "scene",
        "events": [
            {"time": 10.0, "global_frame": 1, "observation": "First action", "confidence": "observed", "evidence_kind": "observed"},
            {"time": 20.0, "global_frame": 2, "evidence_frames": [2], "observation": "The image fades to black; no further action.", "confidence": "observed", "evidence_kind": "observed"},
        ],
        "event_review": [
            {"draft_index": 0, "status": "retained", "final_indices": [0]},
        ],
    }
    ordered = agent._order_evidence_frames(raw_final, ws, order_events=True, normalize_intervals=True, draft=draft)
    final = VideoAnalysisResult.model_validate(ordered)
    assert len(final.event_review) == 2
    assert final.event_review[1].draft_index == 1
    assert final.event_review[1].final_indices == [1]
    assert agent._review_evidence_errors(draft, final, ws) == []


def test_order_evidence_frames_auto_upgrades_retained_reworded_events_and_unbacked_contextual():
    ws = SimpleNamespace(frame_times={1: 10.0, 2: 20.0}, duration=30.0)
    draft = VideoAnalysisResult.model_validate({
        "summary": "scene",
        "events": [
            {"time": 10.0, "global_frame": 1, "observation": "Two figures stand in the field", "confidence": "observed", "evidence_kind": "observed"},
        ],
    })
    raw_final = {
        "summary": "scene",
        "events": [
            {"time": 10.0, "global_frame": 1, "evidence_frames": [1],
             "observation": "Rover and Lily stand together in the flower field",
             "confidence": "observed", "evidence_kind": "observed"},
        ],
        "story_events": [
            {"title": "Reunion", "summary": "They meet", "time_start": 10.0, "time_end": 10.0,
             "evidence_frames": [1], "evidence_kind": "contextual", "context_sources": []},
        ],
        "event_review": [
            {"draft_index": 0, "status": "retained", "final_indices": [0]},
        ],
    }
    ordered = agent._order_evidence_frames(raw_final, ws, order_events=True, normalize_intervals=True, draft=draft)
    final = VideoAnalysisResult.model_validate(ordered)
    assert final.event_review[0].status == "revised"
    assert final.event_review[0].evidence_frames == [1]
    assert final.story_events[0].evidence_kind == "inferred"
    assert agent._review_evidence_errors(draft, final, ws) == []
    assert agent._temporal_evidence_errors(final, ws) == []


@pytest.mark.asyncio
async def test_finish_errors_are_saved_and_retries_bounded(runtime, monkeypatch):
    db, run, settings, storage, _, _, _ = runtime
    invalid = result()
    invalid["identity_tracks"] = [{"identity": "Lily", "appearance": "coat", "confidence": "identified",
                                   "basis": "bad", "time_start": 0, "time_end": 1}]
    post = AsyncMock(return_value=response("finish_video_analysis", invalid))
    monkeypatch.setattr(agent.Provider, "post", post)
    with pytest.raises(ProviderFailure, match="finish_correction_limit"):
        await agent.run_video_analysis_agent(db, run, settings, await workspace(runtime), storage, {})
    assert post.await_count == agent.MAX_FINISH_CORRECTIONS + 1
    assert run.raw_output["finish_diagnostics"]["0:0"]["error"] == "invalid_finish_arguments"
    assert run.raw_output["finish_diagnostics"]["0:0"]["details"][0]["field"] == ["identity_tracks", 0, "basis"]
    assert run.raw_output["finish_diagnostics"]["1:0"]["duplicate_payload"] is True

    valid = result()
    valid["preview_candidates"] = []
    post = AsyncMock(return_value=response("finish_video_analysis", valid))
    monkeypatch.setattr(agent.Provider, "post", post)
    await agent.run_video_analysis_agent(db, run, settings, await workspace(runtime), storage, {})
    assert post.await_count == 1
    assert run.raw_output["finish_diagnostics"]["3:0"]["status"] == "accepted"


@pytest.mark.asyncio
async def test_character_reference_is_sent_to_model_and_replayed(runtime, monkeypatch):
    from wuwa_story_worker import video_identity

    db, run, settings, storage, _, _, _ = runtime
    reference = AsyncMock(return_value=({"reference_id": 7, "sha256": "stable"}, jpeg()))
    monkeypatch.setattr(video_identity, "character_reference_image", reference)
    post = AsyncMock(side_effect=[response("get_character_reference_image", {"reference_id": 7}),
                                  response("finish_video_analysis", {**result(), "preview_candidates": []})])
    monkeypatch.setattr(agent.Provider, "post", post)
    await agent.run_video_analysis_agent(db, run, settings, await workspace(runtime), storage, {})
    assert "Requested character reference images" in json.dumps(post.await_args_list[1].args[-1])
    assert run.raw_output["tool_results"]["0:0"]["output"]["reference_id"] == 7
    post.reset_mock()
    await agent.run_video_analysis_agent(db, run, settings, await workspace(runtime), storage, {})
    post.assert_not_awaited()
    assert reference.await_count == 2


@pytest.mark.asyncio
async def test_region_inspection_uses_original_video_and_rejects_invalid_crop(tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("FFmpeg unavailable")
    movie = tmp_path / "source.mp4"
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=red:size=640x360:rate=10:duration=2", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(movie)], check=True, capture_output=True)
    ws = agent.VideoWorkspace(root=tmp_path, ffmpeg=Path(ffmpeg), movie=movie, duration=2,
                              settings=AgentSettings(_env_file=None), limits={}, frame_times={1: 1.0})
    metadata, data = await ws.inspect_region(1, [0.25, 0.25, 0.75, 0.75])
    with Image.open(io.BytesIO(data)) as image:
        assert image.width == 1024
        assert image.getpixel((100, 100))[0] > 200
    assert metadata["global_frame"] == 1 and metadata["scene_time"] == 1.0
    assert (await ws.inspect_region(1, [0.25, 0.25, 0.75, 0.75]))[0]["sha256"] == metadata["sha256"]
    with pytest.raises(ValueError):
        await ws.inspect_region(1, [0, 0, 0.001, 1])
    with pytest.raises(ValueError):
        await ws.inspect_region(99, [0, 0, 1, 1])


@pytest.mark.asyncio
async def test_region_inspection_is_sent_to_model_and_replayed(runtime, monkeypatch):
    db, run, settings, storage, _, _, _ = runtime
    crop = AsyncMock(return_value=({"global_frame": 1, "sha256": "stable"}, jpeg()))
    monkeypatch.setattr(agent.VideoWorkspace, "inspect_region", crop)
    post = AsyncMock(side_effect=[response("inspect_video_region", {"global_frame": 1, "bbox": [0, 0, 1, 1]}),
                                  response("finish_video_analysis", {**result(), "preview_candidates": []})])
    monkeypatch.setattr(agent.Provider, "post", post)
    await agent.run_video_analysis_agent(db, run, settings, await workspace(runtime), storage, {})
    assert "video region crops" in json.dumps(post.await_args_list[1].args[-1])
    report = next(ref.metadata_json for ref in db.refs if ref.reference_type == "cutscene_visual_description")
    assert report["analysis_metrics"]["spatial_inspections"] == 1
    post.reset_mock()
    await agent.run_video_analysis_agent(db, run, settings, await workspace(runtime), storage, {})
    post.assert_not_awaited()
    assert crop.await_count == 2


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
