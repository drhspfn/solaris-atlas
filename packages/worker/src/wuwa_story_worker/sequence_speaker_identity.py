"""Repair sequence speaker identities from verified retained bundle JSON."""

import hashlib
import json
import zipfile
from pathlib import PurePosixPath

from sqlalchemy import select
from wuwa_story.db.models.graph import NodeRevision
from wuwa_story.ingestion.sequence_speaker_identity import repair_sequence_speakers


async def repair_sequence_bundle_speakers(session, path, target):
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        inventory = manifest.get('files', {})
        names = [name for name in inventory if name.startswith('decoded/') and name.endswith('.json')]
        if len(names) > 10000 or sum(inventory[name]['size_bytes'] for name in names) > 512 * 1024**2:
            raise ValueError('Decoded sequence bundle exceeds limits')
        assets = {}
        for name in names:
            parts = PurePosixPath(name).parts
            if '..' in parts or '\\' in name or name.startswith('/'):
                raise ValueError('Invalid decoded sequence archive path')
            expected = inventory[name]
            if expected['size_bytes'] < 0 or archive.getinfo(name).file_size != expected['size_bytes']:
                raise ValueError('Decoded sequence entry size differs')
            data = archive.read(name)
            if hashlib.sha256(data).hexdigest() != expected['sha256']:
                raise ValueError('Decoded sequence entry checksum differs')
            decoded = json.loads(data.decode('utf-8-sig'))
            matches = [i for i in range(len(parts) - 2) if parts[i:i + 2] == ('Client', 'Content')]
            if not matches:
                continue
            package = '/Game/' + '/'.join(parts[matches[0] + 2:]).removesuffix('.json')
            if package in assets:
                raise ValueError(f'Duplicate decoded asset: {package}')
            assets[package] = decoded
    has_talker = any(
        isinstance(obj, dict) and isinstance(obj.get('Name'), str)
        and obj['Name'].lower().startswith('default__')
        and isinstance(obj.get('Properties'), dict)
        and type(obj['Properties'].get('TalkID')) is int and obj['Properties']['TalkID'] > 0
        for exports in assets.values() if isinstance(exports, list) for obj in exports)
    if not has_talker:
        return {'skipped': 'actor_identity_not_exported', 'candidate_links': 0,
                'missing_links': 0, 'written': 0, 'affected_characters': []}
    source_ids = set(await session.scalars(select(NodeRevision.source_record_id).where(
        NodeRevision.node_id == target['owner'], NodeRevision.release_id == target['release_id'],
        NodeRevision.source_record_id.is_not(None)).distinct()))
    if len(source_ids) != 1:
        raise ValueError('Sequence owner has missing or ambiguous source record in selected release')
    return await repair_sequence_speakers(session, target['release_id'], source_ids.pop(), assets, apply=True,
                                          asset_source={key: manifest.get(key)
                                                        for key in ('asset_version', 'download_id')})
