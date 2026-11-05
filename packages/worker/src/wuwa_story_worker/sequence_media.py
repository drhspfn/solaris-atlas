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

    sections = []
    for export in exports:
        kind = export.get('Type')
        if kind not in {'MovieSceneMediaSection', 'MovieSceneSubSection', 'MovieSceneCinematicShotSection'}:
            continue
        props = export.get('Properties') or {}
        scene = owner_scene(export)
        rate = (scene or {}).get('Properties', {}).get('TickResolution') or {}
        numerator, denominator = rate.get('Numerator'), rate.get('Denominator', 1)
        clock = ({'numerator': numerator, 'denominator': denominator}
                 if type(numerator) is int and numerator > 0
                 and type(denominator) is int and denominator > 0 else None)
        reference = props.get('MediaSource' if kind == 'MovieSceneMediaSection' else 'SubSequence') or {}
        target = object_path(reference.get('ObjectPath')) if isinstance(reference, dict) else None
        section = {
            'source': f'{export["Package"]}.{export["_export_index"]}',
            'kind': 'movie_fragment' if kind == 'MovieSceneMediaSection' else 'subsequence',
            'reference': target, 'clock': clock,
            'scene_source': f'{scene["Package"]}.{scene["_export_index"]}' if scene else None,
            'range': props.get('SectionRange'),
            'parameters': {key: props[key] for key in ('StartFrameOffset', 'Parameters', 'bLooping') if key in props},
        }
        if kind == 'MovieSceneMediaSection':
            candidates = [by_id[target]] if target in by_id else streams.get(target, [])
            candidates = [e for e in candidates if e.get('Type') == 'StreamMediaSource']
            stream = candidates[0] if len(candidates) == 1 else None
            path = movie_archive_path((stream or {}).get('Properties', {}).get('StreamUrl'))
            section['archive_path'] = path
            section['status'] = 'resolved' if path else 'unresolved_media_source'
        else:
            section['status'] = 'referenced' if target else 'unresolved_subsequence'
        sections.append(section)
    return {'sections': sections, 'complete_video': False,
            'timing_basis': 'owning_movie_scene_tick_resolution',
            'runtime_traversal': False}
