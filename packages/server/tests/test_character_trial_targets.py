from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story.ingestion.entity_media import MediaRequest, entity_media_targets


@pytest.mark.asyncio
async def test_character_trial_target_uses_own_persisted_resource_paths():
    session = AsyncMock()
    session.scalar.side_effect = [4, SimpleNamespace(id=25), {'Id': 1503}, {
        'trial_presentation': {'role_id': 1503, 'skeleton': '/Game/Chixia/Skeleton.Skeleton',
                               'atlas': '/Game/Chixia/Atlas.Atlas'}}]
    session.scalars.return_value = []
    request = MediaRequest(download_id='a' * 64, asset_version='3.7.0', game_version='3.7.0',
                           entities=['character:1503'], kinds=['image'])
    targets = await entity_media_targets(session, request)
    assert targets == [{'owner': 25, 'release_id': 4, 'path': 'character_trial:1503',
                        'resource_id': None, 'kind': 'spine', 'language': None,
                        'reference_type': 'character_trial_spine',
                        'spine_recipe': targets[0]['spine_recipe'],
                        'direct_resources': ['/Game/Chixia/Atlas', '/Game/Chixia/Skeleton']}]
