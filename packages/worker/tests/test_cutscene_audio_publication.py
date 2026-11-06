import hashlib
import json
import subprocess
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.dialects import postgresql

from wuwa_story_worker import cutscene_audio as audio


def publication(tmp_path, monkeypatch, *, failure=None, missing_full=False,
                reference_type='cutscene_audio_bundle', video_reference_type='cutscene_video',
                segment_reference_type='cutscene_segment', cutscene_key=None):
    root = tmp_path / 'inputs'
    root.mkdir()
    with wave.open(str(root / 'stem.wav'), 'wb') as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(48000)
        stream.writeframes(b'\0' * 48000 * 4)
    (root / 'source.wem').write_bytes(b'exact-source')
    stems = [{'role': role, 'language': language, 'rover': rover, 'path': 'stem.wav',
              'sources': ['source.wem'], 'evidence': 'authored event and native role'}
             for role, language, rover in [('music', None, None), ('effects', None, None),
                                           ('voice', 'en', 'male'), ('voice', 'en', 'female')]]
    spec = {'asset': 'asset:ue:/Game/M0374.M0374', 'asset_version': '3.7.1', 'duration': 1, 'stems': stems}
    recipe = root / 'recipe.json'
    recipe.write_text(json.dumps(spec), encoding='utf-8')
    prior = {'asset_version': '3.7.1', 'tracks': [{'role': 'mixed', 'file_id': 999}], 'videos': {'full': 888}}
    latest = SimpleNamespace(id=11, file_id=777, metadata_json=prior)
    legacy = SimpleNamespace(id=9, file_id=666, metadata_json=prior)
    references = [latest, legacy]
    video = SimpleNamespace(id=40, file_id=140, metadata_json={
        'asset_version': '3.7.1', 'duration_seconds': 1, 'game_source': {'download_id': 'pinned'}})
    segment = SimpleNamespace(id=30, file_id=130, metadata_json={'segment_id': 'segment-a'})
    old_video = SimpleNamespace(id=20, file_id=120, metadata_json={})
    objects = [(video, 'new-video'), (segment, 'segment'), (old_video, 'old-video')]
    if missing_full:
        objects = [(segment, 'segment')]
    session = AsyncMock()
    session.__aenter__.return_value = session
    def sql(query):
        return str(query.compile(dialect=postgresql.dialect(), compile_kwargs={'literal_binds': True}))

    def assert_scope(query):
        if cutscene_key is None:
            assert "'cutscene_key'" not in query
        else:
            assert "'cutscene_key'" in query and repr(cutscene_key) in query

    def scalar(query):
        if 'storage.file_reference' in str(query):
            query = sql(query)
            assert f"reference_type = '{video_reference_type}'" in query
            assert "'3.7.1'" in query
            assert_scope(query)
            assert session.flush.await_count >= 1
            return video
        return SimpleNamespace(id=7)

    session.scalar.side_effect = scalar

    async def scalars(query):
        query = sql(query)
        assert "owner_node_id = 7" in query and repr(reference_type) in query
        assert "'3.7.1'" in query and 'id DESC' in query and 'FOR UPDATE' in query
        assert_scope(query)
        return list(references)

    async def execute(query, *args):
        query = sql(query)
        if query.startswith('DELETE'):
            assert 'id IN (9)' in query and 'owner_node_id = 7' in query
            assert repr(reference_type) in query and "'3.7.1'" in query
            assert_scope(query)
            references[:] = [latest]
        elif 'storage.file_location.object_key' in query:
            assert f"reference_type IN ('{video_reference_type}', '{segment_reference_type}')" in query
            assert_scope(query)
        return objects

    session.scalars.side_effect = scalars
    session.execute.side_effect = execute
    monkeypatch.setattr(audio, 'SessionFactory', lambda: session)
    monkeypatch.setattr(audio, 'asset_workspace', lambda: tmp_path / 'durable')
    monkeypatch.setattr(audio, 'get_settings', lambda: None)
    storage = SimpleNamespace(bucket='test', get=None)
    downloaded = []

    async def get(key):
        downloaded.append(key)
        yield b'source-video'

    storage.get = get
    monkeypatch.setattr(audio, 'S3Storage', lambda _: storage)
    service = AsyncMock()
    files = {}

    async def register(_session, path, kind, **kwargs):
        if failure == 'upload' and path.name == 'audio.json':
            raise OSError('manifest upload failed')
        data = path.read_bytes()
        key = kind, data
        if key not in files:
            files[key] = SimpleNamespace(id=1000 + len(files))
        return files[key]

    service.register_file.side_effect = register
    monkeypatch.setattr(audio, 'FileRegistrationService', lambda _: service)

    async def run(args):
        if failure == 'render':
            error = subprocess.CalledProcessError(7, args, output=b'O' * 6000, stderr=b'E' * 7000)
            raise RuntimeError('short diagnostic') from error
        if failure != 'empty':
            Path(args[-1]).write_bytes(b'ogg-output' if Path(args[-1]).suffix == '.ogg' else b'silent-video')
        return subprocess.CompletedProcess(args, 0, stdout=b'full stdout', stderr=b'full stderr')

    monkeypatch.setattr(audio, 'run_tool', run)
    return recipe, root, session, service, latest, prior, downloaded, files, spec


@pytest.mark.asyncio
async def test_success_and_reimport_replace_latest_own_bundle_without_duplicate_tracks(tmp_path, monkeypatch):
    recipe, root, session, service, latest, _, downloaded, _, _ = publication(tmp_path, monkeypatch)
    first = await audio.publish_audio(recipe, root, root / 'ffmpeg')
    first_file = latest.file_id
    first_manifest = latest.metadata_json
    assert latest.id == 11 and latest.file_id != 777
    assert latest.source_path == '/Game/M0374.M0374'
    assert first_manifest['publication_complete'] is True
    assert first_manifest['game_source'] == {'download_id': 'pinned'}
    assert first_manifest['video_reference'] == {'id': 40, 'file_id': 140}
    assert [(track['role'], track['language'], track.get('rover')) for track in first_manifest['tracks']] == [
        ('music', None, None), ('effects', None, None), ('voice', 'en', 'male'), ('voice', 'en', 'female')]
    assert len(first_manifest['tracks']) == 4
    assert all(track['source_hashes'][0]['sha256'] == hashlib.sha256(b'exact-source').hexdigest()
               for track in first_manifest['tracks'])
    assert set(first_manifest['videos']) == {'full', 'segment-a'}
    assert downloaded == ['new-video', 'segment']  # The older duplicate full video never wins.
    assert first_file in first_manifest['publication_file_ids']
    assert Path(first['log_directory']).is_dir()
    second = await audio.publish_audio(recipe, root, root / 'ffmpeg')
    assert latest.file_id == first_file and latest.metadata_json == first_manifest
    assert first['log_directory'] != second['log_directory']
    service.register_reference.assert_not_awaited()
    assert session.commit.await_count == 2


@pytest.mark.asyncio
async def test_first_publication_registers_one_asset_owned_bundle(tmp_path, monkeypatch):
    recipe, root, session, service, *_ = publication(tmp_path, monkeypatch)
    session.scalars.side_effect = None
    session.scalars.return_value = []
    created = SimpleNamespace(id=50)
    service.register_reference.return_value = created
    await audio.publish_audio(recipe, root, root / 'ffmpeg')
    service.register_reference.assert_awaited_once()
    assert service.register_reference.await_args.kwargs['owner_node_id'] == 7
    assert service.register_reference.await_args.kwargs['reference_type'] == 'cutscene_audio_bundle'
    assert created.metadata_json['asset_version'] == '3.7.1'
    assert created.source_path == '/Game/M0374.M0374'
    assert len(created.metadata_json['tracks']) == 4
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_caller_transaction_uses_exact_v2_types_without_session_or_early_commit(tmp_path, monkeypatch):
    kinds = {'reference_type': 'cutscene_audio_bundle_v2', 'video_reference_type': 'cutscene_video_v2',
             'segment_reference_type': 'cutscene_segment_v2'}
    recipe, root, session, service, latest, *_ = publication(tmp_path, monkeypatch, **kinds)
    factory = Mock(side_effect=AssertionError('must use caller transaction'))
    monkeypatch.setattr(audio, 'SessionFactory', factory)
    await audio.publish_audio(recipe, root, root / 'ffmpeg', session=session, **kinds)
    assert latest.file_id != 777 and latest.metadata_json['publication_complete']
    factory.assert_not_called()
    session.__aenter__.assert_not_awaited()
    session.__aexit__.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()
    assert session.flush.await_count >= 2
    assert all(call.args[0] is session for call in service.register_file.await_args_list)
    # Caller can commit video, audio and flow together, after publisher returns.
    await session.commit()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_caller_transaction_first_v2_bundle_registers_only_requested_type(tmp_path, monkeypatch):
    kinds = {'reference_type': 'cutscene_audio_bundle_v2', 'video_reference_type': 'cutscene_video_v2',
             'segment_reference_type': 'cutscene_segment_v2'}
    recipe, root, session, service, *_ = publication(tmp_path, monkeypatch, **kinds)
    session.scalars.side_effect = None
    session.scalars.return_value = []
    created = SimpleNamespace(id=50)
    service.register_reference.return_value = created
    factory = Mock(side_effect=AssertionError('must not construct a separate session'))
    monkeypatch.setattr(audio, 'SessionFactory', factory)
    await audio.publish_audio(recipe, root, root / 'ffmpeg', session=session, **kinds)
    assert service.register_reference.await_args.kwargs['reference_type'] == kinds['reference_type']
    assert service.register_reference.await_args.args[0] is session
    assert created.source_path == '/Game/M0374.M0374'
    session.commit.assert_not_awaited()
    factory.assert_not_called()


@pytest.mark.asyncio
async def test_caller_publisher_preserves_localized_music_and_effects_roles(tmp_path, monkeypatch):
    recipe, root, session, _, latest, _, _, _, spec = publication(tmp_path, monkeypatch)
    spec['stems'][0]['language'] = 'ja'
    spec['stems'][1]['language'] = 'zh'
    recipe.write_text(json.dumps(spec), encoding='utf-8')
    await audio.publish_audio(recipe, root, root / 'ffmpeg', session=session)
    assert [(track['role'], track['language']) for track in latest.metadata_json['tracks']] == [
        ('music', 'ja'), ('effects', 'zh'), ('voice', 'en'), ('voice', 'en')]
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_caller_v2_publication_scopes_video_segments_and_bundle_to_matching_cg(tmp_path, monkeypatch):
    context = {'reference_type': 'cutscene_audio_bundle_v2', 'video_reference_type': 'cutscene_video_v2',
               'segment_reference_type': 'cutscene_segment_v2', 'cutscene_key': 'cutscene:M0374'}
    recipe, root, session, _, latest, *_ = publication(tmp_path, monkeypatch, **context)
    factory = Mock(side_effect=AssertionError('must use caller transaction'))
    monkeypatch.setattr(audio, 'SessionFactory', factory)
    await audio.publish_audio(recipe, root, root / 'ffmpeg', session=session, **context)
    assert latest.metadata_json['cutscene_key'] == 'cutscene:M0374'
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()
    factory.assert_not_called()


@pytest.mark.asyncio
async def test_two_cgs_on_same_asset_replace_and_dedupe_only_their_own_bundle():
    kind = 'cutscene_audio_bundle_v2'
    a = SimpleNamespace(id=11, file_id=111, metadata_json={'asset_version': '3.7.1', 'cutscene_key': 'cutscene:A'})
    duplicate_a = SimpleNamespace(id=9, file_id=109, metadata_json=dict(a.metadata_json))
    b = SimpleNamespace(id=20, file_id=220, metadata_json={'asset_version': '3.7.1', 'cutscene_key': 'cutscene:B'})
    rows = [a, duplicate_a, b]

    async def scalars(query):
        sql = str(query.compile(dialect=postgresql.dialect(), compile_kwargs={'literal_binds': True}))
        assert 'owner_node_id = 7' in sql and repr(kind) in sql and "'3.7.1'" in sql
        assert "'cutscene_key'" in sql
        key = 'cutscene:A' if "= 'cutscene:A'" in sql else 'cutscene:B'
        assert repr(key) in sql
        return sorted([row for row in rows if row.metadata_json['cutscene_key'] == key], key=lambda row: -row.id)

    async def execute(query):
        sql = str(query.compile(dialect=postgresql.dialect(), compile_kwargs={'literal_binds': True}))
        assert sql.startswith('DELETE') and 'id IN (9)' in sql
        assert "= 'cutscene:A'" in sql and 'owner_node_id = 7' in sql and repr(kind) in sql
        rows.remove(duplicate_a)

    session = AsyncMock()
    session.scalars.side_effect = scalars
    session.execute.side_effect = execute
    service = AsyncMock()
    await audio._bundle_reference(session, service, 7, '3.7.1', 311,
                                  dict(a.metadata_json, tracks=['new-A']), kind, 'cutscene:A')
    assert a.file_id == 311 and b.file_id == 220 and rows == [a, b]
    await audio._bundle_reference(session, service, 7, '3.7.1', 322,
                                  dict(b.metadata_json, tracks=['new-B']), kind, 'cutscene:B')
    assert a.file_id == 311 and b.file_id == 322
    assert a.metadata_json['tracks'] == ['new-A'] and b.metadata_json['tracks'] == ['new-B']
    session.execute.assert_awaited_once()
    session.commit.assert_not_awaited()
    service.register_reference.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['render', 'upload'])
async def test_caller_transaction_failure_keeps_prior_and_leaves_rollback_to_caller(tmp_path, monkeypatch, failure):
    recipe, root, session, service, latest, prior, *_ = publication(tmp_path, monkeypatch, failure=failure)
    factory = Mock(side_effect=AssertionError('must use caller transaction'))
    monkeypatch.setattr(audio, 'SessionFactory', factory)
    with pytest.raises((RuntimeError, OSError)):
        await audio.publish_audio(recipe, root, root / 'ffmpeg', session=session)
    assert latest.file_id == 777 and latest.metadata_json == prior
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()
    service.register_reference.assert_not_awaited()
    factory.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['render', 'empty', 'upload'])
async def test_failed_render_or_upload_preserves_successful_prior_publication(tmp_path, monkeypatch, failure):
    recipe, root, session, service, latest, prior, _, _, _ = publication(tmp_path, monkeypatch, failure=failure)
    with pytest.raises((RuntimeError, ValueError, OSError)):
        await audio.publish_audio(recipe, root, root / 'ffmpeg')
    assert latest.file_id == 777 and latest.metadata_json == prior
    service.register_reference.assert_not_awaited()
    session.scalars.assert_not_awaited()  # No bundle replacement or duplicate cleanup began.
    session.commit.assert_not_awaited()
    if failure == 'render':
        stdout, = (tmp_path / 'durable/logs/cutscene-audio').rglob('stem-0.stdout.log')
        stderr, = (tmp_path / 'durable/logs/cutscene-audio').rglob('stem-0.stderr.log')
        assert stdout.read_bytes() == b'O' * 6000
        assert stderr.read_bytes() == b'E' * 7000


@pytest.mark.asyncio
async def test_no_complete_full_video_never_replaces_prior_bundle(tmp_path, monkeypatch):
    recipe, root, session, _, latest, prior, *_ = publication(tmp_path, monkeypatch, missing_full=True)
    with pytest.raises(ValueError, match='full video'):
        await audio.publish_audio(recipe, root, root / 'ffmpeg')
    assert latest.file_id == 777 and latest.metadata_json == prior
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('updates', [{'complete': False}, {'missing_sources': ['unresolved event branch']}])
async def test_incomplete_source_evidence_is_rejected_before_any_storage_write(tmp_path, monkeypatch, updates):
    recipe, root, session, service, latest, prior, _, _, spec = publication(tmp_path, monkeypatch)
    recipe.write_text(json.dumps({**spec, **updates}), encoding='utf-8')
    storage = Mock(side_effect=AssertionError('storage must not be constructed'))
    monkeypatch.setattr(audio, 'S3Storage', storage)
    with pytest.raises(ValueError, match='Incomplete soundtrack'):
        await audio.publish_audio(recipe, root, root / 'ffmpeg')
    storage.assert_not_called()
    service.register_file.assert_not_awaited()
    session.commit.assert_not_awaited()
    assert latest.file_id == 777 and latest.metadata_json == prior


def test_truncated_declared_pcm_is_not_a_complete_timeline(tmp_path):
    path = tmp_path / 'truncated.wav'
    with wave.open(str(path), 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(48000)
        stream.writeframes(b'\0' * 96000)
    path.write_bytes(path.read_bytes()[:-1000])
    with pytest.raises(ValueError, match='Truncated PCM'):
        audio.validate_stem(path, 1)
