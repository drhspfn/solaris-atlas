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

    owners = {flow['state_node_id'] for node in encounter_nodes(blocks)
              for flow in node.get('flow_states', []) if flow.get('state_node_id')}
    selected = {}
    for owner, metadata in await media_repository.bundles(session, owners, release_id):
        media = (metadata or {}).get('sequence_media')
        if isinstance(media, dict) and media.get('schema_version') == 1:
            selected.setdefault(owner, media)
    identities = {section['file_id'] for media in selected.values()
                  for section in media.get('sections', [])
                  if section.get('kind') == 'movie_fragment' and section.get('status') == 'published'
                  and type(section.get('file_id')) is int}
    if not identities:
        return {}
    settings = get_settings()
    locations = await media_repository.available_movies(session, identities, settings.s3_bucket)
    storage = S3Storage(settings)
    return {owner: {**media, 'complete_video': False, 'sections': [
        {**section, 'url': storage.public_url(locations[section['file_id']])}
        for section in media.get('sections', [])
        if section.get('kind') == 'movie_fragment' and section.get('status') == 'published'
        and section.get('file_id') in locations
    ]} for owner, media in selected.items()}


async def present_blocks(session, tree_order, locale, release_id):
    if not tree_order:
        return []
    blocks = tree_order.get('blocks', [])
    entries = [entry for block in blocks for entry in (block, *block.get('interactions', []))]
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

    async def present_node(node):
        return {**await present(node),
                'children': [await present_node(child) for child in node.get('children', [])],
                'flow_states': [{**flow, **({'sequence_media': playback[flow['state_node_id']]}
                    if flow.get('state_node_id') in playback else {})}
                    for flow in node.get('flow_states', [])]}

    for block in blocks:
        presented = {**await present(block), 'interactions': [await present(item) for item in block.get('interactions', [])]}
        if block.get('encounter'):
            presented['encounter'] = {**block['encounter'], 'trees': [
                {**tree, 'root': await present_node(tree['root'])}
                for tree in block['encounter']['trees']]}
        result.append(presented)
    return result
