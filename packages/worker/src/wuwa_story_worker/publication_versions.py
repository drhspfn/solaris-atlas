"""Prevent older story recipes replacing a newer proof for the same source."""


def preserve_story_reference(reference_type, metadata, incoming):
    """Call under the publication owner lock, before replacing file or metadata.

    Compare whole proofs; never merge fragments or provenance across versions.
    Different source identities require normal current-source reconciliation.
    """
    if reference_type not in {'story_presentation', 'story_spine'}:
        return False
    previous = metadata.get('game_source') if isinstance(
        metadata, dict) else None
    if not isinstance(previous, dict) or not isinstance(incoming, dict):
        return False
    if any(type(proof.get('schema_version')) is not int or proof['schema_version'] != 1
           for proof in (previous, incoming)):
        return False
    for key in ('asset_version', 'download_id', 'package_path', 'presentation_sha256'):
        if not isinstance(previous.get(key), str) or not previous[key] or previous[key] != incoming.get(key):
            return False
    context_changed = previous.get('soundtrack_context_sha256') != incoming.get(
        'soundtrack_context_sha256')
    versions = []
    keys = ['spine_recipe', 'sequence_media_schema']
    sequence = incoming['package_path'].startswith('story_sequence:') or any(
        type(proof.get('sequence_metadata_revision')
             ) is int and proof['sequence_metadata_revision'] >= 5
        for proof in (previous, incoming))
    if sequence:
        keys.append('sequence_metadata_revision')
    for key in keys:
        old, new = previous.get(key), incoming.get(key)
        old = 0 if old is None else old
        new = 0 if new is None else new
        if type(old) is not int or old < 0 or type(new) is not int or new < 0:
            return False
        versions.append((old, new))
    return (any(old > new for old, new in versions)
            or (sequence and not context_changed
                and all(old == new for old, new in versions)
                and previous.get('control_sources_complete') is True
                and incoming.get('control_sources_complete') is False))
