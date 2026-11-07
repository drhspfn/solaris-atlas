"""Register ORM models with the admin library; no resource API or UI templates."""

from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.templating import Jinja2Templates
from starlette_admin import CustomView, DropDown
from starlette_admin.contrib.sqla import Admin

from wuwa_story.admin.auth import ConsoleProtection, InternalAuth
from wuwa_story.admin.views import (
    BudgetUsage,
    Memory,
    OpenQuestions,
    Quests,
    ReadOnly,
    Runs,
    Sessions,
    Users,
    Work,
)
from wuwa_story.config.settings import Settings, get_settings
from wuwa_story.db.models.agent_workspace import AgentMemory, AgentToolAudit, AgentWorkItem
from wuwa_story.db.models.agents import AgentCall, AgentDailyUsage, AgentJob, AgentNote
from wuwa_story.db.models.auth import AuthSession, User
from wuwa_story.db.models.content import Document
from wuwa_story.db.models.core import Character, Quest
from wuwa_story.db.models.game_events import GameEvent
from wuwa_story.db.models.graph import Node
from wuwa_story.db.models.i18n import Locale
from wuwa_story.db.models.notices import GameNotice
from wuwa_story.db.models.ops import GameRelease, ProcessingRun


class AgentIndex(CustomView):
    async def render(self, request: Request, templates: Jinja2Templates) -> Response:
        return RedirectResponse(request.url_for("console:list", identity="runs"), status_code=303)


def readonly(model: type[Any], label: str, columns: str, identity: Any, search: Sequence[str] = ()) -> ReadOnly:
    view = type(model.__name__ + "Admin", (ReadOnly,),
                {"fields": columns.split(), "searchable_fields": list(search)})
    return cast(ReadOnly, view(model, label=label, identity=identity))


def install_admin(app: Starlette, engine: AsyncEngine, settings: Settings | None = None) -> Admin:
    admin = Admin(engine, title="Solaris Atlas · Admin", base_url="/console", route_name="console",
                  templates_dir=str(Path(__file__).parent / "templates"),
                  statics_dir=str(Path(__file__).parent / "statics"),
                  auth_provider=InternalAuth(settings or get_settings()),
                  middlewares=[Middleware(ConsoleProtection)],
                  index_view=AgentIndex("Research", add_to_menu=False))
    admin.add_view(DropDown("Agent", views=[
        OpenQuestions(AgentWorkItem, label="Open questions",
                      identity="open-questions"),
        Runs(ProcessingRun, label="Research runs", identity="runs"),
        readonly(AgentToolAudit, "Tool and admin audit", "id request_id role run_id step call_id tool status arguments result duration_ms created_at",
                 "tool-audit", ["request_id", "role", "tool", "status"]),
        Memory(AgentMemory, label="Memory", identity="memory"),
        Work(AgentWorkItem, label="Work and questions", identity="work"),
        readonly(AgentJob, "Jobs",
                 "run_id release_id locale_id document_id", "jobs"),
        readonly(AgentCall, "Provider calls", "id run_id step day kind model provider status reserved_usd cost_usd reserved_tokens input_tokens output_tokens created_at",
                 "provider-calls", ["model", "status"]),
        BudgetUsage(AgentDailyUsage, label="Daily budget usage",
                    identity="usage"),
        readonly(AgentNote, "Research notes", "id run_id node_id kind status text citations created_at", "notes", [
                 "text", "kind", "status"]),
    ]))
    admin.add_view(DropDown("Accounts", views=[
        Users(User, label="Users", identity="users"),
        Sessions(AuthSession, label="Sessions", identity="sessions"),
    ]))
    admin.add_view(DropDown("Archive", views=[
        Quests(Quest, label="Quests", identity="quests"),
        readonly(Character, "Characters", "node_id canonical_name playable",
                 "characters", ["canonical_name"]),
        readonly(GameRelease, "Releases",
                 "id game_version sequence released_at", "releases", ["game_version"]),
        readonly(Node, "Nodes", "id canonical_key type_id slug",
                 "nodes", ["canonical_key", "slug"]),
        readonly(Document, "Documents",
                 "id node_id document_type title revision plain_text", "documents", ["title"]),
        readonly(GameEvent, "Events", "id title source event_kind",
                 "events", ["title"]),
        readonly(GameNotice, "Notices",
                 "id title feed locale category", "notices", ["title"]),
        readonly(Locale, "Locales", "id code name",
                 "locales", ["code", "name"]),
    ]))
    admin.mount_to(app)
    return admin
