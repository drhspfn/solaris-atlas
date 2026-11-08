from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story.api.routes.story.media import verified_flow_music
from wuwa_story.ingestion.spine_presentation import flow_presentation


def sample():
    presentation = flow_presentation({'StateKey': 'Story_1_29', 'Actions': [
        {'Name': 'PostAkEvent', 'Params': {'EventConfig': {'Type': 'Global', 'AkEvent': '/Game/Play'}}},
        {'Name': 'ShowTalk', 'Params': {'TalkItems': [{'TidTalk': 'one', 'BackgroundConfig': {'Type': 'SpineImage', 'Id': 7}}, {'TidTalk': 'two'}]}},
        {'Name': 'PostAkEvent', 'Params': {'EventConfig': {'Type': 'Global', 'AkEvent': '/Game/Stop'}}},
    ]})
    proof = {'flow_owner': 7, 'release_id': 21, 'flow_state': 'Story_1_29',
             'presentation_sha256': presentation['sha256'], 'asset_version': '3.7.1', 'download_id': 'client',
             'play_path': '$.Actions[0]', 'talk_path': '$.Actions[1]', 'stop_path': '$.Actions[2]',
             'talk_text_keys': ['one', 'two']}
    section = {'kind': 'audio_event', 'scope': 'flow', 'reconstruction_kind': 'flow_authored_music',
               'reference': '/Game/Play', 'source_proof': proof,
               'tracks': [{'role': 'music', 'status': 'published', 'url': 'https://cdn/music.ogg', 'loop': True}]}
    return presentation, {'game_source': {'asset_version': '3.7.1', 'download_id': 'client'}, 'sections': [section]}


def test_flow_music_exposes_exact_talk_extent_and_published_track():
    presentation, media = sample()
    section, keys = verified_flow_music(media, presentation, 7, 21)
    assert keys == ['one', 'two']
    assert section['tracks'][0]['url'] == 'https://cdn/music.ogg'


def test_flow_music_accepts_authored_image_dialogue():
    presentation, media = sample()
    presentation['source']['Actions'][1]['Params']['TalkItems'][0]['BackgroundConfig'] = {
        'Type': 'Image', 'ImageAsset': '/Game/Scene.Scene'}
    presentation = flow_presentation(presentation['source'])
    media['sections'][0]['source_proof']['presentation_sha256'] = presentation['sha256']
    assert verified_flow_music(media, presentation, 7, 21)[1] == ['one', 'two']


@pytest.mark.parametrize('change', ['owner', 'release', 'client', 'digest', 'coverage', 'play', 'unpublished', 'ambiguous'])
def test_flow_music_rejects_mismatched_or_unavailable_provenance(change):
    presentation, media = sample()
    section = media['sections'][0]
    proof = section['source_proof']
    if change == 'owner':
        proof['flow_owner'] = 8
    if change == 'release':
        proof['release_id'] = 22
    if change == 'client':
        proof['download_id'] = 'other'
    if change == 'digest':
        proof['presentation_sha256'] = 'other'
    if change == 'coverage':
        proof['talk_text_keys'] = ['one']
    if change == 'play':
        section['reference'] = '/Game/Other'
    if change == 'unpublished':
        section['tracks'][0]['status'] = 'rendered'
    if change == 'ambiguous':
        media['sections'].append(deepcopy(section))
    assert verified_flow_music(media, presentation, 7, 21) is None


@pytest.mark.asyncio
async def test_quest_media_exposes_flow_music_without_sequence_cue(monkeypatch):
    from wuwa_story.api.routes.story import media as route
    from wuwa_story.story import runtime_service

    presentation, audio = sample()
    audio.update(schema_version=1, complete_video=False)
    action = SimpleNamespace(node_id=10, action_index=0)
    state = SimpleNamespace(node_id=7, state_key='Story_1_29')
    async def links(_session, nodes, relations, _release):
        if 10 in nodes and 'posts_audio_event' in relations:
            return {10: [{'relation': 'posts_audio_event', 'canonical_key': 'asset:ue:/Game/Play',
                          'node_id': 12, 'basis': 'authored', 'source': {}}]}
        if 1 in nodes and 'references_packaged_cutscene' in relations:
            return {1: [{'node_id': 13, 'canonical_key': 'cutscene:related'}]}
        return {}
    monkeypatch.setattr(route, 'get_quest', AsyncMock(return_value=SimpleNamespace(node_id=1, game_quest_id=1)))
    monkeypatch.setattr(route, '_release_id', AsyncMock(return_value=21))
    monkeypatch.setattr(route, 'authored_order', AsyncMock(return_value=None))
    monkeypatch.setattr(route, '_quest_page_state_ids', AsyncMock(return_value=[7]))
    monkeypatch.setattr(route, '_links', links)
    monkeypatch.setattr(route, 'cutscene_flows', AsyncMock(return_value={}))
    monkeypatch.setattr(route, 'cutscene_videos', AsyncMock(return_value={}))
    monkeypatch.setattr(route, 'scene_continuations', AsyncMock(return_value={}))
    monkeypatch.setattr(route, 'caption_texts', AsyncMock(return_value={}))
    monkeypatch.setattr(runtime_service, 'sequence_playback', AsyncMock(return_value={7: audio}))
    session = SimpleNamespace(scalars=AsyncMock(return_value=[]), scalar=AsyncMock(return_value=None),
        execute=AsyncMock(side_effect=[SimpleNamespace(all=lambda: [(action, state, 'action:1')]),
                                      [(7, presentation['source'])]]),
        get=AsyncMock(return_value=SimpleNamespace(game_version='3.7.1')))
    result = await route.quest_media('1', '3.7.1', 'en', session)
    event, = [event for event in result['events'] if event['kind'] == 'flow_music']
    assert event['flow_state'] == 'Story_1_29' and event['covered_text_keys'] == ['one', 'two']
    assert event['sequence_media']['sections'][0]['tracks'][0]['role'] == 'music'
    assert result['related_cutscenes'][0]['name'] == 'related'
