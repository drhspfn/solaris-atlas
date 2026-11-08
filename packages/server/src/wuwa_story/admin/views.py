"""Direct SQLAlchemy ModelViews. The library supplies all CRUD, forms and tables."""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import Select, or_, select, update
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette_admin import (
    EnumField,
    IntegerField,
    PasswordField,
    RequestAction,
    row_action,
)
from starlette_admin.contrib.sqla import ModelView
from starlette_admin.contrib.sqla.converters import ModelConverter
from starlette_admin.exceptions import ActionFailed, FormValidationError
from starlette_admin.fields import BaseField

from wuwa_story.admin.services import prepare_user, record
from wuwa_story.agents.audit import redacted
from wuwa_story.agents.contracts import AnalysisRequest
from wuwa_story.agents.jobs import enqueue_analysis, resume_analysis
from wuwa_story.agents.settings import get_agent_settings
from wuwa_story.agents.workspace import MemoryWrite, save_memory
from wuwa_story.auth.services import now_utc
from wuwa_story.db.models.agent_workspace import AgentMemory, AgentWorkItem
from wuwa_story.db.models.agents import AgentJob, AgentNote
from wuwa_story.db.models.auth import AuthSession
from wuwa_story.db.models.content import DocumentHead
from wuwa_story.db.models.core import Quest
from wuwa_story.db.models.ops import ProcessingRun


class ScalarForeignKeys(ModelConverter):
    def convert_fields_list(self, *, fields: Sequence[Any], model: type[Any], **kwargs: Any) -> Sequence[BaseField]:
        # These mappings deliberately use scalar FKs rather than ORM relationships.
        # Keep their IDs visible/editable instead of the library's default omission.
        mapper = kwargs["mapper"]
        converted = []
        for field in fields:
            column = mapper.columns.get(
                field) if isinstance(field, str) else None
            if column is not None and column.foreign_keys:
                field = self.convert(
                    name=field, type=column.type, column=column)
            converted.append(field)
        return super().convert_fields_list(fields=converted, model=model, **kwargs)


class ReadOnly(ModelView):
    page_size = 25
    page_size_options = [25, 50, 100]
    export_types = []
    actions = []
    row_actions = ["view"]

    def __init__(self, model: type[Any], **kwargs: Any) -> None:
        super().__init__(model, converter=ScalarForeignKeys(), **kwargs)

    def can_create(self, request: Request) -> bool:
        return False

    def can_edit(self, request: Request) -> bool:
        return False

    def can_delete(self, request: Request) -> bool:
        return False

    async def find_all(self, request: Request, skip: int = 0, limit: int = 100, where: Any = None, order_by: list[str] | None = None) -> Sequence[Any]:
        if not 0 <= skip <= 1_000_000 or not 1 <= limit <= 100:
            raise HTTPException(422, "Use a page size between 1 and 100.")
        return await super().find_all(request, skip, limit, where, order_by)

    async def find_by_pks(self, request: Request, pks: list[Any]) -> Sequence[Any]:
        if len(pks) > 100:
            raise HTTPException(422, "Select at most 100 records.")
        return await super().find_by_pks(request, pks)

    async def serialize_field_value(self, value: Any, field: BaseField, action: RequestAction, request: Request) -> Any:
        if isinstance(value, dict) and action != RequestAction.EDIT:
            value = redacted(value)
        return await super().serialize_field_value(value, field, action, request)

    def handle_exception(self, exc: Exception) -> None:
        if isinstance(exc, IntegrityError):
            raise FormValidationError(
                {"_form": "Duplicate value or invalid referenced record."}) from None
        if isinstance(exc, (ValueError, TypeError)):
            raise FormValidationError(
                {"_form": "Invalid values or stale revision. Reload and check the form."}) from None
        return super().handle_exception(exc)


class BudgetUsage(ReadOnly):
    fields = ["day", "spent_usd", "reserved_usd",
              "spent_tokens", "reserved_tokens"]
    fields_default_sort = [("day", True)]
    list_template = "budget_usage.html"

    def budget_info(self) -> dict[str, Any]:
        settings = get_agent_settings()
        return {
            "daily_budget_usd": settings.daily_budget_usd,
            "daily_token_limit": settings.daily_token_limit,
            "timezone": settings.budget_timezone,
            "today": datetime.now(UTC).astimezone(ZoneInfo(settings.budget_timezone)).date(),
        }


class Editable(ReadOnly):
    row_actions = ["view", "edit"]

    def can_create(self, request: Request) -> bool:
        return True

    def can_edit(self, request: Request) -> bool:
        return True

    async def _recover_form(self, request: Request) -> None:
        db = request.state.session
        if not db.is_active:
            await db.rollback()
            for obj in list(db.identity_map.values()):
                await db.refresh(obj)

    async def create(self, request: Request, data: dict[str, Any]) -> Any:
        try:
            return await super().create(request, data)
        except FormValidationError:
            await self._recover_form(request)
            raise

    async def edit(self, request: Request, pk: Any, data: dict[str, Any]) -> Any:
        try:
            return await super().edit(request, pk, data)
        except FormValidationError:
            await self._recover_form(request)
            raise


class Users(Editable):
    fields: Sequence[Any] = ["id", "email", "nickname",
                             EnumField("role", choices=[(1, "User"),
                                                        (2, "Administrator")], coerce=int),
                             EnumField("status", choices=[
                                 (1, "Active"), (2, "Disabled")], coerce=int),
                             PasswordField("password", label="New password", minlength=8, maxlength=128,
                                           help_text="Required for a new account. Leave blank when editing to keep the password.",
                                           exclude_from_list=True, exclude_from_detail=True), "created_at", "last_login_at"]
    exclude_fields_from_create = ["id", "created_at", "last_login_at"]
    exclude_fields_from_edit = exclude_fields_from_create
    searchable_fields = ["email", "nickname"]

    async def before_create(self, request: Request, data: dict[str, Any], obj: Any) -> None:
        await prepare_user(request, data, obj, creating=True)

    async def before_edit(self, request: Request, data: dict[str, Any], obj: Any) -> None:
        await prepare_user(request, data, obj, creating=False)

    async def serialize_field_value(self, value: Any, field: BaseField, action: RequestAction, request: Request) -> Any:
        if field.name == "password":
            return ""
        return await super().serialize_field_value(value, field, action, request)


class Sessions(ReadOnly):
    fields: Sequence[Any] = ["id", "user_id", "created_at",
                             "expires_at", "last_seen_at", "revoked_at"]
    row_actions = ["view", "revoke"]

    @row_action(name="revoke", text="Revoke session", icon_class="fa fa-ban", confirmation="End this session? Its next request will require sign-in.")
    async def revoke(self, request: Request, pk: Any) -> str:
        db = request.state.session
        await db.execute(update(AuthSession).where(AuthSession.id == int(pk), AuthSession.revoked_at.is_(None))
                         .values(revoked_at=now_utc()))
        record(db, request, "session.revoke", pk)
        await db.commit()
        return "Session revoked."


class Memory(Editable):
    fields: Sequence[Any] = ["id", "key", EnumField("scope", choices=["short", "long"]), "text", "run_id", "node_id", "citations",
                             IntegerField("revision", read_only=True), "updated_at"]
    exclude_fields_from_list = ["text", "citations"]
    exclude_fields_from_create = ["id", "revision", "updated_at"]
    exclude_fields_from_edit = ["id", "key", "scope", "run_id", "updated_at"]
    searchable_fields = ["key", "text"]

    async def create(self, request: Request, data: dict[str, Any]) -> Any:
        return await self._save(request, data)

    async def edit(self, request: Request, pk: Any, data: dict[str, Any]) -> Any:
        return await self._save(request, data, pk)

    async def _save(self, request: Request, data: dict[str, Any], pk: Any = None) -> Any:
        db = request.state.session
        try:
            previous = await self.find_by_pk(request, pk) if pk else None
            if pk and previous is None:
                raise ValueError("Memory no longer exists")
            if previous:
                prefix = "long:" if previous.scope == "long" else f"run:{previous.run_id}:"
                key, scope, run_id = previous.key.removeprefix(
                    prefix), previous.scope, previous.run_id
            else:
                key, scope, run_id = data["key"], data["scope"], data.get(
                    "run_id")
            request_data = MemoryWrite(key=key, scope=scope, text=data["text"], node_id=data.get("node_id"),
                                       citations=data.get("citations") or [], expected_revision=int(data["revision"]) if pk else 0)
            result = await save_memory(db, get_agent_settings(), request_data, run_id=run_id)
            record(db, request,
                   "memory.edit" if pk else "memory.create", result["id"])
            await db.commit()
            # A Core CAS update may have left an older instance in this ORM identity map.
            return await db.get(AgentMemory, result["id"], populate_existing=True)
        except Exception as exc:
            await self._recover_form(request)
            self.handle_exception(exc)


class Work(Editable):
    fields: Sequence[Any] = ["id", "kind", "node_id", "title", "text", "priority", "status", "run_id",
                             IntegerField("revision", read_only=True), "search_terms", "citations", "resolution", "updated_at"]
    exclude_fields_from_list = [
        "text", "search_terms", "citations", "resolution"]
    exclude_fields_from_create = ["id", "kind", "status", "run_id",
                                  "revision", "search_terms", "citations", "resolution", "updated_at"]
    exclude_fields_from_edit = ["id", "kind", "status", "run_id",
                                "search_terms", "citations", "resolution", "updated_at"]
    searchable_fields = ["title", "text", "status"]

    async def validate(self, request: Request, data: dict[str, Any]) -> None:
        if not 1 <= len(data.get("title") or "") <= 512 or not 1 <= len(data.get("text") or "") <= 6000:
            raise FormValidationError(
                {"title": "Enter a title and a description (up to 6000 characters)."})
        if data.get("priority") not in (1, 2, 3):
            raise FormValidationError(
                {"priority": "Use 1 (high), 2 (normal), or 3 (low)."})
        if not await request.state.session.get(Quest, data.get("node_id")):
            raise FormValidationError(
                {"node_id": "Choose a quest's node ID from the Quest table."})

    async def before_create(self, request: Request, data: dict[str, Any], obj: Any) -> None:
        obj.key, obj.kind, obj.status, obj.revision = "manual:" + \
            uuid4().hex, "analysis", "open", 1
        obj.search_terms, obj.citations, obj.resolution = [], [], {}
        await request.state.session.flush()
        record(request.state.session, request, "work.create", obj.id)

    async def before_edit(self, request: Request, data: dict[str, Any], obj: Any) -> None:
        db = request.state.session
        with db.no_autoflush:
            current = (await db.execute(select(AgentWorkItem.kind, AgentWorkItem.status, AgentWorkItem.revision)
                                        .where(AgentWorkItem.id == obj.id).with_for_update())).one()
        if current.kind != "analysis" or current.status not in {"open", "deferred"} or current.revision != data.get("revision"):
            raise FormValidationError(
                {"revision": "Only unqueued analysis tasks at the current revision can be edited."})
        obj.revision = current.revision + 1
        record(db, request, "work.edit", obj.id)


class OpenQuestions(ReadOnly):
    """A custom ORM-backed page using the library's existing table components."""
    fields: Sequence[Any] = ["id", "priority", "node_id",
                             "title", "text", "status", "citations", "updated_at"]
    exclude_fields_from_list = ["text", "citations"]
    searchable_fields = ["title", "text"]
    fields_default_sort = [("priority", False), ("id", True)]

    def _conditions(self) -> tuple[Any, ...]:
        active = select(DocumentHead.document_id).where(
            DocumentHead.document_id == AgentWorkItem.document_id).exists()
        return (AgentWorkItem.kind == "question", AgentWorkItem.status.in_(["open", "partial"]),
                or_(AgentWorkItem.document_id.is_(None), active))

    def get_list_query(self, request: Request) -> Select[Any]:
        return super().get_list_query(request).where(*self._conditions())

    def get_count_query(self, request: Request) -> Select[Any]:
        return super().get_count_query(request).where(*self._conditions())


class Alerts(ReadOnly):
    fields: Sequence[Any] = ["id", "kind", "text", "node_id",
                             IntegerField("run_id", display_template="displays/alert_run.html"),
                             "citations", "created_at"]
    exclude_fields_from_list = ["citations"]
    searchable_fields = ["kind", "text"]
    fields_default_sort = [("id", True)]
    row_actions = ["view", "resolve"]

    def get_list_query(self, request: Request) -> Select[Any]:
        return super().get_list_query(request).where(AgentNote.kind != "note", AgentNote.status == "open")

    def get_count_query(self, request: Request) -> Select[Any]:
        return super().get_count_query(request).where(AgentNote.kind != "note", AgentNote.status == "open")

    @row_action(name="resolve", text="Mark resolved", icon_class="fa fa-check",
                confirmation="Mark this alert as resolved?")
    async def resolve(self, request: Request, pk: Any) -> str:
        try:
            alert_id = int(pk)
        except (TypeError, ValueError):
            raise ActionFailed("Invalid alert ID.") from None
        db = request.state.session
        result = await db.execute(update(AgentNote).where(
            AgentNote.id == alert_id, AgentNote.kind != "note", AgentNote.status == "open"
        ).values(status="resolved").returning(AgentNote.id))
        if result.scalar_one_or_none() is None:
            raise ActionFailed("This alert is no longer open. Refresh the list.")
        record(db, request, "alert.resolve", alert_id)
        await db.commit()
        return f"Alert {pk} resolved."


class Runs(ReadOnly):
    fields: Sequence[Any] = ["id", "status", "target_node_id", "prompt_version", "tokens_input",
                             "tokens_output", "cost", "started_at", "finished_at", "error"]
    searchable_fields = ["status", "prompt_version"]
    row_actions = ["view", "resume"]

    def get_list_query(self, request: Request) -> Select[Any]:
        return super().get_list_query(request).where(ProcessingRun.id.in_(select(AgentJob.run_id)))

    def get_count_query(self, request: Request) -> Select[Any]:
        return super().get_count_query(request).where(ProcessingRun.id.in_(select(AgentJob.run_id)))

    @row_action(name="resume", text="Resume research", icon_class="fa fa-play", confirmation="Resume this supported paused run with two additional turns, within its configured budget?")
    async def resume(self, request: Request, pk: Any) -> str:
        try:
            run = await resume_analysis(request.state.session, int(pk), extra_steps=2)
            record(request.state.session, request, "run.resume", run.id)
            await request.state.session.commit()
            return f"Run {run.id} queued."
        except ValueError:
            raise ActionFailed(
                "This run cannot be resumed. Uncertain billing requires reconciliation first.") from None


class Quests(ReadOnly):
    fields: Sequence[Any] = ["node_id",
                             "game_quest_id", "quest_type", "region_id"]
    row_actions = ["view", "analyze"]

    @row_action(name="analyze", text="Queue story analysis", icon_class="fa fa-book", confirmation="Queue a cited analysis of this quest in the latest imported version? Existing provider and daily budget limits apply.")
    async def analyze(self, request: Request, pk: Any) -> str:
        quest = await self.find_by_pk(request, pk)
        try:
            run = await enqueue_analysis(request.state.session,
                                         AnalysisRequest(quest_id=quest.game_quest_id), get_agent_settings())
            record(request.state.session, request, "quest.analyze", run.id)
            await request.state.session.commit()
            return f"Analysis run {run.id} queued."
        except ValueError:
            raise ActionFailed(
                "Check imported sources, provider configuration and the daily budget.") from None


class GraphEdges(ReadOnly):
    fields: Sequence[Any] = [
        "id", IntegerField("from_node_id", display_template="displays/graph_node.html"),
        "relation_type_id", IntegerField("to_node_id", display_template="displays/graph_node.html"),
        "layer", "basis", "confidence", "created_release_id", "valid_from_release_id",
        "valid_to_release_id", "processor_run_id",
    ]
    searchable_fields = ["layer", "basis"]


class Cutscenes(ReadOnly):
    fields: Sequence[Any] = ["node_id", "cg_name", "cg_file", "media_asset_node_id"]
    searchable_fields = ["cg_name", "cg_file"]
    detail_template = "cutscene_detail.html"
