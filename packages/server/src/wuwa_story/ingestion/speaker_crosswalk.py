"""Exact, typed personal-portrait references between Speaker and RoleInfo.

Pile cards are presentation fallbacks: thousands of speakers reuse Anke's card,
including speakers whose personal head portraits explicitly reference Jingran.
They cannot establish or veto an ordinary personal identity. The source-authored
MainRoleConfig family is the narrow exception: its card must be unique across
playable families and cannot contradict a personal head. No name guesses are used.
"""

from wuwa_story.ingestion.character_artwork import inherited_character_fields
from wuwa_story.ingestion.character_identity import MAIN_ROLE_PATH, main_role_identities

ROLE_PATH = 'BinData/role/roleinfo.json'
SPEAKER_PATH = 'BinData/speaker/speaker.json'
PERSONAL_PORTRAIT_BASIS = 'Exact typed personal portrait reference: '
PORTRAITS = {
    'HeadIconAsset': ('RoleHeadIconCircle', 'RoleHeadIconLarge'),
    'HeadRoundIconAsset': ('RoleHeadIconBig',),
    'HeadRoundIconAssetMaleVariant': ('RoleHeadIconBig',),
}


def speaker_character_links(roles, speakers, main_roles=()):
    """Yield deterministic compiler edges with both source field references.

    Each input is (source row index, decoded source object), from ONE release.
    Duplicate IDs and unrelated shared/conflicting head portraits stay unresolved.
    """
    roles, speakers, main_roles = list(roles), list(speakers), list(main_roles)
    templates = {raw.get('Id'): raw for _, raw in roles}
    identities = main_role_identities(main_roles, [raw for _, raw in roles])
    declared_members = {raw.get('Id') for raw in main_roles
                        if raw.get('Gender') in (0, 1) and raw.get('Id') in identities}
    family_targets = {identities[identity]['canonical_key'] for identity in declared_members}
    role_counts, speaker_counts = {}, {}
    for _, raw in roles:
        identity = raw.get('Id')
        role_counts[identity] = role_counts.get(identity, 0) + 1
    for _, raw in speakers:
        identity = raw.get('Id')
        speaker_counts[identity] = speaker_counts.get(identity, 0) + 1
    assets, cards = {}, {}
    for row, role in roles:
        identity = role.get('Id')
        if type(identity) is not int:
            continue
        target = identities.get(identity, {}).get(
            'canonical_key', f'character:{identity}')
        inherited = inherited_character_fields(
            role, templates.get(role.get('SkillId'), {}))
        # Trial/technical RoleInfo rows also copy Rover cards. Only declared
        # playable identities (or explicit MainRoleConfig members) can own a
        # family card; unrelated playable owners make it ambiguous.
        card = role.get('FormationRoleCard')
        if ((role.get('RoleType') == 1 or identity in declared_members)
                and isinstance(card, str) and card and 'FormationRoleCard' not in inherited):
            cards.setdefault(card, []).append((target, identity, row))
        for field in {field for fields in PORTRAITS.values() for field in fields}:
            if field in inherited:
                continue
            asset = role.get(field)
            if isinstance(asset, str) and asset:
                assets.setdefault((field, asset), []).append(
                    (target, identity, row))
    for row, speaker in speakers:
        identity = speaker.get('Id')
        if type(identity) is not int or speaker_counts[identity] != 1:
            continue
        matches = []
        for field, role_fields in PORTRAITS.items():
            asset = speaker.get(field)
            if not isinstance(asset, str) or not asset:
                continue
            for role_field in role_fields:
                for target, role_id, role_row in assets.get((role_field, asset), []):
                    matches.append({
                        'character': target, 'role_id': role_id, 'role_row': role_row,
                        'role_field': role_field, 'speaker_field': field, 'asset_path': asset,
                    })
        targets = {match['character'] for match in matches}
        method = 'exact_personal_portrait'
        # A male-variant override alone is not the speaker's personal portrait.
        if (any(role_counts[m['role_id']] != 1 for m in matches)
                or len(targets) != 1 or not any(
                    m['speaker_field'] != 'HeadRoundIconAssetMaleVariant' for m in matches)):
            card = speaker.get('RolePileIconAsset')
            card_owners = cards.get(card, []) if isinstance(card, str) else []
            card_targets = {owner for owner, _, _ in card_owners}
            if len(card_targets) != 1 or not card_targets <= family_targets:
                continue
            family_target, = card_targets
            family_heads = {m['asset_path'] for m in matches
                            if m['role_id'] in declared_members and m['character'] == family_target}
            if (any(role_counts[role_id] != 1 for _, role_id, _ in card_owners)
                    or any(role_counts[m['role_id']] != 1 for m in matches)
                    or any(m['asset_path'] not in family_heads
                           or (templates[m['role_id']].get('RoleType') == 1
                               and m['character'] != family_target) for m in matches)):
                continue
            matches = [
                {'character': owner, 'role_id': role_id, 'role_row': role_row,
                 'role_field': 'FormationRoleCard', 'speaker_field': 'RolePileIconAsset',
                 'asset_path': speaker['RolePileIconAsset']}
                for owner, role_id, role_row in card_owners if role_id in declared_members
            ]
            if not matches:
                continue
            targets = {family_target}
            method = 'main_role_config_exact_asset'
        target, = targets
        matches.sort(key=lambda m: (
            m['speaker_field'], m['role_id'], m['role_field']))
        detail = '; '.join(
            f"{m['speaker_field']}={m['asset_path']} = {ROLE_PATH}$[{m['role_row']}].{m['role_field']} (RoleInfo.Id={m['role_id']})"
            for m in matches)
        if method == 'main_role_config_exact_asset':
            detail = f'{MAIN_ROLE_PATH} explicit family membership; ' + detail
        yield {
            'from': f'speaker:{identity}', 'to': target, 'type': 'references_character',
            'basis': PERSONAL_PORTRAIT_BASIS + detail,
            'relation': 'exact_join', 'source': SPEAKER_PATH,
            'raw_path': f"$[{row}].{next(m['speaker_field'] for m in matches if m['speaker_field'] != 'HeadRoundIconAssetMaleVariant')}",
            'crosswalk': {'method': method, 'speaker_row': row,
                          'role_file': ROLE_PATH, 'matches': matches},
        }
