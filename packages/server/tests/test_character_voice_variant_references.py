from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.dialects import postgresql

from wuwa_story.db.repositories import character_voice_media as reader
from wuwa_story.ingestion.media_provenance import missing_media_targets

NAMES = ['en:hirc1:' + 'a' * 24, 'en:hirc1:' + 'b' * 24]
PATH = '/Game/Aki/WwiseAudio/Events/play_voice.play_voice'


def proof(name, bank='c'*64):
    return {'schema': 1, 'bank_sha256': bank, 'init_sha256': 'd'*64, 'wwiser_sha256': 'e'*64,
            'expected_source_names': NAMES,
            'variant': {'id': name.rsplit(':', 1)[-1], 'source_name': name, 'language': 'en',
                        'media_id': 123, 'selector_branches': [{'random': [{'container_id': 10, 'child_id': 20}]}]}}


def reference(name, identity, release=19, bank='c'*64):
    return SimpleNamespace(id=identity, owner_node_id=7, release_id=release, source_path=PATH,
        source_name=name, reference_type='character_voice', file_id=identity,
        metadata_json={'game_source': {'schema_version': 1, 'asset_version': '3.7.1',
            'download_id': 'client', 'voice_plan_id': 'voices', 'package_path': PATH.split('.')[0],
            'tools': {'WUWA_WWISER_PATH': 'e'*64},
            'source_files': [{'package_path': 'Client/Content/Aki/Init.bnk', 'sha256': 'd'*64}],
            'voice_variant': proof(name, bank)}})


@pytest.mark.asyncio
@pytest.mark.parametrize('loss', ['none', 'row', 'blob', 'bank', 'tool', 'owner', 'language', 'legacy'])
async def test_retry_requires_entire_coherent_live_variant_set(loss):
    target = {'kind': 'voice', 'owner': 7, 'release_id': 19, 'path': PATH, 'language': 'en'}
    refs = [reference(name, i+1) for i, name in enumerate(NAMES)]
    if loss in {'row', 'legacy'}:
        refs.pop()
    if loss == 'legacy':
        legacy = reference('en', 100)
        del legacy.metadata_json['game_source']['voice_variant']
        refs.append(legacy)
    if loss == 'bank':
        refs[0].metadata_json['game_source']['voice_variant']['bank_sha256'] = 'f'*64
    if loss == 'owner':
        refs[0].owner_node_id = 8
    if loss == 'language':
        refs[0].metadata_json['game_source']['voice_variant']['variant']['language'] = 'ja'
    rows = [(r, SimpleNamespace(size_bytes=12), SimpleNamespace(object_key=str(r.file_id))) for r in refs]
    session = AsyncMock()
    session.add = Mock()
    # Use a one-shot iterator: production SQLAlchemy Results cannot be traversed twice.
    session.execute.return_value = iter(rows)
    session.scalar.return_value = None
    storage = SimpleNamespace(backend='s3', bucket='media', stat=AsyncMock(
        side_effect=lambda key: None if loss == 'blob' and key == '1' else SimpleNamespace(size_bytes=12)))
    source = {'asset_version': '3.7.1', 'download_id': 'client', 'voice_plan_id': 'voices',
              'tools': {'WUWA_WWISER_PATH': ('f' if loss == 'tool' else 'e')*64}}
    result = await missing_media_targets(session, [target], source=source, storage=storage)
    assert result == ([] if loss == 'none' else [target])
    if loss == 'none':
        assert storage.stat.await_count == 2


@pytest.mark.asyncio
async def test_release_reuse_clones_each_variant_without_a_default_language_reference():
    target = {'kind': 'voice', 'owner': 7, 'release_id': 20, 'path': PATH, 'language': 'en'}
    refs = [reference(name, i+1) for i, name in enumerate(NAMES)]
    session = AsyncMock()
    session.add = Mock()
    session.execute.return_value = [(r, SimpleNamespace(size_bytes=12), SimpleNamespace(object_key=str(r.id))) for r in refs]
    storage = SimpleNamespace(backend='s3', bucket='media', stat=AsyncMock(return_value=SimpleNamespace(size_bytes=12)))
    result = await missing_media_targets(session, [target], source={'asset_version': '3.7.1'}, storage=storage)
    assert result == []
    added = [call.args[0] for call in session.add.call_args_list]
    assert {r.source_name for r in added} == set(NAMES)
    assert all(r.release_id == 20 and r.owner_node_id == 7 for r in added)


@pytest.mark.asyncio
async def test_reader_preserves_selectors_and_visible_missing_sibling_without_fallback(monkeypatch):
    monkeypatch.setattr(reader, 'S3Storage', lambda _: SimpleNamespace(public_url=lambda key: 'https://cdn/' + key))
    session = AsyncMock()
    session.execute.return_value = [(2, PATH, NAMES[1], proof(NAMES[1]), 'audio/wav', None),
                                    (1, PATH, NAMES[0], proof(NAMES[0]), 'audio/wav', 'first.wav')]
    result = await reader.character_voice_alternatives(session, 7, 19, [PATH], SimpleNamespace(s3_bucket='media'))
    assert [v['status'] for v in result[PATH]] == ['available', 'missing']
    assert result[PATH][0]['selector_branches'][0]['random'][0]['child_id'] == 20
    assert result[PATH][1]['url'] is None
    sql = str(session.execute.await_args.args[0].compile(dialect=postgresql.dialect(), compile_kwargs={'literal_binds': True}))
    assert 'file_reference.owner_node_id = 7' in sql and 'file_reference.release_id = 19' in sql
    assert "file_reference.reference_type = 'character_voice'" in sql
    assert "['game_source']['voice_variant']" in sql
    assert 'event_cooked_data' not in sql and session.execute.await_count == 1
