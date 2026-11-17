from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from wuwa_story.agents.cutscene_analysis_plan import build_analysis_plan, enqueue_logical_visual_job
from wuwa_story.agents.settings import AgentSettings


def source(number, duration=10):
    return {"asset_node_id": number, "file_id": number + 100,
            "source_sha256": f"{number:064x}", "duration_seconds": duration}


def clip(name, *, next=None, rover=None, start=0, end=None):
    return {"id": name, "kind": "clip", "asset": f"asset:ue:/Game/{name}",
            "next": next, "rover": rover, "start": start, "end": end}


def flow(nodes, entry):
    return {"version": 1, "entry": entry, "nodes": nodes, "evidence": "authored playback"}


def test_rover_alternatives_choose_female_and_disclose_excluded():
    nodes = [{"id": "choice", "kind": "choice", "prompt": "Rover", "options": [
        {"next": "male", "label": "male", "rover": "male"},
        {"next": "female", "label": "female", "rover": "female"}]},
        clip("male", rover="male"), clip("female", rover="female")]
    plan = build_analysis_plan(15537, "3.8.0", flow(nodes, "choice"),
        {"asset:ue:/Game/male": source(1), "asset:ue:/Game/female": source(2)}, flow_reference_id=55)
    assert [s["asset_node_id"] for s in plan["segments"]] == [2]
    assert plan["selection"]["ignored_alternative_asset_node_ids"] == [1]
    assert plan["segments"][0]["scene_end"] == 10


def test_sequential_parts_all_included_with_source_ranges():
    nodes = [clip("one", next="two", start=2, end=6), clip("two", next="three"), clip("three")]
    sources = {f"asset:ue:/Game/{name}": source(i) for i, name in enumerate(("one", "two", "three"), 1)}
    plan = build_analysis_plan(9, "3.8.0", flow(nodes, "one"), sources, flow_reference_id=55)
    assert [(s["asset_node_id"], s["source_start"], s["source_end"], s["scene_start"], s["scene_end"])
            for s in plan["segments"]] == [(1, 2, 6, 0, 4), (2, 0, 10, 4, 14), (3, 0, 10, 14, 24)]


def test_unknown_choice_or_duration_fails_closed():
    nodes = [{"id": "choice", "kind": "choice", "prompt": "story", "options": [
        {"next": "one", "label": "A"}, {"next": "two", "label": "B"}]}, clip("one"), clip("two")]
    with pytest.raises(ValueError, match="not a proven pair"):
        build_analysis_plan(9, "3.8.0", flow(nodes, "choice"), {}, flow_reference_id=1)
    with pytest.raises(ValueError, match="verified duration"):
        build_analysis_plan(9, "3.8.0", flow([clip("one")], "one"),
                            {"asset:ue:/Game/one": source(1, None)}, flow_reference_id=1)


@pytest.mark.asyncio
async def test_enqueue_identity_pins_complete_plan(monkeypatch):
    from wuwa_story.agents import video_context
    monkeypatch.setattr(video_context, "video_context_snapshot", AsyncMock(return_value={"basis": "test"}))
    db = SimpleNamespace(execute=AsyncMock(), scalar=AsyncMock(side_effect=[1, None, 1, None]),
                         add=Mock(), flush=AsyncMock())
    nodes = [clip("one", next="two"), clip("two")]
    sources = {f"asset:ue:/Game/{name}": source(i) for i, name in enumerate(("one", "two"), 1)}
    plan = build_analysis_plan(9, "3.8.0", flow(nodes, "one"), sources, flow_reference_id=55)
    settings = AgentSettings(_env_file=None, vision_enabled=True)
    ref = SimpleNamespace(id=101)
    first = await enqueue_logical_visual_job(db, plan, ref, settings)
    changed = build_analysis_plan(9, "3.8.0", flow(nodes, "one"),
                                  {**sources, "asset:ue:/Game/two": source(3)}, flow_reference_id=55)
    second = await enqueue_logical_visual_job(db, changed, ref, settings)
    assert first.input_hash != second.input_hash
    assert first.target_node_id == 9
    assert first.metadata_json["selected_asset_node_ids"] == [1, 2]


@pytest.mark.asyncio
async def test_two_selected_variants_enqueue_one_logical_job(monkeypatch):
    from wuwa_story.api.routes import cutscene_analysis as api

    refs = [SimpleNamespace(owner_node_id=1), SimpleNamespace(owner_node_id=2)]
    db = SimpleNamespace(scalars=AsyncMock(return_value=refs), commit=AsyncMock(), rollback=AsyncMock())
    monkeypatch.setattr(api, "get_agent_settings", lambda: SimpleNamespace(vision_enabled=True))
    monkeypatch.setattr(api, "_asset_cutscenes", AsyncMock(return_value={
        1: [{"node_id": 9}], 2: [{"node_id": 9}]}))
    monkeypatch.setattr(api, "_logical_plan", AsyncMock(return_value=({"plan": "test"}, refs[1], {1: refs[0], 2: refs[1]})))
    monkeypatch.setattr(api, "_existing_whole_scene_job", AsyncMock(return_value=None))
    run = SimpleNamespace(id=3)
    enqueue = AsyncMock(return_value=run)
    monkeypatch.setattr(api, "enqueue_logical_visual_job", enqueue)
    monkeypatch.setattr(api, "public_job", lambda item: {"id": item.id})
    result = await api.create_visual_jobs(api.VisualRequest(asset_node_ids=[1, 2], game_version="3.8.0"), db)
    assert result == {"jobs": [{"id": 3}]}
    enqueue.assert_awaited_once()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_existing_alternative_report_reuses_only_exact_published_bytes(monkeypatch):
    from wuwa_story.api.routes import cutscene_analysis as api

    refs = {1: SimpleNamespace(file_id=101, metadata_json={"asset_version": "3.8.0"}),
            2: SimpleNamespace(file_id=102, metadata_json={"asset_version": "3.8.0"})}
    report = SimpleNamespace(id=77, metadata_json={"source_file_id": 102, "source_sha256": b"ok".hex()})
    monkeypatch.setattr(api, "visual_reference", AsyncMock(side_effect=[None, report]))
    run = SimpleNamespace(id=12, target_node_id=2)
    db = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(sha256=b"ok")),
                         scalar=AsyncMock(side_effect=[None, run]))
    plan = {"selection": {"reusable_whole_scene_asset_ids": [1, 2]}}
    assert await api._existing_whole_scene_job(db, plan, refs) == (run, report)
    report.metadata_json["source_sha256"] = "wrong"
    monkeypatch.setattr(api, "visual_reference", AsyncMock(return_value=report))
    db.scalar = AsyncMock(return_value=None)
    assert await api._existing_whole_scene_job(db, plan, refs) is None
    assert db.scalar.await_count == 2


@pytest.mark.asyncio
async def test_legacy_in_progress_blocks_duplicate_cost(monkeypatch):
    from wuwa_story.api.routes import cutscene_analysis as api

    monkeypatch.setattr(api, "visual_reference", AsyncMock(return_value=None))
    db = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(sha256=b"source")),
                         scalar=AsyncMock(return_value=42))
    refs = {1: SimpleNamespace(file_id=101, metadata_json={"asset_version": "3.8.0"})}
    with pytest.raises(ValueError, match="already in progress"):
        await api._existing_whole_scene_job(db, {"selection": {"reusable_whole_scene_asset_ids": [1]}}, refs)
