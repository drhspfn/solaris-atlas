"""Render verified, settled frames of authored UI sequence compositions."""

import copy
import hashlib
import io
import json
import math
import os
import subprocess
import tempfile
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from PIL import Image, ImageChops

from wuwa_story_worker.lgui_composition import (
    apply_settled_tracks,
    prefab_layout,
    sequence_tracks,
    visible_layers,
)
from wuwa_story_worker.riglyph_convert import convert_archive


class UnsupportedComposition(ValueError):
    pass


class RendererDisabled(UnsupportedComposition):
    pass


class ConversionFailed(ValueError):
    pass


MAX_ARCHIVE_ENTRIES = 50000
MAX_ARCHIVE_ENTRY_SIZE = 256 * 1024 * 1024
MAX_ARCHIVE_TOTAL_SIZE = 2 * 1024 * 1024 * 1024


def _validate_archive_limits(infos, label='Archive'):
    """Report which existing archive safety bound was exceeded."""
    if len(infos) > MAX_ARCHIVE_ENTRIES:
        raise ValueError(f'{label} exceeds entry count limit: {len(infos)} > {MAX_ARCHIVE_ENTRIES}')
    duplicates = len(infos) - len({info.filename for info in infos})
    if duplicates:
        raise ValueError(f'{label} contains duplicate entries: {duplicates}')
    largest = max((info.file_size for info in infos), default=0)
    if largest > MAX_ARCHIVE_ENTRY_SIZE:
        raise ValueError(f'{label} exceeds per-entry size limit: {largest} > {MAX_ARCHIVE_ENTRY_SIZE} bytes')
    total = sum(info.file_size for info in infos)
    if total > MAX_ARCHIVE_TOTAL_SIZE:
        raise ValueError(f'{label} exceeds total uncompressed size limit: {total} > {MAX_ARCHIVE_TOTAL_SIZE} bytes')


def _read(archive, manifest, name):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or '\\' in name or name not in manifest['files']:
        raise UnsupportedComposition('Invalid or unrecorded archive entry')
    if archive.namelist().count(name) != 1:
        raise UnsupportedComposition('Missing or ambiguous archive entry')
    info = manifest['files'][name]
    entry = archive.getinfo(name)
    if entry.file_size > MAX_ARCHIVE_ENTRY_SIZE or entry.file_size != info['size_bytes']:
        raise UnsupportedComposition('Invalid archive entry size')
    data = archive.read(entry)
    if len(data) != info['size_bytes'] or hashlib.sha256(data).hexdigest() != info['sha256']:
        raise UnsupportedComposition('Archive entry checksum mismatch')
    return data


def _exports(archive, manifest, source):
    if not isinstance(source, str) or not source.startswith('/Game/') or '..' in source.split('/'):
        raise UnsupportedComposition('Invalid asset source path')
    path = 'decoded/files/Client/Content/' + \
        source.removeprefix('/Game/') + '.json'
    result = json.loads(_read(archive, manifest, path))
    if not isinstance(result, list):
        raise UnsupportedComposition('Invalid decoded asset exports')
    return result


def _asset_file(archive, manifest, source, export_type, field):
    exports = [item for item in _exports(
        archive, manifest, source) if item.get('Type') == export_type]
    if len(exports) != 1:
        raise UnsupportedComposition('Asset has no unique matching export')
    name = exports[0].get('Properties', {}).get(field)
    if not isinstance(name, str):
        raise UnsupportedComposition('Asset filename is missing')
    name = PurePosixPath(name.replace('\\', '/')).name
    folder = hashlib.sha256(source.encode()).hexdigest()[:16]
    path = f'spine/{folder}/{name}'
    if not name or path not in manifest['files']:
        raise UnsupportedComposition('Exact asset file is absent from bundle')
    return path


def _empty_event(archive, manifest, event):
    source = event.get('source', '')
    package, separator, index = source.rpartition('.')
    if not separator or not index.isdigit() or event.get('function') is not None:
        return False
    exports = _exports(archive, manifest, package)
    if int(index) >= len(exports):
        return False
    section = exports[int(index)]
    if section.get('Type') != 'MovieSceneEventTriggerSection':
        return False
    channel = section.get('Properties', {}).get('EventChannel', {})
    times, values, key = channel.get('KeyTimes', []), channel.get(
        'KeyValues', []), event.get('key_index')
    if type(key) is not int or not 0 <= key < len(times) or len(times) != len(values):
        return False
    pointers = values[key].get('Ptrs', {})
    owner = pointers.get('BoundObjectProperty', {})
    return (times[key].get('Value') == event.get('frame') and 'Function' in pointers
            and pointers['Function'] is None and owner.get('Path') == []
            and 'ResolvedOwner' in owner and owner['ResolvedOwner'] is None)


def _plans(archive, manifest):
    controls = json.loads(
        _read(archive, manifest, manifest['controls']['file']))
    calls = []
    for event in controls.get('events', []):
        for call in event.get('calls', []):
            if call.get('function') in {'OpenUiView', 'PlayUiLevelSequence', 'CloseUiView'}:
                calls.append((event, call))
    if not calls:
        raise UnsupportedComposition('No authored UI composition controls')
    calls.sort(key=lambda item: item[0]['time_seconds'])
    if len(calls) > 256 or calls[0][1]['function'] != 'OpenUiView':
        raise UnsupportedComposition('Unsupported UI control sequence')
    first, opening = calls[0]
    source, scene = first.get('source'), first.get('scene_source')
    if not isinstance(source, str) or not source or not isinstance(scene, str) or not scene:
        raise UnsupportedComposition('Missing scene source identity')
    if any(event.get('source') != source or event.get('scene_source') != scene for event, _ in calls):
        raise UnsupportedComposition(
            'Multiple UI control timelines require separate composition')
    if calls[-1][1]['function'] != 'CloseUiView' or any(
        c['function'] != 'PlayUiLevelSequence' for _, c in calls[1:-1]
    ):
        raise UnsupportedComposition('Unsupported UI open/close lifecycle')
    times = [e.get('time_seconds') for e, _ in calls]
    if any(type(t) not in (int, float) or not math.isfinite(t) or t < 0 for t in times) or any(
        a >= b for a, b in zip(times, times[1:])
    ):
        raise UnsupportedComposition('Ambiguous UI control timing')
    if controls.get('unresolved'):
        raise UnsupportedComposition('Unresolved sequence controls')
    for event in controls.get('events', []):
        time = event.get('time_seconds')
        if event.get('scene_source') not in (scene, None):
            continue
        if type(time) not in (int, float) or not math.isfinite(time):
            raise UnsupportedComposition(
                'Unresolved event time during UI composition')
        if not times[0] <= time <= times[-1]:
            continue
        if event.get('scene_source') is None:
            raise UnsupportedComposition(
                'Event has no scene identity during UI composition')
        unknown = [call for call in event.get('calls', []) if call.get('function') not in
                   {'OpenUiView', 'PlayUiLevelSequence', 'CloseUiView'}]
        if unknown:
            raise UnsupportedComposition(
                'Unknown executable control during UI composition')
        diagnostics = event.get('diagnostics', [])
        if diagnostics and not (diagnostics == [{'code': 'unbound_event_function'}]
                                and not event.get('calls') and _empty_event(archive, manifest, event)):
            raise UnsupportedComposition(
                'Unresolved event during UI composition')
        if not event.get('calls') and not diagnostics:
            raise UnsupportedComposition(
                'Unproven empty event during UI composition')
    args = opening.get('arguments', [])
    if len(args) < 6 or not all(isinstance(args[i], str) and args[i] for i in range(4)):
        raise UnsupportedComposition('Unresolved OpenUiView arguments')
    if args[0] != args[1] or args[2] != args[3] or args[4] is not True or args[5] is not False:
        raise UnsupportedComposition('Unsupported UI variant or playback mode')
    variants = [v for v in manifest.get('variants', []) if v.get(
        'prefab_key') == args[0] and v.get('source') == source]
    prefabs = {v.get('prefab') for v in variants}
    if not variants or len(prefabs) != 1 or None in prefabs or any(v.get('animation') != args[2] or v.get('loop') is not True for v in variants):
        raise UnsupportedComposition(
            'OpenUiView prefab or animation binding is ambiguous')
    prefab = next(iter(prefabs))
    exports = [e for e in _exports(
        archive, manifest, prefab) if e.get('Type') == 'PrefabAsset']
    if len(exports) != 1:
        raise UnsupportedComposition('Prefab asset is missing or ambiguous')
    layout = prefab_layout(exports[0])
    if 'Loop' in layout['sequences']:
        raise UnsupportedComposition('Looping UI property sequences need timed composition playback')
    actors = copy.deepcopy(layout['actors'])
    actions = [(first, 'Start')]
    for event, call in calls[1:-1]:
        args = call.get('arguments', [])
        if not args or not isinstance(args[0], str):
            raise UnsupportedComposition('Unresolved UI sequence name')
        actions.append((event, args[0]))
    if len(actions) > 32:
        raise UnsupportedComposition('UI composition exceeds 32 settled states')
    plans = []
    for index, (event, key) in enumerate(actions):
        sequence = layout['sequences'].get(key)
        binding = layout['bindings'].get(sequence)
        if not sequence or binding is None:
            raise UnsupportedComposition(
                'UI sequence has no exact prefab binding')
        duration, tracks = sequence_tracks(
            _exports(archive, manifest, sequence), binding)
        if not math.isfinite(duration) or duration < 0:
            raise UnsupportedComposition('Invalid UI sequence duration')
        when = event['time_seconds'] + duration
        next_time = actions[index + 1][0]['time_seconds'] if index + \
            1 < len(actions) else times[-1]
        if when >= next_time:
            raise UnsupportedComposition(
                'Overlapping UI transitions cannot be sampled as settled frames')
        apply_settled_tracks(actors, tracks, duration)
        layers = visible_layers(layout, actors)
        if not layers:
            raise UnsupportedComposition('No visible layers in settled frame')
        if len(layers) > 16:
            raise UnsupportedComposition('UI composition exceeds 16 visible layers')
        plans.append({'time': when, 'state_time': event['time_seconds'],
                      'sample': when - times[0], 'layers': layers,
                      'source': source, 'scene_source': scene, 'clip': opening['arguments'][2],
                      'width': layout['size'][0], 'height': layout['size'][1]})
    return plans


def _render(bundle, archive, manifest, plans, root, cli, native):
    converted = {}
    frames = {}
    for index, plan in enumerate(plans):
        maximum = int(os.getenv('WUWA_RIGLYPH_MAX_EDGE', '2048'))
        if not 1 <= maximum <= 2048:
            raise UnsupportedComposition('Invalid maximum output dimension')
        factor = min(1, maximum / max(plan['width'], plan['height']))
        size = (max(1, round(plan['width'] * factor)),
                max(1, round(plan['height'] * factor)))
        canvas = Image.new('RGBA', size, (0, 0, 0, 0))
        for layer in plan['layers']:
            if layer.get('kind') == 'solid':
                color = layer['color']
                canvas.alpha_composite(
                    Image.new('RGBA', canvas.size, (*color[:3], round(255 * layer['opacity']))))
                continue
            if max(abs(value) for value in layer['origin']) > 0.5:
                raise UnsupportedComposition(
                    'Nonzero UI origin needs verified projected placement')
            if layer['color'] != [255, 255, 255]:
                raise UnsupportedComposition(
                    'Tinted Spine layers require verified blend semantics')
            skeleton = _asset_file(
                archive, manifest, layer['source'], 'SpineSkeletonDataAsset', 'skeletonDataFileName')
            atlas = _asset_file(
                archive, manifest, layer['atlas'], 'SpineAtlasAsset', 'atlasFileName')
            key = (skeleton, atlas)
            if key not in converted:
                directory = root / f'rig-{len(converted)}'
                try:
                    convert_archive(bundle, directory, cli,
                                    skeleton_file=skeleton, atlas_file=atlas)
                except (OSError, ValueError, subprocess.SubprocessError) as error:
                    detail = getattr(error, 'stderr', None) or str(error)
                    (root / 'conversion-error.log').write_text(str(detail), encoding='utf-8')
                    raise ConversionFailed(str(detail)[-1000:]) from error
                converted[key] = directory
            directory = converted[key]
            camera = {'camera': {'mode': 'fixed', 'x': -layer['origin'][0] / layer['scale'],
                                 'y': -layer['origin'][1] / layer['scale'],
                                 'worldWidth': plan['width'] / layer['scale']}}
            config = root / 'camera.json'
            config.write_text(json.dumps(camera), encoding='utf-8')
            output = root / f'frame-{index}-{layer["actor"]}'
            sample = plan['sample']
            from wuwa_story_worker.riglyph_render import _run
            _run([*cli, 'render', str(directory / 'skeleton.ir.json'), '--images', str(directory / 'textures'),
                            '--clip', plan['clip'], '--loop', '--start', str(
                                sample), '--end', str(sample + .01),
                            '--fps', '1', '--width', str(size[0]), '--height', str(
                                size[1]), '--format', 'png-sequence',
                            '--alpha', '--backend', 'native-cpu', '--native-path', native, '--config', str(
                                config),
                            '--max-frames', '1', '--overwrite', '--progress', 'none', '--out-dir', str(output)],
                 root / f'render-{index}-{layer["actor"]}.log', 300)
            with Image.open(output / 'frame_000000.png') as image:
                image = image.convert('RGBA')
                if image.size != canvas.size:
                    raise ValueError(
                        'Renderer returned incorrect frame dimensions')
                if layer['opacity'] != 1:
                    image.putalpha(ImageChops.multiply(image.getchannel('A'),
                                   Image.new('L', image.size, round(255 * layer['opacity']))))
                canvas.alpha_composite(image)
        result = io.BytesIO()
        canvas.save(result, format='PNG')
        name = f'composition/frame-{index:03}.png'
        frames[name] = result.getvalue()
        if sum(len(data) for data in frames.values()) > 128 * 1024 * 1024:
            raise ValueError('Composed illustrations exceed the publication size bound')
        plan['file'] = name
    return frames


def annotate_bundle(bundle: Path) -> Path:
    """Atomically add a static-frame composition and provenance to an existing bundle."""
    bundle = Path(bundle)
    cli = ['node', os.getenv(
        'WUWA_RIGLYPH_CLI', '/app/tools/riglyph/node_modules/@riglyph/cli/dist/cli/main.js')]
    native = os.getenv('RIGLYPH_NATIVE_BIN',
                       '/app/tools/riglyph-native/riglyph-native')
    with tempfile.TemporaryDirectory(prefix='sequence-composition-', dir=bundle.parent) as temporary:
        root = Path(temporary)
        with zipfile.ZipFile(bundle) as archive:
            infos = archive.infolist()
            _validate_archive_limits(infos)
            manifest = json.loads(archive.read('manifest.json'))
            frames = {}
            stage = 'plan'
            try:
                plans = _plans(archive, manifest)
                if os.getenv('WUWA_RIGLYPH_ENABLED', '').lower() not in {'1', 'true'}:
                    raise RendererDisabled('Riglyph rendering is disabled')
                stage = 'render'
                frames = _render(bundle, archive, manifest,
                                 plans, root, cli, native)
                composition = {'schema_version': 1, 'state': 'ready', 'reasons': [],
                               'frames': [{'file': p['file'], 'source': p['source'],
                                           'scene_source': p['scene_source'],
                                           'time_seconds': p.get('state_time', p['time']),
                                           'sample_time_seconds': p['time']}
                                          for p in plans]}
            except (UnsupportedComposition, ValueError, KeyError, TypeError, IndexError, OSError, subprocess.SubprocessError) as error:
                unsupported = isinstance(error, UnsupportedComposition) or (
                    stage == 'plan' and isinstance(error, (ValueError, KeyError, TypeError, IndexError)))
                state = 'unsupported' if unsupported else 'failed'
                detail = getattr(error, 'stderr', None) or str(error)
                code = 'renderer_disabled' if isinstance(
                    error, RendererDisabled) else f'{stage}_{state}'
                if isinstance(error, ConversionFailed):
                    code = 'conversion_failed'
                composition = {'schema_version': 1, 'state': state,
                               'reasons': [{'code': code, 'detail': str(detail)[-1000:]}], 'frames': []}
                frames = {}
            old = manifest.get('composition', {}).get('frames', [])
            old_files = {f.get('file') for f in old if isinstance(f, dict) and isinstance(f.get('file'), str)
                         and f['file'].startswith('composition/')}
            for name in old_files:
                manifest['files'].pop(name, None)
            for name, data in frames.items():
                manifest['files'][name] = {'sha256': hashlib.sha256(
                    data).hexdigest(), 'size_bytes': len(data)}
            manifest['composition'] = composition
            from wuwa_story_worker.riglyph_render import VERSION
            with bundle.open('rb') as stream:
                source_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
            composition.update(recipe='lgui-settled-v1', riglyph_version=VERSION,
                               source_bundle_sha256=source_hash)
            output = root / 'bundle.zip'
            with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as target:
                for info in archive.infolist():
                    if info.filename != 'manifest.json' and info.filename not in old_files:
                        target.writestr(info, archive.read(info))
                for name, data in frames.items():
                    target.writestr(name, data)
                target.writestr('manifest.json', json.dumps(
                    manifest, ensure_ascii=False, sort_keys=True))
        logs = list(root.glob('*.log'))
        if logs:
            from wuwa_story_worker.tooling import asset_workspace
            destination = asset_workspace() / 'render-logs' / 'sequence-composition' / uuid.uuid4().hex
            destination.mkdir(parents=True, exist_ok=True)
            for log in logs:
                (destination / log.name).write_bytes(log.read_bytes())
            (destination / 'result.json').write_text(json.dumps(composition), encoding='utf-8')
        for attempt in range(5):
            try:
                output.replace(bundle)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.2)
    return bundle
