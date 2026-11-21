"""Patch extraction directories and PostgreSQL execution leases.

Integration contract:
* workspace_id is the positive prepare ProcessingRun ID, never a release ID.
* Hold workspace_lease() around each handler, including entity handlers. Release
  it only after subprocesses/threads have stopped, then call cleanup_workspace().
* Enqueue, retry and cancellation transactions must take
  pg_advisory_xact_lock_shared(706, workspace_id % 2147483648) BEFORE changing runs. This serializes
  graph changes with cleanup without serializing workers with each other.
* Call cleanup after terminal state commits (including skipped cancelled jobs),
  and periodically for crash recovery. Unknown/nonterminal states retain files.
* Retrying a cleaned workspace requires rerunning its extraction prerequisites.

The game/plan/status/keys links are input-only by convention, not an OS security
boundary. Never chmod them: that would change the retained original client.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import select, text
from wuwa_story.db.models.ops import ProcessingRun
from wuwa_story.db.session import SessionFactory, engine

from wuwa_story_worker.client_assets import save_json, workspace_lock
from wuwa_story_worker.tooling import asset_workspace

logger = logging.getLogger(__name__)
TERMINAL = {"completed", "partial", "failed", "blocked", "cancelled", "enqueue_failed"}
_leases: dict[tuple, dict] = {}


async def finish_writes(awaitable):
    """Delay cancellation until an owned thread/subprocess has stopped writing."""
    task = asyncio.ensure_future(awaitable)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        # Retrieve a failure without masking the caller's cancellation.
        if not task.cancelled():
            task.exception()
        raise


def validate_workspace_id(value: int) -> int:
    if type(value) is not int or not 0 < value < 2**63:
        raise ValueError("workspace_id must be a positive ProcessingRun ID")
    return value


def _directory(path: Path) -> Path:
    """Reject redirected managed directories, including Windows junctions."""
    if path.is_symlink() or path.is_junction():
        raise ValueError(f"Managed workspace directory is a link: {path}")
    return path


def workspace_path(workspace_id: int, *, base: Path | None = None) -> Path:
    base = (base or asset_workspace()).resolve()
    container = _directory(base / "patch-workspaces")
    return _directory(container / str(validate_workspace_id(workspace_id)))


def resolve_workspace(client: Path, workspace_id: int | None, *, base: Path | None = None) -> Path:
    """Bind a transient directory to one ready client; legacy requests use client."""
    if workspace_id is None:
        return client
    base = (base or asset_workspace()).resolve()
    root = workspace_path(workspace_id, base=base)
    client = client.resolve()
    if client.parent != (base / "assets").resolve():
        raise ValueError("Workspace client must be inside retained assets")
    return _bind(root, client, workspace_id)


def _bind(root: Path, source: Path, workspace_id: int) -> Path:
    status = json.loads((source / "status.json").read_text(encoding="utf-8"))
    if status.get("state") != "downloaded":
        raise ValueError("Workspace source is not a ready download")
    # The lock lives outside the deletable tree and survives failed initialization.
    locks = _directory(root.parent / ".locks")
    with workspace_lock(_directory(locks / str(workspace_id))):
        _directory(root).mkdir(parents=True, exist_ok=True)
        marker = root / "workspace.json"
        identity = {"workspace_id": workspace_id, "source": str(source)}
        if marker.is_symlink():
            raise ValueError("Workspace identity must not be a link")
        if marker.exists() and json.loads(marker.read_text(encoding="utf-8")) != identity:
            raise ValueError("Workspace is already bound to another client")
        save_json(marker, identity)
        for name in ("game", "plan.json", "status.json", "keys.txt"):
            original, link = source / name, root / name
            if not original.exists():
                # Voice/video downloads use the client's keys rather than their own.
                if name == "keys.txt":
                    continue
                raise ValueError(f"Workspace source is missing {name}")
            if link.is_symlink():
                if link.resolve() != original.resolve():
                    raise ValueError("Workspace input points to another download")
            elif link.exists():
                raise ValueError("Workspace input must be a link")
            else:
                link.symlink_to(original, target_is_directory=original.is_dir())
    return root


def package_root(parent: dict, kind: str, plan_id: str, *, base: Path | None = None) -> Path:
    """Find scoped voice/video exports while retaining their compressed downloads."""
    if kind not in {"voices", "videos"} or not re.fullmatch(r"[0-9a-f]{64}", plan_id):
        raise ValueError("Invalid media package identity")
    if not re.fullmatch(r"\d+\.\d+\.\d+", parent["asset_version"]):
        raise ValueError("Invalid media package version")
    base = (base or asset_workspace()).resolve()
    name = f"{parent['asset_version']}-{plan_id[:16]}"
    if parent.get("workspace_id") is None:
        return base / kind / name
    return workspace_path(parent["workspace_id"], base=base) / kind / name


def bind_package(parent: dict, kind: str, plan_id: str, downloaded: Path,
                 *, base: Path | None = None) -> Path:
    if parent.get("workspace_id") is None:
        return downloaded
    root = package_root(parent, kind, plan_id, base=base)
    _directory(root.parent)
    return _bind(root, downloaded.resolve(), parent["workspace_id"])


@asynccontextmanager
async def workspace_lease(workspace_id: int | None):
    """One DB lease per workspace/process, retained until its last writer exits.

    Nested broker/handler/export leases share a connection, including an export
    that outlives its cancelled caller. Other worker processes hold their own
    shared lock. A process crash loses its lease without a TTL race.
    """
    if workspace_id is None:
        yield
        return
    validate_workspace_id(workspace_id)
    key = (asyncio.get_running_loop(), workspace_id)
    lease = _leases.get(key)
    if lease is None:
        lease = {"users": 0, "ready": asyncio.create_task(_open_lease(workspace_id))}
        _leases[key] = lease
    lease["users"] += 1
    try:
        await asyncio.shield(lease["ready"])
        yield
    finally:
        lease["users"] -= 1
        if not lease["users"]:
            del _leases[key]
            await finish_writes(_close_lease(lease["ready"]))


async def _open_lease(workspace_id):
    manager = engine.connect()
    connection = await manager.__aenter__()
    try:
        # Modulo collisions only delay cleanup; they cannot allow early deletion.
        await connection.execute(text("SELECT pg_advisory_lock_shared(706, :key)"), {"key": workspace_id % 2**31})
        await connection.commit()
        return manager, connection
    except BaseException:
        try:
            await connection.invalidate()
        finally:
            await manager.__aexit__(None, None, None)
        raise


async def _close_lease(ready):
    try:
        manager, connection = await ready
    except BaseException:
        return  # Failed acquisition already invalidated its connection.
    try:
        # Session locks survive rollback; never return this connection to pool.
        await connection.invalidate()
    finally:
        await manager.__aexit__(None, None, None)


async def run_workspace_id(run_id: int, session=None) -> int | None:
    """Follow release dependencies, including failures before raw_output exists."""
    if session is None:
        async with SessionFactory() as session:
            return await run_workspace_id(run_id, session)
    visited = set()
    while run_id and run_id not in visited:
        visited.add(run_id)
        run = await session.get(ProcessingRun, run_id)
        if run is None:
            return None
        metadata = run.metadata_json or {}
        request = metadata.get("request") or metadata.get("payload") or {}
        workspace_id = request.get("workspace_id") or (run.raw_output or {}).get("workspace_id")
        if workspace_id is not None:
            return validate_workspace_id(workspace_id)
        if request.get("kind") == "prepare":
            return validate_workspace_id(run.id)
        run_id = request.get("parent_id")
    return None


def _members(runs, workspace_id):
    """Include transitive release children and explicitly bound entity jobs."""
    members = {workspace_id}
    while True:
        before = len(members)
        for run in runs:
            metadata, output = run.metadata_json or {}, run.raw_output or {}
            request = metadata.get("request") or metadata.get("payload") or {}
            if (request.get("parent_id") in members or request.get("workspace_id") == workspace_id
                    or output.get("workspace_id") == workspace_id or run.id in members):
                members.add(run.id)
                if output.get("entity_run_id"):
                    members.add(output["entity_run_id"])
        if len(members) == before:
            return members


def remove_job_output(parent: dict, run_id: int, kind: str, *, base: Path | None = None) -> bool:
    """Remove one published job's conversions under its active execution lease.

    Derive the target from trusted layout/IDs; never accept an arbitrary deletion
    path or remove shared package exports. The caller also holds its job lock.
    """
    workspace_id = parent.get("workspace_id")
    if workspace_id is None:
        return False
    validate_workspace_id(run_id)
    root = workspace_path(workspace_id, base=base)
    if kind == "voices":
        target = package_root(parent, "voices", parent["voice_plan_id"], base=base) / "voice-batches" / str(run_id)
    elif kind == "cutscene":
        target = root / "release-media" / str(run_id)
    else:
        raise ValueError("Unsupported published job output kind")
    # Check every ancestor before deletion, including Windows junctions. rmtree
    # unlinks nested symlinks without traversing their retained source files.
    checked = root
    for part in target.relative_to(root).parts:
        checked = _directory(checked / part)
    if not target.exists():
        return False
    shutil.rmtree(target)
    logger.info("media_workspace.job_cleaned workspace=%s run=%s kind=%s", workspace_id, run_id, kind)
    return True


def remove_workspace(workspace_id: int, *, base: Path | None = None) -> bool:
    """Delete only the managed tree; shutil never descends into linked inputs.

    Caller must hold the exclusive database lease. No persisted path is trusted
    for deletion. In particular source paths in workspace.json are never deleted.
    """
    root = workspace_path(workspace_id, base=base)
    if not root.exists():
        return False
    marker = root / "workspace.json"
    if marker.is_symlink() or json.loads(marker.read_text(encoding="utf-8")).get("workspace_id") != workspace_id:
        raise ValueError("Workspace identity does not match cleanup request")
    shutil.rmtree(root)
    return True


def workspace_expired(workspace_id: int, *, base: Path | None = None, ttl_hours: float | None = None) -> bool:
    """Return True when a workspace marker is older than the configured TTL."""
    hours = float(os.getenv("WUWA_WORKSPACE_TTL_HOURS", "72")) if ttl_hours is None else ttl_hours
    if hours <= 0:
        return False
    root = workspace_path(workspace_id, base=base)
    if not root.exists():
        return False
    marker = root / "workspace.json"
    probe = marker if marker.exists() and not marker.is_symlink() else root
    try:
        return (time.time() - probe.stat().st_mtime) >= hours * 3600.0
    except OSError:
        return False


async def cleanup_workspace(workspace_id: int | None) -> bool:
    """Recheck the whole graph under an exclusive lease, then remove raw files.

    Failed/partial/cancelled trees are collectable once every descendant is
    terminal AND all execution leases have ended. Active independent workspaces
    and retained downloads are never touched. Safe to call repeatedly.
    """
    if workspace_id is None:
        return False
    validate_workspace_id(workspace_id)
    async with engine.connect() as connection:
        try:
            acquired = await connection.scalar(text("SELECT pg_try_advisory_lock(706, :key)"), {"key": workspace_id % 2**31})
            await connection.commit()
            if not acquired:
                logger.info("media_workspace.retained workspace=%s reason=active_lease", workspace_id)
                return False
            async with SessionFactory(bind=connection) as session:
                # A positive direct match is enough to retain files. Avoid loading
                # terminal reports while current jobs are obviously still queued.
                active = await session.scalar(text("""
                    SELECT EXISTS (
                        SELECT 1 FROM ops.processing_run
                        WHERE status <> ALL(CAST(:terminal AS text[])) AND (
                            id = :workspace_id
                            OR metadata->'request'->>'workspace_id' = :identity
                            OR metadata->'payload'->>'workspace_id' = :identity
                            OR metadata->'request'->>'parent_id' = :identity
                            OR metadata->'payload'->>'parent_id' = :identity)
                    )
                """), {"workspace_id": workspace_id, "identity": str(workspace_id),
                       "terminal": sorted(TERMINAL)})
                stale = await asyncio.to_thread(workspace_expired, workspace_id)
                if active and not stale:
                    return False
                # Project once: recursive UNION over full reports repeatedly detoasts
                # megabytes per run and holds the cleanup lease for minutes.
                graph = text("""
                    WITH RECURSIVE nodes AS MATERIALIZED (
                        SELECT id, status,
                            jsonb_typeof(metadata->'request') = 'object'
                                AND metadata->'request' <> '{}'::jsonb AS has_request,
                            metadata->'request'->'kind' AS request_kind,
                            metadata->'request'->'parent_id' AS request_parent,
                            metadata->'request'->'workspace_id' AS request_workspace,
                            metadata->'payload'->'kind' AS payload_kind,
                            metadata->'payload'->'parent_id' AS payload_parent,
                            metadata->'payload'->'workspace_id' AS payload_workspace,
                            raw_output->'workspace_id' AS output_workspace,
                            raw_output->'entity_run_id' AS entity_run
                        FROM ops.processing_run
                    ), links AS MATERIALIZED (
                        SELECT id::text AS child, request_parent #>> '{}' AS parent
                        FROM nodes WHERE request_parent IS NOT NULL
                        UNION ALL
                        SELECT id::text, payload_parent #>> '{}'
                        FROM nodes WHERE payload_parent IS NOT NULL
                        UNION ALL
                        SELECT entity_run #>> '{}', id::text
                        FROM nodes WHERE entity_run IS NOT NULL
                    ), members(id) AS (
                        SELECT id::text FROM nodes
                        WHERE id = :workspace_id
                           OR request_workspace #>> '{}' = :identity
                           OR payload_workspace #>> '{}' = :identity
                           OR output_workspace #>> '{}' = :identity
                        UNION SELECT links.child FROM links JOIN members
                            ON links.parent = members.id
                    )
                    SELECT run.id, run.status,
                        jsonb_build_object(
                            'request', CASE WHEN run.has_request THEN jsonb_build_object(
                                'kind', run.request_kind, 'parent_id', run.request_parent,
                                'workspace_id', run.request_workspace) END,
                            'payload', jsonb_build_object(
                                'kind', run.payload_kind, 'parent_id', run.payload_parent,
                                'workspace_id', run.payload_workspace)
                        ) AS metadata,
                        jsonb_build_object(
                            'workspace_id', run.output_workspace, 'entity_run_id', run.entity_run
                        ) AS raw_output
                    FROM nodes run JOIN members ON members.id = run.id::text
                """)
                runs = list(await session.scalars(select(ProcessingRun).from_statement(graph),
                                                 {"workspace_id": workspace_id, "identity": str(workspace_id)}))
                members = _members(runs, workspace_id)
                indexed = {run.id: run for run in runs}
                unfinished = [key for key in members if key not in indexed or indexed[key].status not in TERMINAL]
                owner = indexed.get(workspace_id)
                has_running = any(key in indexed and indexed[key].status == "running" for key in unfinished)
                if unfinished and (not stale or has_running or owner is None or owner.status not in TERMINAL):
                    logger.info("media_workspace.retained workspace=%s reason=unfinished_descendants tasks=%s",
                                workspace_id, len(members))
                    return False
                if unfinished:
                    logger.warning("media_workspace.expiring_stale workspace=%s unfinished=%s",
                                   workspace_id, len(unfinished))
                request = (owner.metadata_json or {}).get("request") or (owner.metadata_json or {}).get("payload") or {}
                if request.get("kind") != "prepare":
                    raise ValueError("Workspace owner must be a prepare run")
                # Keep the exclusive lease until deletion actually finishes even
                # when broker shutdown cancels this coroutine.
                removed = await finish_writes(asyncio.to_thread(remove_workspace, workspace_id))
                logger.info("media_workspace.cleaned workspace=%s tasks=%s removed=%s", workspace_id, len(members), removed)
                return removed
        finally:
            await connection.invalidate()


def _parse_version(value: object) -> tuple[int, ...] | None:
    if not isinstance(value, str) or not re.fullmatch(r"\d+\.\d+\.\d+", value):
        return None
    return tuple(int(part) for part in value.split("."))


def _read_package_info(directory: Path) -> dict | None:
    """Return parsed version/id/ready state for an assets/ or voices/ directory."""
    if directory.is_symlink() or directory.is_junction() or not directory.is_dir():
        return None
    plan_path = directory / "plan.json"
    status_path = directory / "status.json"
    if not plan_path.is_file() or not status_path.is_file():
        return None
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    version = _parse_version(plan.get("version"))
    identity = plan.get("id")
    if version is None or not isinstance(identity, str) or not identity:
        return None
    ready = status.get("state") == "downloaded" and status.get("id") == identity
    try:
        mtime = status_path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return {
        "path": directory.resolve(),
        "name": directory.name,
        "version": version,
        "version_str": plan["version"],
        "tier": plan.get("tier"),
        "id": identity,
        "ready": ready,
        "mtime": mtime,
        "plan": plan,
    }


def _workspace_referenced_packages(base: Path) -> set[Path]:
    """Return every assets/ or voices/ directory referenced by any active patch-workspace."""
    referenced: set[Path] = set()
    workspaces = base / "patch-workspaces"
    if not workspaces.is_dir() or workspaces.is_symlink() or workspaces.is_junction():
        return referenced
    managed_roots = {(base / "assets").resolve(), (base / "voices").resolve()}
    for entry in workspaces.iterdir():
        if not entry.is_dir() or entry.is_symlink() or entry.is_junction() or entry.name.startswith("."):
            continue
        markers = [entry / "workspace.json"]
        voices_dir = entry / "voices"
        if voices_dir.is_dir() and not voices_dir.is_symlink():
            markers.extend(child / "workspace.json" for child in voices_dir.iterdir() if child.is_dir())
        for marker in markers:
            if marker.is_file() and not marker.is_symlink():
                try:
                    source = json.loads(marker.read_text(encoding="utf-8")).get("source")
                    if isinstance(source, str) and source:
                        resolved = Path(source).resolve()
                        if resolved.parent in managed_roots:
                            referenced.add(resolved)
                except (OSError, ValueError):
                    pass
        # Also inspect direct input symlinks in case workspace.json is missing/corrupt.
        for link_parent in [entry] + (
            [child for child in voices_dir.iterdir() if child.is_dir()]
            if voices_dir.is_dir() and not voices_dir.is_symlink() else []
        ):
            for name in ("game", "plan.json", "status.json"):
                link = link_parent / name
                if link.is_symlink():
                    try:
                        target = link.resolve().parent
                        if target.parent in managed_roots:
                            referenced.add(target)
                    except OSError:
                        pass
    return referenced


def _extract_run_package_refs(runs) -> tuple[set[str], set[str]]:
    """Collect download/voice plan IDs and short prefixes referenced by non-terminal runs."""
    plan_ids: set[str] = set()
    versions: set[str] = set()
    for run in runs:
        metadata = run.metadata_json or {}
        output = run.raw_output or {}
        for container in (metadata, metadata.get("request"), metadata.get("payload"),
                          metadata.get("client_plan"), output):
            if not isinstance(container, dict):
                continue
            for key in ("download_id", "voice_plan_id", "id"):
                val = container.get(key)
                if isinstance(val, str) and len(val) >= 16:
                    plan_ids.add(val)
                    plan_ids.add(val[:16])
            for key in ("asset_version", "version"):
                val = container.get(key)
                if isinstance(val, str) and _parse_version(val) is not None:
                    versions.add(val)
    return plan_ids, versions


def resolve_installed_client(
    asset_version: str,
    tier: str,
    download_id: str,
    *,
    base: Path | None = None,
) -> tuple[Path, dict | None]:
    """Return the pinned client directory, falling back to a newer ready client if GC'd."""
    root_base = (base or asset_workspace()).resolve()
    assets_dir = root_base / "assets"
    exact = assets_dir / f"{asset_version}-{tier}-{download_id[:16]}"
    if (exact / "status.json").is_file() and (exact / "plan.json").is_file():
        return exact, None
    requested_version = _parse_version(asset_version)
    if requested_version is None or not assets_dir.is_dir():
        return exact, None
    candidates = []
    for child in assets_dir.iterdir():
        info = _read_package_info(child)
        if info is None or not info["ready"]:
            continue
        if info["tier"] and info["tier"] != tier:
            continue
        if info["version"] >= requested_version:
            candidates.append(info)
    if not candidates:
        return exact, None
    newest = max(candidates, key=lambda item: (item["version"], item["mtime"]))
    logger.warning(
        "media_workspace.client_fallback requested=%s-%s-%s resolved=%s",
        asset_version, tier, download_id[:16], newest["name"],
    )
    return newest["path"], newest["plan"]


def remove_superseded_packages(
    referenced_paths: set[Path],
    active_plan_ids: set[str],
    *,
    base: Path | None = None,
) -> list[str]:
    """Remove superseded assets/<old>-* and voices/<old>-* directories safely.

    Since unchanged archive files are hardlinked via os.link(), unlinking an old
    version directory frees blocks unique to the old version without affecting
    the newer client.
    """
    root_base = (base or asset_workspace()).resolve()
    removed: list[str] = []
    for kind in ("assets", "voices"):
        container = root_base / kind
        if not container.is_dir() or container.is_symlink() or container.is_junction():
            continue
        packages = [info for child in sorted(container.iterdir()) if (info := _read_package_info(child)) is not None]
        ready = [info for info in packages if info["ready"]]
        if not ready:
            continue
        newest = max(ready, key=lambda item: (item["version"], item["mtime"]))
        for info in packages:
            if info["path"] == newest["path"]:
                continue
            # Only collect strictly older versions (or older superseded same-version ready downloads).
            if info["version"] > newest["version"]:
                continue
            if info["version"] == newest["version"] and not info["ready"]:
                continue
            if info["path"] in referenced_paths:
                continue
            if info["id"] in active_plan_ids or info["id"][:16] in active_plan_ids:
                continue
            target = _directory(info["path"])
            if target.parent != container.resolve():
                raise ValueError(f"Refusing to remove package outside {kind}: {target}")
            shutil.rmtree(target)
            removed.append(f"{kind}/{info['name']}")
            logger.info("media_workspace.superseded_client_removed package=%s/%s kept=%s/%s",
                        kind, info["name"], kind, newest["name"])
    return removed


async def cleanup_superseded_clients(*, base: Path | None = None) -> list[str]:
    """Garbage-collect old client/voice packages no longer referenced by any workspace or active run."""
    root_base = (base or asset_workspace()).resolve()
    async with engine.connect() as connection:
        try:
            acquired = await connection.scalar(text("SELECT pg_try_advisory_lock(707, 1)"))
            await connection.commit()
            if not acquired:
                return []
            async with SessionFactory(bind=connection) as session:
                runs = list(await session.scalars(
                    select(ProcessingRun).where(ProcessingRun.status.not_in(sorted(TERMINAL)))
                ))
            active_plan_ids, _ = _extract_run_package_refs(runs)
            referenced = await asyncio.to_thread(_workspace_referenced_packages, root_base)
            return await finish_writes(asyncio.to_thread(
                remove_superseded_packages, referenced, active_plan_ids, base=root_base,
            ))
        finally:
            await connection.invalidate()
