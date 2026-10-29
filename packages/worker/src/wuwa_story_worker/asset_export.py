"""FModelCLI export and immutable raw asset publication."""

from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import os
import re
import subprocess
import tempfile
from pathlib import Path

from wuwa_story.config.settings import get_settings
from wuwa_story.storage.s3 import S3Storage

from wuwa_story_worker.client_assets import save_json, validate_plan, wait_workspace_lock
from wuwa_story_worker.media_workspace import finish_writes, workspace_lease

_exports: dict[tuple, asyncio.Task] = {}


async def export_assets(root: Path, executable: Path, asset_filter: str, upload: bool = False) -> Path:
    # Coalesce identical requests. Cancelling one caller must not release a lock
    # while its subprocess is still writing, or cancel other callers' work.
    digest = await asyncio.to_thread(_sha256, executable)
    key = (asyncio.get_running_loop(), root.resolve(), digest, asset_filter, upload)
    task = _exports.get(key)
    if task is None:
        marker = root / "workspace.json"
        workspace_id = json.loads(marker.read_text(encoding="utf-8"))["workspace_id"] if marker.exists() else None
        lease = workspace_lease(workspace_id)
        # Acquire before scheduling: cancellation cannot leave an unleased writer
        # waiting to start after the caller's broker lease has been released.
        await lease.__aenter__()
        # Another caller can have scheduled the same export while this awaited DB.
        task = _exports.get(key)
        if task is not None:
            await lease.__aexit__(None, None, None)
            return await asyncio.shield(task)

        async def leased_export():
            try:
                return await _export_assets(root, executable, asset_filter, upload, digest)
            finally:
                await lease.__aexit__(None, None, None)

        task = asyncio.create_task(leased_export())
        _exports[key] = task

        def finished(done):
            _exports.pop(key, None)
            if not done.cancelled():
                done.exception()  # Retrieve failures even if every caller was cancelled.

        task.add_done_callback(finished)
    return await asyncio.shield(task)


async def _export_assets(root: Path, executable: Path, asset_filter: str, upload: bool, executable_sha: str) -> Path:
    if not asset_filter or not all(re.fullmatch(r"[a-zA-Z0-9_ ./-]+", part)
                                   for part in asset_filter.split("|")):
        raise ValueError("Provide a nonempty FModel path filter, for example ConfigDB or Audio")
    if not executable.is_file():
        raise FileNotFoundError(f"FModelCLI executable not found: {executable}")
    plan = json.loads((root / "plan.json").read_text(encoding="utf-8"))
    validate_plan(plan)
    status = json.loads((root / "status.json").read_text(encoding="utf-8"))
    if status.get("state") != "downloaded" or status.get("id") != plan["id"]:
        raise ValueError("Asset download is not complete for this plan")
    tag = hashlib.sha256(asset_filter.encode()).hexdigest()[:16]
    output = root / "exports" / tag
    output.mkdir(parents=True, exist_ok=True)
    # Scope the OS lock to a filter, not the read-only client archive directory.
    async with wait_workspace_lock(output):
        return await _finish_export(root, executable, asset_filter, upload, plan, tag, output, executable_sha)


async def _finish_export(root, executable, asset_filter, upload, plan, tag, output, executable_sha):
    receipt = output / "manifest.json"
    if receipt.exists() and json.loads(receipt.read_text(encoding="utf-8"))["extractor_sha256"] != executable_sha:
        # Keep previous exports immutable when a newly built extractor is used.
        output = root / "exports" / f"{tag}-{executable_sha[:16]}"
        output.mkdir(parents=True, exist_ok=True)
        receipt = output / "manifest.json"
    if receipt.exists():
        existing = json.loads(receipt.read_text(encoding="utf-8"))
        if existing["extractor_sha256"] != executable_sha:
            raise ValueError("Export exists from a different extractor; use a new asset workspace")
        if existing["job_id"] != plan["id"] or existing["filter"] != asset_filter:
            raise ValueError("Export does not match its pinned download/filter")
        if not (output / "files").is_dir():
            receipt.unlink()  # A removed raw cache must be rebuilt, not trusted.
        elif not upload:
            return receipt  # Our atomic manifest commits an immutable export.
    if not receipt.exists():
        # FModelCLI can exit zero after failed exports, so the log and actual files
        # must be checked before publishing a success manifest.
        log = output / "fmodel.log"
        def extract() -> None:
            with tempfile.TemporaryDirectory(prefix=".export-", dir=output) as temporary:
                _extract_files(root, executable, asset_filter, plan, output, Path(temporary), log)
        await finish_writes(asyncio.to_thread(extract))
        data = output / "files"
        files = []
        for path in sorted(data.rglob("*")):
            if path.is_file():
                files.append({"path": path.relative_to(data).as_posix(), "size": path.stat().st_size,
                              "sha256": await asyncio.to_thread(_sha256, path)})
        save_json(receipt, {"schema_version": 1, "job_id": plan["id"], "version": plan["version"],
                            "tier": plan["tier"], "filter": asset_filter, "keys_commit": plan["keys_commit"],
                            "extractor_sha256": executable_sha, "files": files})
    manifest = json.loads(receipt.read_text(encoding="utf-8"))
    if upload:
        storage = S3Storage(get_settings())
        await storage.ensure_bucket()
        prefix = f"client-assets/{plan['version']}/{plan['tier']}/{plan['id']}/{tag}"
        for item in manifest["files"]:
            source = output / "files" / item["path"]
            if await asyncio.to_thread(_sha256, source) != item["sha256"]:
                raise ValueError(f"Export file changed: {item['path']}")
            key = f"{prefix}/files/{item['sha256']}/{item['path']}"
            await storage.put_file(source, key, mimetypes.guess_type(source.name)[0])
            item["object_key"] = key
        published = output / "published.json"
        save_json(published, manifest)
        # The manifest is the commit point; failures leave no complete publication.
        await storage.put_file(published, prefix + "/manifest.json", "application/json")
    return receipt


def _extract_files(root, executable, asset_filter, plan, output, temporary, log,
                   *, game_dir=None, keys_file=None, processors=2, allow_empty=False):
    data = temporary / "files"
    data.mkdir()
    with log.open("w", encoding="utf-8") as stream:
        subprocess.run(
            [str(executable.resolve()), str((game_dir or root / "game").resolve()),
             "@" + str((keys_file or root / "keys.txt").resolve()), str(data.resolve()), asset_filter],
            stdout=stream, stderr=subprocess.STDOUT, check=False, timeout=7200,
            env={**os.environ, "DOTNET_PROCESSOR_COUNT": str(processors)},
        )
    log_text = log.read_text(encoding="utf-8", errors="replace")
    summary = re.search(r"\[Done\] Extracted (\d+) files", log_text)
    if "[Fail]" in log_text or "[Error]" in log_text or not summary or (int(summary[1]) == 0 and not allow_empty):
        raise RuntimeError(f"FModelCLI failed or exported no files; inspect {log}")
    expected_paks = {Path(item["path"]).name.casefold() for item in plan["files"] if item["path"].endswith(".pak")}
    mounted_paks = {name.casefold() for name in re.findall(r'Pak "([^"]+)":', log_text)}
    if missing := expected_paks - mounted_paks:
        raise RuntimeError(f"FModelCLI did not mount all archives: {sorted(missing)}; inspect {log}")
    if sum(path.is_file() for path in data.rglob("*")) != int(summary[1]):
        raise RuntimeError("FModel export count does not match files on disk")
    # Failed attempts never mix files with a retry. Completed exports are immutable.
    if (output / "files").exists():
        (output / "files").replace(temporary / "previous-files")
    data.replace(output / "files")


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()
