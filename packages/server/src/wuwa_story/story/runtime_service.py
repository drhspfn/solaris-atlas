"""Present source-backed runtime objectives without leaking raw action payloads."""

from wuwa_story.db.repositories import story_browsing as repository
from wuwa_story.story.service import _localized, prefetch_localizations


def encounter_nodes(blocks):
    def visit(node):
        yield node
        for child in node.get('children', []):
            yield from visit(child)
    for block in blocks:
        for tree in block.get('encounter', {}).get('trees', []):
            yield from visit(tree['root'])


async def sequence_playback(session, blocks, release_id):
    from wuwa_story.config.settings import get_settings
    from wuwa_story.db.repositories import sequence_playback as media_repository
    from wuwa_story.storage.s3 import S3Storage

    expected, conflicts = {}, set()
    for block in blocks:
        encounter = block.get('encounter', {})
        client = encounter.get('client') or {}
        if release_id is None or encounter.get('release_id') != release_id:
            continue
        for node in encounter_nodes([block]):
            for flow in node.get('flow_states', []):
                owner = flow.get('state_node_id')
                proof = (client.get('asset_version'), client.get('download_id'),
                         flow.get('presentation_sha256'))
                if type(owner) is not int or not all(isinstance(value, str) and value for value in proof):
                    continue
                if owner in expected and expected[owner] != proof:
                    conflicts.add(owner)
                expected[owner] = proof
    expected = {owner: proof for owner,
                proof in expected.items() if owner not in conflicts}
    if not expected:
        return {}
    selected = {}
    for owner, file_id, path, metadata in await media_repository.bundles(session, set(expected), release_id):
        if owner not in expected or owner in selected or type(file_id) is not int or not isinstance(metadata, dict):
            continue
        media, proof = metadata.get(
            'sequence_media'), metadata.get('game_source')
        if (not isinstance(media, dict) or type(media.get('schema_version')) is not int
                or media['schema_version'] != 1 or not isinstance(proof, dict)
                or type(proof.get('schema_version')) is not int or proof['schema_version'] != 1
                or media.get('game_source') != proof
                or (proof.get('asset_version'), proof.get('download_id'),
                    proof.get('presentation_sha256')) != expected[owner]
                or any(metadata.get(key) != proof[key] for key in ('asset_version', 'download_id'))
                or not isinstance(path, str) or proof.get('package_path') != path
                or not isinstance(media.get('sections'), list)):
            continue
        authored_kinds = {'subsequence', 'camera',
                          'skeletal_animation', 'dialogue', 'event', 'flow_dialogue', 'audio_event'}
        sections = [section for section in media['sections'] if isinstance(section, dict)
                    and ((section.get('kind') in authored_kinds and isinstance(section.get('source'), str))
                    or (section.get('kind') == 'movie_fragment' and section.get('status') == 'published'
                        and type(section.get('file_id')) is int
                        and all(isinstance(section.get(key), str) and section[key]
                                for key in ('source', 'file', 'archive_path'))))]
        # Choose only among fully matching proofs. A stale newer row cannot mask
        # a valid version, and an unavailable selected bundle never borrows files.
        selected[owner] = {'file_id': file_id, 'source_path': path,
                           'media': {**media, 'sections': sections}}
    if not selected:
        return {}
    settings = get_settings()
    locations = await media_repository.available_movies(session, selected, release_id, settings.s3_bucket)

    def published_tracks(section):
        tracks = section.get('tracks')
        return [track for track in tracks if isinstance(track, dict)
                and track.get('status') == 'published' and type(track.get('file_id')) is int
                and isinstance(track.get('file'), str) and track['file']] if isinstance(tracks, list) else []

    audio_selected = {}
    for owner, bundle in selected.items():
        sections = [{**section, 'tracks': published_tracks(section)}
                    for section in bundle['media']['sections'] if section.get('kind') == 'audio_event']
        if any(section['tracks'] for section in sections):
            audio_selected[owner] = {**bundle, 'media': {**bundle['media'], 'sections': sections}}
    audio_locations = (await media_repository.available_audio(session, audio_selected, release_id, settings.s3_bucket)
                       if audio_selected else {})
    storage = S3Storage(settings)

    def present_section(owner, section):
        result = {key: value for key, value in section.items() if key != 'url'}
        if section.get('kind') == 'movie_fragment':
            result['url'] = storage.public_url(locations[(owner, section['file_id'], section['source'])])
        elif section.get('kind') == 'audio_event' and 'tracks' in section:
            result['tracks'] = [{**track, 'url': storage.public_url(audio_locations[(owner, track['file_id'], section['source'])])}
                                for track in published_tracks(section)
                                if (owner, track['file_id'], section['source']) in audio_locations]
        return result

    return {owner: {**bundle['media'], 'complete_video': False, 'owner_node_id': owner,
                    'release_id': release_id, 'bundle_file_id': bundle['file_id'], 'sections': [
        present_section(owner, section)
        for section in bundle['media']['sections']
        if section.get('kind') != 'movie_fragment' or (owner, section['file_id'], section['source']) in locations
    ]} for owner, bundle in selected.items()}


async def present_blocks(session, tree_order, locale, release_id):
    if not tree_order:
        return []
    blocks = tree_order.get('blocks', [])
    entries = [entry for block in blocks for entry in (
        block, *block.get('interactions', []))]
    entries.extend(encounter_nodes(blocks))
    keys = await repository.named_localization_keys(session, {entry['text_key'] for entry in entries
                                                              if entry.get('text_key')})
    await prefetch_localizations(session, list(keys.values()), locale, release_id)
    result = []
    playback = await sequence_playback(session, blocks, release_id)

    async def present(entry):
        key = entry.get('text_key')
        localized = await _localized(session, keys[key], locale, release_id) if key in keys else None
        return {**entry, 'title': (localized or {}).get('content') or entry['title']}

    async def present_node(node, encounter):
        flows = []
        for flow in node.get('flow_states', []):
            media = playback.get(flow.get('state_node_id'))
            proof = (media or {}).get('game_source') or {}
            client = encounter.get('client') or {}
            matches = (media is not None and encounter.get('release_id') == release_id
                       and proof.get('asset_version') == client.get('asset_version')
                       and proof.get('download_id') == client.get('download_id')
                       and proof.get('presentation_sha256') == flow.get('presentation_sha256'))
            if matches:
                media = {**media, 'flow_context': {
                    **{key: flow.get(key) for key in ('state_node_id', 'state', 'phase', 'async', 'timing', 'execution_context', 'source')},
                    'node_owner': node.get('owner'), 'node_condition': node.get('condition'),
                    'branch_condition': node.get('branch_condition'),
                    'boundaries': flow.get('cinematics', []),
                    'markers': [{'kind': 'cinematic_entry' if edge == 'start' else 'cinematic_exit',
                                 'source': boundary.get('source'), 'reference': boundary.get('reference'),
                                 'clock': None, **boundary[edge]}
                                for boundary in flow.get('cinematics', []) for edge in ('start', 'end')
                                if isinstance(boundary.get(edge), dict)],
                    'timing_basis': 'authored_action_extent', 'global_clock': None}}
            flows.append(
                {**flow, **({'sequence_media': media} if matches else {})})
        return {**await present(node),
                'children': [await present_node(child, encounter) for child in node.get('children', [])],
                'flow_states': flows}

    for block in blocks:
        presented = {**await present(block), 'interactions': [await present(item) for item in block.get('interactions', [])]}
        if block.get('encounter'):
            presented['encounter'] = {**block['encounter'], 'trees': [
                {**tree, 'root': await present_node(tree['root'], block['encounter'])}
                for tree in block['encounter']['trees']]}
        result.append(presented)
    return result
