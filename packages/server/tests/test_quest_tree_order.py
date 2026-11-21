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


def _shift_wrapper_slot(data: bytes, old_slot: int, new_slot: int, *, fill_old_offset: int = 0) -> bytes:
    """Shift a synthetic FlatBuffer root table's wrapper field to simulate 3.8.0 schema changes."""
    import struct

    buf = bytearray(data)
    root = struct.unpack_from('<I', buf, 0)[0]
    vtable = root - struct.unpack_from('<i', buf, root)[0]
    vlen = struct.unpack_from('<H', buf, vtable)[0]
    obj_size = struct.unpack_from('<H', buf, vtable + 2)[0]
    old_nfields = (vlen - 4) // 2
    new_nfields = max(old_nfields, new_slot + 1)
    slots = [struct.unpack_from('<H', buf, vtable + 4 + i * 2)[0] if i < old_nfields else 0 for i in range(new_nfields)]
    slots[new_slot] = slots[old_slot]
    slots[old_slot] = fill_old_offset
    # Append new vtable at the end of the buffer and point root to it.
    while len(buf) % 2:
        buf.append(0)
    new_vtable = len(buf)
    buf.extend(struct.pack('<HH', 4 + new_nfields * 2, obj_size))
    for off in slots:
        buf.extend(struct.pack('<H', off))
    struct.pack_into('<i', buf, root, root - new_vtable)
    return bytes(buf)


def test_source_tree_reads_3_8_0_shifted_wrapper_slots():
    # Quest: wrapper shifted from slot 41 -> 42 (with slot 41 empty or occupied by a scalar).
    shifted_quest = _shift_wrapper_slot(TREE, 41, 42)
    positions = read_tree_order(shifted_quest, 'quest', RECORDS, tree_id=42)
    assert [(entry.node_id, entry.path) for entry in positions] == [
        (1, ()), (2, (0,)), (3, (1,)), (4, (1, 0)),
    ]
    # Even when the old slot 41 holds a non-pointer scalar offset (e.g. pointing at tree_id field).
    occupied_old = _shift_wrapper_slot(TREE, 41, 42, fill_old_offset=4)
    positions_occupied = read_tree_order(occupied_old, 'quest', RECORDS, tree_id=42)
    assert [(entry.node_id, entry.path) for entry in positions_occupied] == [
        (1, ()), (2, (0,)), (3, (1,)), (4, (1, 0)),
    ]

    # LevelPlay: identity at slot 4, wrapper shifted from slot 42 -> 43.
    lp_base = _shift_wrapper_slot(_shift_wrapper_slot(TREE, 6, 4), 41, 43)
    lp_positions = read_tree_order(lp_base, 'level_play', RECORDS, tree_id=42)
    assert [(entry.node_id, entry.path) for entry in lp_positions] == [
        (1, ()), (2, (0,)), (3, (1,)), (4, (1, 0)),
    ]
