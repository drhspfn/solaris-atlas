"""Execute reader SQL against competing generations, including late legacy writers."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from wuwa_story.agents.cutscene_vision import visual_reference
from wuwa_story.api.routes.story import media
from wuwa_story.api.routes.story.cutscene_audio import audio_bundles
from wuwa_story.db.models.graph import Node
from wuwa_story.db.models.storage import FileReference
from wuwa_story.story.cutscene_publications import (
    publication_order,
    publication_type,
    publication_types,
)


@pytest.fixture
def published():
    engine = create_engine('sqlite://')
    with engine.connect() as connection:
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS storage")
        connection.exec_driver_sql('''CREATE TABLE storage.file_reference (
            id INTEGER PRIMARY KEY, release_id INTEGER, owner_node_id INTEGER, file_id INTEGER,
            reference_type TEXT, source_name TEXT, source_path TEXT, source_record_id INTEGER,
            metadata JSON)''')
        connection.exec_driver_sql('''CREATE TABLE storage.file_location (
            id INTEGER PRIMARY KEY, file_id INTEGER, object_key TEXT, backend TEXT,
            bucket TEXT, available BOOLEAN, is_primary BOOLEAN)''')
        with Session(connection) as session:
            def add(kind, reference_id, file_id, metadata=None, *, owner=5, source_path=None):
                connection.exec_driver_sql(
                    'INSERT INTO storage.file_reference (id,owner_node_id,file_id,reference_type,metadata,source_path) VALUES (?,?,?,?,?,?)',
                    (reference_id, owner, file_id, kind, json.dumps({'asset_version': '3.7.0', **(metadata or {})}), source_path),
                )

            def location(file_id, key):
                connection.exec_driver_sql(
                    "INSERT INTO storage.file_location (file_id,object_key,backend,bucket,available,is_primary) VALUES (?,?,'s3','test',1,1)",
                    (file_id, key),
                )

            class Reader:
                async def execute(self, query):
                    return session.execute(query)

                async def scalars(self, query):
                    if query.column_descriptions[0]['entity'] is Node:
                        return [SimpleNamespace(id=5, canonical_key='asset:ue:/Game/Test.Test')]
                    return session.scalars(query)

            yield connection, session, Reader(), add, location
    engine.dispose()


@pytest.mark.parametrize('kind', ['cutscene_video', 'cutscene_segment', 'cutscene_audio_bundle', 'cutscene_flow'])
def test_generation_precedes_id_and_late_legacy_writer_cannot_override(published, kind):
    connection, session, _, add, _ = published
    assert publication_type(kind) == kind + '_v2'
    assert publication_types(kind) == (kind, kind + '_v2')
    add(publication_type(kind), 10, 10)
    add(kind, 20, 20)
    query = select(FileReference.id).where(FileReference.reference_type.in_(publication_types(kind))).order_by(
        publication_order(FileReference.reference_type), FileReference.id.desc())
    assert session.scalars(query).first() == 10
    add(kind, 30, 30)  # An old active writer finishes after v2 publication.
    assert session.scalars(query).first() == 10
    add(publication_type(kind), 11, 11)
    assert session.scalars(query).first() == 11
    connection.exec_driver_sql('DELETE FROM storage.file_reference WHERE id IN (10,11)')
    assert session.scalars(query).first() == 30  # Legacy-only snapshots still work.


@pytest.mark.parametrize('kind', ['cutscene_preview', 'cutscene_visual_description', 'cutscene_visual_title'])
def test_derived_publication_types_are_unchanged(kind):
    assert publication_type(kind) == kind
    assert publication_types(kind) == (kind,)


@pytest.mark.asyncio
@pytest.mark.parametrize('v2', [False, True])
async def test_real_video_reader_returns_one_preferred_audio_preserving_video(published, monkeypatch, v2):
    _, _, reader, add, location = published
    monkeypatch.setattr(media, 'get_settings', lambda: SimpleNamespace(s3_bucket='test'))
    monkeypatch.setattr(media, 'S3Storage', lambda _: SimpleNamespace(public_url=lambda key: key))
    if v2:
        add('cutscene_video_v2', 10, 100, {'has_audio': True})
        location(100, 'new-good-audio.mp4')
    add('cutscene_video', 1000, 200, {'has_audio': False})
    location(200, 'late-legacy.mp4')
    result = await media.cutscene_videos(reader, [5], '3.7.0')
    assert list(result) == [5]
    assert result[5]['url'] == ('new-good-audio.mp4' if v2 else 'late-legacy.mp4')
    assert result[5]['has_audio'] is v2
    assert await media.cutscene_videos(reader, [5], '3.6.0') == {}


@pytest.mark.asyncio
@pytest.mark.parametrize('v2', [False, True])
async def test_real_bundle_reader_does_not_merge_legacy_tracks(published, v2):
    _, _, reader, add, location = published
    if v2:
        add('cutscene_audio_bundle_v2', 10, None, {'videos': {'full': 10},
            'tracks': [{'file_id': 11, 'role': 'music', 'language': None}]})
        location(10, 'v2-silent.mp4')
        location(11, 'v2-shared.ogg')
    add('cutscene_audio_bundle', 1000, None, {'videos': {'full': 20},
        'tracks': [{'file_id': 21, 'role': 'voice', 'language': 'en'}]})
    location(20, 'legacy-silent.mp4')
    location(21, 'legacy-en.ogg')
    result = await audio_bundles(reader, [5], '3.7.0', SimpleNamespace(s3_bucket='test'),
                                 SimpleNamespace(public_url=lambda key: key))
    assert list(result) == [5]
    assert result[5]['videos']['full'] == ('v2-silent.mp4' if v2 else 'legacy-silent.mp4')
    assert result[5]['tracks'] == ([{'role': 'music', 'language': None, 'url': 'v2-shared.ogg'}]
                                   if v2 else [{'role': 'voice', 'language': 'en', 'url': 'legacy-en.ogg'}])


@pytest.mark.asyncio
@pytest.mark.parametrize('v2', [False, True])
async def test_real_flow_and_segment_readers_do_not_duplicate_generations(published, monkeypatch, v2):
    _, _, reader, add, location = published
    monkeypatch.setattr(media, 'get_settings', lambda: SimpleNamespace(s3_bucket='test'))
    monkeypatch.setattr(media, 'S3Storage', lambda _: SimpleNamespace(public_url=lambda key: key))
    monkeypatch.setattr(media, 'audio_bundles', AsyncMock(return_value={}))
    monkeypatch.setattr(media, 'cutscene_videos', AsyncMock(return_value={5: {'url': 'full.mp4'}}))
    flow = {'entry': 'clip', 'evidence': 'source', 'nodes': [{'id': 'clip', 'kind': 'clip',
        'asset': 'asset:ue:/Game/Test.Test', 'segment': 'segment-intro'}]}
    for generation, ref_id, file_id in ([('_v2', 10, 100)] if v2 else []) + [('', 1000, 200)]:
        add('cutscene_flow' + generation, ref_id, None, {'flow': {**flow, 'evidence': generation or 'legacy'}})
        add('cutscene_segment' + generation, ref_id + 1, file_id,
            {'segment_id': 'segment-intro', 'start_frame': 0, 'fps': 30, 'has_audio': bool(generation)})
        location(file_id, 'v2-intro.mp4' if generation else 'legacy-intro.mp4')
    result = await media.cutscene_flows(reader, [5])
    assert list(result) == [5]
    assert result[5]['evidence'] == ('_v2' if v2 else 'legacy')
    assert list(result[5]['media']) == ['segment-intro']
    assert result[5]['media']['segment-intro']['url'] == ('v2-intro.mp4' if v2 else 'legacy-intro.mp4')


@pytest.mark.asyncio
async def test_complete_flow_outweighs_new_incomplete_generation(published, monkeypatch):
    _, _, reader, add, location = published
    monkeypatch.setattr(media, 'get_settings', lambda: SimpleNamespace(s3_bucket='test'))
    monkeypatch.setattr(media, 'S3Storage', lambda _: SimpleNamespace(public_url=lambda key: key))
    monkeypatch.setattr(media, 'audio_bundles', AsyncMock(return_value={}))
    monkeypatch.setattr(media, 'cutscene_videos', AsyncMock(return_value={5: {'url': 'full.mp4'}}))
    flow = {'entry': 'clip', 'evidence': 'intact', 'nodes': [{'id': 'clip', 'kind': 'clip',
        'asset': 'asset:ue:/Game/Test.Test', 'segment': 'segment-intro'}]}
    add('cutscene_flow', 1, None, {'flow': flow, 'publication_complete': True})
    add('cutscene_segment', 2, 2, {'segment_id': 'segment-intro', 'start_frame': 0, 'fps': 30})
    location(2, 'complete.mp4')
    add('cutscene_flow_v2', 10, None, {'flow': {**flow, 'evidence': 'partial'},
                                      'publication_complete': False})
    add('cutscene_segment_v2', 11, 11, {'segment_id': 'segment-intro', 'start_frame': 0, 'fps': 30})
    location(11, 'partial.mp4')
    result = await media.cutscene_flows(reader, [5])
    assert result[5]['evidence'] == 'intact'
    assert result[5]['media']['segment-intro']['url'] == 'complete.mp4'


@pytest.mark.asyncio
@pytest.mark.parametrize('v2', [False, True])
async def test_visual_analysis_tracks_preferred_bytes_not_late_legacy_writer(published, v2):
    _, session, _, add, _ = published
    if v2:
        add('cutscene_video_v2', 1, 10)
        add('cutscene_visual_description', 2, 10, {'complete': True, 'source_sha256': '0a' * 32})
    add('cutscene_video', 1000, 20)
    add('cutscene_visual_description', 1001, 20, {'complete': True, 'source_sha256': '14' * 32})

    class Reader:
        async def scalar(self, query):
            return session.scalar(query)

        async def get(self, model, file_id):
            return SimpleNamespace(id=file_id, sha256=bytes([file_id]) * 32)

    report = await visual_reference(Reader(), 5, '3.7.0')
    assert report.id == (2 if v2 else 1001)
    assert report.reference_type == 'cutscene_visual_description'


@pytest.mark.asyncio
async def test_shared_asset_cg_readers_reject_foreign_v2_but_keep_legacy_fallback(published, monkeypatch):
    connection, _, reader, add, location = published
    monkeypatch.setattr(media, 'get_settings', lambda: SimpleNamespace(s3_bucket='test'))
    monkeypatch.setattr(media, 'S3Storage', lambda _: SimpleNamespace(public_url=lambda key: key))
    for key, start, generation in [('cutscene:B', 900, '_v2'), (None, 100, '')]:
        metadata = {'cutscene_key': key} if key else {}
        add('cutscene_video' + generation, start, start, metadata)
        add('cutscene_audio_bundle' + generation, start + 1, None,
            {**metadata, 'videos': {'full': start}, 'tracks': [
                {'file_id': start + 2, 'role': 'voice', 'language': 'en'}]})
        location(start, f'{start}.mp4')
        location(start + 2, f'{start}.ogg')

    async def read(key):
        video = await media.cutscene_videos(reader, [5], '3.7.0', cutscene_key=key)
        bundle = await audio_bundles(reader, [5], '3.7.0', SimpleNamespace(s3_bucket='test'),
                                    SimpleNamespace(public_url=lambda path: path), cutscene_key=key)
        return video, bundle

    for key, expected in [('cutscene:A', 100), ('cutscene:B', 900), (None, 900)]:
        video, bundle = await read(key)
        assert video[5]['url'] == f'{expected}.mp4'
        assert bundle[5]['tracks'][0]['url'] == f'{expected}.ogg'
    add('cutscene_video_v2', 10, 10, {'cutscene_key': 'cutscene:A'})
    add('cutscene_audio_bundle_v2', 11, None, {'cutscene_key': 'cutscene:A', 'videos': {'full': 10},
        'tracks': [{'file_id': 12, 'role': 'voice', 'language': 'en'}]})
    location(10, 'own-a.mp4')
    location(12, 'own-a.ogg')
    video, bundle = await read('cutscene:A')
    assert video[5]['url'] == 'own-a.mp4'
    assert bundle[5]['tracks'] == [{'role': 'voice', 'language': 'en', 'url': 'own-a.ogg'}]
    connection.exec_driver_sql('DELETE FROM storage.file_reference WHERE id IN (10,11,100,101)')
    assert await read('cutscene:A') == ({}, {})  # B's v2 is never an A fallback.


@pytest.mark.asyncio
async def test_two_cg_flows_share_video_bytes_but_keep_distinct_stems(published, monkeypatch):
    _, _, reader, add, location = published
    monkeypatch.setattr(media, 'get_settings', lambda: SimpleNamespace(s3_bucket='test'))
    monkeypatch.setattr(media, 'S3Storage', lambda _: SimpleNamespace(public_url=lambda key: key))
    location(90, 'shared-silent.mp4')
    flow = {'entry': 'clip', 'evidence': 'source', 'nodes': [
        {'id': 'clip', 'kind': 'clip', 'asset': 'asset:ue:/Game/Test.Test'}]}
    for key, owner, start in [('cutscene:A', 111, 10), ('cutscene:B', 112, 20)]:
        # Exercise both metadata and legacy source_path as the flow's CG identity.
        add('cutscene_flow_v2', start, None, {'flow': flow,
            **({'cutscene_key': key} if owner == 111 else {})}, owner=owner, source_path=key)
        add('cutscene_video_v2', start + 1, 90, {'cutscene_key': key})
        add('cutscene_audio_bundle_v2', start + 2, None,
            {'cutscene_key': key, 'videos': {'full': 90}, 'tracks': [
                {'file_id': start + 3, 'role': 'voice', 'language': 'en'}]})
        location(start + 3, key + '.ogg')
    result = await media.cutscene_flows(reader, [111, 112])
    assert set(result) == {111, 112}
    for owner, key in [(111, 'cutscene:A'), (112, 'cutscene:B')]:
        playback = result[owner]['media']['asset:ue:/Game/Test.Test']
        assert playback['url'] == 'shared-silent.mp4'
        assert playback['audio_tracks'] == [{'role': 'voice', 'language': 'en', 'url': key + '.ogg'}]


@pytest.mark.asyncio
@pytest.mark.parametrize('own_generation', ['_v2', '', None])
async def test_cg_segment_reader_never_uses_another_cg_v2(published, monkeypatch, own_generation):
    _, _, reader, add, location = published
    monkeypatch.setattr(media, 'get_settings', lambda: SimpleNamespace(s3_bucket='test'))
    monkeypatch.setattr(media, 'S3Storage', lambda _: SimpleNamespace(public_url=lambda key: key))
    flow = {'entry': 'clip', 'evidence': 'source', 'nodes': [{'id': 'clip', 'kind': 'clip',
        'asset': 'asset:ue:/Game/Test.Test', 'segment': 'segment-intro'}]}
    add('cutscene_flow_v2', 1, None, {'flow': flow, 'cutscene_key': 'cutscene:A'}, owner=111)
    segment = {'segment_id': 'segment-intro', 'start_frame': 0, 'fps': 30}
    add('cutscene_segment_v2', 900, 900, {**segment, 'cutscene_key': 'cutscene:B'})
    location(900, 'wrong-b.mp4')
    if own_generation is not None:
        add('cutscene_segment' + own_generation, 10, 10,
            {**segment, **({'cutscene_key': 'cutscene:A'} if own_generation else {})})
        location(10, 'own-a.mp4')
    result = await media.cutscene_flows(reader, [111])
    if own_generation is None:
        assert result == {}
    else:
        assert result[111]['media']['segment-intro']['url'] == 'own-a.mp4'
