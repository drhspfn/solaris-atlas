"""Native previews preserve archive identity, source capture and display policy."""

from types import SimpleNamespace as Row
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from wuwa_story.api.routes.story import character_archive as archive
from wuwa_story.db.models.game_events import GameEvent
from wuwa_story.db.repositories.event_browsing import SqlAlchemyEventRepository, _summary_source
from wuwa_story.events.domain import Event, EventMedia, EventOccurrence, EventRun
from wuwa_story.events.repository import EventRepository
from wuwa_story.events.service import EventService
from wuwa_story.read_models import source_sections
from wuwa_story.read_models.readers import entities
from wuwa_story.storage.animation_previews import animation_preview, spine_bundle
from wuwa_story.story import illustrations


@pytest.fixture
def preview():
    return {'recipe': 'riglyph-native-v1', 'status': 'ready', 'variants': [
        {'animation': 'idle', 'rover': 'female', 'url': 'https://cdn/idle.webm',
         'file_id': 90, 'mime_type': 'video/webm', 'duration_seconds': 0,
         'width': 640, 'height': 480, 'loop': False}]}


def test_preview_contract_keeps_zero_duration_pose_and_separate_archive_identity(preview):
    preview = {**preview, 'default_animation': 'idle'}
    result = spine_bundle('https://cdn/source.zip', 7, 1024,
                          {'animation_preview': preview, 'riglyph_file_id': 8}, True)
    assert result['animation_preview'] == preview
    assert result['file_id'] == 7 and result['riglyph_file_id'] == 8
    assert result['url'] == 'https://cdn/source.zip'


@pytest.mark.parametrize('change', [
    {'status': 'failed'}, {'recipe': 'other'}, {'variants': []},
    {'variants': [{'url': 'https://cdn/incomplete.webm'}]},
])
def test_unready_or_invalid_preview_is_not_playable(preview, change):
    assert animation_preview({'animation_preview': {**preview, **change}}, True) is None


@pytest.mark.parametrize('enabled,display', [(False, 'animated'), (True, 'static'), (True, 'hidden')])
def test_preview_display_gates(preview, enabled, display):
    assert animation_preview({'animation_preview': preview, 'animation_import': {'display': display}}, enabled) is None


async def test_story_uses_existing_metadata_query_and_only_exact_authored_cues(monkeypatch, preview):
    actions = [(10, 1, [
        {'BackgroundConfig': {'Type': 'SpineImage', 'Id': 7}},
        {'Actions': [{'Name': 'PlaySpine', 'Params': {'Config': {
            'Type': 'Play', 'Name': '1to2', 'IsLoop': False}}}]}, {},
        {'BackgroundConfig': {'Type': 'SpineImage', 'Id': 8}},
    ])]
    monkeypatch.setattr(illustrations.repository, 'illustration_actions', AsyncMock(return_value=actions))
    monkeypatch.setattr(illustrations, 'get_settings', lambda: Row(s3_bucket='public', story_spine_playback_enabled=True))
    monkeypatch.setattr(illustrations, 'S3Storage', lambda _: Row(public_url=lambda key: 'https://cdn/' + key))
    preview = {**preview, 'default_animation': 'idle'}
    other_preview = {**preview, 'default_animation': 'poses5',
                     'variants': [{**preview['variants'][0], 'animation': 'poses5'}]}
    session = Row(execute=AsyncMock(return_value=[
        ('spine_background:7', 'source.zip', {'animation_preview': preview}, 7, 1024),
        ('spine_background:8', 'other.zip', {'animation_preview': other_preview}, 8, 512)]))
    rows = [(Row(node_id=index + 1, source_index=index), Row(node_id=10), Row(node_id=1)) for index in range(4)]
    result = await illustrations.illustration_payloads(session, rows)
    assert result[1]['animation'] is None
    assert result[2]['animation'] == result[3]['animation'] == '1to2'
    assert result[2]['loop'] is False and result[4]['animation'] is None
    assert result[4]['bundle']['file_id'] == 8
    assert result[4]['bundle']['animation_preview']['default_animation'] == 'poses5'
    assert result[1]['bundle']['file_id'] == 7
    assert result[1]['bundle']['animation_preview'] == preview
    session.execute.assert_awaited_once()


@pytest.mark.parametrize('enabled', [False, True])
async def test_character_archive_trial_bundle_is_gated_and_owner_scoped(monkeypatch, preview, enabled):
    monkeypatch.setattr(archive.read_projections, 'should_route_to_projection', lambda *a, **kw: False)
    monkeypatch.setattr(archive, 'get_node', AsyncMock(return_value=Row(id=7, canonical_key='character:70')))
    monkeypatch.setattr(archive, '_release_id', AsyncMock(return_value=12))
    monkeypatch.setattr(archive, '_records', AsyncMock(return_value=[Row(row_index=0, data={})]))
    monkeypatch.setattr(archive, '_record_groups', AsyncMock(side_effect=lambda session, release, groups: {path: [] for path in groups}))
    monkeypatch.setattr(archive, '_localized_strings', AsyncMock(return_value={}))
    monkeypatch.setattr(archive, '_reference_urls', AsyncMock(return_value={}))
    monkeypatch.setattr(archive, 'get_settings', lambda: Row(s3_bucket='public', story_spine_playback_enabled=enabled))
    monkeypatch.setattr(archive, 'S3Storage', lambda _: Row(public_url=lambda key: 'https://cdn/' + key))
    session = Row(get=AsyncMock(return_value=object()), execute=AsyncMock(side_effect=[[], Row(first=lambda: ('source.zip', 7, 1024, {'animation_preview': preview, 'riglyph_file_id': 8}))]))
    result = await archive.character_archive.__wrapped__('character:70', session=session)
    if not enabled:
        assert result['trial_spine'] is None
        assert session.execute.await_count == 1
    else:
        assert result['trial_spine']['animation_preview'] == preview
        assert result['trial_spine']['file_id'] == 7
        sql = str(session.execute.await_args.args[0].compile(dialect=postgresql.dialect(), compile_kwargs={'literal_binds': True}))
        assert 'file_reference.owner_node_id = 7' in sql
        assert "file_reference.reference_type = 'character_trial_spine'" in sql
        assert 'LIMIT 1' in sql and "file_location.bucket = 'public'" in sql


@pytest.mark.parametrize('enabled,display', [(True, 'animated'), (False, 'animated'), (True, 'static')])
async def test_event_calendar_and_detail_match_capture_without_per_run_queries(preview, enabled, display):
    event = Event(7, 'event', 'client:7', 'Event', None, 'banner', None, [], None, '',
                  {'client': {'download_id': 'current'}})
    occurrence = EventOccurrence(42, '3.7', None, None, None, None,
                                {'client': {'download_id': 'old', 'animation_import': {'display': display}}})
    repository = AsyncMock(spec=EventRepository)
    repository.runs.return_value = [EventRun(event, occurrence)]
    for method in ('announcements', 'notice_snapshots', 'reward_nodes', 'media'):
        getattr(repository, method).return_value = []
    repository.spine.return_value = [
        EventMedia('client:7', 'wrong.zip', {'download_id': 'current', 'animation_preview': preview}, 6, 1024),
        EventMedia('client:7', 'source.zip', {'download_id': 'old', 'animation_preview': preview, 'riglyph_file_id': 8}, 7, 1024)]
    service = EventService(repository, lambda key: 'https://cdn/' + key, lambda url: url, animation_enabled=enabled)
    summary = (await service.browse('asia', summary=True))['events'][0]
    detail = await service.detail(7, 'asia', 42)
    if enabled and display == 'animated':
        assert summary['spine'] == detail['spine']
        assert detail['spine'][0]['file_id'] == 7
        assert detail['spine'][0]['animation_preview'] == preview
        repository.spine.assert_awaited_with({('client:7', 'old')})
    else:
        assert 'spine' not in summary and 'spine' not in detail
        repository.spine.assert_not_awaited()
    repository.media.assert_not_awaited()


async def test_event_spine_query_is_bounded_by_exact_source_capture_pairs():
    session = Row(execute=AsyncMock(return_value=[]))
    repository = SqlAlchemyEventRepository(session, 'public')
    assert await repository.spine(set()) == []
    session.execute.assert_not_awaited()
    await repository.spine({('client:7', 'old'), ('client:8', 'new')})
    sql = str(session.execute.await_args.args[0].compile(dialect=postgresql.dialect(), compile_kwargs={'literal_binds': True}))
    assert '(storage.file_reference.source_path, storage.file_reference.source_name) IN' in sql
    for fragment in ("reference_type = 'event_spine'", "mime_type = 'application/zip'", "bucket = 'public'", 'available IS true', 'is_primary IS true'):
        assert fragment in sql
    assert 'animation_import' in _summary_source(GameEvent.source_data, 'en').compile(dialect=postgresql.dialect()).params.values()


@pytest.mark.parametrize('enabled', [True, False])
async def test_projection_preserves_preview_and_obeys_current_flag_without_source_reads(monkeypatch, preview, enabled):
    payload = {'archive': {'trial_spine': {'animation_preview': preview}},
               'transcript': {'lines': [{'illustration': {'bundle': {'animation_preview': preview}}}]}}
    monkeypatch.setattr(entities, 'get_settings', lambda: Row(story_spine_playback_enabled=enabled))
    monkeypatch.setattr(entities, '_require_scope', AsyncMock())
    monkeypatch.setattr(entities, '_locale_id', AsyncMock(return_value=1))
    session = Row(execute=AsyncMock(return_value=Row(mappings=lambda: Row(all=lambda: [
        {'entity_id': 7, 'canonical_key': 'character:7', 'generation': 1, 'payload': payload}]))))
    result = await entities.page_payload(session, 'character', 'character:7', selector=None)
    assert result['archive']['trial_spine']['animation_preview'] == (preview if enabled else None)
    assert result['transcript']['lines'][0]['illustration']['bundle']['animation_preview'] == (preview if enabled else None)
    assert payload['archive']['trial_spine']['animation_preview'] == preview
    session.execute.assert_awaited_once()
    calendar = source_sections._calendar_payload({'event': {'spine': [{'animation_preview': preview}]}, 'occurrences': []})
    assert calendar['event']['spine'][0]['animation_preview'] == preview
