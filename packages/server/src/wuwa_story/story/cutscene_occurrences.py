"""One quest player per cinematic, retaining every authored invocation."""
from copy import deepcopy


def group_cutscene_occurrences(manifest):
    result = {**manifest, 'events': []}
    groups = {}
    for event in manifest.get('events', []):
        if event.get('kind') != 'cutscene':
            result['events'].append(event)
            continue
        groups.setdefault(event['reference'], []).append(event)
    for entries in groups.values():
        # A mapped invocation determines placement; evidence is never discarded
        # merely because another invocation uses the same published movie.
        ordered = sorted(entries, key=lambda e: (
            not bool(e.get('story_position') or e.get('review_position')),
            e.get('story_position') or e.get('review_position') or [],
            e.get('flow_state', ''), e.get('action_index', 0)))
        primary = deepcopy(ordered[0])
        points = []
        seen = set()
        for entry in ordered:
            for point in entry.get('entry_points') or [entry]:
                identity = (point.get('action'), point.get('interaction_id'),
                            tuple(point.get('story_position') or []))
                if identity in seen:
                    continue
                seen.add(identity)
                points.append({k: deepcopy(v) for k, v in point.items()
                               if k not in {'playback', 'resources', 'captions',
                                            'transcript_states', 'entry_points'}})
        if len(points) > 1:
            primary['entry_points'] = points
        primary['transcript_states'] = sorted({state for e in ordered
                                               for state in e.get('transcript_states', [])})
        result['events'].append(primary)
    # Query order is a technical state-key order. Grouping invocations must not
    # leak that order into the scene navigation once authored positions exist.
    # Unmapped events retain their stable discovery order, after mapped events.
    result['events'].sort(key=lambda event: (
        not bool(event.get('story_position') or event.get('review_position')),
        event.get('story_position') or event.get('review_position') or []))
    return result
