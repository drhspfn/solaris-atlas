"""Background builders preserve public payloads, identities and observed history."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from types import SimpleNamespace as Row
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert

from wuwa_story.api.read_projections import catalog_entry
from wuwa_story.db.models.core import Character, Cutscene
from wuwa_story.db.models.game_events import GameEvent
from wuwa_story.db.models.graph import Node, NodeType
from wuwa_story.db.models.i18n import Locale
from wuwa_story.db.models.notices import GameNotice
from wuwa_story.db.repositories.read_models import ENTITY_READ_MODELS
from wuwa_story.read_models import builders
from wuwa_story.read_models import source_payloads as payloads
from wuwa_story.read_models import source_relations as relations
from wuwa_story.read_models import source_sections as sections


@pytest.fixture
def context(monkeypatch):
    locale = Row(id=2, code="ja")
    node = Row(id=7, type_id=1, canonical_key="character:70", slug="7-name")
    node_type = Row(key="character")
    event = Row(
        id=7,
        source=sections.CLIENT_SOURCE,
        slug="7-event",
        title="Event",
        event_kind="limited",
        description="Description",
        source_url="https://example.org/source",
    )
    records = {
        (Locale, 2): locale,
        (Node, 7): node,
        (NodeType, 1): node_type,
        (GameEvent, 7): event,
        (GameNotice, 7): Row(locale="ja"),
        (Cutscene, 7): Row(cg_name="AuthoredMovie"),
        (Character, 7): Row(metadata_json={"role_type": 2}),
    }
    session = Row(
        info={},
        get=AsyncMock(side_effect=lambda model, key: records.get((model, key))),
        scalar=AsyncMock(return_value=locale),
        scalars=AsyncMock(return_value=[]),
        execute=AsyncMock(return_value=[]),
    )
    profile = {
        "character": {"name": {"content": "デニア"}, "slug": node.slug},
        "co_present_characters": [
            {"basis": "shared_flow_state_dialogue", "meaning": "co-presence only"}
        ],
    }
    character = AsyncMock(return_value=profile)
    archive = AsyncMock(return_value={"locale": "ja", "artwork": []})
    monkeypatch.setattr(sections.profiles, "character_profile", character)
    monkeypatch.setattr(payloads.character_archive, "character_archive", archive)
    monkeypatch.setattr(
        sections.profiles,
        "item_profile",
        AsyncMock(return_value={"item": {"name": {"content": "Item"}}}),
    )
    monkeypatch.setattr(
        sections.profiles,
        "location_profile",
        AsyncMock(return_value={"location": {"name": {"content": "Place"}}}),
    )
    monkeypatch.setattr(
        sections.profiles,
        "quest_profile",
        AsyncMock(return_value={"quest": {"name": {"content": "Quest"}}}),
    )
    monkeypatch.setattr(
        sections.transcripts,
        "quest_transcript",
        AsyncMock(return_value={"lines": [{"id": "line:1"}]}),
    )
    monkeypatch.setattr(
        sections.continuity, "quest_continuity", AsyncMock(return_value={"presence": "observed"})
    )
    monkeypatch.setattr(sections.media, "quest_media", AsyncMock(return_value={"events": []}))
    monkeypatch.setattr(
        payloads.notices,
        "notice_detail",
        AsyncMock(
            return_value={"id": 7, "title": "Notice", "slug": "notice", "revisions": ["r1", "r2"]}
        ),
    )
    monkeypatch.setattr(
        payloads.cutscenes, "detail", AsyncMock(return_value={"event": {"kind": "cutscene"}})
    )
    monkeypatch.setattr(
        sections.nodes,
        "node_narrative_context",
        AsyncMock(return_value={"dialogue": {"id": "line:1"}}),
    )
    monkeypatch.setattr(
        sections.nodes,
        "node_detail",
        AsyncMock(return_value=Row(model_dump=lambda: {"canonical_key": "speaker:70"})),
    )
    monkeypatch.setattr(sections.nodes, "node_related", AsyncMock(return_value={"results": []}))
    monkeypatch.setattr(sections, "source_related", AsyncMock(return_value={"results": [], "offset": 0}))
    monkeypatch.setattr(
        sections.nodes,
        "node_source_record",
        AsyncMock(return_value={"source_record": None, "resolution": "no_raw_record_linked"}),
    )
    monkeypatch.setattr(sections, "_node_edges", AsyncMock(return_value=[]))
    monkeypatch.setattr(sections, "_raw_refs", AsyncMock(return_value={}))
    monkeypatch.setattr(
        relations,
        "_dialogue_filters",
        AsyncMock(return_value={"quest_ids": [], "character_keys": []}),
    )
    monkeypatch.setattr(
        sections,
        "_cutscene_catalog",
        AsyncMock(
            return_value={
                "item": {"game_version": "3.10"},
                "cursor_values": [2, 3, 10, 0, 1, "AuthoredMovie", 7],
                "playable": True,
                "release_id": 12,
                "imported_game_versions": ["3.10"],
            }
        ),
    )
    monkeypatch.setattr(
        sections,
        "browse_events",
        AsyncMock(
            return_value={
                "events": [
                    {"id": 7, "occurrence_id": None, "title": "Event", "artwork": []},
                ]
            }
        ),
    )
    monkeypatch.setattr(sections.service, "_release_id", AsyncMock(return_value=12))
    monkeypatch.setattr(sections.service, "_node_label", AsyncMock(return_value={"label": "Label"}))
    monkeypatch.setattr(sections, "_artwork", AsyncMock(return_value=[]))
    return Row(
        session=session,
        locale=locale,
        node=node,
        node_type=node_type,
        records=records,
        character=character,
        archive=archive,
        profile=profile,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,node_kind,payload_keys",
    [
        ("character", "character", {"profile", "archive"}),
        ("item", "item", {"profile"}),
        ("location", "area", {"profile"}),
        ("quest", "quest", {"profile", "transcript", "continuity", "media"}),
        ("game_event", None, {"event", "occurrences", "unversioned_occurrences"}),
        (
            "game_notice",
            None,
            {"id", "title", "slug", "revisions", "revision_snapshot_format", "revision_snapshots"},
        ),
        ("speaker", "speaker", {"node", "related", "source_record", "edges"}),
        ("dialogue", "talk_item", {"dialogue"}),
        ("dialogue", "phone_message", {"dialogue"}),
        ("cutscene", "cutscene", {"event", "catalog"}),
    ],
)
async def test_every_model_builds_a_publishable_card_and_full_public_payload(
    context, kind, node_kind, payload_keys
):
    context.node_type.key = node_kind
    result = await builders.build_entity(context.session, kind, 7, 2)
    assert result["entity_id"] == 7 and result["locale_id"] == 2
    assert result["snapshot_key"] == "current"
    expected = payload_keys | {"card"}
    if kind in relations._NODE_KINDS:
        expected |= {"node", "related", "source_record", "edges"}
    assert result["payload"].keys() == expected
    assert result["title"] and result["sort_title"] == result["title"].casefold()
    assert (
        isinstance(result["searchable_text"], str) and result["title"] in result["searchable_text"]
    )
    assert result.keys() <= set(ENTITY_READ_MODELS[kind].__table__.columns.keys())
    assert result["payload"]["card"]["href"]
    assert isinstance(result["payload"]["card"]["aliases"], list)
    assert result["card"] == result["payload"]["card"]
    assert "role_type" in result and "quest_type" in result
    # The refresher supplies its generation; no fabricated default is published.
    assert "generation" not in result
    insert(ENTITY_READ_MODELS[kind]).values(**result, generation=3).compile(
        dialect=postgresql.dialect()
    )
    card = catalog_entry(Row(**result, generation=3), kind)
    assert card["id"] == 7 and card["title"] == result["title"]
    if kind in {"game_event", "game_notice"}:
        assert result["canonical_key"] is None


@pytest.mark.asyncio
async def test_character_version_locale_evidence_and_artwork_identity_are_preserved(
    context, monkeypatch
):
    context.profile["admin_actions"] = {"delete": "/api/admin/delete"}
    context.profile["co_present_characters"][0]["private_metadata"] = {"credentials": "hidden"}
    context.archive.return_value["artwork"] = [
        {
            "kind": "RoleStand",
            "scope": "shared_resource",
            "url": "https://cdn/shared",
            "engine_path": "shared",
        },
        {
            "kind": "RoleHeadIconLarge",
            "scope": "character",
            "url": "https://cdn/head",
            "engine_path": "head",
        },
    ]
    monkeypatch.setattr(
        sections,
        "_artwork",
        AsyncMock(
            return_value=[
                {"engine_path": "head", "url": "https://cdn/head", "file_id": 81},
            ]
        ),
    )
    result = await builders.build_entity(context.session, "character", 7, "ja", "3.10")
    context.character.assert_awaited_once_with(
        "character:70", locale="ja", game_version="3.10", session=context.session
    )
    context.archive.assert_awaited_once_with(
        "character:70", locale="ja", game_version="3.10", session=context.session
    )
    assert result["title"] == "デニア"
    assert result["payload"]["card"]["role_type"] == 2
    assert result["primary_image_file_id"] == 81
    assert result["primary_image_url"] == "https://cdn/head"
    assert result["artwork"][0]["scope"] == "shared_resource"
    assert "admin_actions" not in result["payload"]["profile"]
    relation = result["payload"]["profile"]["co_present_characters"][0]
    assert relation == {"basis": "shared_flow_state_dialogue", "meaning": "co-presence only"}
    assert "private_metadata" in context.profile["co_present_characters"][0]


@pytest.mark.asyncio
async def test_quest_materializes_all_transcript_pages(context):
    context.node_type.key = "quest"
    first = [{"id": f"line:{i}"} for i in range(2000)]
    sections.transcripts.quest_transcript.side_effect = [
        {"lines": first, "scenes": ["source-scene"]},
        {"lines": [{"id": "line:2000"}]},
    ]
    result = await builders.build_entity(context.session, "quest", 7, 2, "3.10")
    transcript = result["payload"]["transcript"]
    assert len(transcript["lines"]) == transcript["lines_returned"] == 2001
    assert transcript["scenes"] == ["source-scene"]
    calls = sections.transcripts.quest_transcript.await_args_list
    assert [call.kwargs["offset"] for call in calls] == [0, 2000]
    assert all(
        call.kwargs["locale"] == "ja"
        and call.kwargs["game_version"] == "3.10"
        and call.kwargs["include_raw"] is False
        for call in calls
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot", ["current", "3.10"])
async def test_event_history_keeps_all_servers_and_unknown_versions_separate(
    context, monkeypatch, snapshot
):
    context.session.execute.return_value = [
        (1, "asia", "3.10"),
        (2, "region_4", None),
        (3, "america", "3.9"),
    ]

    async def event_response(server, _kind, _version, locale, session, *, event_id, occurrence_id):
        return {
            "events": [
                {
                    "id": event_id,
                    "occurrence_id": occurrence_id,
                    "starts_at": datetime(2026, 1, 2, tzinfo=UTC),
                    "preview_url": f"https://cdn/{occurrence_id}",
                    "artwork": [{"url": f"https://cdn/{occurrence_id}", "role": "content"}],
                }
            ]
        }

    render = AsyncMock(side_effect=event_response)
    monkeypatch.setattr(sections, "browse_events", render)
    result = await builders.build_entity(context.session, "event", 7, 2, snapshot)
    payload = result["payload"]
    assert [run["occurrence_id"] for run in payload["occurrences"]] == (
        [1, 2, 3] if snapshot == "current" else [1]
    )
    assert [run["occurrence_id"] for run in payload["unversioned_occurrences"]] == (
        [] if snapshot == "current" else [2]
    )
    unknown = (
        payload["occurrences"][1]
        if snapshot == "current"
        else payload["unversioned_occurrences"][0]
    )
    assert unknown["server"] == "region_4"
    assert payload["occurrences"][0]["starts_at"].startswith("2026-01-02T")
    assert [call.args[0] for call in render.await_args_list] == (
        ["asia", "region_4", "america", ""] if snapshot == "current" else ["asia", "region_4", ""]
    )
    assert all(call.args[3] == "ja" for call in render.await_args_list)
    assert result["primary_image_file_id"] is None  # External URLs are not storage file IDs.


@pytest.mark.asyncio
async def test_event_without_runs_does_not_invent_a_server_or_occurrence(context, monkeypatch):
    render = AsyncMock(
        return_value={
            "events": [
                {
                    "id": 7,
                    "occurrence_id": None,
                    "description": "Full undated detail",
                    "artwork": [],
                },
            ]
        }
    )
    monkeypatch.setattr(sections, "browse_events", render)
    result = await builders.build_entity(context.session, "event", 7, 2)
    assert result["payload"]["occurrences"] == []
    assert result["payload"]["event"]["description"] == "Full undated detail"
    assert result["payload"]["event"]["server"] is None
    assert result["primary_image_url"] is None
    render.assert_awaited_once_with(
        "", None, None, "ja", context.session, event_id=7, occurrence_id=None
    )


@pytest.mark.asyncio
async def test_regional_event_retains_authoritative_undated_fallback_in_calendar(context):
    context.session.execute.return_value = [(1, "asia", "3.10")]
    context_event = {
        "id": 7,
        "title": "Localized event",
        "description": "Public event detail",
        "artwork": [],
    }
    sections.browse_events.side_effect = [
        {"events": [{**context_event, "occurrence_id": 1, "starts_at": "2026-10-30"}]},
        {"events": [{**context_event, "occurrence_id": None, "starts_at": None}]},
    ]
    entry = await builders.build_entity(context.session, "event", 7, 2)
    fallback = entry["payload"]["event"]
    assert fallback["occurrence_id"] is None and fallback["starts_at"] is None
    assert fallback["server"] is None and fallback["title"] == "Localized event"
    assert entry["card"]["calendar"]["event"] == fallback
    assert [run["server"] for run in entry["payload"]["occurrences"]] == ["asia"]
    sections.browse_events.assert_awaited_with(
        "", None, None, "ja", context.session, event_id=7, occurrence_id=None
    )


@pytest.mark.asyncio
async def test_unknown_event_versions_do_not_establish_historical_membership(context):
    with pytest.raises(HTTPException) as error:
        await builders.build_entity(context.session, "event", 7, 2, "3.10")
    assert error.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["location", "speaker", "notice"])
async def test_unavailable_historical_selection_is_never_published_as_current(context, kind):
    with pytest.raises(ValueError, match="no public historical snapshot selector"):
        await builders.build_entity(context.session, kind, 7, 2, "3.10")
    sections.profiles.location_profile.assert_not_awaited()
    payloads.notices.notice_detail.assert_not_awaited()


@pytest.mark.asyncio
async def test_notice_locale_cannot_be_relabelled(context):
    context.records[(GameNotice, 7)].locale = "en"
    with pytest.raises(ValueError, match="different locale"):
        await builders.build_entity(context.session, "notice", 7, 2)
    payloads.notices.notice_detail.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_snapshot_and_wrong_kind_fail_before_source_render(context):
    sections.service._release_id.return_value = None
    with pytest.raises(ValueError, match="Snapshot not imported"):
        await builders.build_entity(context.session, "character", 7, 2, "99.0")
    context.character.assert_not_awaited()
    context.node_type.key = "item"
    with pytest.raises(HTTPException) as error:
        await builders.build_entity(context.session, "character", 7, 2)
    assert error.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_worker_session_request_caches_are_isolated_and_restored(context, fail):
    original = {
        "story_node_labels": {(7, "ja"): {"label": "stale"}},
        ("quest_type_records", 12): {"stale": True},
        "job": "preserve",
    }
    context.session.info.update(original)

    async def profile(*args, **kwargs):
        assert "story_node_labels" not in context.session.info
        context.session.info["story_node_labels"] = {"fresh": True}
        if fail:
            raise HTTPException(404, "missing source")
        return context.profile

    context.character.side_effect = profile
    if fail:
        with pytest.raises(HTTPException):
            await builders.build_entity(context.session, "character", 7, 2)
    else:
        await builders.build_entity(context.session, "character", 7, 2)
    assert context.session.info == original


@pytest.mark.asyncio
async def test_missing_archive_aborts_the_whole_character_projection(context):
    context.archive.side_effect = HTTPException(404, "character source record not found")
    with pytest.raises(HTTPException) as error:
        await builders.build_entity(context.session, "character", 7, 2)
    assert error.value.status_code == 404
    context.character.assert_awaited_once()
    assert context.session.info == {}
    sections._artwork.assert_not_awaited()


@pytest.mark.asyncio
async def test_speaker_related_payload_does_not_silently_stop_at_first_page(context):
    context.node_type.key = "speaker"
    results = [{"node": {"id": identity}, "basis": "explicit_reference"} for identity in range(501)]
    sections.source_related.return_value = {"results": results, "include_evidence": True, "offset": 0}
    result = await builders.build_entity(context.session, "speaker", 7, 2)
    assert len(result["payload"]["related"]["results"]) == 501
    sections.source_related.assert_awaited_once_with(
        context.session, 7, "character:70", include_evidence=True, locale="ja", limit=None,
    )
    sections.nodes.node_related.assert_not_awaited()
    assert result["payload"]["related"]["include_evidence"] is True


@pytest.mark.asyncio
async def test_cutscene_card_uses_the_public_preview_identity(context):
    context.node_type.key = "cutscene"
    payloads.cutscenes.detail.return_value = {
        "title": "Authored scene",
        "preview_url": "https://cdn/preview",
        "preview_file_id": 88,
        "previews": [{"file_id": 88, "url": "https://cdn/preview"}],
        "passages": [],
    }
    result = await builders.build_entity(context.session, "cutscene", 7, 2, "3.10")
    assert result["title"] == "Authored scene"
    assert result["primary_image_url"] == "https://cdn/preview"
    assert result["primary_image_file_id"] == 88
    payloads.cutscenes.detail.assert_awaited_once_with(
        "AuthoredMovie",
        game_version="3.10",
        locale="ja",
        session=context.session,
    )


@pytest.mark.asyncio
async def test_ambiguous_event_render_fails_without_picking_a_run(context, monkeypatch):
    context.session.execute.return_value = [(1, "asia", "3.10")]
    monkeypatch.setattr(
        sections,
        "browse_events",
        AsyncMock(
            return_value={
                "events": [
                    {"id": 7, "occurrence_id": 999},
                ]
            }
        ),
    )
    with pytest.raises(ValueError, match="unambiguously"):
        await builders.build_entity(context.session, "event", 7, 2)


@pytest.mark.asyncio
async def test_unknown_locale_does_not_trigger_any_source_builder(context):
    context.records[(Locale, 2)] = None
    with pytest.raises(ValueError, match="Unknown locale"):
        await builders.build_entity(context.session, "character", 7, 2)
    context.character.assert_not_awaited()


@pytest.mark.asyncio
async def test_artwork_file_identity_requires_a_public_available_image(monkeypatch):
    session = Row(
        execute=AsyncMock(
            return_value=[
                ("/Game/UIQuest/Shared", 1, "shared"),
                ("/Game/IconRoleHead256/Head", 2, "new-head"),
                ("/Game/IconRoleHead256/Head", 3, "old-head"),
            ]
        )
    )
    monkeypatch.setattr(sections, "get_settings", lambda: Row(s3_bucket="public-game"))
    monkeypatch.setattr(
        sections, "S3Storage", lambda _: Row(public_url=lambda key: f"https://cdn/{key}")
    )
    result = await sections._artwork(session, Row(id=7, canonical_key="character:70"), "character")
    assert result == [
        {"engine_path": "/Game/IconRoleHead256/Head", "file_id": 2, "url": "https://cdn/new-head"}
    ]
    query = str(
        session.execute.await_args.args[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "owner_node_id = 7" in query and "reference_type = 'entity_image'" in query
    assert "available IS true" in query and "is_primary IS true" in query
    assert "bucket = 'public-game'" in query and "mime_type IN" in query


def test_builder_source_kind_mapping_matches_refresh():
    from wuwa_story.read_models.refresh import NODE_KINDS

    assert relations._NODE_KINDS == {kind: set(types) for kind, types in NODE_KINDS.items()}


@pytest.mark.asyncio
async def test_notice_physical_listing_card_preserves_exact_fields_without_body_or_revisions(
    context,
):
    from fastapi.encoders import jsonable_encoder

    listing = {
        "id": 7,
        "slug": "7-notice",
        "href": "/notices/7-notice",
        "source_id": "source-7",
        "locale": "ja",
        "category": "activity",
        "title": "Localized notice",
        "revision": "r2",
        "banner_url": "https://cdn/banner.png",
        "source_url": "https://example.org/notice",
        "available_from": None,
        "observed_at": datetime(2026, 10, 30, tzinfo=UTC),
        "related_event_ids": [1, 2],
    }
    body = [{"type": "text", "text": "Full body " * 10000}]
    payloads.notices.notice_detail.return_value = {
        **listing,
        "blocks": body,
        "links": [{"url": "https://example.org/detail"}],
        "events": [{"id": 1}],
        "revisions": ["r2", "r1"],
    }
    entry = await builders.build_entity(context.session, "notice", 7, 2)
    assert entry["card"]["notice"] == jsonable_encoder(listing)
    assert entry["payload"]["blocks"] == body
    assert "revision_snapshots" in entry["payload"]
    assert len(json.dumps(entry["card"])) < 1000
    assert entry["payload"]["card"] == entry["card"]


@pytest.mark.asyncio
async def test_cutscene_physical_catalog_card_is_independent_of_full_playback_payload(context):
    context.node_type.key = "cutscene"
    catalog = {
        "item": {
            "node_id": 7,
            "name": "AuthoredMovie",
            "title": "Localized scene",
            "game_version": "3.10",
        },
        "cursor_values": [2, 3, 10, 0, 1, "AuthoredMovie", 7],
        "playable": True,
        "release_id": 12,
        "imported_game_versions": ["3.10", "3.9"],
    }
    sections._cutscene_catalog.return_value = catalog
    payloads.cutscenes.detail.return_value = {
        "event": {"kind": "cutscene"},
        "passages": [{"text": "Full playback context " * 10000}],
    }
    entry = await builders.build_entity(context.session, "cutscene", 7, 2)
    assert entry["card"]["catalog"] == catalog == entry["payload"]["catalog"]
    assert entry["payload"]["passages"] == payloads.cutscenes.detail.return_value["passages"]
    assert "passages" not in entry["card"] and "event" not in entry["card"]
    assert len(json.dumps(entry["card"])) < 1000


@pytest.mark.asyncio
async def test_notice_payload_and_artwork_are_jsonb_serializable_after_direct_route_call(context):
    class ImageKind(Enum):
        BANNER = "banner"

    identity = UUID("61e2ae61-9047-4e39-914c-29d603092f19")
    observed_at = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
    payloads.notices.notice_detail.return_value.update(
        observed_at=observed_at,
        related_payload={
            "price": Decimal("12.50"),
            "public_id": identity,
            "kind": ImageKind.BANNER,
        },
        blocks=[{"type": "image", "url": "https://cdn/image", "opacity": Decimal("0.75")}],
    )
    entry = await builders.build_entity(context.session, "notice", 7, 2)
    serialized = json.loads(json.dumps(entry))
    assert serialized["payload"]["observed_at"] == observed_at.isoformat()
    assert serialized["payload"]["related_payload"] == {
        "price": 12.5,
        "public_id": str(identity),
        "kind": "banner",
    }
    assert serialized["artwork"][0]["opacity"] == 0.75


@pytest.mark.asyncio
async def test_notice_revision_overrides_reconstruct_public_response_without_shared_body_copies(
    context, monkeypatch
):
    from fastapi.encoders import jsonable_encoder

    from wuwa_story.read_models.context import SOURCE_BUILD
    from wuwa_story.read_models.readers import events

    current = {
        "id": 7,
        "title": "Current notice",
        "slug": "7-notice",
        "revision": "r2",
        "revisions": ["r2", "r1"],
        "observed_at": datetime(2026, 10, 30, tzinfo=UTC),
        "blocks": [{"type": "text", "text": "Shared body " * 10000}],
        "links": [{"url": "https://example.org", "weight": Decimal("0.5")}],
        "events": [{"id": 2, "title": "Current event"}],
        "related_event_ids": [2],
        "banner_url": "https://cdn/current.png",
    }
    previous = {
        **current,
        "title": "Previous notice",
        "revision": "r1",
        "observed_at": datetime(2026, 10, 29, tzinfo=UTC),
        "events": [],
        "related_event_ids": [],
        "banner_url": None,
    }

    async def detail(identity, revision, session):
        assert SOURCE_BUILD.get()
        assert identity == "7" and session is context.session
        return previous if revision == "r1" else current

    payloads.notices.notice_detail.side_effect = detail
    entry = await builders.build_entity(context.session, "notice", 7, 2)
    payload = entry["payload"]
    assert payload["revision_snapshot_format"] == "overrides-v1"
    assert payload["revision_snapshots"].keys() == {"r1"}
    overrides = payload["revision_snapshots"]["r1"]
    assert "blocks" not in overrides and "links" not in overrides
    assert len(json.dumps(overrides)) < 500
    assert "Previous notice" not in entry["searchable_text"]
    assert payloads.notices.notice_detail.await_count == 2
    monkeypatch.setattr(events.entities, "page_payload", AsyncMock(return_value=payload))
    assert await events.notice_detail_payload(context.session, "7", "r1") == jsonable_encoder(
        previous
    )
    assert await events.notice_detail_payload(context.session, "7", "r2") == jsonable_encoder(
        current
    )
    assert await events.notice_detail_payload(context.session, "7") == jsonable_encoder(current)
    # Request-time reads use only the projection; source rendering remains background-only.
    assert payloads.notices.notice_detail.await_count == 2


@pytest.mark.asyncio
async def test_missing_notice_revision_aborts_publication_instead_of_listing_unreadable_history(
    context,
):
    async def detail(identity, revision, session):
        if revision:
            raise HTTPException(404, "Announcement revision not found")
        return {"revision": "r2", "revisions": ["r2", "r1"]}

    payloads.notices.notice_detail.side_effect = detail
    with pytest.raises(HTTPException) as error:
        await builders.build_entity(context.session, "notice", 7, 2)
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_direct_builder_uses_source_context_without_requiring_worker_wrapper(context):
    from wuwa_story.read_models.context import SOURCE_BUILD

    async def source_profile(*args, **kwargs):
        assert SOURCE_BUILD.get() is True
        return context.profile

    context.character.side_effect = source_profile
    assert SOURCE_BUILD.get() is False
    await builders.build_entity(context.session, "character", 7, 2)
    assert SOURCE_BUILD.get() is False


@pytest.mark.asyncio
async def test_quest_repeated_full_page_fails_instead_of_looping_or_publishing_truncation(context):
    context.node_type.key = "quest"
    sections.transcripts.quest_transcript.return_value = {
        "lines": [{"id": f"line:{i}"} for i in range(2000)]
    }
    with pytest.raises(ValueError, match="pagination did not advance"):
        await builders.build_entity(context.session, "quest", 7, 2)
    assert sections.transcripts.quest_transcript.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot", ["3.10", "3.10.0", "3.10.45"])
async def test_event_snapshot_matches_schedule_major_minor_not_prefix(context, snapshot):
    context.session.execute.return_value = [
        (1, "asia", "3.10"),
        (2, "asia", "3.1"),
        (3, "asia", "3.100"),
    ]
    sections.browse_events.side_effect = [
        {"events": [{"id": 7, "occurrence_id": 1}]},
        {"events": [{"id": 7, "occurrence_id": None}]},
    ]
    entry = await builders.build_entity(context.session, "event", 7, 2, snapshot)
    assert [run["occurrence_id"] for run in entry["payload"]["occurrences"]] == [1]
    assert entry["payload"]["occurrences"][0]["game_version"] == "3.10"
    assert sections.browse_events.await_count == 2


@pytest.mark.asyncio
async def test_saturated_source_quest_profile_is_not_published_when_its_tree_is_truncated(context):
    context.node_type.key = "quest"
    sections.profiles.quest_profile.return_value = {
        "quest": {"game_quest_id": 70},
        "tree": [{}] * 2000,
    }
    context.session.scalar.return_value = 2001
    with pytest.raises(ValueError, match="truncated its authored node tree"):
        await builders.build_entity(context.session, "quest", 7, 2)
    sections.transcripts.quest_transcript.assert_not_awaited()


@pytest.mark.asyncio
async def test_current_card_keeps_localized_aliases_without_customer_service_placeholders(context):
    context.profile["character"]["name"] = {"content": "Please contact our customer service"}
    context.profile["character"]["aliases"] = ["Existing alias"]
    context.session.scalars.return_value = ["Existing alias", "日本語の別名"]
    result = await builders.build_entity(context.session, "character", 7, 2)
    assert result["title"] == "Label"
    assert result["payload"]["card"]["aliases"] == ["Existing alias", "日本語の別名"]


@pytest.mark.asyncio
async def test_quest_filter_is_a_standalone_string_column(context):
    context.node_type.key = "quest"
    sections.profiles.quest_profile.return_value = {
        "quest": {"quest_type": 2, "name": {"content": "Quest"}}
    }
    entry = await builders.build_entity(context.session, "quest", 7, 2)
    assert entry["quest_type"] == "2"
    assert entry["card"]["quest_type"] == "2"


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot", ["current", "3.10"])
async def test_story_map_builder_keeps_all_patches_distinct_from_exact_version(
    context, monkeypatch, snapshot
):
    from wuwa_story.read_models.context import SOURCE_BUILD

    async def source_map(**kwargs):
        assert SOURCE_BUILD.get() is True
        assert kwargs == {
            "locale": "ja",
            "game_version": None if snapshot == "current" else snapshot,
            "quest_type_id": 4,
            "session": context.session,
        }
        return {
            "selected_game_version": kwargs["game_version"],
            "quest_type_id": 4,
            "chapters": [{"id": 1}],
            "imported_game_versions": ["3.9", "3.10"],
        }

    render = AsyncMock(side_effect=source_map)
    monkeypatch.setattr(payloads.story_map_routes, "story_map", render)
    entry = await builders.build_entity(context.session, "story_map", 5, 2, snapshot)
    assert entry["entity_id"] == 5 and entry["snapshot_key"] == snapshot
    assert entry["payload"]["selected_game_version"] == (
        None if snapshot == "current" else snapshot
    )
    assert entry["payload"]["chapters"] == [{"id": 1}]
    assert entry["card"]["href"] == "/story-map"
    assert entry["canonical_key"] is None and entry["primary_image_url"] is None
    assert SOURCE_BUILD.get() is False
    render.assert_awaited_once()


@pytest.mark.asyncio
async def test_story_map_entity_zero_is_reserved_for_bulk_scope(context, monkeypatch):
    render = AsyncMock()
    monkeypatch.setattr(payloads.story_map_routes, "story_map", render)
    with pytest.raises(ValueError, match="quest_type_id"):
        await builders.build_entity(context.session, "story_map", 0, 2)
    render.assert_not_awaited()


@pytest.mark.asyncio
async def test_dialogue_card_has_authored_search_order_and_exact_context_quest_ids(context):
    context.node_type.key = "talk_item"
    sections.nodes.node_narrative_context.return_value = {
        "dialogue": {
            "text": {"content": "Authored words"},
            "flow_state": "flow:3",
            "action": {"index": 8},
            "source_index": 2,
        },
        "quests": [{"game_quest_id": 70}, {"game_quest_id": 70}, {"game_quest_id": 71}],
    }
    relations._dialogue_filters.return_value = {
        "quest_ids": [70, 71],
        "character_keys": ["character:70"],
    }
    entry = await builders.build_entity(context.session, "dialogue", 7, 2)
    assert entry["title"] == "Authored words"
    assert {
        key: entry["card"][key]
        for key in ("flow_state", "action_index", "source_index", "quest_ids")
    } == {
        "flow_state": "flow:3",
        "action_index": 8,
        "source_index": 2,
        "quest_ids": [70, 71],
    }
    assert entry["card"]["character_keys"] == ["character:70"]


@pytest.mark.asyncio
async def test_future_root_source_cannot_be_relabelled_as_historical(context):
    context.profile["source"] = {"version": "3.11.0", "source_file": "roleinfo.json"}
    with pytest.raises(ValueError, match="future revision"):
        await builders.build_entity(context.session, "character", 7, 2, "3.10.0")


@pytest.mark.asyncio
async def test_nonpublic_legacy_event_is_absence_not_a_permanent_render_retry(context):
    context.records[(GameEvent, 7)].source = "retired-legacy-feed"
    with pytest.raises(HTTPException) as error:
        await builders.build_entity(context.session, "game_event", 7, 2)
    assert error.value.status_code == 404
    sections.browse_events.assert_not_awaited()


@pytest.mark.asyncio
async def test_exact_alias_keys_normalize_case_and_whitespace_without_changing_ui_aliases(context):
    aliases = ["  Silver\tEcho  ", "silver echo", "STRASSE", "Straße", " "]
    context.profile["character"]["aliases"] = aliases
    entry = await builders.build_entity(context.session, "character", 7, 2)
    assert entry["card"]["aliases"] == aliases
    assert entry["card"]["alias_keys"] == ["silver echo", "strasse"]


@pytest.mark.asyncio
async def test_event_calendar_card_preserves_all_runs_and_undated_detail_but_trims_gallery(
    context, monkeypatch
):
    gallery = [
        {"role": role, "url": f"https://cdn/{role}"}
        for role in [
            "content",
            "background",
            "tab",
            "featured_portrait",
            "prefab_texture",
            "skill_icon",
        ]
    ]
    runs = [
        {
            "id": 7,
            "occurrence_id": identity,
            "server": server,
            "game_version": version,
            "title": "Run",
            "artwork": gallery,
            "preview_url": "https://cdn/content",
        }
        for identity, server, version in [
            (1, "asia", "3.10"),
            (2, "america", "3.9"),
            (3, "region_4", None),
        ]
    ]
    payload = {
        "event": {"id": 7, "title": "Event", "artwork": gallery, "occurrence_id": None},
        "occurrences": runs,
        "unversioned_occurrences": [],
    }
    monkeypatch.setattr(sections, "_event_payload", AsyncMock(return_value=payload))
    entry = await builders.build_entity(context.session, "game_event", 7, 2)
    calendar = entry["card"]["calendar"]
    assert [
        (run["occurrence_id"], run["server"], run["game_version"])
        for run in calendar["occurrences"]
    ] == [
        (1, "asia", "3.10"),
        (2, "america", "3.9"),
        (3, "region_4", None),
    ]
    assert calendar["event"]["occurrence_id"] is None
    assert calendar["unversioned_occurrences"] == []
    assert all(
        [art["role"] for art in run["artwork"]]
        == ["content", "background", "tab", "featured_portrait"]
        for run in [calendar["event"], *calendar["occurrences"]]
    )
    assert len(entry["payload"]["occurrences"][0]["artwork"]) == 6
    assert len(payload["occurrences"][0]["artwork"]) == 6
