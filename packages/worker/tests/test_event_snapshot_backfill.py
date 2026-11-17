import hashlib
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from wuwa_story.db.models.game_events import GameEventSnapshot, GameEventSnapshotMember

from wuwa_story_worker import event_snapshot_backfill as backfill
from wuwa_story_worker.client_assets import plan_id


def write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def exports(tmp_path):
    plan = {"schema_version": 1, "job_type": "assets.download", "version": "3.8.0", "tier": "hd",
            "keys_commit": "a" * 40, "cdns": ["https://hw-pcdownload-qcloud.aki-game.net/"], "base_path": "assets",
            "files": [{"path": "Client/Content/Paks/test.pak", "size": 1, "md5": "b" * 32}]}
    plan["id"] = plan_id(plan)
    plan_path = tmp_path / "plan.json"
    write_json(plan_path, plan)
    write_json(tmp_path / "status.json", {"state": "downloaded", "id": plan["id"]})
    config = tmp_path / "config" / "files" / "ConfigDB"
    readers = tmp_path / "readers" / "files" / "Config"
    config.mkdir(parents=True)
    readers.mkdir(parents=True)
    for file, table, ids in [("db_activity.db", "activity", [100, 101]),
                              ("db_gacha.db", "gachaviewinfo", [200])]:
        with sqlite3.connect(config / file) as db:
            db.execute(f"CREATE TABLE {table} (Id INTEGER)")
            db.executemany(f"INSERT INTO {table} VALUES (?)", [(value,) for value in ids])
    for file, methods in {"Activity.js": {"title": 46, "desc": 50, "bgresource": 52},
                          "GachaViewInfo.js": {"showidlist": 36, "underbgtexturepath": 24}}.items():
        (readers / file).write_text("".join(f"{name}(){{return this.x.__offset(this.y,{offset})}}"
                                            for name, offset in methods.items()), encoding="utf-8")
    manifests = []
    for parent, directory, filter_name in [(tmp_path / "config", config, "ConfigDB"),
                                            (tmp_path / "readers", readers, "JavaScript/Core/Define/Config")]:
        entries = []
        for file in directory.iterdir():
            content = file.read_bytes()
            entries.append({"path": file.relative_to(parent / "files").as_posix(), "size": len(content),
                            "sha256": hashlib.sha256(content).hexdigest()})
        path = parent / "manifest.json"
        write_json(path, {"schema_version": 1, "job_id": plan["id"], "version": plan["version"],
                          "tier": plan["tier"], "keys_commit": plan["keys_commit"],
                          "extractor_sha256": "c" * 64, "filter": filter_name, "files": entries})
        manifests.append(path)
    return plan, plan_path, *manifests


def test_catalog_uses_both_verified_client_databases(exports):
    plan, plan_path, config, readers = exports
    actual, identities = backfill.load_identities(plan_path, config, readers)
    assert actual["id"] == plan["id"]
    assert identities == {"activity:100", "activity:101", "gacha:200"}


def test_backfill_rejects_corrupt_or_mismatched_exports(exports):
    plan, plan_path, config, readers = exports
    manifest = json.loads(readers.read_text())
    manifest["job_id"] = "wrong"
    write_json(readers, manifest)
    with pytest.raises(ValueError, match="pinned client"):
        backfill.load_identities(plan_path, config, readers)
    manifest["job_id"] = plan["id"]
    write_json(readers, manifest)
    path = config.parent / "files" / "ConfigDB" / "db_activity.db"
    with path.open("ab") as stream:
        stream.write(b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        backfill.load_identities(plan_path, config, readers)


def session_for(rows, snapshot=None, existing=()):
    session = AsyncMock()
    session.execute.return_value = Mock(all=Mock(return_value=rows))
    session.get.return_value = snapshot
    session.scalars.return_value = list(existing)
    added = []
    session.add = added.append
    session.add_all = added.extend
    return session, added


@pytest.mark.asyncio
async def test_apply_writes_only_membership_and_refuses_missing_ids(exports):
    plan, *_ = exports
    identities = {"activity:100", "gacha:200"}
    session, added = session_for([("activity:100", 1)])
    result = await backfill.backfill(session, plan, identities, apply=True)
    assert result["missing"] == ["gacha:200"] and not result["applied"]
    assert added == [] and session.execute.await_count == 2  # lock and lookup only
    session, added = session_for([("activity:100", 1), ("gacha:200", 2)])
    result = await backfill.backfill(session, plan, identities, apply=True)
    assert result["applied"]
    assert [type(row) for row in added] == [GameEventSnapshot, GameEventSnapshotMember, GameEventSnapshotMember]
    assert {row.event_id for row in added[1:]} == {1, 2}
    assert all("DELETE" not in str(call.args[0]) for call in session.execute.await_args_list)


@pytest.mark.asyncio
async def test_dry_run_is_read_only_and_conflicting_complete_snapshot_is_rejected(exports):
    plan, *_ = exports
    identities = {"activity:100"}
    session, added = session_for([("activity:100", 1)])
    assert not (await backfill.backfill(session, plan, identities))["applied"]
    assert added == [] and session.execute.await_count == 1
    session, _ = session_for([("activity:100", 1)], SimpleNamespace(download_id="other", complete=True))
    with pytest.raises(ValueError, match="different download"):
        await backfill.backfill(session, plan, identities, apply=True)
    session, _ = session_for([("activity:100", 1)], SimpleNamespace(download_id=plan["id"], complete=True), [2])
    with pytest.raises(ValueError, match="differs"):
        await backfill.backfill(session, plan, identities, apply=True)
    session, added = session_for([("activity:100", 1)], SimpleNamespace(download_id=plan["id"], complete=True), [1])
    assert not (await backfill.backfill(session, plan, identities, apply=True))["applied"]
    assert added == []


@pytest.mark.asyncio
async def test_incomplete_backfill_preserves_existing_rows_and_rejects_extra_members(exports):
    plan, *_ = exports
    head = SimpleNamespace(download_id=plan["id"], complete=False)
    session, added = session_for([("activity:100", 1), ("gacha:200", 2)], head, [1])
    result = await backfill.backfill(session, plan, {"activity:100", "gacha:200"}, apply=True)
    assert result["applied"] and head.complete
    assert [(row.event_id, row.client_capture) for row in added] == [(2, None)]
    assert all("DELETE" not in str(call.args[0]) for call in session.execute.await_args_list)
    head.complete = False
    session, _ = session_for([("activity:100", 1)], head, [1, 2])
    with pytest.raises(ValueError, match="membership differs"):
        await backfill.backfill(session, plan, {"activity:100"}, apply=True)
