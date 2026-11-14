"""Bounded-memory reading of FModel decoded export JSON.

Decoded Sequence packages can reach hundreds of MiB (a real 3.8 sample is 497735180 bytes).
Almost all of that weight is runtime-derived data (compiled evaluation templates, baked
animation curves, easing objects) that no Solaris consumer reads. ``iter_exports`` streams
one export at a time and, for files above ``PRUNE_THRESHOLD``, keeps only a stub for those
derived types so the composed structure (names, outers, indices) is unchanged.
"""

import hashlib
import json
import os
from pathlib import Path
from typing import Iterator

import ijson

# Above this size heavy derived exports are replaced by stubs; smaller files are returned verbatim.
PRUNE_THRESHOLD = int(os.getenv('WUWA_DECODED_JSON_PRUNE_BYTES', str(32 * 1024 * 1024)))

# Derived/baked data. None of these carry events, dialogue, spine, media or prefab references.
DERIVED_TYPES = frozenset({
    'MovieSceneCompiledData',
    'MovieSceneSeqAnimDataSection',
    'MovieSceneSeqAnimDataTrack',
    'MovieScene3DTransformSection',
    'MovieSceneBuiltInEasingFunction',
})

_STUB_KEYS = ('Type', 'Name', 'Outer', 'Class')


def stub(export: dict) -> dict:
    """Keep identity and ownership of a pruned export without its payload."""
    result = {key: export[key] for key in _STUB_KEYS if key in export}
    result['Properties'] = {}
    result['_pruned'] = True
    return result


class _Reader:
    """Binary reader that drops a UTF-8 BOM, hashes what it hands out and counts bytes."""

    def __init__(self, handle):
        self._handle = handle
        self._digest = hashlib.sha256()
        self.size = 0
        self._first = True

    def read(self, size=-1):
        data = self._handle.read(size)
        if data:
            self._digest.update(data)
            self.size += len(data)
            if self._first:
                self._first = False
                if data.lstrip(b'\xef\xbb\xbf \t\r\n')[:1] != b'[':
                    raise ValueError('Decoded JSON is not an export array')
                if data.startswith(b'\xef\xbb\xbf'):
                    data = data[3:]
        return data

    def drain(self):
        while self.read(1024 * 1024):
            pass

    def hexdigest(self):
        return self._digest.hexdigest()


def _parse(reader, prune):
    try:
        for item in ijson.items(reader, 'item', use_float=True):
            if prune and isinstance(item, dict) and item.get('Type') in DERIVED_TYPES:
                item = stub(item)
            yield item
    except ijson.JSONError as error:
        raise ValueError(f'Invalid decoded JSON: {error}') from error


def iter_stream(handle, *, size: int, prune: bool | None = None,
                expected_sha256: str | None = None) -> Iterator:
    """Yield the items of a JSON array from a binary stream, verifying ``expected_sha256`` at the end.

    Decoded exports are always arrays; any other document is rejected rather than loaded whole.
    """
    reader = _Reader(handle)
    prune = size > PRUNE_THRESHOLD if prune is None else prune
    yield from _parse(reader, prune)
    reader.drain()
    if reader.size != size:
        raise ValueError('Decoded JSON size differs from its record')
    if expected_sha256 is not None and reader.hexdigest() != expected_sha256:
        raise ValueError('Decoded JSON checksum differs from its record')


def iter_exports(path: Path, *, prune: bool | None = None) -> Iterator:
    path = Path(path)
    with path.open('rb') as handle:
        yield from iter_stream(handle, size=path.stat().st_size, prune=prune)


def load_exports(path: Path, *, prune: bool | None = None):
    """Return the exports of a decoded file as a list (large files are pruned of derived data).

    Documents that are not JSON arrays (small property files) are returned unchanged.
    """
    path = Path(path)
    with path.open('rb') as handle:
        first = handle.read(4096).lstrip(b'\xef\xbb\xbf \t\r\n')[:1]
    if first != b'[':
        if path.stat().st_size > PRUNE_THRESHOLD:
            raise ValueError('Large decoded JSON is not an export array')
        try:
            return json.loads(path.read_text(encoding='utf-8-sig'))
        except json.JSONDecodeError as error:
            raise ValueError(f'Invalid decoded JSON: {error}') from error
    return list(iter_exports(path, prune=prune))
