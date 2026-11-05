import pytest

from wuwa_story.ingestion.speaker_crosswalk import PERSONAL_PORTRAIT_BASIS, ROLE_PATH, SPEAKER_PATH
from wuwa_story.ingestion.speaker_crosswalk_audit import audit_snapshot


def snapshot():
    return {'release_id': 21, 'source_rows': [
        {'file': ROLE_PATH, 'row_index': 141, 'data': {'Id': 1212, 'RoleHeadIconCircle': '/jingran'}},
        {'file': SPEAKER_PATH, 'row_index': 5797,
         'data': {'Id': 400049, 'HeadIconAsset': '/jingran', 'RolePileIconAsset': '/anke-card'}},
    ], 'existing_crosswalks': [
        {'speaker': 400049, 'character': 'character:1203', 'explanations': ['legacy pile card']},
    ]}


def test_offline_audit_identifies_conflicting_legacy_card_without_deleting_it():
    source = snapshot()
    result = audit_snapshot(source)
    assert result['missing_identity_links'] == result['personal_proof_repairs'] == 1
    assert result['role_ids'] == [1212]
    assert result['legacy_conflicting_links'] == [('speaker:400049', 'character:1203')]
    assert len(source['existing_crosswalks']) == 1
    source['existing_crosswalks'].append({'speaker': 400049, 'character': 'character:1212',
                                         'explanations': [PERSONAL_PORTRAIT_BASIS + 'source fields']})
    assert audit_snapshot(source)['personal_proof_repairs'] == 0


def test_offline_audit_requires_bounded_single_release_sources():
    source = snapshot()
    with pytest.raises(ValueError, match='bounded'):
        audit_snapshot(source, max_rows=1)
    source['source_rows'][0]['release_id'] = 20
    with pytest.raises(ValueError, match='different releases'):
        audit_snapshot(source)
