from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from wuwa_story.api.routes.visual_memory import IdentityInput, IdentityUpdate
from wuwa_story.visual_memory.kinds import KIND_LABELS, VisualKind
from wuwa_story.visual_memory.service import VisualMemoryService
from wuwa_story.visual_memory.tools import IdentityCreateInput, SearchInput
from test_visual_memory_service import memory  # noqa: F401


@pytest.mark.parametrize("kind", ["npm", "npb", "custom_kind", "", "NPC"])
def test_identity_and_query_schemas_reject_unknown_kinds(kind):
    for model, payload in (
        (IdentityInput, {"name": "Test", "kind": kind}),
        (IdentityUpdate, {"kind": kind}),
        (IdentityCreateInput, {"name": "Test", "kind": kind}),
        (SearchInput, {"image": {"reference_id": 1}, "kind": kind}),
    ):
        with pytest.raises(ValidationError):
            model.model_validate(payload)


def test_all_enum_types_have_ui_labels_and_are_supported():
    assert set(KIND_LABELS) == set(VisualKind)
    for kind in VisualKind:
        assert IdentityInput(name="Test", kind=kind).model_dump(mode="json")["kind"] == kind.value
    assert IdentityUpdate(kind="npc").kind is VisualKind.NPC
    schema = IdentityCreateInput.model_json_schema()
    assert schema["$defs"]["VisualKind"]["enum"] == [kind.value for kind in VisualKind]


@pytest.mark.asyncio
async def test_direct_service_cannot_create_typo_kind():
    session = AsyncMock()
    service = VisualMemoryService(session, storage=object())
    with pytest.raises(ValueError):
        await service.create_identity("Mysterious Woman", "npm")
    session.flush.assert_not_awaited()
    assert not session.add.called


@pytest.mark.asyncio
async def test_service_update_keeps_identity_on_invalid_kind(memory):
    service, _, _ = memory
    identity = await service.create_identity("Mysterious Woman", VisualKind.NPC)
    with pytest.raises(ValueError):
        await service.update_identity(identity["id"], kind="npb")
    result = await service._identity_dict(await service._identity(identity["id"]))
    assert result["kind"] == "npc"
    assert (await service.update_identity(identity["id"], kind=VisualKind.OTHER))["kind"] == "other"
