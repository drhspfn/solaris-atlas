from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story.api.routes.cutscenes import with_public_descriptions
from wuwa_story.db.models.content import Document
from wuwa_story.db.models.storage import FileObject
from wuwa_story.story import cutscene_descriptions as descriptions


def document(identity, *, manual=False, source_hash=b'video', revision=1, asset=10):
    return Document(id=identity, node_id=asset, locale_id=1,
                    document_type=descriptions.description_type(21, manual=manual), revision=revision,
                    title='The fox returns', plain_text='Rover and the Sentinel continue the battle.',
                    source_hash=source_hash, body_ast=[], metadata_json={'visual_reference_id': 90})


def session_for(documents):
    session = SimpleNamespace(scalars=AsyncMock(return_value=documents), get=AsyncMock(
        return_value=SimpleNamespace(sha256=b'video')))
    return session


@pytest.mark.asyncio
async def test_manual_head_wins_without_exposing_system_observations(monkeypatch):
    session = session_for([document(1), document(2, manual=True)])
    monkeypatch.setattr(descriptions, 'current_video', AsyncMock(
        return_value=SimpleNamespace(file_id=5)))
    result = await descriptions.public_descriptions(session, [10], 21, 'en')
    assert result[10]['document_id'] == 2
    assert result[10]['origin'] == 'manual'
    assert 'events' not in result[10]
    sql = str(session.scalars.call_args.args[0])
    assert 'document_head' in sql and 'locale.code' in sql
    assert 'en' in session.scalars.call_args.args[0].compile().params.values()


@pytest.mark.asyncio
async def test_replaced_bytes_cannot_reuse_old_generated_description(monkeypatch):
    session = session_for([document(1, source_hash=b'old')])
    monkeypatch.setattr(descriptions, 'current_video', AsyncMock(
        return_value=SimpleNamespace(file_id=5)))
    assert await descriptions.public_descriptions(session, [10], 21, 'en') == {}


@pytest.mark.asyncio
async def test_new_video_reference_with_same_bytes_keeps_summary(monkeypatch):
    session = session_for([document(1)])
    monkeypatch.setattr(descriptions, 'current_video', AsyncMock(
        return_value=SimpleNamespace(id=999, file_id=5)))
    assert (await descriptions.public_descriptions(session, [10], 21, 'en'))[10]['text']


@pytest.mark.asyncio
async def test_variants_keep_separate_summaries(monkeypatch):
    session = session_for([document(1), document(2, asset=11, manual=True)])
    monkeypatch.setattr(descriptions, 'current_video', AsyncMock(
        return_value=SimpleNamespace(file_id=5)))
    flow = {'media': {'female': {'asset_node_id': 10},
                      'male': {'asset_node_id': 11}}}
    result = await descriptions.attach_public_descriptions(session, {0: flow}, 21, 'en')
    assert len(result) == 2
    assert flow['media']['female']['description']['document_id'] == 1
    assert flow['media']['male']['description']['document_id'] == 2


@pytest.mark.asyncio
async def test_standalone_projected_detail_does_not_need_quest_and_is_not_mutated(monkeypatch):
    monkeypatch.setattr(descriptions, 'current_video', AsyncMock(
        return_value=SimpleNamespace(file_id=5)))
    result = {'quests': [], 'event': {'playback': {
        'media': {'clip': {'asset_node_id': 10}}}}}
    value = await with_public_descriptions(session_for([document(1)]), result, 21, 'en')
    assert value['public_descriptions'][0]['asset_node_id'] == 10
    assert 'description' not in result['event']['playback']['media']['clip']


@pytest.mark.asyncio
async def test_summary_survives_absent_playback_manifest(monkeypatch):
    from wuwa_story.api.routes import cutscenes

    links = AsyncMock(side_effect=[{9: [{'node_id': 99}]}, {99: [{'node_id': 10}]}])
    monkeypatch.setattr(cutscenes, '_links', links)
    monkeypatch.setattr(descriptions, 'current_video', AsyncMock(return_value=SimpleNamespace(file_id=5)))
    result = await with_public_descriptions(session_for([document(1)]), {'node_id': 9, 'quests': [], 'event': None}, 21, 'en')
    assert result['public_descriptions'][0]['asset_node_id'] == 10
    assert links.call_args_list[0].args[-1] == 21


@pytest.mark.asyncio
async def test_manual_write_rejects_lost_update():
    session = SimpleNamespace(scalar=AsyncMock(
        side_effect=[10, document(2, manual=True, revision=3)]))
    with pytest.raises(ValueError, match='reload'):
        await descriptions.save_description(session, asset_id=10, release_id=21, locale_id=1,
                                            title='Title', text='Edited summary', source_hash=b'video', metadata={},
                                            manual=True, expected_revision=2)


@pytest.mark.asyncio
async def test_generated_publication_uses_separate_head():
    inserted = []

    async def flush():
        inserted[0].id = 50
    session = SimpleNamespace(scalar=AsyncMock(side_effect=[10, None, 0]), add=inserted.append,
                              flush=flush, execute=AsyncMock())
    result = await descriptions.save_description(session, asset_id=10, release_id=21, locale_id=1,
                                                 title='Title', text='Generated', source_hash=b'video',
                                                 metadata={'events': ['private'], 'visual_reference_id': 90})
    assert result.document_type == 'cutscene-public:21'
    assert 'events' not in result.metadata_json
    assert 'cutscene-public-edit' not in str(
        session.execute.call_args.args[0].compile().params)


@pytest.mark.asyncio
async def test_clear_override_preserves_document_history():
    session = SimpleNamespace(scalar=AsyncMock(
        side_effect=[10, document(2, manual=True)]), execute=AsyncMock())
    await descriptions.clear_manual_description(session, asset_id=10, release_id=21, locale_id=1, expected_revision=1)
    sql = str(session.execute.call_args.args[0])
    assert 'DELETE FROM content.document_head' in sql
    assert 'DELETE FROM content.document ' not in sql


@pytest.mark.asyncio
async def test_edit_binds_to_current_video_hash(monkeypatch):
    monkeypatch.setattr(descriptions, 'current_video', AsyncMock(
        return_value=SimpleNamespace(file_id=5, metadata_json={'asset_version': '3.7.1'})))
    save = AsyncMock()
    monkeypatch.setattr(descriptions, 'save_description', save)
    session = session_for([])
    await descriptions.edit_public_description(session, asset_id=10, release_id=21, locale_id=1,
                                               title='Title', text='Manual summary', expected_revision=0)
    assert save.call_args.kwargs['source_hash'] == b'video'
    assert save.call_args.kwargs['manual'] is True
    session.get.assert_awaited_once_with(FileObject, 5)
