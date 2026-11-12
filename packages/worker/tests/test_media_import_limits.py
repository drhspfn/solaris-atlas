import hashlib
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from wuwa_story_worker import decoded_json, sequence_composition as compositor
from wuwa_story_worker.story_spine import _stream_digest, _write_zip_entry

REAL_DECODED_SIZE = 497735180  # M2_9_19_1Z_seq.json from client 3.8.0, run #25443


def info(name, size, compressed=None):
    return SimpleNamespace(filename=name, file_size=size, compress_size=compressed or size)


def test_real_sized_decoded_sequence_is_accepted():
    compositor._validate_archive_limits([
        info('decoded/files/Client/Content/Aki/Sequence/M2_9_19_1Z_seq.json', REAL_DECODED_SIZE, 11383239)])


def test_streamed_source_and_media_may_exceed_in_memory_bound():
    compositor._validate_archive_limits([
        info('source/Client/Content/Aki/Movies/a.mp4', 300 * 1024 * 1024),
        info('media/Client/Content/Aki/Movies/b.mp4', 400 * 1024 * 1024)])


@pytest.mark.parametrize('entry', [
    info('controls.json', 300 * 1024 * 1024),
    info('spine/abc/skeleton.skel', 257 * 1024 * 1024),
    info('decoded/x.json', 1024 ** 3 + 1),
    info('source/x.uexp', 2 * 1024 ** 3 + 1),
])
def test_in_memory_and_extreme_entries_stay_bounded(entry):
    with pytest.raises(ValueError, match='per-entry size limit'):
        compositor._validate_archive_limits([entry])


def test_decompression_bomb_is_rejected():
    with pytest.raises(ValueError, match='compression ratio'):
        compositor._validate_archive_limits([info('decoded/x.json', 900 * 1024 * 1024, 1024)])


def export_file(tmp_path, exports, bom=False):
    path = tmp_path / 'seq.json'
    path.write_bytes((b'\xef\xbb\xbf' if bom else b'') + json.dumps(exports).encode())
    return path


def test_decoded_json_prunes_only_derived_exports_of_large_files(tmp_path):
    exports = [{'Type': 'MovieSceneCompiledData', 'Name': 'C', 'Outer': {'ObjectPath': 'p'}, 'Properties': {'x': [1] * 100}},
               {'Type': 'MovieSceneEventTriggerSection', 'Name': 'E', 'Properties': {'keep': True}}]
    path = export_file(tmp_path, exports, bom=True)
    assert decoded_json.load_exports(path) == exports
    pruned = decoded_json.load_exports(path, prune=True)
    assert pruned[0] == {'Type': 'MovieSceneCompiledData', 'Name': 'C', 'Outer': {'ObjectPath': 'p'},
                         'Properties': {}, '_pruned': True}
    assert pruned[1] == exports[1]


def test_decoded_json_rejects_corrupt_and_non_array(tmp_path):
    corrupt = tmp_path / 'c.json'
    corrupt.write_bytes(b'[{"Type": "A"}, {"Type": ')
    with pytest.raises(ValueError):
        decoded_json.load_exports(corrupt)
    with pytest.raises(ValueError, match='not an export array'):
        with open(export_file(tmp_path, {'a': 1}), 'rb') as handle:
            list(decoded_json.iter_stream(handle, size=8))


def decoded_archive(tmp_path, payload, sha=None):
    name = 'decoded/files/Client/Content/Aki/S.json'
    manifest = {'files': {name: {'sha256': sha or hashlib.sha256(payload).hexdigest(), 'size_bytes': len(payload)}}}
    archive_path = tmp_path / 'b.zip'
    with zipfile.ZipFile(archive_path, 'w') as archive:
        archive.writestr(name, payload)
    return zipfile.ZipFile(archive_path), manifest


def test_composition_streams_decoded_exports_and_verifies_checksum(tmp_path):
    payload = json.dumps([{'Type': 'MovieScene', 'Properties': {}}]).encode()
    archive, manifest = decoded_archive(tmp_path, payload)
    assert compositor._exports(archive, manifest, '/Game/Aki/S') == [{'Type': 'MovieScene', 'Properties': {}}]
    archive, manifest = decoded_archive(tmp_path, payload, sha='0' * 64)
    with pytest.raises(compositor.UnsupportedComposition, match='checksum'):
        compositor._exports(archive, manifest, '/Game/Aki/S')


def test_streamed_bundle_entries_match_their_manifest_digest(tmp_path):
    source = tmp_path / 'big.bin'
    source.write_bytes(b'abc' * 1_000_000)
    with zipfile.ZipFile(tmp_path / 'o.zip', 'w') as archive:
        _write_zip_entry(archive, 'source/big.bin', source)
        _write_zip_entry(archive, 'x.txt', b'hi')
    with zipfile.ZipFile(tmp_path / 'o.zip') as archive:
        assert archive.read('source/big.bin') == source.read_bytes()
        assert archive.read('x.txt') == b'hi'
    digest = _stream_digest(source)
    assert digest == {'sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'size_bytes': 3_000_000}
