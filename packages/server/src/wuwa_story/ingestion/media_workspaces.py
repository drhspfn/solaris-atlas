"""Coordinate job graph mutations with the worker's scratch-directory collector."""

import os
from pathlib import Path

from sqlalchemy import text


async def retain_workspace(session, workspace_id):
    if type(workspace_id) is not int or not 0 < workspace_id < 2**63:
        raise ValueError("workspace_id must be a positive ProcessingRun ID")
    await session.execute(text("SELECT pg_advisory_xact_lock_shared(706, :key)"),
                          {"key": workspace_id % 2**31})


def workspace_expired(prepare):
    output = getattr(prepare, "raw_output", None) or {}
    if not output.get("asset_version"):
        return False
    # Previously completed preparation used persistent exports. A retry must
    # recreate those prerequisites in a scoped workspace after the rollout.
    workspace_id = output.get("workspace_id")
    if workspace_id is None:
        return True
    if type(workspace_id) is not int or workspace_id < 1:
        raise ValueError("Invalid workspace identity in preparation result")
    base = Path(os.getenv("WUWA_ASSET_WORKSPACE", "/var/lib/wuwa-worker/client-assets"))
    return not (base / "patch-workspaces" / str(workspace_id) / "workspace.json").is_file()
