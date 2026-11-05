import copy
import importlib.util
from pathlib import Path
from types import SimpleNamespace as Row
from unittest.mock import AsyncMock

import pytest

SCRIPT = Path(__file__).parents[1] / "plan-image-recovery.py"
spec = importlib.util.spec_from_file_location("image_recovery_plan", SCRIPT)
planner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(planner)


def target(path="/Game/Aki/Image.Test", **extra):
    return {"owner": 1, "release_id": 4, "path": path, "kind": "image",
            "language": None, **extra}


def request():
    return planner.MediaRequest(download_id="a" * 64, asset_version="3.7.1", game_version="1.4.0",
                                release_id=4, workspace_id=4787, entities=["character:5011"],
                                kinds=["image"])


def test_diff_preserves_removed_added_and_changed_targets():
    old = [target(), target("removed"), target("spine", kind="spine", spine_recipe=3)]
    current = [target(), target("added"), target("spine", kind="spine", spine_recipe=4)]
    diff = planner.compare_targets(old, current)
    assert diff["removed"] == [old[1]]
    assert diff["added"] == [current[1]]
    assert diff["changed"] == [{"old": old[2], "current": current[2]}]


@pytest.mark.parametrize("original,current,count,action", [
    ("completed", None, 1, "skip_completed"),
    ("failed", "completed", 1, "skip_completed"),
    ("failed", "queued", 1, "reuse_current_delivery"),
    ("failed", "waiting_dependency", 1, "reuse_current_delivery"),
    ("running", None, 1, "leave_original_delivery"),
    ("queued", "completed", 1, "leave_original_delivery"),
    ("cancelled", None, 1, "hold_cancelled"),
    ("failed", "cancelled", 1, "hold_cancelled"),
    ("failed", None, 0, "no_current_authored_targets"),
    ("failed", None, 1, "review_then_normal_enqueue_current_request"),
])
def test_actions_never_duplicate_completed_or_active_work(original, current, count, action):
    assert planner.action_for(original, current, [target()] * count) == action


def test_removed_foreign_skills_are_explained_by_current_source_filter():
    raw = {"Id": 5011, "RoleType": 2, "SkillId": 1402, "Name": "Trial",
           "FormationRoleCard": "/Game/Shared/Card"}
    template = {"Id": 1402, "Name": "Yangyang", "FormationRoleCard": "/Game/Shared/Card"}
    removed = target("/Game/Yangyang/Skill")
    assert planner.removal_reason(removed, raw, template, {removed["path"]}) == (
        "inherited_foreign_role_skill_tree_resonance_filter"
    )
    assert planner.removal_reason(target("/Game/Shared/Card"), raw, template, set()) == (
        "inherited_foreign_role_field"
    )
    assert planner.removal_reason(removed, {**raw, "RoleType": 1}, template, {removed["path"]}) == (
        "unexplained_source_or_resolver_change"
    )


def test_placeholder_and_unknown_removals_are_not_conflated():
    path = "/Game/Common/T_ActivityRoleYangyang.T_ActivityRoleYangyang"
    raw = {"Id": 5011, "RolePortrait": path}
    assert planner.removal_reason(target(path), raw, {}, set()) == "shared_or_placeholder_artwork_filter"
    assert planner.removal_reason(target("unrelated"), raw, {}, set()) == "unexplained_source_or_resolver_change"


@pytest.mark.asyncio
async def test_changed_identity_plans_current_request_and_skips_matching_completed_run(monkeypatch):
    req = request()
    old, current = [target("removed"), target()], [target()]
    payload = planner.payload_for(req, old)
    run = Row(id=25126, processor_id=2, status="failed",
              input_hash=bytes.fromhex(planner.digest(payload)),
              metadata_json={"request": req.model_dump(), "payload": {**payload, "run_id": 25126}})
    session = AsyncMock()
    session.get.side_effect = [run, Row(key="entity_media")]
    session.scalar.return_value = Row(id=30000, status="completed")
    monkeypatch.setattr(planner, "entity_media_targets", AsyncMock(return_value=current))
    monkeypatch.setattr(planner, "explain_removed", AsyncMock(return_value=[]))
    monkeypatch.setattr(planner, "coverage", AsyncMock(return_value=[]))
    report = await planner.plan_run(session, 25126)
    assert report["action"] == "skip_completed"
    assert report["current_request"] == req.model_dump()
    assert report["current_input_hash"] == planner.digest(planner.payload_for(req, current))
    assert report["current_input_hash"] != report["source_input_hash"]
    assert report["old_targets"] == old and report["current_targets"] == current
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_saved_identity_mismatch_stops_before_resolution(monkeypatch):
    req = request()
    run = Row(processor_id=2, input_hash=b"x" * 32,
              metadata_json={"request": req.model_dump(), "payload": {
                  **planner.payload_for(req, [target()]), "run_id": 25126,
              }})
    session = AsyncMock()
    session.get.side_effect = [run, Row(key="entity_media")]
    resolve = AsyncMock()
    monkeypatch.setattr(planner, "entity_media_targets", resolve)
    with pytest.raises(ValueError, match="input_hash mismatch"):
        await planner.plan_run(session, 25126)
    resolve.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("available,state", [(True, "db_candidate_unverified"), (False, "missing_or_unavailable")])
async def test_coverage_does_not_mutate_locations_or_claim_storage_verification(available, state):
    provenance = {"schema_version": 1, "download_id": "a" * 64, "asset_version": "3.7.1",
                  "tier": "hd", "package_path": "/Game/Aki/Image", "tools": {},
                  "source_files": [{"package_path": "Client/Content/file", "sha256": "b" * 64}]}
    ref = Row(owner_node_id=1, release_id=4, source_path=target()["path"], source_name=None,
              reference_type="entity_image", metadata_json={"game_source": provenance})
    loc = Row(available=available, verified_at="unchanged")
    before = copy.deepcopy(vars(loc))
    session = AsyncMock()
    session.execute.return_value = [(ref, Row(id=9), loc)]
    report = await planner.coverage(session, request(), [target(), target("missing")])
    assert [r["state"] for r in report] == [state, "no_reference"]
    assert vars(loc) == before
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_entrypoint_uses_read_only_snapshots_and_only_rolls_back(monkeypatch):
    from wuwa_story.db import session as db

    session = AsyncMock()
    session.__aenter__.return_value = session
    engine = Row(dispose=AsyncMock())
    monkeypatch.setattr(db, "SessionFactory", lambda: session)
    monkeypatch.setattr(db, "engine", engine)
    monkeypatch.setattr(planner, "plan_run", AsyncMock(return_value={"dry": True}))
    report = await planner.main_async(Row(run_id=[25117, 25126], max_seconds=10))
    assert report["mode"] == "dry_run_only"
    commands = [str(call.args[0]) for call in session.execute.await_args_list]
    assert sum("REPEATABLE READ, READ ONLY" in command for command in commands) == 2
    assert session.rollback.await_count == 2
    session.commit.assert_not_awaited()
    engine.dispose.assert_awaited_once()
