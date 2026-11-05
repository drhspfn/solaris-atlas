"""Attach existing source roles to public identities without reimporting media.

python -m wuwa_story.ingestion.character_identity_backfill --apply --refresh
Without --apply this command only reports the proposed changes.
"""

import argparse
import asyncio
import json

from sqlalchemy import select

from wuwa_story.db.models.core import Character
from wuwa_story.db.models.graph import Node
from wuwa_story.db.models.ops import GameRelease
from wuwa_story.db.models.raw import SourceFile, SourceRecord
from wuwa_story.db.session import SessionFactory
from wuwa_story.ingestion.character_artwork import inherited_character_fields
from wuwa_story.ingestion.character_identity import MAIN_ROLE_PATH, main_role_identities
from wuwa_story.read_models.refresh import mark_dirty


async def backfill(*, apply=False, refresh=False):
    async with SessionFactory() as session:
        release = await session.scalar(select(GameRelease.id).join(
            SourceFile, SourceFile.release_id == GameRelease.id
        ).where(SourceFile.logical_source_path == MAIN_ROLE_PATH).order_by(
            GameRelease.sequence.desc()).limit(1))
        paths = (MAIN_ROLE_PATH, "BinData/role/roleinfo.json", "BinData/element_info/elementinfo.json")
        rows = list(await session.execute(select(SourceFile.logical_source_path, SourceRecord.data)
            .join(SourceRecord, SourceRecord.source_file_id == SourceFile.id)
            .where(SourceFile.release_id == release, SourceFile.logical_source_path.in_(paths))))
        groups = {path: [data for actual, data in rows if actual == path] for path in paths}
        identities = main_role_identities(groups[paths[0]], groups[paths[1]], groups[paths[2]])
        if not identities:
            raise ValueError("No complete main-role configuration; no identities were modified")
        roles = {row["Id"]: row for row in groups[paths[1]]}
        changed, refresh_ids, family_ids = [], set(), set()
        characters = await session.execute(select(Character, Node).join(Node, Node.id == Character.node_id))
        for character, node in characters:
            role_id = (character.metadata_json or {}).get("game_character_id")
            identity = identities.get(role_id)
            if identity:
                family_ids.add(node.id)
            metadata = character.metadata_json or {}
            if metadata.get("public_identity") != identity:
                changed.append(node.id)
                if apply:
                    character.metadata_json = {**metadata, "public_identity": identity}
            role = roles.get(role_id, {})
            if inherited_character_fields(role, roles.get(role.get("SkillId"), {})):
                refresh_ids.add(node.id)
        if changed:
            refresh_ids.update(changed)
            refresh_ids.update(family_ids)
        if refresh:
            refresh_ids.update(family_ids)
        if apply:
            if refresh_ids and (changed or refresh):
                await mark_dirty(session, "character", refresh_ids)
            await session.commit()
        return {"release_id": release, "family_members": len(family_ids),
                "changed": len(changed), "refresh_candidates": len(refresh_ids), "applied": apply}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--refresh", action="store_true", help="Refresh affected public pages even if identity metadata matches")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(backfill(apply=args.apply, refresh=args.refresh))))


if __name__ == "__main__":
    main()
