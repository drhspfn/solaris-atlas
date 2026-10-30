"""Present source-backed runtime objectives without leaking raw action payloads."""

from wuwa_story.db.repositories import story_browsing as repository
from wuwa_story.story.service import _localized, prefetch_localizations


async def present_blocks(session, tree_order, locale, release_id):
    if not tree_order:
        return []
    blocks = tree_order.get('blocks', [])
    keys = await repository.named_localization_keys(session, {block['text_key'] for block in blocks
                                                            if block.get('text_key')})
    await prefetch_localizations(session, list(keys.values()), locale, release_id)
    result = []
    for block in blocks:
        localized = await _localized(session, keys[block['text_key']], locale, release_id) if block['text_key'] in keys else None
        result.append({**block, 'title': (localized or {}).get('content') or block['title']})
    return result
