"""Cutscene Analysis console view: imported cutscene assets, video preview, and vision jobs."""

from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import Response
from starlette.templating import Jinja2Templates
from starlette_admin import CustomView

from wuwa_story.api.routes.cutscene_analysis import (
    assets as load_assets,
)
from wuwa_story.api.routes.cutscene_analysis import (
    visual_jobs as load_visual_jobs,
)


class CutsceneAnalysisView(CustomView):
    """Console view replacing the legacy frontend Cutscene Analysis page."""

    def __init__(self) -> None:
        super().__init__(
            "Cutscene Analysis",
            path="/cutscene-analysis",
            name="cutscene-analysis",
            template_path="cutscene_analysis.html",
        )

    def is_active(self, request: Request) -> bool:
        path = request.url.path.rstrip("/")
        return path == self.path or path.endswith(self.path)

    async def render(self, request: Request, templates: Jinja2Templates) -> Response:
        session = request.state.session
        load_error = ""
        query = request.query_params.get("q", "").strip()
        raw_job = request.query_params.get("job", "").strip()
        run_id = int(raw_job) if raw_job.isdigit() and int(
            raw_job) > 0 else None

        assets_data: dict[str, Any] = {
            "enabled": False, "assets": [], "next_before": None}
        jobs_data: dict[str, Any] = {"jobs": [], "next_before": None}

        try:
            assets_data = await load_assets(q=query, before=None, limit=30, session=session)
            jobs_data = await load_visual_jobs(
                before=None, run_id=run_id, limit=30, session=session
            )
        except Exception as exc:
            load_error = f"Could not load cutscene analysis data: {type(exc).__name__}"

        return templates.TemplateResponse(
            request,
            self.template_path,
            {
                "title": "Cutscene Analysis",
                "query": query,
                "selected_run_id": run_id,
                "assets_data": assets_data,
                "jobs_data": jobs_data,
                "load_error": load_error,
            },
        )
