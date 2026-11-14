"""Story Agent console view: runs, daily budget, traces, resume & revisits."""

from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import Response
from starlette.templating import Jinja2Templates
from starlette_admin import CustomView

from wuwa_story.api.routes.story_agent import (
    job_status as load_job_detail,
)
from wuwa_story.api.routes.story_agent import (
    jobs as load_jobs,
)
from wuwa_story.api.routes.story_agent import (
    revisit_jobs as load_revisits,
)
from wuwa_story.api.routes.story_agent import (
    usage as load_usage,
)


class StoryAgentView(CustomView):
    """Console view replacing the legacy frontend Story Agent page."""

    def __init__(self) -> None:
        super().__init__(
            "Story Agent",
            path="/story-agent",
            name="story-agent",
            template_path="story_agent.html",
        )

    def is_active(self, request: Request) -> bool:
        path = request.url.path.rstrip("/")
        return path == self.path or path.endswith(self.path)

    async def render(self, request: Request, templates: Jinja2Templates) -> Response:
        session = request.state.session
        load_error = ""

        usage_data: dict[str, Any] = {
            "daily_budget_usd": "0",
            "daily_token_limit": 0,
            "timezone": "UTC",
            "today": "",
            "days": [],
        }
        jobs_data: dict[str, Any] = {"jobs": [], "next_before": None}
        revisits_data: dict[str, Any] = {"revisits": [], "next_before": None}
        selected_detail: dict[str, Any] | None = None

        try:
            usage_data = await load_usage(session=session)
            jobs_data = await load_jobs(limit=30, before=None, session=session)
            revisits_data = await load_revisits(before=None, session=session)
        except Exception as exc:
            load_error = f"Could not load story agent data: {type(exc).__name__}"

        raw_job = request.query_params.get("job", "").strip()
        selected_job_id: int | None = int(
            raw_job) if raw_job.isdigit() and int(raw_job) > 0 else None
        if selected_job_id is not None and not load_error:
            try:
                selected_detail = await load_job_detail(selected_job_id, session=session)
            except Exception:
                selected_detail = None

        today_str = usage_data.get("today", "")
        today_row = next((d for d in usage_data.get("days", [])
                         if d.get("day") == today_str), None)

        return templates.TemplateResponse(
            request,
            self.template_path,
            {
                "title": "Story Agent",
                "usage": usage_data,
                "today_row": today_row,
                "jobs_data": jobs_data,
                "revisits_data": revisits_data,
                "selected_job_id": selected_job_id,
                "selected_detail": selected_detail,
                "load_error": load_error,
            },
        )
