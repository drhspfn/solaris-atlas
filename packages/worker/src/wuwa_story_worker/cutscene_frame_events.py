"""Pure discovery of movie-clock audio commands, not Wwise rendering.

Verified chain: FlowActionPlayMovie.Mp4FrameEvents -> VideoLauncher.SetupFrameEvent
-> OnCheckFrameEvent(player milliseconds). Second is seconds, not caption frames.
VideoSound, caption voices and embedded audio are independent source lanes.
"""

import hashlib
import json
import math
import re
import sqlite3
import struct
import time
from contextlib import closing
from copy import deepcopy
from pathlib import Path

from flatbuffers.table import Table

_EVENT = re.compile(r'^/Game/[A-Za-z0-9_/]+\.[A-Za-z0-9_]+$')
_VISUAL = {'FadeInScreen', 'FadeOutScreen', 'OpenSystemBoard', 'TriggerGamepadShake'}


def _source(source, path):
    return {**(source or {}), 'raw_path': path}


def _issue(result, code, source, path, value):
    result['unresolved'].append({'code': code, 'source': _source(source, path),
                                 'value': deepcopy(value)})


def _decoded(value, result, source, path):
    if isinstance(value, str):
        try:
            return json.loads(value), path + '<json>'
        except (ValueError, RecursionError):
            _issue(result, 'invalid_source_json', source, path, value)
            return None, path
    return value, path


def _frames(frames, movie, source, path, result):
    frames, path = _decoded(frames, result, source, path)
    if not isinstance(frames, list):
        _issue(result, 'invalid_frame_events', source, path, frames)
        return
    pending = []
    for index, frame in enumerate(frames):
        fp = f'{path}[{index}]'
        if not isinstance(frame, dict):
            _issue(result, 'invalid_frame_event', source, fp, frame)
            continue
        second = frame.get('Second')
        if type(second) not in (int, float) or not math.isfinite(second) or second < 0:
            _issue(result, 'invalid_frame_seconds', source, fp + '.Second', frame)
            continue
        pending.append((second, -index, fp, frame))
    # JS stable descending sort followed by pop: equal-time frame lists reverse,
    # while EventActions within one list execute in their original order.
    for second, _, fp, frame in sorted(pending, key=lambda item: item[:2]):
        unknown = set(frame) - {'Second', 'EventActions'}
        if unknown:
            _issue(result, 'uncompiled_frame_flags', source, fp,
                   {key: frame[key] for key in sorted(unknown)})
        actions, ap = _decoded(frame.get('EventActions'), result, source, fp + '.EventActions')
        if not isinstance(actions, list):
            _issue(result, 'invalid_event_actions', source, ap, actions)
            continue
        for index, action in enumerate(actions):
            cp = f'{ap}[{index}]'
            preserved = {'video_name': movie, 'start_seconds': second,
                         'source': _source(source, cp), 'raw_action': deepcopy(action),
                         'classification': 'unresolved'}
            result['frame_actions'].append(preserved)
            if not isinstance(action, dict):
                _issue(result, 'invalid_frame_action', source, cp, action)
                continue
            name = action.get('Name')
            if name in _VISUAL:
                preserved['classification'] = 'non_audio'
                continue
            if name != 'PostAkEvent':
                # Never flatten conditional branches and play every alternative.
                _issue(result, 'uncompiled_frame_action', source, cp, action)
                continue
            params, pp = _decoded(action.get('Params'), result, source, cp + '.Params')
            if not isinstance(params, dict):
                _issue(result, 'invalid_audio_params', source, pp, params)
                continue
            config, ep = _decoded(params.get('EventConfig'), result, source, pp + '.EventConfig')
            if not isinstance(config, dict):
                _issue(result, 'invalid_audio_event_config', source, ep, config)
                continue
            reference = config.get('AkEvent')
            if not isinstance(reference, str) or not _EVENT.fullmatch(reference):
                _issue(result, 'invalid_audio_event_path', source, ep + '.AkEvent', config)
                continue
            mode = config.get('Type')
            preserved['classification'] = 'audio_event'
            event = {**preserved, 'reference': reference, 'end_seconds': None,
                     'operation': 'post', 'mode': mode, 'event_config': deepcopy(config),
                     'raw_frame_event': deepcopy(frame), 'timing_basis': 'VideoLauncher.Second',
                     'stop_policy': 'not_inferred'}
            diagnostics = []
            if mode not in {'Global', 'MusicSubtitle'}:
                diagnostics.append('target_audio_requires_entity_context' if mode == 'Target'
                                   else 'unknown_audio_event_mode')
            if mode == 'MusicSubtitle' and not isinstance(config.get('MusicSubtitleGroupTag'), str):
                diagnostics.append('missing_music_subtitle_group')
            allowed = {'Type', 'AkEvent', 'MusicSubtitleGroupTag'} if mode == 'MusicSubtitle' else {'Type', 'AkEvent'}
            if set(config) - allowed or set(params) - {'EventConfig'}:
                diagnostics.append('uncompiled_audio_flags')
            if set(action) - {'Name', 'Params', 'ActionGuid', 'ActionId'}:
                diagnostics.append('uncompiled_action_flags')
            event['resolved'] = not diagnostics and not unknown
            result['events'].append(event)
            for code in diagnostics:
                _issue(result, code, source, cp, action)


def _movie(action, video_name, source, path, result):
    ap = path + '.Params'
    params, ap = _decoded(action.get('Params'), result, source, ap)
    if not isinstance(params, dict):
        _issue(result, 'invalid_movie_params', source, ap, params)
        return
    movie = params.get('VideoName')
    if video_name is not None and movie != video_name:
        return
    for alias in ('FrameEvents', 'MovieEvent', 'MovieEvents', 'VideoData'):
        if alias in params:
            _issue(result, 'unverified_native_movie_field', source, ap + '.' + alias, params[alias])
    if 'Mp4FrameEvents' in params:
        _frames(params['Mp4FrameEvents'], movie, source, ap + '.Mp4FrameEvents', result)


def _nested_movies(value, path):
    if isinstance(value, dict):
        if value.get('Name') == 'PlayMovie':
            yield path, value
        else:
            for key, child in value.items():
                yield from _nested_movies(child, path + '.' + key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _nested_movies(child, f'{path}[{index}]')


def _movie_name(action):
    params = action.get('Params')
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except ValueError:
            return None
    return params.get('VideoName') if isinstance(params, dict) else None


def discover_frame_audio(actions, *, video_name=None, source=None):
    """Discover exact PlayMovie frame events from decoded or JSON-string Actions.

    Caller supplies file/record identity and the Actions raw_path. Returned source
    paths identify every command. Unsupported fields retain their original values
    in unresolved diagnostics; a caller must not render them as verified silence.
    """
    result = {'events': [], 'unresolved': [], 'frame_actions': []}
    path = (source or {}).get('raw_path', '$.Actions')
    actions, path = _decoded(actions, result, source, path)
    if not isinstance(actions, list):
        _issue(result, 'invalid_actions', source, path, actions)
        return result
    for index, action in enumerate(actions):
        if not isinstance(action, dict) or action.get('Name') != 'PlayMovie':
            for nested_path, nested in _nested_movies(action, f'{path}[{index}]'):
                if video_name is None or _movie_name(nested) == video_name:
                    _issue(result, 'nested_movie_requires_runtime_context', source, nested_path, nested)
            continue
        _movie(action, video_name, source, f'{path}[{index}]', result)
    return result


def discover_launcher_frame_audio(frame_events, *, video_name, source=None):
    """The verified native VideoLauncher.FrameEvents list uses the same schema."""
    result = {'events': [], 'unresolved': [], 'frame_actions': []}
    _frames(frame_events, video_name, source, (source or {}).get('raw_path', 'VideoLauncher.FrameEvents'), result)
    return result


discover_movie_audio = discover_frame_audio


def movie_frame_bindings(config_directory: Path, name: str) -> list[dict]:
    """Read every exact named PlayMovie context, without merging their timelines.

    Native FlowState.Actions is the UTF-8 string at vtable offset16 in the
    verified client db_flowState.db. A missing/malformed source is not silence.
    """
    if not isinstance(name, str) or not name:
        raise ValueError('Select a movie name')
    database = Path(config_directory) / 'db_flowState.db'
    if not database.is_file():
        raise FileNotFoundError('Frame-event discovery requires db_flowState.db')
    if database.stat().st_size > 512 * 1024**2:
        raise ValueError('Frame-event source database exceeds512MiB bound')
    deadline = time.monotonic() + 10
    with database.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    result = []
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
        db.execute('PRAGMA query_only=ON')
        db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        cursor = db.execute('SELECT StateKey, BinData FROM flowstate WHERE instr(BinData, ?) > 0 ORDER BY StateKey',
                            (name.encode('utf-8'),))
        for state_key, blob in cursor:
            source = {'file': 'db_flowState.db', 'database_sha256': digest, 'state_key': state_key,
                      'record_sha256': hashlib.sha256(blob).hexdigest(), 'actions_vtable_offset': 16,
                      'raw_path': 'flowstate[' + json.dumps(state_key, ensure_ascii=False) + '].Actions'}
            try:
                table = Table(blob, struct.unpack_from('<I', blob)[0])
                field = table.Offset(16)
                if not field:
                    raise ValueError('Missing FlowState.Actions')
                actions = json.loads(table.String(table.Pos + field).decode('utf-8'))
                if not isinstance(actions, list):
                    raise ValueError('FlowState.Actions must be an array')
            except (ValueError, TypeError, IndexError, struct.error, UnicodeError) as error:
                result.append({'video_name': name, 'source': source, 'events': [], 'frame_actions': [],
                    'unresolved': [{'code': 'invalid_native_actions', 'source': source,
                                    'error': type(error).__name__}], 'complete': False})
                continue
            for index, action in enumerate(actions):
                if not isinstance(action, dict) or action.get('Name') != 'PlayMovie':
                    for nested_path, nested in _nested_movies(action, source['raw_path'] + f'<json>[{index}]'):
                        if _movie_name(nested) == name:
                            unresolved = {'events': [], 'unresolved': [], 'frame_actions': []}
                            _issue(unresolved, 'nested_movie_requires_runtime_context', source, nested_path, nested)
                            result.append({**unresolved, 'video_name': name, 'source': source,
                                           'action_index': index, 'raw_action': deepcopy(action), 'complete': False})
                    continue
                params = action.get('Params')
                if isinstance(params, str):
                    try:
                        params = json.loads(params)
                    except ValueError as error:
                        raise ValueError('Malformed PlayMovie.Params in candidate source') from error
                if not isinstance(params, dict):
                    raise ValueError('Missing PlayMovie.Params in candidate source')
                if params.get('VideoName') != name:
                    continue
                binding = {'events': [], 'unresolved': [], 'frame_actions': []}
                _movie(action, name, source, source['raw_path'] + f'<json>[{index}]', binding)
                result.append({**binding, 'video_name': name, 'source': source, 'action_index': index,
                               'raw_action': deepcopy(action), 'complete': not binding['unresolved']})
    return result
