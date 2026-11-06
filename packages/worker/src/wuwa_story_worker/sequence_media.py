"""Keep authored movie fragments and subsequences separate from full cinematics."""

from pathlib import PurePosixPath


def movie_archive_path(url):
    """Accept only local cooked movie URLs, never remote URLs or traversal."""
    if not isinstance(url, str) or not url.startswith('filepath://./Aki/Movies/'):
        return None
    relative = url.removeprefix('filepath://./')
    if ('\\' in relative or ':' in relative or '%' in relative or '?' in relative
            or '#' in relative or '..' in relative.split('/')
            or PurePosixPath(relative).suffix.lower() != '.mp4'):
        return None
    return 'Client/Content/' + relative


def sequence_media_score(exports):
    # Imports stay local to reuse the existing cooked-path normalization without
    # coupling sequence_controls' public entry point to parser initialization.
    from wuwa_story_worker.sequence_controls import object_path

    def audio_event_path(value):
        if isinstance(value, dict):
            if value.get('SubPathString'):
                return None
            value = value.get('ObjectPath') or value.get('AssetPathName')
        path = object_path(value)
        if not path or path.endswith('/') or any(c in path for c in ('\\', '?', '#', '%', ':')):
            return None
        package, dot, name = path.rpartition('.')
        if dot:
            if name != PurePosixPath(package).name:
                return None
            return package
        return path

    by_id = {f'{e["Package"]}.{e["_export_index"]}': e for e in exports}
    streams = {}
    for export in exports:
        if export.get('Type') == 'StreamMediaSource':
            streams.setdefault(export['Package'], []).append(export)

    def owner_scene(export):
        seen = set()
        while export and export.get('Type') != 'MovieScene':
            parent = object_path((export.get('Outer') or {}).get('ObjectPath'))
            if not parent or parent in seen:
                return None
            seen.add(parent)
            export = by_id.get(parent)
        return export

    def scene_clock(scene):
        rate = (scene or {}).get('Properties', {}).get('TickResolution') or {}
        numerator, denominator = rate.get('Numerator'), rate.get('Denominator', 1)
        return ({'numerator': numerator, 'denominator': denominator}
                if type(numerator) is int and numerator > 0
                and type(denominator) is int and denominator > 0 else None)

    def track_binding(scene, track):
        if not scene or not track:
            return None
        properties = scene.get('Properties') or {}
        source = f'{track["Package"]}.{track["_export_index"]}'
        bindings = [binding for binding in properties.get('ObjectBindings', [])
                    if any(object_path(ref.get('ObjectPath')) == source
                           for ref in binding.get('Tracks', []) if isinstance(ref, dict))]
        if len(bindings) != 1:
            return None
        binding = bindings[0]
        spawnable = next((item for item in properties.get('Spawnables', [])
                          if item.get('Guid') == binding.get('ObjectGuid')), {})
        return {'guid': binding.get('ObjectGuid'), 'name': binding.get('BindingName'),
                'object': spawnable.get('ObjectTemplate')}

    # Bind only through actual post-process objects and material parents. A movie
    # filename, black background, or dissolve-like name is not a compositing rule.
    materials = {}
    for export in exports:
        if export.get('Type') in {'Material', 'MaterialInstanceConstant'}:
            materials.setdefault(export['Package'], []).append(export)

    def material_chain(reference):
        path = object_path((reference or {}).get('ObjectPath'))
        chain, seen = [], set()
        while path and path not in seen:
            seen.add(path)
            matches = [by_id[path]] if path in by_id else materials.get(path, [])
            if len(matches) != 1 or matches[0].get('Type') not in {'Material', 'MaterialInstanceConstant'}:
                break
            material = matches[0]
            chain.append(material)
            path = object_path((material.get('Properties', {}).get('Parent') or {}).get('ObjectPath'))
        return chain

    def media_bindings(scene, texture):
        if not scene or not texture:
            return []
        result = []
        for candidate in exports:
            if candidate.get('Type') not in {'PostProcessVolume', 'KuroPostProcessVolume'} or owner_scene(candidate) is not scene:
                continue
            blends = ((candidate.get('Properties', {}).get('Settings') or {}).get('WeightedBlendables') or {}).get('Array', [])
            for blend in blends:
                chain = material_chain(blend.get('Object'))
                if not chain or chain[-1].get('Properties', {}).get('MaterialDomain') != 'MD_PostProcess':
                    continue
                parameters, ambiguous = {}, False
                for material in chain:  # Child first: an override shadows the same parent parameter.
                    for value in material.get('Properties', {}).get('TextureParameterValues', []):
                        info = value.get('ParameterInfo') or {}
                        if (not isinstance(info, dict) or not isinstance(info.get('Name'), str)
                                or not info['Name'] or type(info.get('Association')) not in {str, int}
                                or type(info.get('Index')) is not int):
                            ambiguous = True
                            break
                        key = (info['Name'], info['Association'], info['Index'])
                        parameters.setdefault(key, value.get('ParameterValue'))
                if ambiguous:
                    continue
                textures = [ref for ref in parameters.values() if isinstance(ref, dict)]
                if not parameters:
                    # Cached texture lists do not identify parameter names. They
                    # prove inheritance only when no override can replace a default.
                    textures = [ref for material in chain for ref in
                                (material.get('Properties', {}).get('CachedExpressionData') or {}).get('ReferencedTextures', [])
                                if isinstance(ref, dict)]
                if not any(object_path(ref.get('ObjectPath')) == texture for ref in textures):
                    continue
                result.append({'source': f'{candidate["Package"]}.{candidate["_export_index"]}',
                               'material': blend.get('Object'), 'weight': blend.get('Weight'),
                               'material_chain': [f'{item["Package"]}.{item["_export_index"]}' for item in chain],
                               'material_domain': 'MD_PostProcess',
                               'blend_mode': chain[-1].get('Properties', {}).get('BlendMode')})
        return result

    sections = []
    for export in exports:
        kind = export.get('Type')
        if kind not in {'MovieSceneMediaSection', 'MovieSceneSubSection', 'MovieSceneCinematicShotSection',
                        'MovieSceneSkeletalAnimationSection', 'MovieSceneCameraCutSection',
                        'MovieSceneSpawnSection', 'MovieSceneDialogueSection', 'MovieSceneEventTriggerSection',
                        'MovieSceneKuroAudioEventSection'}:
            continue
        props = export.get('Properties') or {}
        scene = owner_scene(export)
        clock = scene_clock(scene)
        reference = props.get('MediaSource' if kind == 'MovieSceneMediaSection' else 'SubSequence') or {}
        target = object_path(reference.get('ObjectPath')) if isinstance(reference, dict) else None
        track = by_id.get(object_path((export.get('Outer') or {}).get('ObjectPath')))
        binding = track_binding(scene, track)
        camera_object = (binding or {}).get('object') or {}
        if kind == 'MovieSceneSpawnSection' and not camera_object.get('ObjectName', '').startswith("CineCameraActor'"):
            continue
        section_kind = {'MovieSceneMediaSection': 'movie_fragment',
                        'MovieSceneSkeletalAnimationSection': 'skeletal_animation',
                        'MovieSceneCameraCutSection': 'camera', 'MovieSceneSpawnSection': 'camera',
                        'MovieSceneKuroAudioEventSection': 'audio_event',
                        'MovieSceneDialogueSection': 'dialogue', 'MovieSceneEventTriggerSection': 'event'}.get(kind, 'subsequence')
        section = {
            'source': f'{export["Package"]}.{export["_export_index"]}',
            'kind': section_kind,
            'reference': target, 'clock': clock,
            'scene_source': f'{scene["Package"]}.{scene["_export_index"]}' if scene else None,
            'range': props.get('SectionRange'),
            'parameters': {key: props[key] for key in ('StartFrameOffset', 'Parameters', 'Params', 'bLooping') if key in props},
        }
        if binding:
            section['binding'] = binding
        if kind == 'MovieSceneMediaSection':
            texture = object_path((props.get('MediaTexture') or {}).get('ObjectPath'))
            section['media_texture'] = props.get('MediaTexture')
            section['track_tags'] = (track or {}).get('Properties', {}).get('TrackTags', [])
            tags = {tag for tag in section['track_tags'] if isinstance(tag, str)} & {'Male', 'Female'}
            if len(tags) == 1:
                section['rover'] = 'male' if 'Male' in tags else 'female'
            bindings = media_bindings(scene, texture)
            if bindings:
                section.update(composition_role='post_process_layer', material_bindings=bindings)
            candidates = [by_id[target]] if target in by_id else streams.get(target, [])
            candidates = [e for e in candidates if e.get('Type') == 'StreamMediaSource']
            stream = candidates[0] if len(candidates) == 1 else None
            path = movie_archive_path((stream or {}).get('Properties', {}).get('StreamUrl'))
            section['archive_path'] = path
            section['status'] = 'resolved' if path else 'unresolved_media_source'
        elif section_kind == 'subsequence':
            section['status'] = 'referenced' if target else 'unresolved_subsequence'
        else:
            section['status'] = 'authored'
            if section_kind == 'skeletal_animation':
                section['reference'] = object_path(((props.get('Params') or {}).get('Animation') or {}).get('ObjectPath'))
            elif section_kind == 'camera':
                section['coverage'] = 'spawn' if kind == 'MovieSceneSpawnSection' else 'camera_cut'
                section['camera_binding'] = props.get('CameraBindingID') or camera_object
            elif section_kind == 'dialogue':
                section['text_key'] = (props.get('DialogueInfo') or {}).get('SourceString')
                section['dialogue_origin'] = 'cinematic'
            elif section_kind == 'event':
                section['event_channel'] = props.get('EventChannel')
            elif section_kind == 'audio_event':
                hard = audio_event_path(props.get('AudioEvent'))
                soft = audio_event_path(props.get('SoftAudioEvent'))
                section['audio_event'] = props.get('AudioEvent')
                section['soft_audio_event'] = props.get('SoftAudioEvent')
                section['parameters'].update({key: props[key] for key in ('bSyncAudio', 'FadeDuration') if key in props})
                if hard and soft and hard != soft:
                    section['reference'] = None
                    section['status'] = 'unresolved_audio_event'
                    section['diagnostics'] = [{'code': 'conflicting_audio_event_references'}]
                else:
                    section['reference'] = hard or soft
                    section['status'] = 'referenced' if section['reference'] else 'unresolved_audio_event'
                    if section['reference']:
                        section['reference_field'] = 'AudioEvent' if hard else 'SoftAudioEvent'
        sections.append(section)
    scenes = [{'source': f'{export["Package"]}.{export["_export_index"]}',
               'sequence': (export.get('Outer') or {}).get('ObjectPath'),
               'range': export.get('Properties', {}).get('PlaybackRange'),
               'clock': scene_clock(export)}
              for export in exports if export.get('Type') == 'MovieScene']
    return {'sections': sections, 'scenes': scenes, 'complete_video': False,
            'timing_basis': 'owning_movie_scene_tick_resolution',
            'runtime_traversal': False}
