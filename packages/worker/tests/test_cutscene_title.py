from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from wuwa_story.agents.settings import AgentSettings

from wuwa_story_worker import cutscene_title


@pytest.mark.asyncio
async def test_existing_visual_report_gets_title_without_touching_video(monkeypatch):
    report = SimpleNamespace(id=25, file_id=2, metadata_json={
        "events": [{"time": 1, "observation": "A fighter faces a creature"}],
        "batch_summaries": ["A fight"], "source_sha256": "abc", "asset_version": "3.7.0",
    })
    monkeypatch.setattr(cutscene_title, "visual_reference", AsyncMock(return_value=report))
    call = SimpleNamespace(id=1, input_tokens=None, output_tokens=None, cost_usd=None)
    monkeypatch.setattr(cutscene_title, "reserve", AsyncMock(return_value=call))

    async def settle(*args):
        call.input_tokens, call.output_tokens, call.cost_usd = 100, 30, Decimal("0.001")
        return True

    monkeypatch.setattr(cutscene_title, "settle", settle)
    response = {"status": "completed", "usage": {"input_tokens": 100, "output_tokens": 30},
                "output": [{"type": "message", "content": [{"type": "output_text",
                            "text": '{"title":"A Last Stand"}'}]}]}
    monkeypatch.setattr(cutscene_title.Provider, "post", AsyncMock(return_value=response))
    db = SimpleNamespace(scalar=AsyncMock(return_value=None), commit=AsyncMock(), add=Mock())
    run = SimpleNamespace(id=7, target_node_id=5, raw_output={"step": 0}, metadata_json={
        "title_only_report_id": 25, "file_id": 2, "asset_version": "3.7.0"})
    settings = AgentSettings(_env_file=None, api_key="test", daily_budget_usd=1)
    await cutscene_title.analyze_title_only(db, run, settings)
    assert run.status == "completed" and run.raw_output["title"] == "A Last Stand"
    saved = db.add.call_args.args[0]
    assert saved.reference_type == "cutscene_visual_title"
    assert saved.metadata_json["source_report_id"] == 25 and saved.file_id == 2
    assert report.metadata_json.get("title") is None  # Existing evidence stays immutable.


@pytest.mark.asyncio
async def test_title_call_with_unknown_outcome_is_never_repeated(monkeypatch):
    report = SimpleNamespace(id=25, file_id=2, metadata_json={"events": [], "batch_summaries": []})
    monkeypatch.setattr(cutscene_title, "visual_reference", AsyncMock(return_value=report))
    post = AsyncMock()
    monkeypatch.setattr(cutscene_title.Provider, "post", post)
    db = SimpleNamespace(scalar=AsyncMock(return_value=SimpleNamespace(status="reserved", response=None)))
    run = SimpleNamespace(id=7, target_node_id=5, raw_output={"step": 0}, metadata_json={
        "title_only_report_id": 25, "file_id": 2, "asset_version": "3.7.0"})
    with pytest.raises(ValueError, match="automatic retry prohibited"):
        await cutscene_title.analyze_title_only(db, run, AgentSettings(_env_file=None))
    post.assert_not_awaited()


@pytest.mark.asyncio
async def test_changed_visual_source_is_not_published(monkeypatch):
    report = SimpleNamespace(id=26, file_id=2)
    monkeypatch.setattr(cutscene_title, "visual_reference", AsyncMock(return_value=report))
    reserve = AsyncMock()
    monkeypatch.setattr(cutscene_title, "reserve", reserve)
    db = SimpleNamespace(add=Mock())
    run = SimpleNamespace(id=7, target_node_id=5, metadata_json={
        "title_only_report_id": 25, "file_id": 2, "asset_version": "3.7.0"})
    await cutscene_title.analyze_title_only(db, run, AgentSettings(_env_file=None))
    assert run.status == "skipped"
    reserve.assert_not_awaited()
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_short_report_is_not_sent_for_paid_title(monkeypatch):
    report = SimpleNamespace(id=25, file_id=2, metadata_json={"duration": 2})
    monkeypatch.setattr(cutscene_title, "visual_reference", AsyncMock(return_value=report))
    reserve = AsyncMock()
    monkeypatch.setattr(cutscene_title, "reserve", reserve)
    db = SimpleNamespace(add=Mock())
    run = SimpleNamespace(id=7, target_node_id=5, metadata_json={
        "title_only_report_id": 25, "file_id": 2, "asset_version": "3.7.0"})
    await cutscene_title.analyze_title_only(db, run, AgentSettings(_env_file=None))
    assert run.status == "skipped" and run.raw_output["skip_reason"] == "short_video"
    reserve.assert_not_awaited()


@pytest.mark.asyncio
async def test_completed_title_call_is_replayed_without_recharging(monkeypatch):
    report = SimpleNamespace(id=25, file_id=2, metadata_json={
        "duration": 10, "events": [{"time": 1, "observation": "A fighter"}],
        "source_sha256": "abc", "asset_version": "3.7.0"})
    monkeypatch.setattr(cutscene_title, "visual_reference", AsyncMock(return_value=report))
    response = {"status": "completed", "usage": {"input_tokens": 100, "output_tokens": 30},
                "output": [{"type": "message", "content": [{"type": "output_text",
                            "text": '{"title":"A Last Stand"}'}]}]}
    call = SimpleNamespace(status="completed", response=response, input_tokens=100,
                           output_tokens=30, cost_usd=Decimal("0.001"))
    db = SimpleNamespace(scalar=AsyncMock(return_value=call), add=Mock())
    post = AsyncMock()
    reserve = AsyncMock()
    monkeypatch.setattr(cutscene_title.Provider, "post", post)
    monkeypatch.setattr(cutscene_title, "reserve", reserve)
    run = SimpleNamespace(id=7, target_node_id=5, raw_output={"step": 0}, metadata_json={
        "title_only_report_id": 25, "file_id": 2, "asset_version": "3.7.0"})
    await cutscene_title.analyze_title_only(db, run, AgentSettings(_env_file=None))
    assert run.status == "completed"
    post.assert_not_awaited()
    reserve.assert_not_awaited()
