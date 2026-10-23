"""Project already imported WeaponConf rows into the item catalog without reimporting a snapshot."""

import argparse
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from sqlalchemy import select

from wuwa_story.db.models.core import Item
from wuwa_story.db.models.graph import Node, NodeRevision
from wuwa_story.db.models.i18n import Locale, LocalizationKey, LocalizationValue
from wuwa_story.db.models.ops import GameRelease
from wuwa_story.db.models.raw import SourceFile, SourceRecord
from wuwa_story.db.session import SessionFactory
from wuwa_story.ingestion.canonical_import import import_canonical_entities
from wuwa_story.search.indexer import build_lexical_index

SOURCE = "BinData/weapon/weaponconf.json"


async def backfill(release_id: int, dry_run: bool = False):
    async with SessionFactory() as session:
        release = await session.get(GameRelease, release_id)
        if release is None:
            raise ValueError("Unknown imported release")
        rows = list(await session.scalars(select(SourceRecord).join(SourceFile).where(
            SourceRecord.release_id == release_id, SourceFile.logical_source_path == SOURCE
        ).order_by(SourceRecord.row_index)))
        existing = set(await session.scalars(select(Node.canonical_key).join(Item, Item.node_id == Node.id)
            .join(NodeRevision, NodeRevision.node_id == Node.id).where(
            NodeRevision.release_id == release_id, Node.canonical_key.in_(
                [f"item:{row.data['ItemId']}" for row in rows]))))
        rows = [row for row in rows if f"item:{row.data['ItemId']}" not in existing]
        if dry_run or not rows:
            return {"release_id": release_id, "missing_weapons": len(rows), "imported": 0}
        keys = {row.data.get(field) for row in rows for field in ("WeaponName", "BgDescription")} - {None}
        labels = dict((await session.execute(select(LocalizationKey.key, LocalizationValue.content)
            .join(LocalizationValue, LocalizationValue.key_id == LocalizationKey.id)
            .join(Locale, Locale.id == LocalizationValue.locale_id).where(
                LocalizationValue.release_id == release_id, Locale.code == "en", LocalizationKey.key.in_(keys)))).all())
        records = []
        for row in rows:
            raw = row.data
            records.append({"id": f"item:{raw['ItemId']}", "game_id": raw["ItemId"],
                            "kind": "item", "version": release.game_version,
                            "source_repository": release.upstream_name, "source_commit": release.upstream_commit,
                            "source": {"file": SOURCE, "row": row.row_index, "raw_path": f"$[{row.row_index}]"},
                            "raw": raw, "name": {"key": raw.get("WeaponName"), "en": labels.get(raw.get("WeaponName"))},
                            "bgdescription": {"key": raw.get("BgDescription"), "en": labels.get(raw.get("BgDescription"))}})
        # The importer consumes only this projection; source record IDs still
        # resolve against the original durable raw snapshot in PostgreSQL.
        with TemporaryDirectory(prefix="weapon-projection-") as temporary:
            root = Path(temporary)
            path = root / "entities/item.jsonl"
            path.parent.mkdir()
            path.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")
            adapter = SimpleNamespace(root=root, entity_files=lambda: [path],
                                      iter_entities=lambda kind: iter(records) if kind == "item" else iter(()))
            count = await import_canonical_entities(adapter, session, release_id, release.sequence, batch_size=100)
        await build_lexical_index(session, release.game_version, batch_size=100, categories={"item"})
        await session.commit()
        return {"release_id": release_id, "missing_weapons": len(rows), "imported": count}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-id", type=int, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(backfill(args.release_id, args.dry_run))))
