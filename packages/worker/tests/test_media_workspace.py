import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story_worker import media_workspace as workspaces


def ready_client(base, name="assets/client"):
    source = base / name
    (source / "game").mkdir(parents=True)
    (source / "game/archive.pak").write_bytes(b"retained")
    (source / "plan.json").write_text(json.dumps({"id": "a" * 64}))
    (source / "status.json").write_text(json.dumps({"state": "downloaded", "id": "a" * 64}))
    (source / "keys.txt").write_text("fixture-key")
    return source


def test_workspace_binds_inputs_and_keeps_raw_outputs_separate(tmp_path):
    client = ready_client(tmp_path)
    first = workspaces.resolve_workspace(client, 11, base=tmp_path)
    second = workspaces.resolve_workspace(client, 12, base=tmp_path)
    assert workspaces.resolve_workspace(client, 11, base=tmp_path) == first
    for name in ("game", "plan.json", "status.json", "keys.txt"):
        assert (first / name).is_symlink()
        assert (first / name).resolve() == client / name
    (first / "exports").mkdir()
    (first / "exports/raw").write_bytes(b"temporary")
    assert not (client / "exports").exists()
    assert not (second / "exports").exists()
    assert workspaces.remove_workspace(11, base=tmp_path)
    assert not workspaces.remove_workspace(11, base=tmp_path)
    assert (client / "game/archive.pak").read_bytes() == b"retained"
    assert second.is_dir()
    assert workspaces.resolve_workspace(client, 11, base=tmp_path) == first


@pytest.mark.parametrize("value", [0, -1, True, "12", 2**63])
def test_invalid_workspace_ids_cannot_choose_paths(tmp_path, value):
    with pytest.raises(ValueError, match="positive"):
        workspaces.workspace_path(value, base=tmp_path)


def test_workspace_cannot_be_rebound_or_redirected(tmp_path):
    first, second = ready_client(tmp_path), ready_client(tmp_path, "assets/other")
    root = workspaces.resolve_workspace(first, 11, base=tmp_path)
    with pytest.raises(ValueError, match="another client"):
        workspaces.resolve_workspace(second, 11, base=tmp_path)
    (root / "exports").symlink_to(second, target_is_directory=True)
    workspaces.remove_workspace(11, base=tmp_path)
    assert (second / "game/archive.pak").is_file()
    root.symlink_to(second, target_is_directory=True)
    with pytest.raises(ValueError, match="directory is a link"):
        workspaces.remove_workspace(11, base=tmp_path)
    assert (second / "keys.txt").is_file()


def test_workspace_container_cannot_escape_through_symlink(tmp_path):
    client = ready_client(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / "patch-workspaces").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(ValueError, match="directory is a link"):
        workspaces.resolve_workspace(client, 11, base=tmp_path)
    assert not list(elsewhere.iterdir())


@pytest.mark.parametrize("kind", ["voices", "videos"])
def test_package_outputs_are_scoped_but_downloads_survive(tmp_path, kind):
    client = ready_client(tmp_path)
    workspaces.resolve_workspace(client, 11, base=tmp_path)
    package = ready_client(tmp_path, f"{kind}/3.7.0-{'a' * 16}")
    parent = {"workspace_id": 11, "asset_version": "3.7.0"}
    root = workspaces.bind_package(parent, kind, "a" * 64, package, base=tmp_path)
    assert root == workspaces.package_root(parent, kind, "a" * 64, base=tmp_path)
    (root / "plot-audio").mkdir()
    (root / "plot-audio/raw.wem").write_bytes(b"raw")
    workspaces.remove_workspace(11, base=tmp_path)
    assert (package / "game/archive.pak").is_file()
    assert not (package / "plot-audio").exists()


@pytest.mark.parametrize("kind,relative", [
    ("cutscene", "release-media/90"), ("voices", "voices/3.7.0-aaaaaaaaaaaaaaaa/voice-batches/90")])
def test_job_cleanup_preserves_inputs_shared_exports_and_other_jobs(tmp_path, kind, relative):
    client = ready_client(tmp_path)
    root = workspaces.resolve_workspace(client, 11, base=tmp_path)
    owned = root / relative
    owned.mkdir(parents=True)
    (owned / "converted.wav").write_bytes(b"large temporary output")
    (owned / "linked-input").symlink_to(client / "game", target_is_directory=True)
    sibling = owned.parent / "91"
    sibling.mkdir()
    (sibling / "other.wav").write_bytes(b"other job")
    shared = root / "exports/shared.raw"
    shared.parent.mkdir()
    shared.write_bytes(b"shared raw")
    parent = {"workspace_id": 11, "asset_version": "3.7.0", "voice_plan_id": "a" * 64}
    assert workspaces.remove_job_output(parent, 90, kind, base=tmp_path)
    assert not workspaces.remove_job_output(parent, 90, kind, base=tmp_path)
    assert not owned.exists()
    assert shared.is_file() and (sibling / "other.wav").is_file()
    assert (client / "game/archive.pak").read_bytes() == b"retained"


def test_job_cleanup_rejects_redirected_parent(tmp_path):
    root = workspaces.resolve_workspace(ready_client(tmp_path), 11, base=tmp_path)
    outside = tmp_path / "outside"
    (outside / "90").mkdir(parents=True)
    original = outside / "90/retain.mp4"
    original.write_bytes(b"retained")
    (root / "release-media").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="directory is a link"):
        workspaces.remove_job_output({"workspace_id": 11}, 90, "cutscene", base=tmp_path)
    assert original.is_file()


def run(key, status="completed", parent=None, workspace=None, child=None, kind="images"):
    request = {"kind": kind, "parent_id": parent, "workspace_id": workspace}
    return SimpleNamespace(id=key, status=status, metadata_json={"request": request},
                           raw_output={"entity_run_id": child} if child else {})


def test_graph_includes_nested_children_and_entity_jobs_but_not_other_patch():
    runs = [run(11, kind="prepare"), run(20, parent=11), run(21, parent=20, child=30),
            run(30), run(31, workspace=11), run(40, parent=90)]
    assert workspaces._members(runs, 11) == {11, 20, 21, 30, 31}


@pytest.mark.asyncio
async def test_workspace_resolver_uses_ancestry_before_prepare_has_output():
    owner, leaf = run(11, kind="prepare"), run(20, parent=11)
    session = AsyncMock()
    session.get.side_effect = [leaf, owner]
    assert await workspaces.run_workspace_id(20, session) == 11
    session.get.side_effect = [run(30, workspace=11)]
    assert await workspaces.run_workspace_id(30, session) == 11
    session.get.side_effect = [run(40, parent=40)]
    assert await workspaces.run_workspace_id(40, session) is None


def mock_database(monkeypatch, runs, *, acquired=True):
    connection, session = AsyncMock(), AsyncMock()
    connection.scalar.return_value = acquired
    session.scalars.return_value = runs

    @asynccontextmanager
    async def connect():
        yield connection

    @asynccontextmanager
    async def factory(**kwargs):
        assert kwargs["bind"] is connection
        yield session

    monkeypatch.setattr(workspaces, "engine", SimpleNamespace(connect=connect))
    monkeypatch.setattr(workspaces, "SessionFactory", factory)
    return connection, session


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "queued", "running", "waiting_dependency", "unknown"])
async def test_cleanup_never_deletes_unfinished_descendants(monkeypatch, status):
    _, session = mock_database(monkeypatch, [run(11, kind="prepare"), run(20, status, parent=11)])
    monkeypatch.setattr(workspaces, "remove_workspace", lambda *_: pytest.fail("active task"))
    assert not await workspaces.cleanup_workspace(11)
    assert "WITH RECURSIVE" in str(session.scalars.await_args.args[0])


@pytest.mark.asyncio
async def test_active_execution_lease_blocks_cleanup_even_for_cancelled_rows(monkeypatch):
    connection, session = mock_database(monkeypatch, [run(11, "cancelled", kind="prepare")], acquired=False)
    monkeypatch.setattr(workspaces, "remove_workspace", lambda *_: pytest.fail("leased task"))
    assert not await workspaces.cleanup_workspace(11)
    session.scalars.assert_not_awaited()
    assert connection.scalar.await_args.args[1] == {"key": 11}
    connection.invalidate.assert_awaited_once()


@pytest.mark.asyncio
async def test_terminal_tree_is_cleaned_after_recheck_under_lock(monkeypatch):
    connection, session = mock_database(monkeypatch, [run(11, kind="prepare"),
        run(20, "partial", parent=11, child=30), run(30, "failed"), run(31, "cancelled", workspace=11)])
    calls = []

    def remove(key):
        connection.commit.assert_awaited_once()
        session.scalars.assert_awaited_once()
        calls.append(key)
        return True

    monkeypatch.setattr(workspaces, "remove_workspace", remove)
    assert await workspaces.cleanup_workspace(11)
    assert calls == [11]
    connection.invalidate.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_entity_child_is_retained_conservatively(monkeypatch):
    mock_database(monkeypatch, [run(11, kind="prepare"), run(20, parent=11, child=99)])
    monkeypatch.setattr(workspaces, "remove_workspace", lambda *_: pytest.fail("missing child"))
    assert not await workspaces.cleanup_workspace(11)


@pytest.mark.asyncio
async def test_shared_lease_is_invalidated_even_on_cancellation(monkeypatch):
    connection, _ = mock_database(monkeypatch, [])
    with pytest.raises(asyncio.CancelledError):
        async with workspaces.workspace_lease(11):
            raise asyncio.CancelledError()
    assert "pg_advisory_lock_shared" in str(connection.execute.await_args.args[0])
    connection.invalidate.assert_awaited_once()


@pytest.mark.asyncio
async def test_nested_and_detached_writer_leases_share_one_connection(monkeypatch):
    connection, _ = mock_database(monkeypatch, [])
    started, finish = asyncio.Event(), asyncio.Event()

    async def writer():
        async with workspaces.workspace_lease(11):
            started.set()
            await finish.wait()

    async with workspaces.workspace_lease(11):
        async with workspaces.workspace_lease(11):
            task = asyncio.create_task(writer())
            await started.wait()
            connection.execute.assert_awaited_once()
    connection.invalidate.assert_not_awaited()  # Detached export still owns lease.
    finish.set()
    await task
    connection.invalidate.assert_awaited_once()
    assert not workspaces._leases


@pytest.mark.asyncio
async def test_cancellation_waits_for_owned_writer():
    started, finish = asyncio.Event(), asyncio.Event()
    stopped = False

    async def write():
        nonlocal stopped
        started.set()
        await finish.wait()
        stopped = True

    task = asyncio.create_task(workspaces.finish_writes(write()))
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped
