import hashlib
import json
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story_worker import sequence_speaker_identity as repair


@pytest.mark.asyncio
async def test_bundle_validates_json_hash_before_repair(tmp_path, monkeypatch):
    path = tmp_path / 'sequence.zip'
    name = 'decoded/Aki/Client/Content/Foo.json'
    data = b'[{"Type":"Blueprint","Name":"Default__Actor","Properties":{"TalkID":123}}]'
    async def run(digest):
        manifest = {'files': {name: {'size_bytes': len(data), 'sha256': digest}}}
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('manifest.json', json.dumps(manifest))
            archive.writestr(name, data)
        session = SimpleNamespace(scalars=AsyncMock(return_value=[42]))
        return await repair.repair_sequence_bundle_speakers(
            session, path, {'owner': 9, 'release_id': 21})
    service = AsyncMock(return_value={'written': 1})
    monkeypatch.setattr(repair, 'repair_sequence_speakers', service)
    with pytest.raises(ValueError, match='checksum'):
        await run('bad')
    service.assert_not_awaited()
    assert await run(hashlib.sha256(data).hexdigest()) == {'written': 1}
    assert service.await_args.args[1:] == (21, 42, {'/Game/Foo': [{'Type': 'Blueprint', 'Name': 'Default__Actor', 'Properties': {'TalkID': 123}}]})
    assert service.await_args.kwargs == {
        'apply': True, 'asset_source': {'asset_version': None, 'download_id': None}}


@pytest.mark.asyncio
async def test_bundle_without_actor_defaults_skips_database(tmp_path):
    path = tmp_path / 'old.zip'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('manifest.json', json.dumps({'files': {}}))
    session = SimpleNamespace(scalars=AsyncMock())
    report = await repair.repair_sequence_bundle_speakers(session, path, {'owner': 1, 'release_id': 2})
    assert report['skipped'] == 'actor_identity_not_exported' and report['written'] == 0
    session.scalars.assert_not_awaited()
