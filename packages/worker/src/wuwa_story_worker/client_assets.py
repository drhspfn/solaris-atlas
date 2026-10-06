"""Pinned official-launcher manifests and resumable client archive downloads."""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)
INDEX_URL = (
    "https://prod-alicdn-gamestarter.kurogame.com/launcher/game/G153/"
    "50004_obOHXFrFanqsaIEOmuKroCcbZkQRBC7c/index.json"
)
CDN_HOSTS = {
    "hw-pcdownload-qcloud.aki-game.net", "hw-pcdownload-aws.aki-game.net",
    "pcdownload-huoshan.aki-game.net", "hw-pcdownload-akamai.aki-game.net",
}
KEYS_REPOSITORY = "https://api.github.com/repos/yarik0chka/wuwa-keys"
CHUNK_SIZE = 1024 * 1024


def read_url(url: str) -> bytes:
    with urlopen(Request(url, headers={"User-Agent": "solaris-atlas-worker/0.1"}), timeout=60) as response:
        body = response.read()
    return gzip.decompress(body) if body.startswith(b"\x1f\x8b") else body


def md5(value: str) -> str:
    # Some launcher hashes omit leading zeroes.
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{1,32}", value):
        raise ValueError("Invalid launcher MD5")
    return value.lower().zfill(32)


def safe_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError("Invalid resource path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in value.split("/")):
        raise ValueError(f"Unsafe resource path: {value!r}")
    return value


def cdn_url(base: str, path: str) -> str:
    parsed = urlparse(base)
    if parsed.scheme != "https" or parsed.hostname not in CDN_HOSTS or parsed.username or parsed.port:
        raise ValueError("Resource URL must use an official HTTPS CDN")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("CDN base must be a host URL")
    return base.rstrip("/") + "/" + quote(safe_path(path), safe="/")


def plan_id(plan: dict[str, Any]) -> str:
    body = {key: value for key, value in plan.items() if key not in ("id", "run_id")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def discover_plan(version: str, tier: str = "hd") -> dict[str, Any]:
    if not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", version):
        raise ValueError("Game version must be major.minor or major.minor.patch")
    if tier not in {"sd", "hd", "uhd"}:
        raise ValueError("Resource tier must be sd, hd or uhd")
    index = json.loads(read_url(INDEX_URL))["default"]
    config = index["config"]
    actual_version = config["version"]
    if actual_version != version and not (version.count(".") == 1 and actual_version.startswith(version + ".")):
        raise ValueError(f"Official CDN serves {actual_version}, requested {version}; old patches are not old clients")
    cdns = [item["url"] for item in index["cdnList"]]
    failures = []
    for cdn in cdns:
        try:
            body = read_url(cdn_url(cdn, config["indexFile"]))
            if hashlib.md5(body).hexdigest() != md5(config["indexFileMd5"]):
                raise ValueError("Launcher manifest checksum mismatch")
            manifest = json.loads(body)
            break
        except (OSError, ValueError) as exc:
            failures.append(str(exc))
    else:
        raise RuntimeError(f"No official CDN returned a valid manifest: {failures}")
    files = []
    available_tiers = set()
    paths = set()
    for item in manifest["resource"]:
        path = safe_path(item["dest"])
        parts = path.split("/")
        if len(parts) < 4 or parts[:2] != ["Client", "Content"]:
            continue
        group = parts[2].lower()
        if group in {"sd", "hd", "uhd"}:
            available_tiers.add(group)
        if group not in {"paks", tier}:
            continue
        if not path.lower().endswith((".pak", ".sig", ".utoc", ".ucas")):
            continue
        if path.casefold() in paths:
            raise ValueError(f"Duplicate resource path: {path}")
        paths.add(path.casefold())
        size = item["size"]
        if type(size) is not int or size < 0:
            raise ValueError(f"Invalid resource size: {path}")
        files.append({"path": path, "size": size, "md5": md5(item["md5"])})
    if available_tiers and tier not in available_tiers:
        raise ValueError(f"Official manifest offers {', '.join(sorted(available_tiers))}, not {tier}; no tier substitution")
    if not available_tiers and tier != "hd":
        raise ValueError("Legacy manifest has no selectable tiers; only the default hd is supported")
    if not any(item["path"].lower().endswith((".pak", ".utoc")) for item in files):
        raise ValueError("Launcher manifest contains no game archives")
    # Commit messages explicitly associate key updates with a game version.
    commits = json.loads(read_url(KEYS_REPOSITORY + "/commits?path=keys.json&per_page=100"))
    key_commit = next((item["sha"] for item in commits
                       if f"[{actual_version}]" in item["commit"]["message"]), None)
    if key_commit is None:
        raise ValueError(f"No version-labelled AES key update found for {actual_version}")
    plan = {
        "schema_version": 1, "job_type": "assets.download", "version": actual_version,
        "tier": tier, "index_url": INDEX_URL, "manifest_sha256": hashlib.sha256(body).hexdigest(),
        "cdns": cdns, "base_path": safe_path(config["baseUrl"].rstrip("/")),
        "keys_commit": key_commit, "files": sorted(files, key=lambda item: item["path"]),
    }
    plan["id"] = plan_id(plan)
    return plan


def validate_plan(plan: dict[str, Any]) -> None:
    if plan.get("schema_version") != 1 or plan.get("job_type") != "assets.download":
        raise ValueError("Unsupported asset job")
    if not isinstance(plan.get("version"), str) or not re.fullmatch(r"\d+\.\d+\.\d+", plan["version"]):
        raise ValueError("Asset version must be major.minor.patch")
    if plan.get("tier") not in {"sd", "hd", "uhd"} or plan.get("id") != plan_id(plan):
        raise ValueError("Invalid asset tier or job identity")
    if not isinstance(plan.get("keys_commit"), str) or not re.fullmatch(r"[0-9a-f]{40}", plan["keys_commit"]):
        raise ValueError("AES keys must be pinned to a commit")
    if not isinstance(plan.get("cdns"), list) or not plan["cdns"]:
        raise ValueError("Asset job must include CDNs")
    for cdn in plan["cdns"]:
        cdn_url(cdn, plan["base_path"])
    if not isinstance(plan.get("files"), list) or not plan["files"]:
        raise ValueError("Asset job must include files")
    paths = set()
    for item in plan["files"]:
        path = safe_path(item["path"])
        if not path.startswith(("Client/Content/Paks/", f"Client/Content/{plan['tier'].upper()}/")):
            raise ValueError("Asset job contains an unexpected resource directory")
        if path.casefold() in paths:
            raise ValueError("Duplicate resource path")
        paths.add(path.casefold())
        if md5(item["md5"]) != item["md5"]:
            raise ValueError("Asset hashes must be normalized MD5 values")
        if not path.lower().endswith((".pak", ".sig", ".utoc", ".ucas")):
            raise ValueError("Unexpected game archive extension")
        if type(item["size"]) is not int or item["size"] < 0:
            raise ValueError("Invalid asset size")


def matches(path: Path, item: dict[str, Any]) -> bool:
    if not path.is_file() or path.stat().st_size != item["size"]:
        return False
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "md5").hexdigest() == item["md5"]


def download_file(item: dict[str, Any], urls: list[str], target: Path) -> None:
    if matches(target, item):
        logger.info("Verified existing %s", item["path"])
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    for attempt in range(3):
        for url in urls:
            try:
                offset = partial.stat().st_size if partial.exists() else 0
                if offset >= item["size"]:
                    if matches(partial, item):
                        partial.replace(target)
                        return
                    partial.unlink()
                    offset = 0
                headers = {"User-Agent": "solaris-atlas-worker/0.1", "Accept-Encoding": "identity"}
                if offset:
                    headers["Range"] = f"bytes={offset}-"
                with urlopen(Request(url, headers=headers), timeout=60) as response:
                    resumed = response.status == 206
                    if resumed:
                        expected = f"bytes {offset}-"
                        if not response.headers.get("Content-Range", "").startswith(expected):
                            raise ValueError("CDN returned an invalid byte range")
                    else:
                        offset = 0
                    with partial.open("ab" if resumed else "wb") as stream:
                        written = offset
                        while chunk := response.read(CHUNK_SIZE):
                            written += len(chunk)
                            if written > item["size"]:
                                raise ValueError("CDN file exceeds manifest size")
                            stream.write(chunk)
                if not matches(partial, item):
                    if partial.stat().st_size == item["size"]:
                        partial.unlink()
                    raise ValueError("Resource size or MD5 mismatch")
                partial.replace(target)
                logger.info("Downloaded %s (%d bytes)", item["path"], item["size"])
                return
            except (OSError, ValueError) as exc:
                logger.warning("Download retry %d for %s: %s", attempt + 1, item["path"], exc)
        if attempt < 2:
            time.sleep(2 ** attempt)
    raise RuntimeError(f"All CDN attempts failed for {item['path']}")


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


@contextmanager
def workspace_lock(workspace: Path):
    """OS locks survive neither a process crash nor a host reboot."""
    workspace.mkdir(parents=True, exist_ok=True)
    with (workspace / "assets.lock").open("a+b") as stream:
        if os.name == "nt":
            import msvcrt
            stream.write(b"\0")
            stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def download_plan(plan: dict[str, Any], workspace: Path, concurrency: int = 4) -> Path:
    with workspace_lock(workspace):
        return _download_plan(plan, workspace, concurrency)


def _download_plan(plan: dict[str, Any], workspace: Path, concurrency: int) -> Path:
    validate_plan(plan)
    if not 1 <= concurrency <= 16:
        raise ValueError("Asset download concurrency must be between 1 and 16")
    root = workspace / "assets" / f"{plan['version']}-{plan['tier']}-{plan['id'][:16]}"
    game = root / "game"
    game.mkdir(parents=True, exist_ok=True)
    save_json(root / "plan.json", plan)
    required = sum(max(0, item["size"] - (
        (game / item["path"]).stat().st_size if (game / item["path"]).exists() else
        min((game / (item["path"] + ".part")).stat().st_size, item["size"])
        if (game / (item["path"] + ".part")).exists() else 0
    )) for item in plan["files"])
    # Reserve room for download repairs and extraction metadata.
    reserve = min(concurrency, len(plan["files"])) * max(item["size"] for item in plan["files"]) + 5 * 1024 ** 3
    if shutil.disk_usage(game).free < required + reserve:
        raise RuntimeError(f"Insufficient disk space: need {required / 1024 ** 3:.1f} GiB plus {reserve / 1024 ** 3:.1f} GiB reserve")
    save_json(root / "status.json", {"state": "downloading", "id": plan["id"]})
    logger.info("Downloading WuWa %s %s: %d files, %.1f GiB", plan["version"], plan["tier"], len(plan["files"]), required / 1024 ** 3)
    def download(item: dict[str, Any]) -> None:
        urls = [cdn_url(cdn, plan["base_path"] + "/" + item["path"]) for cdn in plan["cdns"]]
        target = (game / item["path"]).resolve()
        if not target.is_relative_to(game.resolve()):
            raise ValueError("Resource path escapes the managed game directory")
        download_file(item, urls, target)
    try:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            for _ in pool.map(download, plan["files"]):
                pass
        keys_url = f"https://raw.githubusercontent.com/yarik0chka/wuwa-keys/{plan['keys_commit']}/keys.json"
        keys = json.loads(read_url(keys_url))
        values = list(dict.fromkeys([keys["mainKey"], *(item["key"] for item in keys["dynamicKeys"])]))
        if not values or any(not re.fullmatch(r"0x[0-9a-fA-F]{64}", value) for value in values):
            raise ValueError("Invalid pinned AES key document")
        (root / "keys.txt").write_text("\n".join(values) + "\n", encoding="utf-8")
        save_json(root / "status.json", {"state": "downloaded", "id": plan["id"], "key_count": len(values)})
    except Exception:
        save_json(root / "status.json", {"state": "failed", "id": plan["id"]})
        raise
    return root
