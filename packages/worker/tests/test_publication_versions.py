"""Queued pre-schema payloads cannot downgrade current21 story publication."""

import copy
import json
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from wuwa_story.ingestion.entity_media import MediaRequest

from wuwa_story_worker import entity_media, sequence_fragments
from wuwa_story_worker.publication_versions import preserve_story_reference


def proof(**changes):
    return {'schema_version': 1, 'asset_version': '3.7.1', 'download_id': 'a' * 64,
            'package_path': 'story_sequence:flow_state:Story_28_1',
            'presentation_sha256': 'c' * 64, 'spine_recipe': 4,
            'sequence_media_schema': 1, **changes}


@pytest.mark.parametrize('reference_type', ['story_presentation', 'story_spine'])
def test_old26922_style_payload_preserves_entire_newer_proof_even_if_partial(reference_type):
    metadata = {'game_source': proof(control_sources_complete=False),
                'sequence_media': {'schema_version': 1, 'sections': [{'file_id': 575568}]}}
    before = copy.deepcopy(metadata)
    assert preserve_story_reference(reference_type, metadata, proof(sequence_media_schema=None))
    assert metadata == before


@pytest.mark.parametrize(('old', 'new', 'preserve'), [
    (proof(), proof(), False),
    (proof(sequence_media_schema=None), proof(), False),
    (proof(spine_recipe=3), proof(), False),
    (proof(spine_recipe=5), proof(), True),
    (proof(spine_recipe=3), proof(sequence_media_schema=None), True),
])
def test_versions_never_decrease_and_equal_recipes_can_repair_missing_output(old, new, preserve):
    assert preserve_story_reference('story_presentation', {'game_source': old}, new) is preserve


@pytest.mark.parametrize(('key', 'value'), [('asset_version', '3.7.2'), ('download_id', 'b' * 64),
    ('package_path', 'story_sequence:flow_state:Other_1_1'), ('presentation_sha256', 'd' * 64)])
def test_different_source_identity_is_not_silently_reused(key, value):
    assert not preserve_story_reference('story_presentation', {'game_source': proof()},
                                        proof(sequence_media_schema=None, **{key: value}))


@pytest.mark.parametrize('metadata', [None, {}, {'game_source': None}, {'game_source': proof(schema_version=True)},
    {'game_source': proof(sequence_media_schema=True)}, {'game_source': proof(spine_recipe=-1)}])
def test_malformed_existing_proof_does_not_protect_bad_metadata(metadata):
    assert not preserve_story_reference('story_presentation', metadata, proof(sequence_media_schema=None))


def test_voice_and_image_publication_are_untouched():
    for reference_type in ('character_voice', 'entity_image'):
        assert not preserve_story_reference(reference_type, {'game_source': proof()}, proof(sequence_media_schema=None))


@pytest.mark.asyncio
@pytest.mark.parametrize(('incoming_schema', 'preserve'), [(None, True), (1, False), (2, False)])
async def test_worker_refreshes_locked_reference_before_any_publication(tmp_path, monkeypatch, incoming_schema, preserve):
    request = MediaRequest(download_id='a' * 64, asset_version='3.7.1', game_version='3.7.0',
                           release_id=21, entities=['flow_state:Story_28_1'], kinds=['image'])
    (tmp_path / 'plan.json').write_text(json.dumps({'id': request.download_id, 'version': '3.7.1', 'tier': 'hd'}))
    (tmp_path / 'status.json').write_text(json.dumps({'id': request.download_id, 'state': 'downloaded'}))
    target = {'owner': 4648369, 'release_id': 21, 'path': 'story_sequence:flow_state:Story_28_1',
              'kind': 'spine', 'language': None, 'reference_type': 'story_presentation',
              'spine_recipe': 4, 'presentation_sha256': 'c' * 64,
              'sequence_media_schema': incoming_schema}
    payload = {'request': request.model_dump(), 'run_id': 26922, 'targets': [target]}
    run = SimpleNamespace(id=26922, status='queued', metadata_json={'payload': payload})
    metadata = {'asset_version': '3.7.1', 'game_source': proof(control_sources_complete=False),
                'sequence_media': {'schema_version': 1, 'sections': [{'file_id': 575568}]}}
    before = copy.deepcopy(metadata)
    # Simulate an identity-map object cached before a newer writer committed.
    reference = SimpleNamespace(id=10, file_id=1,
                                metadata_json={'game_source': proof(sequence_media_schema=None)})
    session = AsyncMock()
    session.get.return_value = run

    async def lookup(statement):
        assert 'pg_advisory_xact_lock' in str(session.execute.await_args.args[0])
        assert session.execute.await_args.args[1] == {'id': target['owner']}
        if statement.get_execution_options().get('populate_existing'):
            reference.file_id = 575384
            reference.metadata_json = copy.deepcopy(metadata)
        return reference

    session.scalar.side_effect = lookup
    factory = Mock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(entity_media, 'SessionFactory', factory)
    monkeypatch.setattr(entity_media, 'validate_plan', lambda _: None)
    monkeypatch.setattr(entity_media, 'missing_media_targets', AsyncMock(return_value=[target]))
    monkeypatch.setattr(entity_media, 'publish_inventory', AsyncMock(return_value={}))
    monkeypatch.setattr(entity_media, 'S3Storage', lambda _: AsyncMock())
    monkeypatch.delenv('WUWA_RIGLYPH_ENABLED', raising=False)
    service = AsyncMock()
    service.register_file.return_value = SimpleNamespace(id=11)
    service.register_variant.return_value = SimpleNamespace(metadata_json={})
    monkeypatch.setattr(entity_media, 'FileRegistrationService', lambda _: service)
    tool = tmp_path / 'tool'
    tool.write_bytes(b'pinned tool')
    monkeypatch.setattr(entity_media, 'tool_path', lambda _: tool)
    raw = tmp_path / 'Client/Content/Aki/source.uasset'
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b'pinned package')
    bundle = tmp_path / 'source.zip'
    with zipfile.ZipFile(bundle, 'w') as archive:
        archive.writestr('manifest.json', json.dumps({'control_sources': {}}))
    monkeypatch.setattr(entity_media, 'build_entity_files', AsyncMock(
        return_value=([(target, bundle, 'story_presentation', 'application/zip', [raw])], [])))
    fragments = AsyncMock(return_value={'schema_version': 1, 'sections': []})
    invalidate = AsyncMock()
    monkeypatch.setattr(sequence_fragments, 'publish_sequence_fragments', fragments)
    monkeypatch.setattr(sequence_fragments, 'invalidate_sequence_quests', invalidate)
    await entity_media._process_entity_workspace(payload, None, tmp_path)
    assert run.status == 'completed'
    assert run.raw_output['skipped'] == int(preserve)
    assert run.raw_output['published'] == int(not preserve)
    if preserve:
        assert reference.file_id == 575384 and reference.metadata_json == before
        service.register_file.assert_not_awaited()
        service.register_variant.assert_not_awaited()
        fragments.assert_not_awaited()
        invalidate.assert_not_awaited()
    else:
        assert reference.file_id == 11
        assert reference.metadata_json['game_source']['sequence_media_schema'] == incoming_schema
        fragments.assert_awaited_once()
        invalidate.assert_awaited_once()
