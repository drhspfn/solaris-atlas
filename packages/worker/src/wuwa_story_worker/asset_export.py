"""Windows FModelCLI export and immutable raw asset publication."""

from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import re
import subprocess
from pathlib import Path

from wuwa_story.config.settings import get_settings
from wuwa_story.storage.s3 import S3Storage

from wuwa_story_worker.client_assets import save_json, validate_plan, workspace_lock


async def export_assets(root: Path, executable: Path, asset_filter: str, upload: bool = False) -> Path:
    with workspace_lock(root):
        return await _export_assets(root, executable, asset_filter, upload)


async def _export_assets(root: Path, executable: Path, asset_filter: str, upload: bool) -> Path:
    if not asset_filter or not re.fullmatch(r"[a-zA-Z0-9_./-]+", asset_filter):
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
    executable_sha = await asyncio.to_thread(_sha256, executable)
    receipt = output / "manifest.json"
    if receipt.exists():
        existing = json.loads(receipt.read_text(encoding="utf-8"))
        if existing["extractor_sha256"] != executable_sha:
            raise ValueError("Export exists from a different extractor; use a new asset workspace")
    else:
        # FModelCLI can exit zero after failed exports, so the log and actual files
        # must be checked before publishing a success manifest.
        log = output / "fmodel.log"
        data = output / "files"
        data.mkdir(exist_ok=True)
        def extract() -> None:
            with log.open("w", encoding="utf-8") as stream:
                subprocess.run([str(executable.resolve()), str((root / "game").resolve()),
                                "@" + str((root / "keys.txt").resolve()), str(data.resolve()), asset_filter],
                               stdout=stream, stderr=subprocess.STDOUT, check=True, timeout=7200)
        await asyncio.to_thread(extract)
        log_text = log.read_text(encoding="utf-8", errors="replace")
        summary = re.search(r"\[Done\] Extracted (\d+) files", log_text)
        if "[Fail]" in log_text or "[Error]" in log_text or not summary or int(summary[1]) == 0:
            raise RuntimeError(f"FModelCLI failed or exported no files; inspect {log}")
        expected_paks = {Path(item["path"]).name.casefold() for item in plan["files"] if item["path"].endswith(".pak")}
        mounted_paks = {name.casefold() for name in re.findall(r'Pak "([^"]+)":', log_text)}
        if missing := expected_paks - mounted_paks:
            raise RuntimeError(f"FModelCLI did not mount all archives: {sorted(missing)}; inspect {log}")
        files = []
        for path in sorted(data.rglob("*")):
            if path.is_file():
                files.append({"path": path.relative_to(data).as_posix(), "size": path.stat().st_size,
                              "sha256": await asyncio.to_thread(_sha256, path)})
        if len(files) != int(summary[1]):
            raise RuntimeError("FModel export count does not match files on disk")
        save_json(receipt, {"schema_version": 1, "job_id": plan["id"], "version": plan["version"],
                            "tier": plan["tier"], "filter": asset_filter, "keys_commit": plan["keys_commit"],
                            "extractor_sha256": executable_sha, "files": files})
    manifest = json.loads(receipt.read_text(encoding="utf-8"))
    for item in manifest["files"]:
        if await asyncio.to_thread(_sha256, output / "files" / item["path"]) != item["sha256"]:
            raise ValueError(f"Export file changed: {item['path']}")
    if upload:
        storage = S3Storage(get_settings())
        await storage.ensure_bucket()
        prefix = f"client-assets/{plan['version']}/{plan['tier']}/{plan['id']}/{tag}"
        for item in manifest["files"]:
            source = output / "files" / item["path"]
            key = f"{prefix}/files/{item['sha256']}/{item['path']}"
            await storage.put_file(source, key, mimetypes.guess_type(source.name)[0])
            item["object_key"] = key
        published = output / "published.json"
        save_json(published, manifest)
        # The manifest is the commit point; failures leave no complete publication.
        await storage.put_file(published, prefix + "/manifest.json", "application/json")
    return receipt


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()
