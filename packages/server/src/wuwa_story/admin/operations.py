"""Data operations, releases, imports and queues console page."""

from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import Response
from starlette.templating import Jinja2Templates
from starlette_admin import CustomView

from wuwa_story.api.routes.admin_data import overview as load_overview
from wuwa_story.ingestion.queue_status import queue_status


class DataOperationsView(CustomView):
    """Unified operations view replacing the legacy frontend Game Data admin."""

    def __init__(self) -> None:
        super().__init__(
            "Data Operations",
            path="/data-operations",
            name="data-operations",
            template_path="data_operations.html",
        )

    def is_active(self, request: Request) -> bool:
        path = request.url.path.rstrip("/")
        return path == self.path or path.endswith(self.path)

    async def render(self, request: Request, templates: Jinja2Templates) -> Response:
        session = request.state.session
        overview_data: dict[str, Any] = {}
        overview_error = ""
        try:
            overview_data = await load_overview(session=session)
        except Exception as exc:
            overview_error = f"Could not load overview: {type(exc).__name__}"

        queues_data: dict[str, Any] = {
            "available": False, "error": None, "queues": []}
        try:
            queues_data = await queue_status()
        except Exception as exc:
            queues_data = {"available": False, "error": str(exc), "queues": []}

        return templates.TemplateResponse(
            request=request,
            name=self.template_path,
            context={
                "title": self.title(request),
                "overview": overview_data,
                "overview_error": overview_error,
                "queues": queues_data,
            },
        )
