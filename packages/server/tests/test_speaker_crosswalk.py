import json
import os
from pathlib import Path

import pytest

from wuwa_story.ingestion.character_identity import MAIN_ROLE_PATH
from wuwa_story.ingestion.speaker_crosswalk import (
    PERSONAL_PORTRAIT_BASIS,
    ROLE_PATH,
    SPEAKER_PATH,
    speaker_character_links,
)


def links(roles, speakers, configs=()):
    return list(speaker_character_links(enumerate(roles), enumerate(speakers), configs))


def test_jingran_personal_portraits_override_borrowed_presentation_card_for_all_variants():
    roles = [{'Id': 1212, 'RoleHeadIconCircle': '/jingran-circle',
              'RoleHeadIconBig': '/jingran-head', 'FormationRoleCard': '/jingran-card'},
             {'Id': 1203, 'FormationRoleCard': '/anke-card'}]
    speakers = [{'Id': 400049, 'HeadIconAsset': '/jingran-circle',
                 'HeadRoundIconAsset': '/jingran-head', 'RolePileIconAsset': '/anke-card'},
                {'Id': 800545, 'HeadIconAsset': '/jingran-circle', 'RolePileIconAsset': '/anke-card'}]
    result = links(roles, speakers)
    assert [(r['from'], r['to']) for r in result] == [
        ('speaker:400049', 'character:1212'), ('speaker:800545', 'character:1212')]
    assert all(r['relation'] == 'exact_join' for r in result)
    assert result[0]['raw_path'] == '$[0].HeadIconAsset'
    assert result[1]['raw_path'] == '$[1].HeadIconAsset'
    assert {m['role_field'] for m in result[0]['crosswalk']['matches']} == {
        'RoleHeadIconCircle', 'RoleHeadIconBig'}


def test_card_only_names_numeric_ids_and_wrong_typed_fields_do_not_prove_identity():
    role = {'Id': 1, 'Name': 'same', 'RoleHeadIconCircle': '/head', 'FormationRoleCard': '/card'}
    assert not links([role], [{'Id': 1, 'Name': 'same', 'RolePileIconAsset': '/card'}])
    assert not links([role], [{'Id': 2, 'HeadRoundIconAsset': '/head'}])
    assert not links([role], [{'Id': 2, 'HeadRoundIconAssetMaleVariant': '/head'}])


def test_shared_unrelated_heads_and_conflicting_head_fields_remain_unresolved():
    roles = [{'Id': 1, 'RoleHeadIconCircle': '/shared', 'RoleHeadIconBig': '/one'},
             {'Id': 2, 'RoleHeadIconCircle': '/shared', 'RoleHeadIconBig': '/two'}]
    assert not links(roles, [{'Id': 3, 'HeadIconAsset': '/shared'}])
    roles[1]['RoleHeadIconCircle'] = '/other'
    assert not links(roles, [{'Id': 3, 'HeadIconAsset': '/shared', 'HeadRoundIconAsset': '/two'}])


def test_rover_shared_portraits_require_main_role_config_not_name_or_id_ranges():
    roles = [{'Id': 1501, 'RoleHeadIconCircle': '/male'},
             {'Id': 1503, 'RoleHeadIconCircle': '/male'},
             {'Id': 1502, 'RoleHeadIconCircle': '/female', 'RoleHeadIconBig': '/female-head'},
             {'Id': 1504, 'RoleHeadIconCircle': '/female', 'RoleHeadIconBig': '/female-head'}]
    speakers = [{'Id': 10, 'HeadIconAsset': '/male'}, {'Id': 11, 'HeadIconAsset': '/female'}]
    assert not links(roles, speakers)
    configs = [{'Id': role['Id'], 'Gender': role['Id'] % 2,
                'UnlockCondition': 0 if role['Id'] in (1501, 1502) else 1} for role in roles]
    result = links(roles, speakers, configs)
    assert {r['to'] for r in result} == {'character:1501'}
    assert {m['role_id'] for m in result[0]['crosswalk']['matches']} == {1501, 1503}


def test_duplicate_speaker_id_never_emits_ambiguous_import_edges():
    roles = [{'Id': 1, 'RoleHeadIconLarge': '/head'}]
    assert not links(roles, [{'Id': 2, 'HeadIconAsset': '/head'}] * 2)


def test_duplicate_role_id_cannot_be_ignored_to_make_an_asset_appear_unique():
    roles = [{'Id': 1, 'RoleHeadIconLarge': '/head'},
             {'Id': 2, 'RoleHeadIconLarge': '/head'}, {'Id': 2}]
    assert not links(roles, [{'Id': 3, 'HeadIconAsset': '/head'}])


def test_skill_template_borrowed_head_and_cards_are_not_npc_personal_identity():
    yangyang = {'Id': 1402, 'SkillId': 1402, 'RoleType': 1, 'Name': 'RoleInfo_1402_Name',
                'RoleHeadIconBig': '/yangyang', 'FormationRoleCard': '/yangyang-card'}
    borrowed = {**yangyang, 'Id': 5002, 'SkillId': 1402, 'RoleType': 2,
                'Name': 'RoleInfo_5002_Name'}
    monster = {**borrowed, 'Id': 5015, 'Name': 'RoleInfo_5015_Name',
               'RoleHeadIconBig': '/monster'}
    result = links([yangyang, borrowed, monster], [
        {'Id': 100, 'HeadRoundIconAsset': '/yangyang', 'RolePileIconAsset': '/yangyang-card'},
        {'Id': 101, 'HeadRoundIconAsset': '/monster', 'RolePileIconAsset': '/yangyang-card'},
        {'Id': 102, 'RolePileIconAsset': '/yangyang-card'},
    ])
    assert [(r['from'], r['to']) for r in result] == [
        ('speaker:100', 'character:1402'), ('speaker:101', 'character:5015')]
    assert not any(m['role_id'] == 5002 for r in result for m in r['crosswalk']['matches'])


def family_sources():
    roles = [{'Id': 1501, 'RoleType': 1, 'FormationRoleCard': '/male', 'RoleHeadIconBig': '/male-head'},
             {'Id': 1502, 'RoleType': 1, 'FormationRoleCard': '/female', 'RoleHeadIconBig': '/female-head'}]
    configs = [{'Id': 1501, 'Gender': 1, 'UnlockCondition': 0},
               {'Id': 1502, 'Gender': 0, 'UnlockCondition': 0}]
    speakers = [{'Id': 317, 'RolePileIconAsset': '/female'},
                {'Id': 750088, 'RolePileIconAsset': '/female', 'HeadRoundIconAsset': '/female-head',
                 'HeadRoundIconAssetMaleVariant': '/male-head'},
                {'Id': 1450, 'RolePileIconAsset': '/male'},
                {'Id': 300013, 'RolePileIconAsset': '/female', 'HeadIconAsset': '/generic'}]
    # Trial rows copy the heads/cards but are not source MainRoleConfig members.
    roles.append({'Id': 7001, 'RoleType': 4, 'FormationRoleCard': '/female',
                  'RoleHeadIconBig': '/female-head'})
    return roles, speakers, configs


def test_mainrole_family_only_card_fallback_preserves_all_four_source_variants():
    roles, speakers, configs = family_sources()
    result = links(roles, speakers, configs)
    assert {r['from'] for r in result} == {'speaker:317', 'speaker:750088', 'speaker:1450', 'speaker:300013'}
    assert {r['to'] for r in result} == {'character:1501'}
    assert all(r['crosswalk']['method'] == 'main_role_config_exact_asset' for r in result)
    assert all(r['basis'].startswith(PERSONAL_PORTRAIT_BASIS) and MAIN_ROLE_PATH in r['basis'] for r in result)
    assert all(r['raw_path'].endswith('.RolePileIconAsset') for r in result)
    assert not links(roles, speakers[:1], [])  # No family inferred from cards or numeric IDs.


def test_mainrole_card_shared_with_unrelated_playable_family_is_not_unique():
    roles, speakers, configs = family_sources()
    roles.append({'Id': 90, 'RoleType': 1, 'FormationRoleCard': '/female'})
    assert not links(roles, speakers[:1], configs)


def test_family_card_cannot_override_conflicting_or_unrelated_personal_heads():
    roles, _, configs = family_sources()
    roles.append({'Id': 1212, 'RoleType': 1, 'RoleHeadIconCircle': '/jingran'})
    result = links(roles, [{'Id': 4, 'HeadIconAsset': '/jingran', 'RolePileIconAsset': '/female'}], configs)
    assert len(result) == 1 and result[0]['to'] == 'character:1212'
    roles.append({'Id': 90, 'RoleType': 1, 'RoleHeadIconBig': '/female-head'})
    assert not links(roles, [{'Id': 5, 'HeadRoundIconAsset': '/female-head',
                             'RolePileIconAsset': '/female'}], configs)


@pytest.mark.skipif(not os.getenv('SPEAKER_CROSSWALK_SNAPSHOTS'), reason='Requires retained private source snapshots')
def test_real_latest_snapshot_retains_every_baseline_rover_speaker():
    path = Path(os.environ['SPEAKER_CROSSWALK_SNAPSHOTS'])
    snapshots = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    latest = next(s for s in snapshots if s['release_id'] == 21)
    groups = {p: [(r['row_index'], r['data']) for r in latest['source_rows'] if r['file'] == p]
              for p in (ROLE_PATH, SPEAKER_PATH, MAIN_ROLE_PATH)}
    result = list(speaker_character_links(groups[ROLE_PATH], groups[SPEAKER_PATH],
                                          [raw for _, raw in groups[MAIN_ROLE_PATH]]))
    family = {r['from'] for r in result if r['to'] == 'character:1501'}
    assert family == {'speaker:317', 'speaker:750088', 'speaker:1450', 'speaker:300013'}
    baseline_path = os.getenv('CHARACTER_CONNECTION_BASELINES')
    if baseline_path:
        baseline = json.loads(Path(baseline_path).read_text(encoding='utf-8'))['15499-rover-spectro']['profile']
        assert {speaker['canonical_key'] for speaker in baseline['speakers']} <= family
        assert len(baseline['quests_with_dialogue']) == 392

def guest_links(roles, speakers, guests, english):
    return list(speaker_character_links(
        enumerate(roles), enumerate(speakers), guests=enumerate(guests),
        english={key: {'content': value} for key, value in english.items()}))


def test_explicit_guest_identity_restores_sigrika_without_stale_jianxin_or_tulia():
    roles = [{'Id': 1412, 'RoleType': 1, 'Name': 'sigrika'},
             {'Id': 1405, 'RoleType': 1, 'Name': 'jianxin'},
             {'Id': 1409, 'RoleType': 1, 'Name': 'cartethyia'}]
    speakers = [{'Id': 150088}, {'Id': 850296}]
    guests = [{'GuestID': 1005, 'Name': 'jianxin', 'SpeakerID': [150088]},
              {'GuestID': 1010, 'Name': 'sigrika', 'SpeakerID': [150088]},
              {'GuestID': 1002, 'Name': 'cartethyia', 'SpeakerID': [850296]}]
    english = {'sigrika': 'Sigrika', 'jianxin': 'Jianxin', 'cartethyia': 'Cartethyia',
               'Speaker_150088_Name': 'Sigrika', 'Speaker_850296_Name': 'Tulia'}
    result = guest_links(roles, speakers, guests, english)
    assert [(r['from'], r['to']) for r in result] == [('speaker:150088', 'character:1412')]
    assert result[0]['source'] == 'BinData/PlotGuest/plotguest.json'
    assert result[0]['raw_path'] == '$[1].SpeakerID[0]'
    assert result[0]['crosswalk']['method'] == 'exact_plot_guest'


@pytest.mark.parametrize('case', ['missing_guest', 'missing_name', 'empty_name', 'different_key',
                                  'duplicate_role', 'duplicate_speaker', 'technical_role', 'string_id'])
def test_guest_proof_requires_explicit_unique_playable_reference_and_resolved_name(case):
    roles = [{'Id': 1, 'RoleType': 1, 'Name': 'role_name'}]
    speakers = [{'Id': 2}]
    guests = [{'Name': 'role_name', 'SpeakerID': [2]}]
    english = {'role_name': 'Name', 'Speaker_2_Name': 'Name'}
    if case == 'missing_guest':
        guests = []
    elif case == 'missing_name':
        english.pop('Speaker_2_Name')
    elif case == 'empty_name':
        english = {key: '' for key in english}
    elif case == 'different_key':
        guests[0]['Name'] = 'Name'
    elif case == 'duplicate_role':
        roles.append(dict(roles[0]))
    elif case == 'duplicate_speaker':
        speakers.append(dict(speakers[0]))
    elif case == 'technical_role':
        roles[0]['RoleType'] = 3
    else:
        guests[0]['SpeakerID'] = ['2']
    assert not guest_links(roles, speakers, guests, english)


def test_ambiguous_guest_targets_and_personal_portrait_conflicts_stay_unresolved():
    roles = [{'Id': 1, 'RoleType': 1, 'Name': 'one'},
             {'Id': 2, 'RoleType': 1, 'Name': 'two'}]
    speakers = [{'Id': 3}]
    guests = [{'Name': 'one', 'SpeakerID': [3]}, {'Name': 'two', 'SpeakerID': [3]}]
    english = {'one': 'Same', 'two': 'Same', 'Speaker_3_Name': 'Same'}
    assert not guest_links(roles, speakers, guests, english)
    roles[1]['RoleHeadIconCircle'] = '/personal'
    speakers[0]['HeadIconAsset'] = '/personal'
    result = guest_links(roles, speakers, guests[:1], english)
    assert len(result) == 1 and result[0]['to'] == 'character:2'
    assert result[0]['crosswalk']['method'] == 'exact_personal_portrait'

def test_guest_context_preserves_conflicting_source_without_promoting_identity():
    from wuwa_story.ingestion.speaker_crosswalk import plot_guest_role_links
    roles = [{'Id': 1412, 'RoleType': 1, 'Name': 'sigrika'},
             {'Id': 1405, 'RoleType': 1, 'Name': 'jianxin'}]
    guests = [{'GuestID': 1005, 'Name': 'jianxin', 'SpeakerID': [150088]},
              {'GuestID': 1010, 'Name': 'sigrika', 'SpeakerID': [150088]}]
    english = {k: {'content': v} for k, v in {'sigrika': 'Sigrika', 'jianxin': 'Jianxin',
                                           'Speaker_150088_Name': 'Sigrika'}.items()}
    records = list(plot_guest_role_links(enumerate(roles), enumerate(guests), english=english))
    assert [(r['from'], r['to']) for r in records] == [
        ('plot_guest:1005', 'character:1405'), ('plot_guest:1010', 'character:1412')]
    assert records[0]['crosswalk']['identity_status'] == 'suspect'
    assert records[0]['crosswalk']['warnings'] == ['conflicting_speaker_name']
    assert 'identity_status=suspect' in records[0]['basis']
    assert records[0]['raw_path'] == '$[0].Name'
    assert records[1]['crosswalk']['identity_status'] == 'corroborated'
    identities = list(speaker_character_links(enumerate(roles), [(0, {'Id': 150088})],
                                            guests=enumerate(guests), english=english))
    assert {r['to'] for r in identities} == {'character:1412'}


def test_guest_context_keeps_unknown_names_but_does_not_guess_ambiguous_role_keys():
    from wuwa_story.ingestion.speaker_crosswalk import plot_guest_role_links
    guest = {'GuestID': 1, 'Name': 'key', 'SpeakerID': [3]}
    roles = [{'Id': 2, 'Name': 'key'}]
    result = list(plot_guest_role_links(enumerate(roles), [(0, guest)]))
    assert result[0]['crosswalk']['warnings'] == ['missing_resolved_name']
    assert result[0]['crosswalk']['identity_status'] == 'suspect'
    roles.append({'Id': 4, 'Name': 'key'})
    assert not list(plot_guest_role_links(enumerate(roles), [(0, guest)]))
