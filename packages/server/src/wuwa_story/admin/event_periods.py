"""Manual event periods using the console's existing permissions and audit."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified
from starlette.requests import Request
from starlette_admin import DateTimeField, EnumField, IntegerField, RequestAction, StringField
from starlette_admin.exceptions import FormValidationError

from wuwa_story.admin.services import record
from wuwa_story.admin.views import Editable
from wuwa_story.db.models.game_events import GameEvent, GameEventOccurrence
from wuwa_story.ingestion.game_events import SERVER_KEYS


class UTCDateTimeField(DateTimeField):
    async def parse_form_data(self, request, form_data, action):
        raw = form_data.get(self.id)
        if not raw:
            return None
        try:
            value = datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            raise FormValidationError({self.name: "Enter a valid UTC date and time."}) from None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    async def serialize_value(self, request, value, action):
        if value is None:
            return None
        value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        return value.replace(tzinfo=None).isoformat() if action == RequestAction.EDIT else value.isoformat()


class EventPeriods(Editable):
    fields = [
        "id", IntegerField("event_id", label="Event ID", required=True,
                           help_text="Existing event ID from Archive → Events."),
        StringField("event_title", label="Event", orderable=False, searchable=False,
                    exclude_from_create=True, exclude_from_edit=True),
        EnumField("server", choices=list(SERVER_KEYS), required=True),
        "game_version",
        UTCDateTimeField("starts_at", label="Start (UTC)", help_text="UTC; leave empty if unknown."),
        UTCDateTimeField("ends_at", label="End (UTC)", help_text="UTC; leave empty if unknown."),
    ]
    exclude_fields_from_create = ["id"]
    exclude_fields_from_edit = ["id", "event_id", "server"]
    fields_default_sort = [("id", True)]

    async def find_all(self, request, skip=0, limit=100, where=None, order_by=None):
        rows = await super().find_all(request, skip, limit, where, order_by)
        if rows:
            titles = dict((await request.state.session.execute(select(GameEvent.id, GameEvent.title)
                          .where(GameEvent.id.in_({row.event_id for row in rows})))).all())
            for row in rows:
                row.event_title = titles.get(row.event_id)
        return rows

    async def find_by_pk(self, request, pk):
        row = await super().find_by_pk(request, pk)
        if row is not None:
            event = await request.state.session.get(GameEvent, row.event_id)
            row.event_title = event.title if event else None
        return row

    async def validate(self, request: Request, data: dict[str, Any]) -> None:
        start, end = data.get("starts_at"), data.get("ends_at")
        if start is not None and end is not None and end <= start:
            raise FormValidationError({"ends_at": "End must be later than start."})
        event_id = data.get("event_id")
        if "event_id" in data and (event_id is None or not await request.state.session.get(GameEvent, event_id)):
            raise FormValidationError({"event_id": "Choose an existing event ID."})
        if "server" in data and data["server"] not in SERVER_KEYS:
            raise FormValidationError({"server": "Choose a supported server."})

    async def before_create(self, request, data, obj):
        obj.source_occurrence_id = "manual:" + uuid4().hex
        obj.source_data = {"schedule_source": "manual"}
        await request.state.session.flush()
        record(request.state.session, request, "event_period.create", obj.id)

    async def before_edit(self, request, data, obj):
        db = request.state.session
        # Refresh provenance under the same row lock as the importers, without
        # flushing the edited fields before acquiring it.
        with db.no_autoflush:
            previous = await db.scalar(select(GameEventOccurrence.source_data)
                                       .where(GameEventOccurrence.id == obj.id).with_for_update())
        obj.source_data = {**(previous or {}), "schedule_source": "manual"}
        obj.source_data.pop("start_estimate", None)
        # An importer may have committed after this form loaded but before the
        # lock was acquired; write the submitted values even if initially equal.
        for field in ("starts_at", "ends_at", "game_version"):
            flag_modified(obj, field)
        record(db, request, "event_period.edit", obj.id)
