from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story.story import service


@pytest.mark.asyncio
@pytest.mark.parametrize(('resolution', 'content', 'alias', 'hidden', 'label'), [
    ('resolved_empty', '', None, True, ''),
    ('resolved_empty', 'An older name', None, True, ''),
    ('missing_key', None, None, False, 'speaker:83'),
    ('resolved_nonempty', '???', None, False, '???'),
    ('resolved_empty', '', 'Confirmed name', False, 'Confirmed name'),
])
async def test_authored_empty_speaker_name_is_not_a_technical_fallback(monkeypatch, resolution, content, alias, hidden, label):
    node = SimpleNamespace(id=83, canonical_key='speaker:83', slug=None, status='active')
    session = SimpleNamespace(info={
        'story_locales': {'en': 1},
        'story_releases': {21: SimpleNamespace(id=21, game_version='3.7.0', sequence=21)},
        'wuwa_localized_cache': {(89, 'en', 21): {
            'content': content, 'resolution': 'resolved_nonempty' if content else resolution,
            'requested_resolution': resolution,
        }},
    })
    monkeypatch.setattr(service.repository, 'node_label_rows', AsyncMock(return_value=(
        [(node, 'speaker', 89, None)], {83: alias} if alias else {})))
    await service.prefetch_node_labels(session, [83], 'en')
    result = session.info['story_node_labels'][(83, 'en')]
    assert result.get('name_hidden', False) is hidden
    assert result['label'] == label
    assert result['canonical_key'] == 'speaker:83'
