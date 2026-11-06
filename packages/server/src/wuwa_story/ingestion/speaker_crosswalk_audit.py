"""Offline audit of bounded retained-source snapshots (one JSON object per line).

Each snapshot has release_id, game_version, source_rows (file, row_index, data),
existing_crosswalks (speaker, character, explanations), and optionally english
(localization key to {content: text}). No database/network access or writes:
output is a per-release selective-repair report on stdout.
"""

import argparse
import json
from pathlib import Path

from wuwa_story.ingestion.character_identity import MAIN_ROLE_PATH
from wuwa_story.ingestion.speaker_crosswalk import (
    PERSONAL_PORTRAIT_BASIS,
    PLOT_GUEST_BASIS,
    PLOT_GUEST_PATH,
    ROLE_PATH,
    SPEAKER_PATH,
    speaker_character_links,
)


def audit_snapshot(snapshot, *, max_rows=20000):
    release_id = snapshot['release_id']
    if type(release_id) is not int or release_id <= 0:
        raise ValueError('A positive pinned release_id is required')
    rows = snapshot['source_rows']
    if not isinstance(rows, list) or not 1 <= len(rows) <= max_rows:
        raise ValueError(
            'Source rows exceed the bounded snapshot profile or are empty')
    groups = {path: [] for path in (ROLE_PATH, SPEAKER_PATH, MAIN_ROLE_PATH, PLOT_GUEST_PATH)}
    for row in rows:
        if row.get('release_id', release_id) != release_id:
            raise ValueError('Cannot mix source rows from different releases')
        if row['file'] in groups:
            groups[row['file']].append((row['row_index'], row['data']))
    if not groups[ROLE_PATH] or not groups[SPEAKER_PATH]:
        raise ValueError('Both retained identity tables are required')
    candidates = list(speaker_character_links(
        groups[ROLE_PATH], groups[SPEAKER_PATH], [
            raw for _, raw in groups[MAIN_ROLE_PATH]],
        guests=groups[PLOT_GUEST_PATH], english=snapshot.get('english'),
    ))
    existing = snapshot.get('existing_crosswalks') or []
    old_pairs = {(f"speaker:{row['speaker']}",
                  row['character']) for row in existing}
    proved_pairs = {(f"speaker:{row['speaker']}", row['character']) for row in existing
                    if any(text.startswith((PERSONAL_PORTRAIT_BASIS, PLOT_GUEST_BASIS))
                           for text in row.get('explanations', []) if isinstance(text, str))}
    expected = {(row['from'], row['to']) for row in candidates}
    new_pairs = expected - old_pairs
    pending = expected - proved_pairs
    targets = {speaker: character for speaker, character in expected}
    conflicts = sorted((speaker, character) for speaker, character in old_pairs
                       if speaker in targets and targets[speaker] != character)
    return {
        'release_id': release_id, 'game_version': snapshot.get('game_version'),
        'source_rows': len(rows), 'candidate_links': len(expected),
        'characters': len({character for _, character in expected}),
        'missing_identity_links': len(new_pairs), 'personal_proof_repairs': len(pending),
        'new_identity_role_ids': sorted({int(character.split(':')[1]) for _, character in new_pairs}),
        'role_ids': sorted({int(character.split(':')[1]) for _, character in pending}),
        'legacy_conflicting_links': conflicts,
        'legacy_unresolved_links': sorted(old_pairs - expected),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshots', type=Path)
    parser.add_argument('--max-releases', type=int, default=21)
    parser.add_argument('--max-rows', type=int, default=20000)
    args = parser.parse_args()
    if not 1 <= args.max_releases <= 100 or not 1 <= args.max_rows <= 100000:
        parser.error(
            'Audit limits must be positive and bounded (100 releases / 100000 rows)')
    seen = set()
    with args.snapshots.open(encoding='utf-8') as stream:
        for line in stream:
            if not line.strip():
                continue
            if len(line) > 16 * 1024 * 1024:
                raise ValueError('Snapshot line exceeds 16 MiB')
            snapshot = json.loads(line)
            if snapshot['release_id'] in seen or len(seen) >= args.max_releases:
                raise ValueError(
                    'Duplicate release or release budget exceeded')
            seen.add(snapshot['release_id'])
            print(json.dumps(audit_snapshot(
                snapshot, max_rows=args.max_rows)), flush=True)


if __name__ == '__main__':
    main()
