from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from wuwa_story.ingestion.entity_media import flow_background_paths
from wuwa_story.ingestion.talk_backgrounds import background_changes, background_config
from wuwa_story.story import illustrations


def test_backgrounds_preserve_rover_variants_and_explicit_clear():
    config = background_config({'Type': 'ImageByMcGender', 'ImageAssetMale': '/Game/M.M',
                                'ImageAssetFemale': '/Game/F.F'})
    assert [(image['rover'], image['engine_path']) for image in config['images']] == [('male', '/Game/M.M'), ('female', '/Game/F.F')]
    assert background_config({'Type': 'Image', 'ImageAsset': 'https://untrusted.test'}) is None
    assert background_config({'Type': 'SpineImage', 'Id': 850007})['resource_id'] == 850007
    assert list(background_changes([{}, {'BackgroundConfig': {'Type': 'Clean'}}])) == [(1, {'kind': 'clear', 'images': []})]


def test_existing_flow_sources_can_recover_only_authored_background_images():
    import json
    actions = [{'Params': {'TalkItems': [{'BackgroundConfig': {'Type': 'Image', 'ImageAsset': '/Game/A.A'}}]}},
               {'Params': None}, {'Params': {'TalkItems': None}}]
    assert flow_background_paths({'Actions': json.dumps(actions)}) == {'/Game/A.A'}
    assert flow_background_paths({'Actions': None}) == set()


@pytest.mark.asyncio
async def test_background_persists_across_talks_and_actions_but_not_other_states(monkeypatch):
    actions = [(10, 1, [{}, {'BackgroundConfig': {'Type': 'Image', 'ImageAsset': '/Game/A.A'}}, {}]),
               (11, 1, [{}, {'BackgroundConfig': {'Type': 'Clean'}}, {}]), (12, 2, [{}])]
    monkeypatch.setattr(illustrations.repository, 'illustration_actions', AsyncMock(return_value=actions))
    monkeypatch.setattr(illustrations, 'get_settings', lambda: NS(s3_bucket='test'))
    monkeypatch.setattr(illustrations, 'S3Storage', lambda _: NS(public_url=lambda key: 'https://cdn.test/' + key))
    session = NS(execute=AsyncMock(return_value=[('/Game/A.A', 'a.png')]))
    rows = [(NS(node_id=identity, source_index=index), NS(node_id=action), NS(node_id=state))
            for identity, action, state, index in [(1, 10, 1, 0), (2, 10, 1, 2), (3, 11, 1, 0),
                                                  (4, 11, 1, 1), (5, 11, 1, 2), (6, 12, 2, 0)]]
    result = await illustrations.illustration_payloads(session, rows)
    assert 1 not in result and 6 not in result
    assert result[2] == result[3]
    assert result[2]['images'][0]['url'] == 'https://cdn.test/a.png'
    assert result[4]['kind'] == result[5]['kind'] == 'clear'
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('known_asset', [True, False])
async def test_transition_movie_is_an_ordered_quest_event_without_flow_action(monkeypatch, known_asset):
    from wuwa_story.api.routes.story import media
    block = {'id': 'q119', 'owner': 'quest_node:121000040:119', 'branches': [],
             'source': {'file': 'quest.json', 'key': '121000040_119'},
             'media': [{'kind': 'cutscene', 'reference': 'cutscene:M0355_Loading',
                        'raw_path': '$.Data.FinishActions[0].Params.TransitionOption.Mp4Path',
                        'basis': 'PlayMp4.Mp4Path = CgName', 'transition': True, 'position': [9, 100001, 2, 4]}]}
    monkeypatch.setattr(media, 'get_quest', AsyncMock(return_value=NS(node_id=1, game_quest_id=121000040)))
    monkeypatch.setattr(media, '_release_id', AsyncMock(return_value=21))
    monkeypatch.setattr(media, 'authored_order', AsyncMock(return_value={'blocks': [block], 'state_ids': [], 'positions': {}, 'mapped_flow_states': []}))
    monkeypatch.setattr(media, '_quest_page_state_ids', AsyncMock(return_value=[]))
    for name in ['_links', 'cutscene_flows', 'cutscene_videos', 'caption_texts']:
        monkeypatch.setattr(media, name, AsyncMock(return_value={}))
    session = NS(scalars=AsyncMock(return_value=[]), scalar=AsyncMock(return_value=None),
        execute=AsyncMock(side_effect=[NS(all=lambda: []), [('cutscene:M0355_Loading', 55)] if known_asset else [], []]),
        get=AsyncMock(return_value=NS(game_version='3.7.0')))
    result = await media.quest_media('121000040', '3.7.0', 'en', session)
    event, = result['events']
    assert event['reference'] == 'cutscene:M0355_Loading'
    assert event['story_position'] == [9, 100001, 2, 4]
    assert event['transition'] and event['story_order_mapped'] and not event['context_inferred']
