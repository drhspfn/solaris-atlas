"""Exact, typed personal-portrait references between Speaker and RoleInfo.

Pile cards are presentation fallbacks: thousands of speakers reuse Anke's card,
including speakers whose personal head portraits explicitly reference Jingran.
They cannot establish or veto a personal speaker identity. No names/ID equality
are used. Shared portraits resolve only inside the source-authored Rover family.
"""

from wuwa_story.ingestion.character_artwork import inherited_character_fields
from wuwa_story.ingestion.character_identity import main_role_identities

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
    roles, speakers = list(roles), list(speakers)
    templates = {raw.get('Id'): raw for _, raw in roles}
    identities = main_role_identities(main_roles, [raw for _, raw in roles])
    role_counts, speaker_counts = {}, {}
    for _, raw in roles:
        identity = raw.get('Id')
        role_counts[identity] = role_counts.get(identity, 0) + 1
    for _, raw in speakers:
        identity = raw.get('Id')
        speaker_counts[identity] = speaker_counts.get(identity, 0) + 1
    assets = {}
    for row, role in roles:
        identity = role.get('Id')
        if type(identity) is not int:
            continue
        target = identities.get(identity, {}).get(
            'canonical_key', f'character:{identity}')
        inherited = inherited_character_fields(
            role, templates.get(role.get('SkillId'), {}))
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
        # A male-variant override alone is not the speaker's personal portrait.
        if (any(role_counts[m['role_id']] != 1 for m in matches)
                or len(targets) != 1 or not any(
                    m['speaker_field'] != 'HeadRoundIconAssetMaleVariant' for m in matches)):
            continue
        target, = targets
        matches.sort(key=lambda m: (
            m['speaker_field'], m['role_id'], m['role_field']))
        detail = '; '.join(
            f"{m['speaker_field']}={m['asset_path']} = {ROLE_PATH}$[{m['role_row']}].{m['role_field']} (RoleInfo.Id={m['role_id']})"
            for m in matches)
        yield {
            'from': f'speaker:{identity}', 'to': target, 'type': 'references_character',
            'basis': PERSONAL_PORTRAIT_BASIS + detail,
            'relation': 'exact_join', 'source': SPEAKER_PATH,
            'raw_path': f"$[{row}].{next(m['speaker_field'] for m in matches if m['speaker_field'] != 'HeadRoundIconAssetMaleVariant')}",
            'crosswalk': {'method': 'exact_personal_portrait', 'speaker_row': row,
                          'role_file': ROLE_PATH, 'matches': matches},
        }
