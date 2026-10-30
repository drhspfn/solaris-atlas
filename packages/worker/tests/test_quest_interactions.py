import json
import sqlite3
from contextlib import closing

import flatbuffers
import pytest
from wuwa_story.ingestion.level_interactions import (
    interaction_links,
    required_level_links,
    verified_interactions,
)

from wuwa_story_worker.quest_interactions import read_interactions


def blob(integers, strings, size):
    builder = flatbuffers.Builder(512)
    offsets = {field: builder.CreateString(value) for field, value in strings.items()}
    builder.StartObject(size)
    for field, value in integers.items():
        builder.PrependInt32Slot(field, value, 0)
    for field, value in offsets.items():
        builder.PrependUOffsetTRelativeSlot(field, value, 0)
    root = builder.EndObject()
    builder.Finish(root)
    return bytes(builder.Output())


def client_tables(tmp_path):
    data = {'Id': 183, 'Type': 'Quest', 'Reference': ['e_909_179'], 'WeakReference': ['e_909_180']}
    with closing(sqlite3.connect(tmp_path / 'db_LevelPlayData.db')) as db, db:
        db.execute('CREATE TABLE levelplaydata (LevelPlayId INT, BinData BLOB)')
        db.execute('INSERT INTO levelplaydata VALUES (?, ?)', (183, blob({0: 183}, {1: json.dumps(data)}, 2)))
    components = {'InteractComponent': {'Options': [{'TidContent': 'Inspect', 'Type': {'Actions': [
        {'Name': 'PlayFlow', 'Params': {'FlowListName': 'Cave', 'FlowId': 1, 'StateId': 41}}]}}]}}
    with closing(sqlite3.connect(tmp_path / 'db_level_entity.db')) as db, db:
        db.execute('CREATE TABLE levelentityconfig (Id INT, MapId INT, EntityId INT, BinData BLOB)')
        for identity in [179, 180]:
            db.execute('INSERT INTO levelentityconfig VALUES (?, ?, ?, ?)',
                       (identity, 909, identity, blob({0: identity, 1: 909, 2: identity},
                                                    {10: json.dumps(components)}, 11)))
    return data


def test_client_index_requires_explicit_membership_and_preserves_exact_source(tmp_path):
    data = client_tables(tmp_path)
    index = read_interactions(tmp_path)
    root = {'path': [], 'parent_id': 0, **index[183]}
    option, = verified_interactions([root], data)
    assert option['id'] == 'e_909_179:0'
    assert option['flow_states'][0]['state'] == 'Cave_1_41'
    assert len(option['source']['sha256']) == 64
    assert option['text_key'] == 'Inspect'
    # A later client with a changed scenario must not contaminate an old patch.
    assert verified_interactions([root], {**data, 'Reference': ['e_909_180']}) == []
    quests = [(42, {'Key': '119000000_120', 'Data': {'Condition': {
        'Type': 'CheckLevelPlay', 'LevelPlayIds': [183], 'CompleteNumber': 1}}})]
    levels = [(0, {'LevelPlayId': 183, 'Data': data})]
    links = list(interaction_links(quests, levels, {183: [root]}, {'Cave_1_41'}))
    assert [(link['from'], link['to']) for link in links] == [('quest_node:119000000:120', 'flow_state:Cave_1_41')]
    assert list(interaction_links(quests, levels, {183: [root]}, set())) == []
    assert list(interaction_links(quests, levels, {}, {'Cave_1_41'})) == []
    assert read_interactions(tmp_path) == index
    # Connections close on completion so export cleanup works on Windows too.
    (tmp_path / 'db_level_entity.db').unlink()


def test_wrong_sqlite_identity_is_not_accepted(tmp_path):
    client_tables(tmp_path)
    with closing(sqlite3.connect(tmp_path / 'db_LevelPlayData.db')) as db, db:
        db.execute('UPDATE levelplaydata SET LevelPlayId=184')
    with pytest.raises(ValueError, match='LevelPlay ID'):
        read_interactions(tmp_path)


def test_required_levelplay_links_movie_wrapper_for_caption_ownership_only_after_tree_verification():
    quests = [(12, {'Key': '119000000_120', 'Data': {'Condition': {
        'Type': 'CheckLevelPlay', 'LevelPlayIds': [183], 'CompleteNumber': 1}}})]
    levels = [(14, {'Key': '183_138', 'Data': {'FinishActions': [{'Name': 'PlayFlow',
        'Params': {'FlowListName': 'Cave', 'FlowId': 1, 'StateId': 39}}]}})]
    link, = required_level_links(quests, levels, {183}, {'Cave_1_39'})
    assert (link['from'], link['to']) == ('quest_node:119000000:120', 'flow_state:Cave_1_39')
    assert 'branch occurrence is not guaranteed' in link['basis']
    assert list(required_level_links(quests, levels, set(), {'Cave_1_39'})) == []
    assert list(required_level_links(quests, levels, {183}, set())) == []
    quests[0][1]['Data']['Condition']['LevelPlayIds'].append(184)
    assert list(required_level_links(quests, levels, {183}, {'Cave_1_39'})) == []
