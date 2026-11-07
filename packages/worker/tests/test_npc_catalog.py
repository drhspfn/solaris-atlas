import json

import pytest
from wuwa_story.ingestion.raw import verify_indexed_file

from wuwa_story_worker import npc_catalog


def test_pinned_dataset_renames_raw_provenance_and_reuses_only_same_source(tmp_path, monkeypatch):
    def sources(config, destination, version):
        path = destination / 'BinData/blueprint/blueprintconfig.json'
        path.parent.mkdir(parents=True)
        path.write_text('[{"Id":17288}]', encoding='utf-8')
        import hashlib
        return {'tables': [{'file': path.relative_to(destination).as_posix(), 'rows': 1,
                            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}]}
    def compile_npcs(source, writer, english):
        writer.emit('character', {'id': 'character:npc:17288',
                    'source': {'file': 'BinData/blueprint/blueprintconfig.json', 'row': 0},
                    'raw': {'Id': 17288}, 'npc': {'blueprint_id': 17288}})
        return {'character': 1}
    monkeypatch.setattr(npc_catalog, 'export_native_sources', sources)
    monkeypatch.setattr(npc_catalog, 'compile_npcs', compile_npcs)
    output = tmp_path / 'dataset'
    args = dict(asset_version='3.7.1', download_id='a'*64, game_version='3.7.0')
    manifest = npc_catalog.build_npc_dataset(tmp_path, output, **args)
    row = json.loads((output/'entities/character.jsonl').read_text())
    assert row['source']['file'].startswith('ClientData/3.7.1/'+'a'*64+'/')
    entry, = json.loads((output/'raw-evidence/index.json').read_text())
    verify_indexed_file(output/'raw-evidence'/entry['file'], entry)
    monkeypatch.setattr(npc_catalog, 'export_native_sources', lambda *_: pytest.fail('re-extracted cached source'))
    assert npc_catalog.build_npc_dataset(tmp_path, output, **args) == manifest
    with pytest.raises(ValueError, match='pinned source'):
        npc_catalog.build_npc_dataset(tmp_path, output, **{**args, 'download_id': 'b'*64})


def test_native_output_rejects_path_injection_before_writing(tmp_path):
    with pytest.raises(ValueError, match='pinned'):
        npc_catalog.build_npc_dataset(tmp_path, tmp_path/'out', asset_version='../unsafe',
                                     download_id='a'*64, game_version='3.7.0')
    assert not (tmp_path/'out').exists()


@pytest.mark.asyncio
async def test_full_native_roster_dirties_projections_in_bounded_batches(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    (tmp_path/'manifest.json').write_text(json.dumps({
        'game_version': '3.7.0', 'asset_version': '3.7.1', 'download_id': 'a'*64,
    }))
    release = SimpleNamespace(id=1, sequence=1, game_version='3.7.0')
    session = AsyncMock()
    session.get.return_value = release
    session.scalars.side_effect = [[release], list(range(13636))]
    monkeypatch.setattr(npc_catalog, 'CanonicalRecordAdapter', lambda _: SimpleNamespace(
        root=tmp_path, entity_files=lambda: [], iter_entities=lambda _: iter(())))
    for name in ('import_raw_snapshot', 'import_canonical_entities',
                 'import_canonical_edges', 'import_npc_scenario_edges', 'build_lexical_index'):
        monkeypatch.setattr(npc_catalog, name, AsyncMock(return_value=0))
    dirty = AsyncMock()
    monkeypatch.setattr(npc_catalog, 'mark_dirty', dirty)
    monkeypatch.setattr(npc_catalog, 'repair_npc_identity', AsyncMock(return_value={}))
    monkeypatch.setattr(npc_catalog, 'backfill_public_identity', AsyncMock(return_value={}))
    await npc_catalog.import_npc_dataset(session, tmp_path, 1)
    batches = [call.args[2] for call in dirty.await_args_list]
    assert max(map(len, batches)) == 1000
    assert [identity for batch in batches for identity in batch] == list(range(13636))
    npc_catalog.build_lexical_index.assert_awaited_once_with(
        session, '3.7.0', categories={'character'}, release_id=1)


@pytest.mark.asyncio
async def test_repair_runs_after_edge_import_and_before_search_on_cached_replay(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    (tmp_path/'manifest.json').write_text(json.dumps({
        'game_version': '3.7.0', 'asset_version': '3.7.1', 'download_id': 'a'*64}))
    release = SimpleNamespace(id=1, sequence=1, game_version='3.7.0')
    legacy = {'from': 'character:npc:1', 'to': 'speaker:7', 'type': 'has_speaker',
              'relation': 'explicit_reference', 'basis': npc_catalog.NAME_BASIS}
    kept = [{**legacy, 'from': 'character:1'}, {**legacy, 'basis': 'independent source'},
            {**legacy, 'relation': 'exact_join'}, {**legacy, 'type': 'presents_talk_item'}]
    old_npc = {'id': 'character:npc:1', 'npc': {'speakers': ['speaker:7'], 'instances': [1]}}
    playable = {'id': 'character:1', 'npc': {'speakers': ['speaker:7']}}
    adapter = SimpleNamespace(root=tmp_path, entity_files=lambda: [],
                              iter_entities=lambda _: iter([old_npc, playable]),
                              iter_edges=lambda: iter([legacy, *kept]))
    monkeypatch.setattr(npc_catalog, 'CanonicalRecordAdapter', lambda _: adapter)
    session = AsyncMock()
    session.get.return_value = release
    session.scalars.side_effect = [[release], [], [release], []]
    order = []
    async def import_entities(source, *args, **kwargs):
        rows = list(source.iter_entities('character'))
        assert rows[0]['npc'] == {'speakers': [], 'instances': [1]}
        assert rows[1] == playable and old_npc['npc']['speakers'] == ['speaker:7']
        return 2
    async def import_edges(source, *args, **kwargs):
        order.append('edges')
        assert list(source.iter_edges()) == kept
        return len(kept)
    async def repair(_session):
        order.append('repair')
        return {'affected_npcs': 1 if order.count('repair') == 1 else 0,
                'removed_evidence': 1 if order.count('repair') == 1 else 0,
                'removed_edges': 1 if order.count('repair') == 1 else 0,
                'cleaned_metadata': 1 if order.count('repair') == 1 else 0}
    async def index(*args, **kwargs):
        order.append('index')
        return 3
    for name in ('import_raw_snapshot', 'import_npc_scenario_edges'):
        monkeypatch.setattr(npc_catalog, name, AsyncMock(return_value=0))
    monkeypatch.setattr(npc_catalog, 'import_canonical_entities', import_entities)
    monkeypatch.setattr(npc_catalog, 'import_canonical_edges', import_edges)
    monkeypatch.setattr(npc_catalog, 'repair_npc_identity', repair)
    monkeypatch.setattr(npc_catalog, 'backfill_public_identity', AsyncMock(return_value={}))
    monkeypatch.setattr(npc_catalog, 'build_lexical_index', index)
    first = await npc_catalog.import_npc_dataset(session, tmp_path, 1)
    second = await npc_catalog.import_npc_dataset(session, tmp_path, 1)
    assert order == ['edges', 'repair', 'index'] * 2
    assert first['identity_repair']['removed_edges'] == 1
    assert second['identity_repair'] == {key: 0 for key in first['identity_repair']}
