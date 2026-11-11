"""Read authored Unreal montage timelines without inventing section traversal."""
import math


def package_path(value):
    if not isinstance(value, str) or not value:
        raise ValueError('Animation reference has no path')
    path = value.replace('\\', '/').split('.', 1)[0]
    if path.casefold().startswith('/game/'):
        path = 'Client/Content/' + path[6:]
    if path.startswith('/') or any(part in ('', '.', '..') for part in path.split('/')):
        raise ValueError(f'Unsafe animation reference: {value}')
    return path.casefold()


def number(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'Invalid {field}: {value!r}')
    return float(value)


def parse_montage(row, sequence_index):
    """Return a finite authored timeline plus the independent section graph.

    sequence_index maps normalized package paths to verified source records. Each
    sequence record must supply file, sha256, duration and matches_skeleton.
    """
    if row.get('Type') != 'AnimMontage':
        raise ValueError('Expected AnimMontage')
    p = row.get('Properties') or {}
    length = number(p.get('SequenceLength'), 'SequenceLength')
    if length <= 0:
        raise ValueError('Montage duration must be positive')
    tracks = p.get('SlotAnimTracks') or []
    if len(tracks) != 1:
        raise ValueError('Multiple or missing montage slots require explicit blending')
    segments = (tracks[0].get('AnimTrack') or {}).get('AnimSegments') or []
    if not segments:
        raise ValueError('Montage has no segments')
    result = []
    last_end = 0.0
    for segment in segments:
        ref = package_path((segment.get('AnimReference') or {}).get('ObjectPath'))
        sequence = sequence_index.get(ref)
        if not sequence or not sequence.get('matches_skeleton') or not sequence.get('file') or not sequence.get('sha256'):
            raise ValueError(f'Missing or incompatible montage sequence: {ref}')
        start = number(segment.get('StartPos'), 'StartPos')
        anim_start = number(segment.get('AnimStartTime'), 'AnimStartTime')
        anim_end = number(segment.get('AnimEndTime'), 'AnimEndTime')
        rate = number(segment.get('AnimPlayRate', 1), 'AnimPlayRate')
        loops = segment.get('LoopingCount', 1)
        if isinstance(loops, bool) or not isinstance(loops, int) or loops < 1 or rate == 0:
            raise ValueError('Invalid segment looping count or play rate')
        duration = number(sequence.get('duration'), 'sequence duration')
        if start < 0 or anim_start < 0 or anim_end <= anim_start or anim_end > duration + 1e-4:
            raise ValueError(f'Segment out of bounds: {ref}')
        if abs(start - last_end) > 1e-4:
            raise ValueError(f'Montage has unsupported gap or overlap at {start}')
        end = start + (anim_end - anim_start) * loops / abs(rate)
        result.append({'reference': ref, 'file': sequence['file'], 'sha256': sequence['sha256'],
                       'start': start, 'end': end, 'anim_start': anim_start, 'anim_end': anim_end,
                       'rate': rate, 'loops': loops})
        last_end = end
    if abs(last_end - length) > 1e-4:
        raise ValueError('Montage segments disagree with authored duration')
    sections = []
    for section in p.get('CompositeSections') or []:
        if section.get('LinkMethod', 'EAnimLinkMethod::Absolute') != 'EAnimLinkMethod::Absolute':
            raise ValueError('Non-absolute montage section timing requires resolution')
        name, next_name = section.get('SectionName'), section.get('NextSectionName')
        at = number(section.get('LinkValue'), 'section LinkValue')
        if not isinstance(name, str) or not name or not isinstance(next_name, str) or at < 0 or at > length + 1e-4:
            raise ValueError('Invalid montage section')
        sections.append({'name': name, 'next': None if next_name == 'None' else next_name, 'time': at})
    names = [s['name'] for s in sections]
    if len(names) != len(set(names)):
        raise ValueError('Duplicate montage section names')
    unresolved = sorted({s['next'] for s in sections if s['next'] is not None and s['next'] not in names})
    return {'name': row['Name'], 'duration': length, 'slot': tracks[0].get('SlotName'),
            'segments': result, 'sections': sections, 'unresolved_sections': unresolved,
            'blend_in': p.get('BlendIn'), 'blend_out': p.get('BlendOut'),
            'notifies': p.get('Notifies', []), 'source_sections': p.get('CompositeSections', []),
            'playback': 'authored_linear_preview'}


def segment_samples(segment, source_times):
    """Map clip keys and exact trim boundaries into montage seconds."""
    start, end, rate, loops = (segment[k] for k in ('anim_start', 'anim_end', 'rate', 'loops'))
    inside = sorted({start, end, *(t for t in source_times if start < t < end)})
    duration = (end - start) / abs(rate)
    result = []
    for loop in range(loops):
        for source in (inside if rate > 0 else reversed(inside)):
            delta = source - start if rate > 0 else end - source
            result.append((segment['start'] + loop * duration + delta / abs(rate), source))
    return result
