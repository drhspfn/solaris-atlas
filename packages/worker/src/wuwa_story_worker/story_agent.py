"""Story worker consumes persisted IDs; all source/model settings come from the job."""

import asyncio
import logging

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from wuwa_story.agents.revisits import REVISIT_POLL_SECONDS, dispatch_revisits
from wuwa_story.agents.runner import execute_job
from wuwa_story.agents.settings import get_agent_settings, load_effective_agent_settings
from wuwa_story.agents.work import dispatch_work
from wuwa_story.db.session import engine


async def dispatch_story_revisits() -> None:
    cursor = 0
    while True:
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                settings = await load_effective_agent_settings(
                    session, get_agent_settings(), scope="story"
                )
                cursor = await dispatch_revisits(session, settings, after=cursor)
        except Exception:
            logging.getLogger(__name__).exception(
                "Story revisit outbox unavailable; retrying")
        await asyncio.sleep(REVISIT_POLL_SECONDS)


async def process_story_analysis(payload: dict) -> None:
    run_id = payload.get("run_id")
    if type(run_id) is not int or run_id <= 0:
        raise ValueError("Invalid story analysis run ID")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        live = await load_effective_agent_settings(
            session, get_agent_settings(), scope="story"
        )
    async with httpx.AsyncClient() as client:
        await execute_job(run_id, engine, live, client)


async def dispatch_story_work() -> None:
    while True:
        settings = get_agent_settings()
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                settings = await load_effective_agent_settings(
                    session, settings, scope="story"
                )
                if settings.autonomous_enabled:
                    await dispatch_work(session, settings)
        except Exception as error:
            logging.getLogger(__name__).warning(
                "agent.work_dispatch_deferred type=%s", type(error).__name__)
        await asyncio.sleep(settings.autonomous_poll_seconds)
