import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story_worker import sequence_levelplay_context as context


def audio(name):
    return {'Name': 'PostAkEvent', 'Params': {'EventConfig': {
        'AkEvent': '/Game/Aki/WwiseAudio/Events/' + name + '.' + name}}}


def test_native_tree_binds_flow_and_audio_without_snapshot_parent_guess(tmp_path, monkeypatch):
    db = tmp_path / 'nodes.db'
    flow = {'Type': 'PlayFlow', 'Flow': {
        'FlowListName': 'Story', 'FlowId': 2, 'StateId': 18}}
    records = {'99_1': {'Id': 1, 'Type': 'Sequence'},
               '99_30': {'Id': 30, 'Type': 'ChildQuest', 'ParentNodeId': 1,
                         'FinishActions': [audio('play_music')]},
               '99_31': {'Id': 31, 'Type': 'ChildQuest', 'ParentNodeId': 1},
               '99_42': {'Id': 42, 'Type': 'ParallelSelect', 'ParentNodeId': 1},
               '99_43': {'Id': 43, 'Type': 'ChildQuest', 'ParentNodeId': 42},
               '99_32': {'Id': 32, 'Type': 'ChildQuest', 'ParentNodeId': 1, 'Condition': flow,
                         'EnterActions': [audio('set_state_stage')], 'FinishActions': [audio('stop_music')]}}
    with sqlite3.connect(db) as connection:
        connection.execute(
            'CREATE TABLE levelplaynodedata (Key TEXT, BinData BLOB)')
        connection.executemany('INSERT INTO levelplaynodedata VALUES (?,?)',
                               [(key, json.dumps([key, value]).encode()) for key, value in records.items()])
    monkeypatch.setattr(context, 'read_tree_node_record',
                        lambda blob: json.loads(blob))
    positions = [SimpleNamespace(node_id=i, parent_id=p, node_type=t, path=path) for i, p, t, path in
                 [(1, 0, 'Sequence', ()), (30, 1, 'ChildQuest', (0,)),
                  (42, 1, 'ParallelSelect', (1,)), (43, 42, 'ChildQuest', (1, 0)),
                  (31, 1, 'ChildQuest', (2,)), (32, 1, 'ChildQuest', (3,))]]
    monkeypatch.setattr(context, 'read_tree_order',
                        lambda *args, **kwargs: positions)
    tree = tmp_path / '99.bytes'
    tree.write_bytes(b'native-tree')
    target = {'owner': 7, 'release_id': 21, 'presentation_sha256': 'flow-proof',
              'presentation': {'source': {'StateKey': 'Story_2_18'}}}
    client = {'asset_version': '3.7.1', 'download_id': 'pinned'}
    candidate, = context.find_candidates(db, 'Story_2_18')
    result = context.resolve_context(db, tree, target, client, candidate)
    assert result['play_event'].endswith('/play_music.play_music')
    assert result['state_event'].endswith('/set_state_stage.set_state_stage')
    assert result['stop_event'].endswith('/stop_music.stop_music')
    assert result['source_proof']['play']['key'] == '99_30'
    assert result['source_proof']['flow']['key'] == '99_32'
    assert result['source_proof']['tree']['flow_path'] == [3]
    assert {node['key'] for node in result['source_proof']
            ['intervening_nodes']} == {'99_31', '99_42', '99_43'}
    assert result['source_proof']['runtime_order_confirmed'] is False
    positions[0].node_type = 'ParallelSelect'
    assert context.resolve_context(db, tree, target, client, candidate) is None


def test_new_soundtrack_context_cannot_be_masked_by_old_publication():
    from wuwa_story_worker.publication_versions import preserve_story_reference

    proof = {'schema_version': 1, 'asset_version': '3.7.1', 'download_id': 'client',
             'package_path': 'story_sequence:flow', 'presentation_sha256': 'hash',
             'spine_recipe': 4, 'sequence_media_schema': 1, 'sequence_metadata_revision': 5,
             'control_sources_complete': True, 'soundtrack_context_sha256': 'old'}
    incoming = {**proof, 'control_sources_complete': False,
                'soundtrack_context_sha256': 'new'}
    assert not preserve_story_reference(
        'story_presentation', {'game_source': proof}, incoming)
    incoming['soundtrack_context_sha256'] = 'old'
    assert preserve_story_reference(
        'story_presentation', {'game_source': proof}, incoming)


@pytest.mark.asyncio
@pytest.mark.parametrize('same_client', [True, False])
async def test_changed_soundtrack_recompiles_retained_bundle_only_for_same_client(tmp_path, same_client):
    from wuwa_story_worker.sequence_metadata import retained_bundle

    target = {'owner': 7, 'release_id': 21, 'path': 'story_sequence:flow', 'language': None,
              'kind': 'spine', 'reference_type': 'story_presentation', 'spine_recipe': 4,
              'presentation_sha256': 'flow', 'sequence_media_schema': 1, 'sequence_metadata_revision': 5,
              'soundtrack_context_sha256': 'new'}
    proof = {'schema_version': 1, 'asset_version': '3.7.1', 'download_id': 'client',
             'package_path': target['path'], 'spine_recipe': 4, 'sequence_media_schema': 1,
             'presentation_sha256': 'flow', 'sequence_metadata_revision': 5,
             'soundtrack_context_sha256': 'old', 'control_sources_complete': True,
             'source_files': [{'package_path': 'source', 'sha256': 'a' * 64}], 'tools': {}}
    session = AsyncMock()
    session.execute.return_value = [({'game_source': proof}, 'key')]

    async def get(_):
        yield b'retained exact archive'
    storage = SimpleNamespace(backend='s3', bucket='bucket', get=get)
    result = await retained_bundle(session, storage, tmp_path, target,
                                   {'asset_version': '3.7.1', 'download_id': 'client' if same_client else 'different', 'tools': {}})
    assert bool(result) is same_client
