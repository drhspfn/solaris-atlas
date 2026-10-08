from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from wuwa_story.api.routes.admin_data import tasks


@pytest.mark.asyncio
async def test_running_tasks_remain_visible_outside_history_page_and_filter():
    run = SimpleNamespace(id=4, status="running", started_at=None, finished_at=None,
                          error=None, metadata_json={"request": {"kind": "prepare"}},
                          raw_output={"stage": "client_discovery", "private_history": "hidden"})
    session = AsyncMock()
    session.execute.side_effect = [Mock(all=Mock(return_value=[])),
                                   Mock(all=Mock(return_value=[(run, "release_media")]))]
    result = await tasks(before=24, status="queued", limit=50, session=session)
    assert result["tasks"] == []
    assert result["running"][0]["id"] == 4
    assert result["running"][0]["result"] == {"stage": "client_discovery"}
    assert result["next_before"] is None
    history, current = [str(call.args[0]) for call in session.execute.await_args_list]
    assert "processing_run.id <" in history
    assert "processing_run.id <" not in current
