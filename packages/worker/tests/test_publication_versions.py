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


@pytest.mark.asyncio
@pytest.mark.parametrize('has_missing_targets', [False, True])
async def test_reuse_transaction_commits_before_build_and_inventory_session(tmp_path, monkeypatch, has_missing_targets):
    from wuwa_story_worker import media_diagnostics, sequence_metadata

    request = MediaRequest(download_id='a' * 64, asset_version='3.7.1', game_version='3.7.0',
                           release_id=21, entities=['character:1'], kinds=['image'])
    (tmp_path / 'plan.json').write_text(json.dumps({'id': request.download_id, 'version': '3.7.1', 'tier': 'hd'}))
    (tmp_path / 'status.json').write_text(json.dumps({'id': request.download_id, 'state': 'downloaded'}))
    target = {'owner': 1, 'release_id': 21, 'path': 'asset:ue:/Game/Portrait', 'kind': 'image', 'language': None}
    payload = {'request': request.model_dump(), 'run_id': 28057, 'targets': [target]}
    run = SimpleNamespace(id=28057, status='queued', metadata_json={'payload': payload})
    session, inventory_session = AsyncMock(), AsyncMock()
    session.get.return_value = run
    events = []
    transaction = {'dirty': False}

    async def commit():
        transaction['dirty'] = False
        events.append('commit')

    async def reuse(*args, **kwargs):
        transaction['dirty'] = True  # Reused voice references and mark_dirty share this transaction.
        events.append('reuse')
        return [target] if has_missing_targets else []

    async def retained(*args, **kwargs):
        assert not transaction['dirty'], 'reuse locks held during retained bundle lookup'
        events.append('retained')

    async def build(*args, **kwargs):
        assert not transaction['dirty'], 'reuse locks held during lengthy build'
        events.append('build')
        return [], [target['path']]

    async def open_inventory():
        assert not transaction['dirty'], 'reuse locks held while a separate inventory session runs'
        events.append('inventory_session')
        return inventory_session

    factory, inventory_factory = Mock(), Mock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    inventory_factory.return_value.__aenter__ = AsyncMock(side_effect=open_inventory)
    inventory_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    session.commit.side_effect = commit
    monkeypatch.setattr(entity_media, 'SessionFactory', factory)
    monkeypatch.setattr(entity_media, 'validate_plan', lambda _: None)
    monkeypatch.setattr(entity_media, 'missing_media_targets', AsyncMock(side_effect=reuse))
    monkeypatch.setattr(sequence_metadata, 'retained_bundle', AsyncMock(side_effect=retained))
    monkeypatch.setattr(entity_media, 'build_entity_files', AsyncMock(side_effect=build))
    monkeypatch.setattr(entity_media, 'S3Storage', lambda _: AsyncMock())
    monkeypatch.setattr(media_diagnostics, 'SessionFactory', inventory_factory)
    monkeypatch.setattr(media_diagnostics, 'S3Storage', lambda _: AsyncMock())
    monkeypatch.setattr(media_diagnostics, 'write_inventory', lambda *args, **kwargs: {})
    service = AsyncMock()
    service.register_file.return_value = SimpleNamespace(id=12)
    monkeypatch.setattr(media_diagnostics, 'FileRegistrationService', lambda _: service)
    tool = tmp_path / 'tool'
    tool.write_bytes(b'pinned tool')
    monkeypatch.setattr(entity_media, 'tool_path', lambda _: tool)

    await entity_media._process_entity_workspace(payload, None, tmp_path)

    reuse_commit = events.index('commit', events.index('reuse') + 1)
    assert reuse_commit < events.index('inventory_session')
    if has_missing_targets:
        assert reuse_commit < events.index('retained') < events.index('build')
    else:
        assert 'retained' not in events and 'build' not in events
    inventory_session.commit.assert_awaited_once()
    assert run.status == ('partial' if has_missing_targets else 'completed')
    assert run.raw_output['skipped'] == int(not has_missing_targets)


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


@pytest.mark.parametrize(('previous_revision', 'incoming_revision', 'preserve'), [(2, None, True), (2, 1, True), (1, 2, False), (2, 2, False)])
def test_sequence_metadata_revision_does_not_allow_stale_payload_downgrade(previous_revision, incoming_revision, preserve):
    assert preserve_story_reference('story_presentation', {'game_source': proof(sequence_metadata_revision=previous_revision)},
                                    proof(sequence_metadata_revision=incoming_revision)) is preserve


def test_equal_metadata_revision_preserves_complete_controls_against_partial_replacement():
    assert preserve_story_reference('story_presentation', {'game_source': proof(sequence_metadata_revision=2, control_sources_complete=True)},
                                    proof(sequence_metadata_revision=2, control_sources_complete=False))
    assert not preserve_story_reference('story_presentation', {'game_source': proof(sequence_metadata_revision=2, control_sources_complete=False)},
                                        proof(sequence_metadata_revision=2, control_sources_complete=True))


def test_sequence_backdrop_revision_five_cannot_be_downgraded_by_legacy_worker():
    current = proof(package_path='spine_background:850009', sequence_metadata_revision=5)
    legacy = proof(package_path='spine_background:850009')
    assert preserve_story_reference('story_spine', {'game_source': current}, legacy)
    assert not preserve_story_reference('story_spine', {'game_source': legacy}, current)


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


@pytest.mark.asyncio
async def test_sequence_repair_failure_rolls_back_reference_and_retry_repairs(tmp_path, monkeypatch):
    from wuwa_story_worker import sequence_speaker_identity

    request = MediaRequest(download_id='a' * 64, asset_version='3.7.1', game_version='3.7.0',
                           release_id=21, entities=['flow_state:Story_28_1'], kinds=['image'])
    (tmp_path / 'plan.json').write_text(json.dumps({'id': request.download_id, 'version': '3.7.1', 'tier': 'hd'}))
    (tmp_path / 'status.json').write_text(json.dumps({'id': request.download_id, 'state': 'downloaded'}))
    target = {'owner': 4648369, 'release_id': 21, 'path': 'story_sequence:flow_state:Story_28_1',
              'kind': 'spine', 'language': None, 'reference_type': 'story_presentation',
              'spine_recipe': 4, 'presentation_sha256': 'c' * 64, 'sequence_media_schema': 1}
    payload = {'request': request.model_dump(), 'run_id': 26922, 'targets': [target]}
    run = SimpleNamespace(id=26922, status='queued', metadata_json={'payload': payload})
    # Emulate transaction visibility: only commit makes the pending reference
    # visible to the next call to missing_media_targets.
    state = {'pending': None, 'committed': None, 'links': False}
    session = AsyncMock()
    session.get.return_value = run
    session.scalar.return_value = None

    async def commit():
        if state['pending'] is not None:
            state['committed'] = copy.deepcopy(state['pending'])
            state['pending'] = None

    async def rollback():
        state['pending'] = None

    session.commit.side_effect = commit
    session.rollback.side_effect = rollback
    factory = Mock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(entity_media, 'SessionFactory', factory)
    monkeypatch.setattr(entity_media, 'validate_plan', lambda _: None)

    async def missing_targets(*args, **kwargs):
        return [] if state['committed'] else [target]

    monkeypatch.setattr(entity_media, 'missing_media_targets', AsyncMock(side_effect=missing_targets))
    monkeypatch.setattr(entity_media, 'publish_inventory', AsyncMock(return_value={}))
    monkeypatch.setattr(entity_media, 'S3Storage', lambda _: AsyncMock())
    monkeypatch.delenv('WUWA_RIGLYPH_ENABLED', raising=False)
    service = AsyncMock()
    service.register_file.return_value = SimpleNamespace(id=11)
    service.register_variant.return_value = SimpleNamespace(metadata_json={})

    async def register_reference(*args, **kwargs):
        state['pending'] = SimpleNamespace(file_id=None, metadata_json={})
        return state['pending']

    service.register_reference.side_effect = register_reference
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
    build = AsyncMock(return_value=([(target, bundle, 'story_presentation', 'application/zip', [raw])], []))
    monkeypatch.setattr(entity_media, 'build_entity_files', build)
    monkeypatch.setattr(sequence_fragments, 'publish_sequence_fragments',
                        AsyncMock(return_value={'schema_version': 1, 'sections': []}))
    monkeypatch.setattr(sequence_fragments, 'invalidate_sequence_quests', AsyncMock())

    async def repair(selected_session, path, selected_target):
        assert selected_session is session and path == bundle and selected_target == target
        assert state['pending'].metadata_json['sequence_media']['schema_version'] == 1
        assert run.raw_output['published'] == 1
        if not repair.called:
            repair.called = True
            raise RuntimeError('identity repair failed')
        state['links'] = True
        await selected_session.commit()  # The canonical edge importer commits its batch.

    repair.called = False
    actor_repair = AsyncMock(side_effect=repair)
    monkeypatch.setattr(sequence_speaker_identity, 'repair_sequence_bundle_speakers', actor_repair)
    with pytest.raises(RuntimeError, match='identity repair failed'):
        await entity_media._process_entity_workspace(payload, None, tmp_path)
    assert state['committed'] is None and state['pending'] is None and not state['links']
    session.rollback.assert_awaited_once()

    await entity_media._process_entity_workspace(payload, None, tmp_path)
    assert state['committed'].file_id == 11 and state['links']
    assert run.status == 'completed' and run.raw_output['published'] == 1
    assert build.await_count == actor_repair.await_count == 2
