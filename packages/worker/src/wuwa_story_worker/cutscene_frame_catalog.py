"""Native launcher catalog: generated fields only, no serialized-blob guessing."""

import hashlib
import json
import re
import sqlite3
import struct
import time
from contextlib import closing
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

from wuwa_story_worker.cutscene_frame_events import discover_movie_audio, discover_launcher_frame_audio, movie_frame_bindings
from wuwa_story_worker.map_sources import integer, open_db, table, text

# Same native readers used by map_sources/quest_interactions. Quest/LevelPlay
# Data is generated vtable6; LevelEntity ComponentsData is field10/vtable24.
_SOURCES = (
    ('db_flowState.db', 'SELECT StateKey, BinData FROM flowstate ORDER BY StateKey', 'Actions', 6),
    ('db_QuestNodeData.db', 'SELECT Key, BinData FROM questnodedata ORDER BY Key', 'Data', 1),
    ('db_LevelPlayNodeData.db', 'SELECT Key, BinData FROM levelplaynodedata ORDER BY Key', 'Data', 1),
    ('db_level_entity.db', 'SELECT Id, MapId, EntityId, BinData FROM levelentityconfig ORDER BY Id', 'ComponentsData', 10),
)
_ALTERNATIVES = {'Options', 'Branches', 'ThenActions', 'ElseActions', 'Cases', 'RandomActions'}


def _launchers(value, path, conditional=False):
    if isinstance(value, list):
        for index, item in enumerate(value):
            yield from _launchers(item, f'{path}[{index}]', conditional)
    elif isinstance(value, dict):
        name, kind = value.get('Name'), value.get('Type')
        nested = conditional or isinstance(name, str) and name in {'If', 'Branch', 'Random', 'Switch'} or (
            isinstance(kind, str) and kind in {'If', 'Condition', 'Branch', 'Random', 'Switch', 'Selector', 'Select'})
        if value.get('Name') == 'PlayMovie':
            yield path, value, conditional
            nested = True
        for key, item in value.items():
            child = path + '.' + key
            if (isinstance(item, str) and key in {'Actions', 'Data', 'Params', 'EventActions', 'Mp4FrameEvents'}
                    and item.lstrip().startswith(('{', '['))):
                item = json.loads(item)
                child += '<json>'
            if isinstance(item, (dict, list)):
                yield from _launchers(item, child, nested or key in _ALTERNATIVES)


def _binding(action, source, path, conditional):
    params = action.get('Params')
    params = json.loads(params) if isinstance(params, str) else params
    name = params.get('VideoName') if isinstance(params, dict) else None
    if name == '':
        # VideoLauncher.ShowVideoCg('', callback) invokes callback without opening.
        return None
    if not isinstance(name, str) or not name:
        raise ValueError('PlayMovie has no exact VideoName')
    parsed = discover_movie_audio([action], video_name=name, source={**source, 'raw_path': path})
    # Preserve the actual JSON-tree path, rather than the parser's temporary [0].
    for item in [*parsed['events'], *parsed['frame_actions'], *parsed['unresolved']]:
        prefix = path + '[0]'
        raw = item['source']['raw_path']
        if raw.startswith(prefix):
            item['source']['raw_path'] = path + raw[len(prefix):]
    if conditional:
        parsed['unresolved'].append({'code': 'nested_movie_requires_runtime_context',
            'source': {**source, 'raw_path': path}, 'value': deepcopy(action)})
        for event in parsed['events']:
            event['resolved'] = False
    return {**parsed, 'video_name': name, 'source': {**source, 'raw_path': path},
            'raw_action': deepcopy(action), 'conditional': conditional,
            'complete': not parsed['unresolved']}


def _preceding_music(actions, movie, source, root):
    """Retain explicit music crossing ShowTalk -> PlayMovie; never invent elapsed time."""
    if not isinstance(actions, list) or not any(item is movie for item in actions):
        return []
    index = next(i for i, item in enumerate(actions) if item is movie)
    before = index - 1
    while before >= 0 and isinstance(actions[before], dict) and actions[before].get('Name') == 'SetPlotMode':
        before -= 1
    if before < 0 or not isinstance(actions[before], dict) or actions[before].get('Name') != 'ShowTalk':
        return []
    params = actions[before].get('Params', {})
    if not isinstance(params, dict):
        return []
    events = []
    frames, items = params.get('TalkFrameEvents', []), params.get('TalkItems', [])
    if not isinstance(frames, list) or not isinstance(items, list):
        return []
    for i, frame in enumerate(frames):
        if not isinstance(frame, dict) or not isinstance(frame.get('Position'), dict):
            continue
        position = frame.get('Position', {})
        if not any(isinstance(item, dict) and item.get('Id') == position.get('TalkItemId') for item in items):
            continue
        definition = frame.get('FrameEvent', {})
        frame_actions = definition.get('EventActions', []) if isinstance(definition, dict) else []
        if not isinstance(frame_actions, list):
            continue
        for j, action in enumerate(frame_actions):
            if not isinstance(action, dict) or not isinstance(action.get('Params'), dict):
                continue
            config = action['Params'].get('EventConfig')
            if not isinstance(config, dict):
                continue
            if (action.get('Name') != 'PostAkEvent' or
                    config.get('Type') != 'MusicSubtitle'):
                continue
            proof = {**source,
                'raw_path': f'{root}[{before}].Params.TalkFrameEvents[{i}].FrameEvent.EventActions[{j}]',
                'position': deepcopy(position),
                'reconstruction': 'Fixed-start continuation from preceding dialogue; elapsed player/sequence time is not reproduced.'}
            parsed = discover_launcher_frame_audio([{'Second': 0, 'EventActions': [action]}],
                video_name=movie['Params']['VideoName'], source=proof)
            for event in parsed['events']:
                event['source'] = proof
                event['timing_basis'] = 'fixed_start_continuation'
                event.pop('raw_frame_event', None)
                events.append(event)
    return events


def _fingerprint(directory):
    result = []
    for filename, *_ in _SOURCES:
        path = directory / filename
        stat = path.stat() if path.is_file() else None
        result.append((str(path), stat.st_size if stat else None, stat.st_mtime_ns if stat else None))
    return tuple(result)


@lru_cache(maxsize=2)
def _catalog(fingerprint):
    result = {'bindings': {}, 'sources': [], 'unresolved': []}
    for spec, identity in zip(_SOURCES, fingerprint, strict=True):
        deadline = time.monotonic() + 60
        filename, query, field, slot = spec
        path = Path(identity[0])
        source = {'file': filename, 'json_field': field, 'vtable_offset': 4 + slot * 2}
        coverage = {**source, 'status': 'not_read', 'records': 0, 'launchers': 0, 'record_errors': 0, 'noop_launchers': 0}
        result['sources'].append(coverage)
        if identity[1] is None or identity[1] > 512 * 1024**2:
            coverage['status'] = 'missing' if identity[1] is None else 'size_bound_exceeded'
            result['unresolved'].append({'code': 'native_source_unavailable', 'source': source})
            continue
        with path.open('rb') as stream:
            source['database_sha256'] = hashlib.file_digest(stream, 'sha256').hexdigest()
        try:
            with closing(open_db(path)) as db:
                db.execute('PRAGMA query_only=ON')
                db.set_progress_handler(lambda deadline=deadline: int(time.monotonic() >= deadline), 1000)
                for row in db.execute(query):
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Native launcher source60s bound')
                    coverage['records'] += 1
                    record_key = ':'.join(str(v) for v in row[:-1])
                    try:
                        blob = row[-1]
                        native = table(blob)
                        key = row[0]
                        if filename == 'db_level_entity.db':
                            if tuple(integer(native, i) for i in range(3)) != tuple(row[:3]):
                                raise ValueError('LevelEntity identity differs from native record')
                        elif text(native, 0) != key:
                            raise ValueError('Story key differs from native record')
                        raw = text(native, slot)
                        value = json.loads(raw) if raw else ([] if field == 'Actions' else {})
                        proof = {**source, 'key': record_key, 'record_sha256': hashlib.sha256(blob).hexdigest()}
                        root = filename + '[' + json.dumps(record_key, ensure_ascii=False) + '].' + field + '<json>'
                        for at, action, conditional in _launchers(value, root):
                            binding = _binding(action, proof, at, conditional)
                            if binding is None:
                                coverage['noop_launchers'] += 1
                                continue
                            binding['development_only'] = bool(re.search(r'(?:^|_)TEST(?:_|[ .])', record_key, re.I))
                            if filename == 'db_flowState.db' and not conditional:
                                carried = _preceding_music(value, action, proof, root)
                                if len(carried) == 1 and not any(event['mode'] == 'MusicSubtitle' for event in binding['events']):
                                    binding['events'].extend(carried)
                                elif len(carried) > 1:
                                    binding['unresolved'].append({'code': 'ambiguous_preceding_music', 'source': proof})
                                    binding['complete'] = False
                            result['bindings'].setdefault(binding['video_name'], []).append(binding)
                            coverage['launchers'] += 1
                    except (ValueError, IndexError, TypeError, RecursionError, struct.error) as error:
                        coverage['record_errors'] += 1
                        result['unresolved'].append({'code': 'native_record_decode_incomplete',
                            'source': {**source, 'key': record_key}, 'error': type(error).__name__,
                            'reason': str(error)[:160]})
                coverage['status'] = 'reviewed_with_record_errors' if coverage['record_errors'] else 'reviewed'
        except (sqlite3.Error, ValueError, IndexError, TypeError, TimeoutError, RecursionError, struct.error) as error:
            coverage['status'] = 'incomplete'
            result['unresolved'].append({'code': 'native_source_decode_incomplete', 'source': source,
                                         'error': type(error).__name__})
    for name, bindings in result['bindings'].items():
        timelines = set()
        for binding in bindings:
            if binding['complete'] and not binding.get('development_only'):
                timelines.add(json.dumps([{key: event[key] for key in (
                    'reference', 'start_seconds', 'end_seconds', 'mode', 'operation', 'event_config')}
                    for event in binding['events']], sort_keys=True))
        if len(timelines) > 1:
            sources = [binding['source'] for binding in bindings]
            for binding in bindings:
                if binding.get('development_only'):
                    continue
                binding['unresolved'].append({'code': 'conflicting_launcher_timelines',
                    'video_name': name, 'sources': deepcopy(sources)})
                binding['complete'] = False
                for event in binding['events']:
                    event['resolved'] = False
    return result


def movie_launcher_catalog(config_directory: Path) -> dict:
    """One bounded scan for ALL exact CG names; caller can reuse for leaf plans."""
    return deepcopy(_catalog(_fingerprint(Path(config_directory).resolve())))


def catalog_movie_frame_bindings(config_directory: Path, name: str) -> list[dict]:
    """Keep distinct source recipes; unknown/missing launchers never mean silence."""
    if not isinstance(name, str) or not name:
        raise ValueError('Select a movie name')
    catalog = _catalog(_fingerprint(Path(config_directory).resolve()))
    bindings = deepcopy(catalog['bindings'].get(name, []))
    if not bindings and (Path(config_directory) / 'db_flowState.db').is_file():
        # Generic exact-name native reader, independent of the broad scan budget.
        # This preserves source identity; never manufactures a movie/state mapping.
        bindings = movie_frame_bindings(Path(config_directory), name)
    if not bindings:
        bindings.append({'video_name': name, 'source': {'catalog_sources': deepcopy(catalog['sources'])},
            'events': [], 'frame_actions': [], 'complete': False,
            'unresolved': [{'code': 'unmapped_launcher', 'video_name': name}]})
    if catalog['unresolved']:
        bindings.append({'video_name': name, 'source': {'catalog_sources': deepcopy(catalog['sources'])},
            'events': [], 'frame_actions': [], 'complete': False,
            'unresolved': deepcopy(catalog['unresolved'])})
    return bindings
