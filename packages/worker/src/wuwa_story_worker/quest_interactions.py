"""Compact narrative entity index from the pinned client's existing ConfigDB export."""

import hashlib
import json
from collections import defaultdict
from contextlib import closing

from wuwa_story.ingestion.level_interactions import (
    entity_members,
    interaction_options,
    membership_hash,
)

from wuwa_story_worker.map_sources import integer, open_db, table, text


def read_interactions(config):
    memberships = {}
    owners = defaultdict(list)
    with closing(open_db(config / 'db_LevelPlayData.db')) as db:
        for identity, blob in db.execute('SELECT LevelPlayId, BinData FROM levelplaydata'):
            row = table(blob)
            if integer(row, 0) != identity:
                raise ValueError('Client LevelPlay ID does not match its ConfigDB record')
            data = json.loads(text(row, 1))
            memberships[identity] = membership_hash(data)
            for entity in entity_members(data):
                owners[entity].append(identity)
    found = defaultdict(list)
    with closing(open_db(config / 'db_level_entity.db')) as db:
        for identity, map_id, entity_id, blob in db.execute(
                'SELECT Id, MapId, EntityId, BinData FROM levelentityconfig ORDER BY Id'):
            key = f'e_{map_id}_{entity_id}'
            if key not in owners:
                continue
            row = table(blob)
            if (integer(row, 0), integer(row, 1), integer(row, 2)) != (identity, map_id, entity_id):
                raise ValueError('Client entity ID does not match its ConfigDB record')
            source = {'file': 'Client/Content/Aki/ConfigDB/db_level_entity.db',
                      'key': str(identity), 'sha256': hashlib.sha256(blob).hexdigest()}
            options = list(interaction_options(map_id, entity_id, json.loads(text(row, 10) or '{}'), source))
            for owner in owners[key]:
                found[owner].extend(options)
    return {identity: {'interaction_membership_sha256': digest, 'interactions': found[identity]}
            for identity, digest in memberships.items()}
