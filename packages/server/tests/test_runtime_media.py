import json

import pytest

from wuwa_story.ingestion.runtime_media import quest_runtime_links, runtime_media

ASSET = '/Game/Aki/Sequence/LevelA_Seq/Main/03_1/M0334/DA_M0334.DA_M0334'


@pytest.mark.parametrize('names', [['M0334_1Z'], [], ['DA_X.X_1Z', 'DA_X.X_2A', 'DA_X.X_1Z']])
def test_showtalk_preserves_exact_da_and_selectors_without_inventing_movies(names):
    action = {'Name': 'ShowTalk', 'Params': {
        'SequenceDataAsset': ASSET, 'SequenceNames': names}}
    raw = {'EnterActions': json.dumps([action])}
    expected = {'kind': 'sequence', 'reference': 'asset:ue:' + ASSET,
                'raw_path': '$[11844].EnterActions[0].Params.SequenceDataAsset',
                'basis': 'ShowTalk.Params.SequenceDataAsset', 'sequence_names': names,
                'sequence_names_raw_path': '$[11844].EnterActions[0].Params.SequenceNames'}
    assert list(runtime_media(raw, '$[11844]')) == [expected]
    assert list(runtime_media(
        action, '$[11844].EnterActions[0]', recursive=False)) == [expected]
    # Even a matching catalog name is not proof this DA is a complete movie.
    assert list(quest_runtime_links([(3, {'Key': '114000031_231', 'Data': raw})],
                                    {'cutscene:M0334', 'asset:ue:' + ASSET})) == []


@pytest.mark.parametrize('asset', [None, '', 1, {}, 'M0334', 'asset:ue:/Game/DA.DA'])
def test_selectors_cannot_replace_a_missing_or_invalid_da(asset):
    assert list(runtime_media({'Name': 'ShowTalk', 'Params': {
        'SequenceDataAsset': asset, 'SequenceNames': ['/Game/NotAnAsset.NotAnAsset'],
    }})) == []


@pytest.mark.parametrize('names', [None, 'M0334_1Z', {}, [None], ['M0334_1Z', 2], ['']])
def test_invalid_selectors_do_not_lose_the_authoritative_da(names):
    ref, = runtime_media({'Name': 'ShowTalk', 'Params': {
        'SequenceDataAsset': ASSET, 'SequenceNames': names,
    }})
    assert ref['reference'] == 'asset:ue:' + ASSET
    assert 'sequence_names' not in ref and 'sequence_names_raw_path' not in ref


def test_nested_references_are_once_per_authored_occurrence_and_nonrecursive_is_local():
    action = {'Name': 'ShowTalk', 'Params': {'SequenceDataAsset': ASSET}}
    raw = {'Actions': [action, action, {
        'Name': 'PlaySequenceData', 'Params': {'Path': ASSET}}]}
    refs = list(runtime_media(raw))
    assert [ref['raw_path'] for ref in refs] == [
        '$.Actions[0].Params.SequenceDataAsset', '$.Actions[1].Params.SequenceDataAsset',
        '$.Actions[2].Params.Path',
    ]
    assert {ref['reference'] for ref in refs} == {'asset:ue:' + ASSET}
    assert list(runtime_media(raw, recursive=False)) == []
