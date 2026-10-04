"""Story worker consumes persisted IDs; all source/model settings come from the job."""

import httpx
from wuwa_story.agents.runner import execute_job
from wuwa_story.agents.settings import get_agent_settings
from wuwa_story.db.session import engine


async def process_story_analysis(payload: dict) -> None:
    run_id = payload.get("run_id")
    if type(run_id) is not int or run_id <= 0:
        raise ValueError("Invalid story analysis run ID")
    async with httpx.AsyncClient() as client:
        await execute_job(run_id, engine, get_agent_settings(), client)
