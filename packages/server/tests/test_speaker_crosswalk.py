from wuwa_story.ingestion.speaker_crosswalk import speaker_character_links


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
