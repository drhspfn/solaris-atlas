"""Present source-backed runtime objectives without leaking raw action payloads."""

from wuwa_story.db.repositories import story_browsing as repository
from wuwa_story.story.service import _localized, prefetch_localizations


async def present_blocks(session, tree_order, locale, release_id):
    if not tree_order:
        return []
    blocks = tree_order.get('blocks', [])
    entries = [entry for block in blocks for entry in (block, *block.get('interactions', []))]
    keys = await repository.named_localization_keys(session, {entry['text_key'] for entry in entries
                                                            if entry.get('text_key')})
    await prefetch_localizations(session, list(keys.values()), locale, release_id)
    result = []
    async def present(entry):
        key = entry.get('text_key')
        localized = await _localized(session, keys[key], locale, release_id) if key in keys else None
        return {**entry, 'title': (localized or {}).get('content') or entry['title']}

    for block in blocks:
        result.append({**await present(block), 'interactions': [await present(item) for item in block.get('interactions', [])]})
    return result
