from unittest.mock import AsyncMock

import pytest

from wuwa_story_worker import story_agent
from wuwa_story_worker.cli import _parser
from wuwa_story_worker.queues import QUEUES


@pytest.mark.asyncio
async def test_worker_validates_message_and_dispatches_persisted_run(monkeypatch):
    execute = AsyncMock()
    monkeypatch.setattr(story_agent, "execute_job", execute)
    for value in (None, "123", True, 0, -1):
        with pytest.raises(ValueError):
            await story_agent.process_story_analysis({"run_id": value})
    await story_agent.process_story_analysis({"run_id": 123})
    assert execute.await_args.args[0] == 123
    assert execute.await_count == 1


def test_agent_queue_is_explicit_and_cli_requires_snapshot():
    args = _parser().parse_args(["run", "--queue", "story_agent"])
    assert args.queue == ["story_agent"]
    assert QUEUES["story_agent"].name == "wuwa.story-agent.v1"
    assert _parser().parse_args(["run"]).queue is None
    with pytest.raises(SystemExit):
        _parser().parse_args(["enqueue-analysis", "--quest-id", "1"])
