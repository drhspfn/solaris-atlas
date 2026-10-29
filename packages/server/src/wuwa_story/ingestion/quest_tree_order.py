"""Read authored quest/LevelPlay tree order from the client's binary resources.

The flattened node tables retain parent IDs, but not sibling order. Validate every
decoded node against the matching client table before accepting the order.
"""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TreePosition:
    node_id: int
    parent_id: int
    node_type: str
    path: tuple[int, ...]


class _Reader:
    def __init__(self, data: bytes):
        self.data = data

    def number(self, pos: int, fmt: str) -> int:
        size = struct.calcsize(fmt)
        if pos < 0 or pos + size > len(self.data):
            raise ValueError("Invalid tree buffer offset")
        return int(struct.unpack_from(fmt, self.data, pos)[0])

    def field(self, pos: int, index: int) -> int | None:
        vtable = pos - self.number(pos, '<i')
        length = self.number(vtable, '<H')
        slot = vtable + 4 + index * 2
        if length < 4 or slot + 2 > vtable + length:
            return None
        offset = self.number(slot, '<H')
        return pos + offset if offset else None

    def pointer(self, pos: int, index: int) -> int | None:
        field = self.field(pos, index)
        if field is None:
            return None
        target = field + self.number(field, '<I')
        if target <= field or target >= len(self.data):
            raise ValueError("Invalid tree buffer pointer")
        return target

    def string(self, pos: int, index: int) -> str:
        target = self.pointer(pos, index)
        if target is None:
            raise ValueError("Missing tree table string")
        length = self.number(target, '<I')
        if target + 4 + length > len(self.data):
            raise ValueError("Invalid tree table string")
        return self.data[target + 4:target + 4 + length].decode('utf-8')

    def vector(self, pos: int, index: int) -> list[int]:
        vector = self.pointer(pos, index)
        if vector is None:
            return []
        count = self.number(vector, '<I')
        if count > 100_000 or vector + 4 + count * 4 > len(self.data):
            raise ValueError("Invalid tree child count")
        result = []
        for offset in range(count):
            entry = vector + 4 + 4 * offset
            target = entry + self.number(entry, '<I')
            if target <= entry or target >= len(self.data):
                raise ValueError("Invalid tree child pointer")
            result.append(target)
        return result


def read_tree_node_record(data: bytes) -> tuple[str, dict[str, Any]]:
    """Decode a QuestNodeData/LevelPlayNodeData row from the client ConfigDB."""
    reader = _Reader(data)
    root = reader.number(0, '<I')
    key = reader.string(root, 0)
    value = json.loads(reader.string(root, 1))
    if not isinstance(value, dict):
        raise ValueError("Invalid tree node record")
    return key, value


def read_tree_order(data: bytes, kind: str, records: dict[int, dict[str, Any]]) -> list[TreePosition]:
    """Return source order, rejecting mismatched client tables and incomplete trees.

    Paths describe authored child position, not a guaranteed traversal through
    conditional, parallel or looping branches.
    """
    if kind not in {'quest', 'level_play'}:
        raise ValueError("Unsupported tree kind")
    reader = _Reader(data)
    root = reader.number(0, '<I')
    wrapper = reader.pointer(root, 41 if kind == 'quest' else 42)
    wrapper = reader.pointer(wrapper, 1) if wrapper is not None else None
    root = reader.pointer(wrapper, 7) if wrapper is not None else None
    if root is None:
        raise ValueError("Tree root is missing")
    positions: list[TreePosition] = []
    seen: set[int] = set()

    def visit(pos: int, parent_id: int, path: tuple[int, ...]) -> None:
        if len(path) > 256:
            raise ValueError("Tree nesting is too deep")
        field = reader.field(pos, 1)
        node_id = reader.number(field, '<I') if field is not None else None
        record = records.get(node_id) if node_id is not None else None
        if (node_id in seen or not record or record.get('Id') != node_id
                or (record.get('ParentNodeId') or 0) != parent_id):
            raise ValueError("Tree does not match the pinned client node table")
        node_type = record.get('Type')
        if not isinstance(node_type, str):
            raise ValueError("Tree node type is missing")
        seen.add(node_id)
        positions.append(TreePosition(node_id, parent_id, node_type, path))
        if node_type in {'Sequence', 'Select', 'ParallelSelect'}:
            children = reader.vector(pos, 39)
        elif node_type == 'ConditionSelector':
            children = [reader.pointer(slot, 2) for slot in reader.vector(pos, 39)]
        elif node_type in {'Start', 'AlwaysTrue', 'AlwaysFalse', 'Repeater'}:
            child = reader.pointer(pos, 7)
            children = [child] if child is not None else []
        else:
            children = []
        for index, child in enumerate(children):
            if child is None:
                raise ValueError("Tree branch has no node")
            visit(child, node_id, (*path, index))

    visit(root, 0, ())
    if seen != set(records):
        raise ValueError("Tree is incomplete or does not match the pinned client node table")
    return positions
