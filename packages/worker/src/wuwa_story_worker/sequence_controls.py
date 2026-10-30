"""Source-backed MovieScene events and dialogue; no clip-name chronology guesses."""

import json

from wuwa_story_worker.sequence_bytecode import resolve_event_calls


def object_path(value):
    if not isinstance(value, str):
        return None
    if '/Client/Content/' in value:
        value = '/Game/' + value.split('/Client/Content/', 1)[1]
    elif value.startswith('Client/Content/'):
        value = '/Game/' + value.removeprefix('Client/Content/')
    if not value.startswith('/Game/') or '..' in value.split('/'):
        return None
    return value


def exported_controls(assets):
    exports = []
    for package, asset in sorted(assets.items()):
        if asset.get('decode_failed'):
            continue
        for path in sorted(asset['decoded'].rglob('*.json')):
            values = json.loads(path.read_text(encoding='utf-8-sig'))
            if not isinstance(values, list) or not any(isinstance(v, dict) and v.get('Type') for v in values):
                continue
            # Normalize extraction-root prefixes without confusing packages that
            # happen to have identically named functions or event tracks.
            def normalize(value):
                if isinstance(value, dict):
                    return {key: object_path(child) or child if key == 'ObjectPath' else normalize(child)
                            for key, child in value.items()}
                if isinstance(value, list):
                    return [normalize(child) for child in value]
                return value
            for index, value in enumerate(values):
                if isinstance(value, dict) and value.get('Type'):
                    exports.append({**normalize(value), 'Package': package, '_export_index': index})
    return exports


def sequence_control_score(assets, presentation=None):
    exports = exported_controls(assets)
    by_id = {f'{e["Package"]}.{e["_export_index"]}': e for e in exports}
    events, dialogues, unresolved, preloads = [], [], [], []

    def movie_scene(export):
        seen = set()
        while export and export.get('Type') != 'MovieScene':
            owner = object_path((export.get('Outer') or {}).get('ObjectPath'))
            if owner in seen:
                return None
            seen.add(owner)
            export = by_id.get(owner)
        return export

    for export in exports:
        kind, props = export.get('Type'), export.get('Properties', {})
        if kind == 'BP_SequenceData_Generated_C':
            keys = props.get('PreloadUiArray') or []
            for index, key in enumerate(keys if isinstance(keys, list) else []):
                if isinstance(key, str) and key:
                    preloads.append({'prefab_key': key, 'rover': None, 'animation': None,
                                     'usage': 'preload_only',
                                     'source': f'{export["Package"]}.{export["_export_index"]}.Properties.PreloadUiArray[{index}]'})
        if kind not in {'MovieSceneEventTriggerSection', 'MovieSceneDialogueSection'}:
            continue
        scene = movie_scene(export)
        rate = (scene or {}).get('Properties', {}).get('TickResolution') or {}
        # FFrameRate's native default denominator is 1; tagged properties omit it.
        numerator, denominator = rate.get('Numerator'), rate.get('Denominator', 1)
        valid_rate = type(numerator) is int and numerator > 0 and type(denominator) is int and denominator > 0
        clock = {'numerator': numerator, 'denominator': denominator} if valid_rate else None
        source = f'{export["Package"]}.{export["_export_index"]}'
        scene_source = f'{scene["Package"]}.{scene["_export_index"]}' if scene else None
        if kind == 'MovieSceneDialogueSection':
            text_key = (props.get('DialogueInfo') or {}).get('SourceString')
            talks = [cue for cue in (presentation or {}).get('cues', [])
                     if cue['kind'] == 'dialogue' and cue.get('text_key') == text_key and text_key]
            dialogues.append({'source': source, 'scene_source': scene_source, 'clock': clock, 'command': props,
                              'text_key': text_key, 'talk_sources': [cue['source_path'] for cue in talks]})
            continue
        channel = props.get('EventChannel') or {}
        times, values = channel.get('KeyTimes', []), channel.get('KeyValues', [])
        if not isinstance(times, list) or not isinstance(values, list) or len(times) != len(values):
            unresolved.append({'source': source, 'code': 'mismatched_event_channel'})
            continue
        for index, (time, value) in enumerate(zip(times, values, strict=True)):
            frame = time.get('Value') if isinstance(time, dict) else None
            function = ((value.get('Ptrs') or {}).get('Function')) if isinstance(value, dict) else None
            result = resolve_event_calls(exports, function) if function else {
                'calls': [], 'diagnostics': [{'code': 'unbound_event_function'}]}
            events.append({'source': source, 'scene_source': scene_source, 'key_index': index, 'frame': frame, 'clock': clock,
                           'time_seconds': frame * denominator / numerator if valid_rate and type(frame) is int else None,
                           'function': function, **result})
    return {'format': 'solaris.sequence-controls.v1', 'events': events, 'dialogues': dialogues,
            'unresolved': unresolved, 'preloads': preloads,
            'flow_audio_actions': [cue for cue in (presentation or {}).get('cues', []) if cue['kind'] == 'audio_action'],
            'timing_basis': 'owning_movie_scene_tick_resolution',
            'runtime_traversal': False}


def opened_ui_resources(score):
    """Only executed open commands; a preload or string literal is not playback."""
    result = []
    for event in score['events']:
        for call in event['calls']:
            target = call.get('receiver') or {}
            reference = target.get('reference') if isinstance(target, dict) else None
            receiver = reference.get('ObjectPath') if isinstance(reference, dict) else None
            if call['function'] not in {'OpenUiView', 'OpenUiViewInArray'} or receiver != '/Game/Aki/TypeScript/Game/Module/Sequence/PlotBlueprintFunctionLibrary':
                continue
            args = call['arguments']
            if len(args) < (5 if call['function'] == 'OpenUiView' else 4):
                continue
            for rover, key, authored in [('male', args[0], args[2]), ('female', args[1], args[3])]:
                clips = ([{'Name': authored, 'NeedLoop': args[4]}] if call['function'] == 'OpenUiView'
                         else authored if isinstance(authored, list) else [])
                # Unknown/empty arrays still prove the UI key was opened, but
                # do not justify substituting a default animation from ConfigDB.
                for clip in clips or [{}]:
                    if not isinstance(key, str) or not key or not isinstance(clip, dict):
                        continue
                    animation, loop = clip.get('Name'), clip.get('NeedLoop')
                    value = {'rover': rover, 'prefab_key': key,
                             'animation': animation if isinstance(animation, str) and animation else None,
                             'loop': loop if type(loop) is bool else None,
                             'source': event['source'], 'frame': event['frame']}
                    if value not in result:
                        result.append(value)
    return result
