"""Archive authored Spine prefabs, skeletons, atlases and textures as one portable bundle."""

import asyncio
import hashlib
import json
import zipfile
from contextlib import closing
from pathlib import Path, PurePosixPath

from wuwa_story_worker.asset_export import export_assets
from wuwa_story_worker.event_resources import prefab_images, ui_references
from wuwa_story_worker.map_icons import build_icons
from wuwa_story_worker.map_sources import integer, open_db, table, text


def background_resource(config, identity):
    with closing(open_db(config / 'db_SpineBackground.db')) as db:
        found = db.execute(
            'SELECT Id, BinData FROM spinebackground WHERE Id=?', (identity,)).fetchone()
    if not found or integer(table(found[1]), 0) != identity:
        raise ValueError(
            f'Spine background {identity} is absent or has an incompatible client schema')
    row = table(found[1])
    variants = [(None, text(row, 2), text(row, 3)),
                ('male', text(row, 4), text(row, 5))]
    result = []
    with closing(open_db(config / 'db_ui_resource.db')) as db:
        for rover, key, animation in variants:
            if not key:
                continue
            resource = db.execute(
                'SELECT Path, PcPath FROM uiresource WHERE Id=?', (key,)).fetchone()
            path = resource and (resource[1] or resource[0])
            if not path or not path.startswith('/Game/') or '..' in path.split('/'):
                raise ValueError(
                    f'Spine background {identity} has no valid prefab for {key}')
            result.append({'rover': rover, 'prefab_key': key,
                          'prefab': path.split('.')[0], 'animation': animation})
    if not result:
        raise ValueError(f'Spine background {identity} has no authored prefab')
    return {'resource_id': identity, 'variants': result,
            'config_blob_sha256': hashlib.sha256(found[1]).hexdigest()}


def portable_bundle(output, resource, assets, icons, identity):
    entries = {}
    originals = set()
    skeletons = []
    texture_files = {}
    source_packages = []
    dependencies = {}
    for source, asset in sorted(assets.items()):
        package = Path(asset['package'])
        raw = asset['raw'] / package
        raw_parts = [raw.with_suffix(suffix)
                     for suffix in ('.uasset', '.uexp', '.ubulk')]
        if not raw.is_file():
            raise ValueError(f'Missing exported Spine dependency: {source}')
        for path in raw_parts:
            if path.is_file():
                originals.add(path)
                entries['source/' +
                        path.relative_to(asset['raw']).as_posix()] = path.read_bytes()
        source_packages.append(source)
        exports = []
        for path in sorted(asset['decoded'].rglob('*.json')):
            decoded = json.loads(path.read_text(encoding='utf-8-sig'))
            if isinstance(decoded, list) and any(isinstance(item, dict) and item.get('Type') for item in decoded):
                exports.extend(decoded)
                entries['decoded/' +
                        path.relative_to(asset['decoded']).as_posix()] = path.read_bytes()
        dependencies[source] = {path for kind, path in ui_references(
            exports) if kind == 'dependency'}
        folder = 'spine/' + \
            hashlib.sha256(source.encode()).hexdigest()[:16] + '/'
        for export in exports:
            kind, props = export.get('Type'), export.get('Properties', {})
            if kind not in {'SpineAtlasAsset', 'SpineSkeletonDataAsset'}:
                continue
            filename = props.get(
                'atlasFileName' if kind == 'SpineAtlasAsset' else 'skeletonDataFileName', '')
            name = PurePosixPath(filename.replace('\\', '/')).name
            if name in {'', '.', '..'}:
                raise ValueError('Spine export has no portable filename')
            data = props.get('rawData')
            if kind == 'SpineAtlasAsset':
                if not isinstance(data, str) or not data.strip():
                    raise ValueError('Spine atlas data is empty')
                entries[folder + name] = data.encode('utf-8')
                for _, texture in ui_references(props.get('atlasPages', [])):
                    icon = icons.get(texture)
                    if not icon:
                        raise ValueError(
                            f'Spine atlas page was not decoded: {texture}')
                    destination = folder + PurePosixPath(texture).name + '.png'
                    entries[destination] = Path(icon['path']).read_bytes()
                    texture_files[texture] = destination
            else:
                if not isinstance(data, list) or not data or any(type(value) is not int or not 0 <= value <= 255 for value in data):
                    raise ValueError('Spine skeleton binary data is invalid')
                entries[folder + name] = bytes(data)
                skeletons.append({'source': source, 'file': folder + name})
    for source, icon in sorted(icons.items()):
        if source not in texture_files:
            destination = 'textures/' + source.removeprefix('/Game/') + '.png'
            entries[destination] = Path(icon['path']).read_bytes()
            texture_files[source] = destination
        for raw_path in icon['raw_paths']:
            path = Path(raw_path)
            originals.add(path)
            # Raw image packages have the same stable Client/Content path as the prefab exports.
            from wuwa_story_worker.media_provenance import package_path
            entries['source/' + package_path(path)] = path.read_bytes()
    if not skeletons or not any(name.endswith('.atlas') for name in entries):
        raise ValueError(
            'Prefab did not resolve both a Spine skeleton and atlas')
    from wuwa_story_worker.spine_open import open_playback_entries
    generated, playback = open_playback_entries(
        entries, resource, skeletons, dependencies)
    entries.update(generated)
    manifest = {'playback': playback, 'schema_version': 2, 'kind': 'story_spine', **identity, **resource,
                'packages': source_packages, 'skeletons': skeletons, 'textures': texture_files,
                'files': {name: {'sha256': hashlib.sha256(data).hexdigest(), 'size_bytes': len(data)}
                          for name, data in sorted(entries.items())}}
    entries['manifest.json'] = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True).encode('utf-8')
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_suffix('.partial')
    with zipfile.ZipFile(partial, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(entries.items()):
            info = zipfile.ZipInfo(name, date_time=(2000, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    partial.replace(output)
    return output, sorted(originals)


async def build_spine_bundle(root, resource_id, fmodel, converter):
    receipt = await export_assets(root, fmodel, 'ConfigDB')
    configs = list((receipt.parent / 'files').rglob('db_SpineBackground.db'))
    if len(configs) != 1:
        raise ValueError('Expected one pinned SpineBackground client database')
    resource = await asyncio.to_thread(background_resource, configs[0].parent, resource_id)
    assets = {}
    textures, missing = await prefab_images(root, [variant['prefab'] for variant in resource['variants']], assets=assets)
    if missing:
        raise ValueError(
            f'Spine dependency is incomplete: {missing[0]["expected"]}')
    pages = set()
    for asset in assets.values():
        for path in asset['decoded'].rglob('*.json'):
            exports = json.loads(path.read_text(encoding='utf-8-sig'))
            for export in exports if isinstance(exports, list) else []:
                if isinstance(export, dict) and export.get('Type') == 'SpineAtlasAsset':
                    pages.update(texture for _, texture in ui_references(
                        export.get('Properties', {}).get('atlasPages', [])))
    # A default UI sprite may live in the enormous common atlas. Only authored
    # Spine atlas pages are needed by the portable skeleton; preserve other UI
    # references in the prefab JSON rather than exporting that unrelated atlas.
    resource['ui_image_references'] = sorted(
        {image['path'] for image in textures} - pages)
    icons = await build_icons(root, fmodel, converter,
                              [{'metadata_json': {'icon_source': path}}
                                  for path in sorted(pages)],
                              entity_media=True, exact_textures=True)
    absent = pages - icons.keys()
    if absent:
        raise ValueError(f'Spine texture is unavailable: {sorted(absent)[0]}')
    plan = json.loads((root / 'plan.json').read_text(encoding='utf-8'))
    identity = {'asset_version': plan['version'],
                'download_id': plan['id'], 'tier': plan['tier']}
    return await asyncio.to_thread(portable_bundle, root / 'story-spine' / f'{resource_id}.zip',
                                   resource, assets, icons, identity)


async def publish_open_playback(session, bundle, target, published, service, storage, run_id, asset_version):
    """Publish only the browser's open JSON/PNG files, with retained archive identity."""
    from sqlalchemy import select
    from wuwa_story.db.models.storage import FileReference

    with zipfile.ZipFile(bundle) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        playback = manifest.get('playback', {})
        files = {}
        components = []
        for variant in playback.get('variants', []):
            for entry in [variant['scene'], *variant['textures'].values()]:
                if entry in files:
                    continue
                expected = manifest['files'][entry]
                if expected['size_bytes'] > 64 * 1024 * 1024:
                    raise ValueError(
                        'Open scene component exceeds its bounded profile')
                data = archive.read(entry)
                if len(data) != expected['size_bytes'] or hashlib.sha256(data).hexdigest() != expected['sha256']:
                    raise ValueError(
                        'Open scene component does not match its archive manifest')
                mime = 'application/json' if entry.endswith(
                    '.json') else 'image/png' if entry.endswith('.png') else None
                if mime is None:
                    raise ValueError('Unsupported browser scene component')
                path = bundle.parent / 'open-playback' / expected['sha256']
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                file = await service.register_file(session, path, 'image' if mime == 'image/png' else 'unknown', mime_type=mime)
                await service.register_variant(session, published.id, file.id, 'spine_open_component', run_id)
                reference = await session.scalar(select(FileReference).where(
                    FileReference.owner_node_id == target['owner'], FileReference.release_id == target['release_id'],
                    FileReference.reference_type == 'story_spine_part', FileReference.source_path == target[
                        'path'],
                    FileReference.source_name == entry))
                if reference is None:
                    reference = await service.register_reference(session, file_id=file.id,
                                                                 reference_type='story_spine_part', owner_node_id=target['owner'], release_id=target['release_id'],
                                                                 source_path=target['path'], source_name=entry)
                reference.file_id = file.id
                reference.metadata_json = {
                    'asset_version': asset_version, 'bundle_file_id': published.id, 'archive_entry': entry}
                from wuwa_story.storage.hashing import content_object_key
                object_key = content_object_key(file.sha256)
                files[entry] = storage.public_url(object_key)
                components.append({'file_id': file.id, 'object_key': object_key, 'size_bytes': len(data)})
        return {'format': 'solaris.skeletal-scene.v1', 'components': components, 'variants': [
            {'rover': variant['rover'], 'animation': variant['animation'],
             'scene_url': files[variant['scene']],
             'textures': {name: files[entry] for name, entry in variant['textures'].items()}}
            for variant in playback.get('variants', [])], 'unsupported': playback.get('unsupported', [])}
