from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql
from wuwa_story.agents.cutscene_vision import VideoAnalysisResult

from wuwa_story_worker.video_analysis_agent import _publish_cutscene_knowledge


@pytest.mark.asyncio
async def test_new_relation_has_label_and_serialized_id_allocation():
    db = SimpleNamespace(scalar=AsyncMock(side_effect=[42, None, 80, None]),
                         execute=AsyncMock(), add=lambda value: None)
    run = SimpleNamespace(id=1, target_node_id=100, metadata_json={"asset_version": "3.8.0"})
    result = VideoAnalysisResult.model_validate({
        "summary": "A scene", "events": [{"time": 0, "observation": "A figure", "confidence": "observed"}],
        "entity_links": [{"target_node_id": 42, "target_name": "Event", "kind": "event",
                          "relation": "related_to_event", "confidence": "observed", "evidence": "Shown"}],
    })
    await _publish_cutscene_knowledge(db, run, result)
    statements = [call.args[0] for call in db.execute.await_args_list]
    assert any("LOCK TABLE ontology.relation_type" in str(statement) for statement in statements)
    statement = next(statement for statement in statements if str(statement).startswith("INSERT INTO ontology.relation_type"))
    compiled = statement.compile(dialect=postgresql.dialect())
    assert compiled.params["label"] == "Related To"
    assert "max(ontology.relation_type.id)" in str(compiled)
