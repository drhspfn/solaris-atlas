from pathlib import Path

import pytest

from wuwa_story.ingestion.quest_tree_order import read_tree_order

# Synthetic FlatBuffer: Sequence [ChildQuest, ConditionSelector [ChildQuest]].
TREE = (Path(__file__).parent / 'fixtures/quest_tree_synthetic.bin').read_bytes()
RECORDS = {
    1: {'Id': 1, 'ParentNodeId': 0, 'Type': 'Sequence'},
    2: {'Id': 2, 'ParentNodeId': 1, 'Type': 'ChildQuest'},
    3: {'Id': 3, 'ParentNodeId': 1, 'Type': 'ConditionSelector'},
    4: {'Id': 4, 'ParentNodeId': 3, 'Type': 'ChildQuest'},
}


def test_source_tree_preserves_sibling_order_and_branch_paths():
    positions = read_tree_order(TREE, 'quest', RECORDS)
    assert [(entry.node_id, entry.path) for entry in positions] == [
        (1, ()), (2, (0,)), (3, (1,)), (4, (1, 0)),
    ]


def test_rejects_mismatched_snapshots_and_corrupt_data():
    wrong_parent = {**RECORDS, 4: {**RECORDS[4], 'ParentNodeId': 1}}
    with pytest.raises(ValueError, match='pinned client node table'):
        read_tree_order(TREE, 'quest', wrong_parent)
    with pytest.raises(ValueError):
        read_tree_order(TREE[:-20], 'quest', RECORDS)
    with pytest.raises(ValueError, match='identity'):
        read_tree_order(TREE, 'quest', RECORDS, tree_id=43)
    with pytest.raises(ValueError, match='incomplete'):
        read_tree_order(TREE, 'quest', {**RECORDS, 5: {'Id': 5, 'ParentNodeId': 1, 'Type': 'ChildQuest'}})
