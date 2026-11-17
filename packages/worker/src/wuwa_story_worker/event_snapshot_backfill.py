"""Restore event membership from verified exports; defaults to a read-only report."""

import argparse
import asyncio
import hashlib
import json
import re
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import select, text
from wuwa_story.db.models.game_events import GameEvent, GameEventSnapshot, GameEventSnapshotMember
from wuwa_story.db.session import SessionFactory
from wuwa_story.ingestion.game_events import CLIENT_SOURCE

from wuwa_story_worker.client_assets import validate_plan
from wuwa_story_worker.event_catalog import validate_event_schema


def _verified_files(manifest_path, plan, expected_filter, names, destination):
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("schema_version") != 1 or manifest.get("job_id") != plan["id"]
            or manifest.get("version") != plan["version"] or manifest.get("tier") != plan["tier"]
            or manifest.get("keys_commit") != plan["keys_commit"] or manifest.get("filter") != expected_filter
            or not re.fullmatch(r"[0-9a-f]{64}", manifest.get("extractor_sha256", ""))):
        raise ValueError("Export manifest does not match the pinned client and filter")
    root = (manifest_path.parent / "files").resolve()
    found = {}
    for entry in manifest.get("files", []):
        path = Path(entry["path"])
        if path.name not in names:
            continue
        if path.is_absolute() or ".." in path.parts or path.name in found:
            raise ValueError("Ambiguous or unsafe export path")
        source = (root / path).resolve()
        if not source.is_relative_to(root) or not source.is_file():
            raise ValueError("Export file is missing or outside its manifest")
        digest = hashlib.sha256()
        target = destination / path.name
        size = 0
        with source.open("rb") as stream, target.open("wb") as output:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
                size += len(block)
                output.write(block)
        if size != entry.get("size") or digest.hexdigest() != entry.get("sha256"):
            raise ValueError(f"Export checksum mismatch: {path.name}")
        found[path.name] = target
    if found.keys() != set(names):
        raise ValueError("Export manifest does not contain every required catalog file")
    return found


def load_identities(plan_path, config_manifest, readers_manifest):
    plan_path = Path(plan_path)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    validate_plan(plan)
    status = json.loads((plan_path.parent / "status.json").read_text(encoding="utf-8"))
    if status.get("state") != "downloaded" or status.get("id") != plan["id"]:
        raise ValueError("Client download is not complete")
    if not re.fullmatch(r"\d+\.\d+\.\d+", plan["version"]):
        raise ValueError("A full client version is required")
    with TemporaryDirectory(prefix="event-snapshot-") as temporary:
        destination = Path(temporary)
        config = _verified_files(config_manifest, plan, "ConfigDB", {"db_activity.db", "db_gacha.db"}, destination)
        _verified_files(readers_manifest, plan, "JavaScript/Core/Define/Config",
                        {"Activity.js", "GachaViewInfo.js"}, destination)
        validate_event_schema(destination)
        identities = set()
        for filename, table_name, prefix in (("db_activity.db", "activity", "activity:"),
                                             ("db_gacha.db", "gachaviewinfo", "gacha:")):
            with closing(sqlite3.connect(config[filename].as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
                rows = list(db.execute(f"SELECT Id FROM {table_name}"))
            if not rows or any(identifier is None or str(identifier) == "" for (identifier,) in rows):
                raise ValueError(f"Empty or invalid {table_name} catalog")
            group = {prefix + str(identifier) for (identifier,) in rows}
            if len(group) != len(rows):
                raise ValueError(f"Duplicate IDs in {table_name} catalog")
            identities.update(group)
    return plan, identities


async def backfill(session, plan, identities, *, apply=False):
    if not identities:
        raise ValueError("Empty catalog")
    if apply:
        # Serialize against the regular importer and other backfills.
        await session.execute(text("SELECT pg_advisory_xact_lock(705, 1)"))
    rows = (await session.execute(select(GameEvent.source_id, GameEvent.id).where(
        GameEvent.source == CLIENT_SOURCE, GameEvent.source_id.in_(sorted(identities))
    ))).all()
    mapped = dict(rows)
    missing = sorted(identities - mapped.keys())
    snapshot = await session.get(GameEventSnapshot, plan["version"])
    if snapshot and snapshot.download_id != plan["id"]:
        raise ValueError("Existing snapshot belongs to a different download")
    existing = set(await session.scalars(select(GameEventSnapshotMember.event_id).where(
        GameEventSnapshotMember.version == plan["version"]
    ))) if snapshot else set()
    ids = set(mapped.values())
    if snapshot and snapshot.complete and (missing or existing != ids):
        raise ValueError("Existing complete snapshot differs from verified catalog")
    if snapshot and not missing and not existing <= ids:
        raise ValueError("Existing snapshot membership differs from verified catalog")
    result = {"version": plan["version"], "download_id": plan["id"], "catalog_records": len(identities),
              "matched": len(mapped), "missing": missing, "applied": False}
    if not apply or missing or snapshot and snapshot.complete:
        return result
    if snapshot is None:
        session.add(GameEventSnapshot(version=plan["version"], download_id=plan["id"], complete=True))
        await session.flush()
    else:
        snapshot.complete = True
    session.add_all([GameEventSnapshotMember(version=plan["version"], event_id=event_id)
                     for event_id in sorted(ids - existing)])
    await session.flush()
    result["applied"] = True
    return result


async def run(args):
    plan, identities = await asyncio.to_thread(load_identities, args.plan, args.config_manifest, args.readers_manifest)
    async with SessionFactory() as session:
        result = await backfill(session, plan, identities, apply=args.apply)
        if args.apply and result["applied"]:
            await session.commit()
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--config-manifest", type=Path, required=True)
    parser.add_argument("--readers-manifest", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="Publish only if every identity matches")
    args = parser.parse_args()
    result = asyncio.run(run(args))
    print(json.dumps(result))
    if args.apply and result["missing"]:
        sys.exit(2)


if __name__ == "__main__":
    main()
