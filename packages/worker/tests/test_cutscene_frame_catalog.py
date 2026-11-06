import copy
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import flatbuffers
import pytest

from wuwa_story_worker.cutscene_frame_catalog import (
    catalog_movie_frame_bindings,
    movie_launcher_catalog,
)

FIXTURES = Path(__file__).parent / 'fixtures'


@pytest.fixture
def action():
    return json.loads((FIXTURES / 'cutscene_frame_events.json').read_text())['m0374']


def node_blob(key, data):
    builder = flatbuffers.Builder(512)
    key = builder.CreateString(key)
    data = builder.CreateString(json.dumps(data))
    builder.StartObject(2)
    builder.PrependUOffsetTRelativeSlot(0, key, 0)
    builder.PrependUOffsetTRelativeSlot(1, data, 0)
    root = builder.EndObject()
    builder.Finish(root)
    return bytes(builder.Output())


def flow_blob(key, actions):
    builder = flatbuffers.Builder(512)
    key = builder.CreateString(key)
    actions = builder.CreateString(json.dumps(actions))
    builder.StartObject(7)
    builder.PrependUOffsetTRelativeSlot(0, key, 0)
    builder.PrependUOffsetTRelativeSlot(6, actions, 0)
    root = builder.EndObject()
    builder.Finish(root)
    return bytes(builder.Output())


def entity_blob(data):
    builder = flatbuffers.Builder(512)
    data = builder.CreateString(json.dumps(data))
    builder.StartObject(11)
    for slot, value in enumerate((1, 2, 3)):
        builder.PrependInt32Slot(slot, value, 0)
    builder.PrependUOffsetTRelativeSlot(10, data, 0)
    root = builder.EndObject()
    builder.Finish(root)
    return bytes(builder.Output())


@pytest.fixture
def configs(tmp_path):
    for filename, schema in [
        ('db_flowState.db', 'CREATE TABLE flowstate(StateKey, BinData)'),
        ('db_QuestNodeData.db', 'CREATE TABLE questnodedata(Key, BinData)'),
        ('db_LevelPlayNodeData.db', 'CREATE TABLE levelplaynodedata(Key, BinData)'),
        ('db_level_entity.db', 'CREATE TABLE levelentityconfig(Id, MapId, EntityId, BinData)'),
    ]:
        with closing(sqlite3.connect(tmp_path / filename)) as db:
            db.execute(schema)
            db.commit()
    return tmp_path


def insert(path, table, row):
    with closing(sqlite3.connect(path)) as db:
        db.execute(f'INSERT INTO {table} VALUES ({",".join("?" for _ in row)})', row)
        db.commit()


def test_all_native_sources_keep_separate_exact_contexts(configs, action):
    actual = json.loads((FIXTURES / 'cutscene_frame_events.json').read_text())
    insert(configs / 'db_flowState.db', 'flowstate',
           (actual['state_key'], (FIXTURES / 'm0374_frame_flowstate.bin').read_bytes()))
    other = copy.deepcopy(action)
    other['Params']['Mp4FrameEvents'][0]['Second'] = 7
    insert(configs / 'db_QuestNodeData.db', 'questnodedata', ('Q_1', node_blob('Q_1', {'Actions': [other]})))
    insert(configs / 'db_LevelPlayNodeData.db', 'levelplaynodedata', ('L_1', node_blob('L_1', {'Actions': [action]})))
    insert(configs / 'db_level_entity.db', 'levelentityconfig', (1, 2, 3, entity_blob({'Actions': [action]})))
    before = {p: p.read_bytes() for p in configs.iterdir()}
    result = catalog_movie_frame_bindings(configs, 'M0374')
    assert len(result) == 4 and all(not b['complete'] for b in result)
    assert sorted(b['events'][0]['start_seconds'] for b in result) == [0, 0, 0, 7]
    assert len({b['source']['file'] for b in result}) == 4
    assert all(b['source']['database_sha256'] and b['source']['record_sha256'] for b in result)
    assert all(b['unresolved'][0]['code'] == 'conflicting_launcher_timelines' for b in result)
    assert all(not event['resolved'] for binding in result for event in binding['events'])
    assert {s['vtable_offset'] for s in movie_launcher_catalog(configs)['sources']} == {6, 16, 24}
    assert all(p.read_bytes() == data for p, data in before.items())


def test_reviewed_frameless_launcher_is_known_empty_not_unmapped(configs):
    action = {'Name': 'PlayMovie', 'Params': {'VideoName': 'NoFrames'}}
    insert(configs / 'db_flowState.db', 'flowstate', ('key', flow_blob('key', [action])))
    binding, = catalog_movie_frame_bindings(configs, 'NoFrames')
    assert binding['complete'] and not binding['events'] and not binding['unresolved']


def test_no_binding_anywhere_is_explicit_unmapped(configs):
    binding, = catalog_movie_frame_bindings(configs, 'Unknown')
    assert not binding['complete'] and binding['unresolved'][0]['code'] == 'unmapped_launcher'
    assert all(s['status'] == 'reviewed' for s in binding['source']['catalog_sources'])


def test_missing_native_source_does_not_claim_full_coverage(configs, action):
    insert(configs / 'db_flowState.db', 'flowstate', ('key', flow_blob('key', [action])))
    (configs / 'db_level_entity.db').unlink()
    bindings = catalog_movie_frame_bindings(configs, 'M0374')
    assert len(bindings) == 2
    assert bindings[0]['events']
    assert bindings[1]['unresolved'][0]['code'] == 'native_source_unavailable'


def test_quest_and_entity_nested_alternatives_are_not_merged(configs, action):
    branch = {'Type': 'Selector', 'Children': [{'Actions': [action]}, {'Actions': [action]}]}
    insert(configs / 'db_QuestNodeData.db', 'questnodedata', ('Q', node_blob('Q', branch)))
    components = {'InteractComponent': {'Options': [{'Actions': [action]}]}}
    insert(configs / 'db_level_entity.db', 'levelentityconfig', (1, 2, 3, entity_blob(components)))
    result = catalog_movie_frame_bindings(configs, 'M0374')
    assert len(result) == 3
    assert all(b['conditional'] and not b['complete'] for b in result)
    assert all(not event['resolved'] for binding in result for event in binding['events'])
    assert len({b['source']['raw_path'] for b in result}) == 3
    assert all(b['unresolved'][0]['code'] == 'nested_movie_requires_runtime_context' for b in result)


def test_nested_movie_in_frame_actions_keeps_distinct_child_binding(configs, action):
    outer = copy.deepcopy(action)
    child = copy.deepcopy(action)
    child['Params']['VideoName'] = 'Child'
    outer['Params']['Mp4FrameEvents'][0]['EventActions'].append(child)
    insert(configs / 'db_flowState.db', 'flowstate', ('key', flow_blob('key', [outer])))
    child_binding, = catalog_movie_frame_bindings(configs, 'Child')
    assert child_binding['conditional'] and not child_binding['complete']
    assert 'EventActions[1]' in child_binding['source']['raw_path']


def test_json_actions_in_native_level_data_and_exact_name_filter(configs, action):
    value = {'Actions': json.dumps([action])}
    insert(configs / 'db_LevelPlayNodeData.db', 'levelplaynodedata', ('L', node_blob('L', value)))
    binding, = catalog_movie_frame_bindings(configs, 'M0374')
    assert binding['events'][0]['reference'].endswith('play_all_sound_m0374.play_all_sound_m0374')
    assert 'Actions<json>[0]' in binding['source']['raw_path']
    unknown, = catalog_movie_frame_bindings(configs, 'M037')
    assert unknown['unresolved'][0]['code'] == 'unmapped_launcher'


def test_native_identity_mismatch_is_not_parsed_by_blob_substring(configs, action):
    insert(configs / 'db_QuestNodeData.db', 'questnodedata', ('WRONG', node_blob('RIGHT', {'Actions': [action]})))
    result = catalog_movie_frame_bindings(configs, 'M0374')
    assert not any(b['events'] for b in result)
    assert any(u['code'] == 'native_record_decode_incomplete' for b in result for u in b['unresolved'])


def test_changed_source_invalidates_catalog_cache(configs, action):
    assert catalog_movie_frame_bindings(configs, 'M0374')[0]['unresolved'][0]['code'] == 'unmapped_launcher'
    insert(configs / 'db_QuestNodeData.db', 'questnodedata', ('new', node_blob('new', {'Actions': [action]})))
    binding, = catalog_movie_frame_bindings(configs, 'M0374')
    assert binding['complete'] and len(binding['events']) == 1


@pytest.mark.parametrize('family', ['flow', 'quest', 'level', 'entity'])
def test_bad_nonmovie_record_does_not_drop_later_valid_launcher(configs, action, family):
    if family == 'entity':
        path, table_name = configs / 'db_level_entity.db', 'levelentityconfig'
        insert(path, table_name, (0, 2, 3, b'bad'))
        insert(path, table_name, (1, 2, 3, entity_blob({'Actions': [action]})))
    else:
        filename, table_name = {'flow': ('db_flowState.db', 'flowstate'),
            'quest': ('db_QuestNodeData.db', 'questnodedata'),
            'level': ('db_LevelPlayNodeData.db', 'levelplaynodedata')}[family]
        path = configs / filename
        insert(path, table_name, ('A_bad', b'bad'))
        blob = flow_blob('Z_good', [action]) if family == 'flow' else node_blob('Z_good', {'Actions': [action]})
        insert(path, table_name, ('Z_good', blob))
    catalog = movie_launcher_catalog(configs)
    assert len(catalog['bindings']['M0374']) == 1
    assert catalog['bindings']['M0374'][0]['events'][0]['reference'].endswith('play_all_sound_m0374.play_all_sound_m0374')
    source = next(s for s in catalog['sources'] if s['file'] == path.name)
    assert source['records'] == 2 and source['record_errors'] == 1


def test_native_empty_movie_noop_and_unhashable_labels_do_not_abort_scan(configs, action):
    placeholder = {'Name': 'PlayMovie', 'Params': {'VideoName': ''}, 'ActionId': 5000188,
                   'ActionGuid': '2afcc411a31243ccb693cd6d916caf71'}
    strange = {'Name': {'native': 'object'}, 'Type': ['native', 'list'],
               'Params': 'ordinary native text', 'Data': 'not JSON', 'Actions': []}
    insert(configs / 'db_flowState.db', 'flowstate', ('A_native', flow_blob('A_native', [placeholder, strange])))
    insert(configs / 'db_flowState.db', 'flowstate', ('Z_M0374', flow_blob('Z_M0374', [action])))
    insert(configs / 'db_QuestNodeData.db', 'questnodedata', ('A', node_blob('A', strange)))
    insert(configs / 'db_LevelPlayNodeData.db', 'levelplaynodedata', ('A', node_blob('A', strange)))
    insert(configs / 'db_level_entity.db', 'levelentityconfig', (1, 2, 3, entity_blob(strange)))
    catalog = movie_launcher_catalog(configs)
    assert not catalog['unresolved']
    assert catalog['sources'][0]['noop_launchers'] == 1
    binding, = catalog_movie_frame_bindings(configs, 'M0374')
    assert binding['complete'] and len(binding['events']) == 1
