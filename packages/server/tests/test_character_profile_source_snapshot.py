from types import SimpleNamespace as Row
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from wuwa_story.api.routes.story import profiles


def setup_profile(monkeypatch, release):
    character = Row(canonical_name='Phrolova', name_key_id=None, description=None,
                    playable=True, metadata_json={'role_type': 1})
    session = Row(get=AsyncMock(return_value=character), execute=AsyncMock(return_value=[]),
                  scalar=AsyncMock(return_value=None), scalars=AsyncMock(return_value=[]))
    monkeypatch.setattr(profiles, 'get_node', AsyncMock(return_value=Row(
        id=2486699, canonical_key='character:1608', slug='2486699-phrolova')))
    monkeypatch.setattr(profiles, '_release_id', AsyncMock(return_value=release))
    monkeypatch.setattr(profiles, 'character_family', AsyncMock(return_value=([2486699], None)))
    monkeypatch.setattr(profiles, 'prefetch_node_labels', AsyncMock())
    monkeypatch.setattr(profiles, '_localized', AsyncMock(return_value='Phrolova'))
    monkeypatch.setattr(profiles, '_character_progression_materials', AsyncMock(return_value=[]))
    return session


@pytest.mark.asyncio
@pytest.mark.parametrize('version,release,missing', [
    ('3.5.0', 19, False), ('3.5.0', 19, True), (None, 21, False),
])
async def test_character_source_and_asset_refs_follow_requested_revision_not_latest(monkeypatch, version, release, missing):
    session = setup_profile(monkeypatch, release)

    async def lookup(_session, node_id, *, release_id=None):
        assert node_id == 2486699
        if release_id == 19 and missing:
            return None
        historical = release_id == 19
        icon = 'HistoricalIcon' if historical else 'FutureIcon'
        return {'source_file': 'BinData/role/roleinfo.json', 'source_row': 76,
                'version': '3.5.0' if historical else '3.7.0', 'raw_path': '$[76]',
                'raw_record': {'Id': 1608, 'RoleHeadIcon': f'/Game/{icon}.{icon}'}}

    source = AsyncMock(side_effect=lookup)
    monkeypatch.setattr(profiles, '_latest_source_record', source)
    result = await profiles.character_profile.__wrapped__('character:1608', game_version=version, session=session)
    if version:
        source.assert_awaited_once_with(session, 2486699, release_id=19)
    else:
        source.assert_awaited_once_with(session, 2486699)
    if missing:
        assert result['source'] is None
        assert result['media']['asset_references'] == []
    else:
        assert result['source']['version'] == (version or '3.7.0')
        assert result['source']['raw_path'] == '$[76]'
        assert ('HistoricalIcon' if version else 'FutureIcon') in str(result['media']['asset_references'])
    if version:
        assert 'FutureIcon' not in str(result)


@pytest.mark.asyncio
async def test_unknown_character_snapshot_cannot_turn_none_release_into_latest(monkeypatch):
    session = setup_profile(monkeypatch, None)
    source = AsyncMock()
    monkeypatch.setattr(profiles, '_latest_source_record', source)
    with pytest.raises(HTTPException) as error:
        await profiles.character_profile.__wrapped__('character:1608', game_version='9.9.0', session=session)
    assert error.value.status_code == 404
    source.assert_not_awaited()
