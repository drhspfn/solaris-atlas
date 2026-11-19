"""Admin Settings console view backed by ops.app_setting."""

from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import Response
from starlette.templating import Jinja2Templates
from starlette_admin import CustomView

from wuwa_story.api.routes.admin_settings import load_settings_payload


class SettingsView(CustomView):
    """Manage typed, validated runtime settings defined in backend code."""

    def __init__(self) -> None:
        super().__init__(
            "Settings",
            path="/settings",
            name="settings",
            template_path="settings.html",
        )

    def is_active(self, request: Request) -> bool:
        path = request.url.path.rstrip("/")
        return path == self.path or path.endswith(self.path)

    async def render(self, request: Request, templates: Jinja2Templates) -> Response:
        session = request.state.session
        load_error = ""
        payload: dict[str, Any] = {"categories": [], "settings": []}
        try:
            payload = await load_settings_payload(session)
        except Exception as exc:
            load_error = f"Could not load settings: {type(exc).__name__}"

        return templates.TemplateResponse(
            request,
            self.template_path,
            {
                "title": "Settings",
                "settings_data": payload,
                "load_error": load_error,
            },
        )
