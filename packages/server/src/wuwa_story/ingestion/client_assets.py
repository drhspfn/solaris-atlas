"""Pinned official-launcher manifests and client archive inspection."""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import re
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

INDEX_URL = (
    "https://prod-alicdn-gamestarter.kurogame.com/launcher/game/"
    "50004_P7xcUZnEr1AXIGON25E6KjpOgTlVrg6e/G153/official/index.json"
)
CDN_HOSTS = {
    "hw-pcdownload-qcloud.aki-game.net",
    "hw-pcdownload-aws.aki-game.net",
    "pcdownload-huoshan.aki-game.net",
    "hw-pcdownload-akamai.aki-game.net",
}
KEYS_REPOSITORY = "https://api.github.com/repos/yarik0chka/wuwa-keys"
CHUNK_SIZE = 1024 * 1024


def read_url(url: str, timeout: int = 60) -> bytes:
    with urlopen(
        Request(url, headers={"User-Agent": "solaris-atlas/0.1"}), timeout=timeout
    ) as response:
        body = response.read()
    return gzip.decompress(body) if body.startswith(b"\x1f\x8b") else body


def md5(value: str) -> str:
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


def get_live_launcher_info(timeout: int = 15) -> dict[str, Any]:
    try:
        body = read_url(INDEX_URL, timeout=timeout)
        index = json.loads(body)
        data = index if "resourcePacks" in index else index["default"]
        config = data["resourcePacks"]["common"] if "resourcePacks" in data else data.get("config", {})
        return {
            "version": config.get("version"),
            "index_file": config.get("indexFile"),
            "cdns": [item["url"] for item in data.get("cdnList", [])],
        }
    except Exception as error:
        logger.warning("Could not resolve official launcher index: %s", error)
        return {"version": None, "error": str(error)}


def discover_plan(
    version: str | None = None,
    tier: str = "hd",
    *,
    completed_pins: Sequence[dict[str, Any]] = (),
    completed_workspace: Path | None = None,
) -> dict[str, Any]:
    if version is not None and not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", version):
        raise ValueError("Game version must be major.minor or major.minor.patch")
    if tier not in {"sd", "hd", "uhd"}:
        raise ValueError("Resource tier must be sd, hd or uhd")
    index = json.loads(read_url(INDEX_URL))
    packs = index.get("resourcePacks")
    if packs is not None:
        if not isinstance(packs, dict) or "common" not in packs or tier not in packs:
            raise ValueError(f"Official index does not offer common and {tier}")
        configs = [packs["common"], packs[tier]]
        actual_version = configs[0]["version"]
        if configs[1]["version"] != actual_version:
            raise ValueError("Resource pack versions do not match")
        data = index
    else:
        data = index["default"]
        configs = [data["config"]]
        actual_version = configs[0]["version"]
    if version is not None and actual_version != version and not (version.count(".") == 1 and actual_version.startswith(version + ".")):
        raise ValueError(f"Official CDN serves {actual_version}, requested {version}; old patches are not old clients")
    cdns = [item["url"] for item in data["cdnList"]]
    files = []
    available_tiers = set()
    paths = set()
    manifest_hashes = []
    tier_archive_found = False
    for config in configs:
        base_path = safe_path(config["baseUrl"].rstrip("/"))
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
        manifest_hashes.append(hashlib.sha256(body).hexdigest())
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
            if packs is not None and config is configs[1] and group == tier and path.lower().endswith((".pak", ".utoc")):
                tier_archive_found = True
            if path.casefold() in paths:
                raise ValueError(f"Duplicate resource path: {path}")
            paths.add(path.casefold())
            size = item["size"]
            if type(size) is not int or size < 0:
                raise ValueError(f"Invalid resource size: {path}")
            file = {"path": path, "size": size, "md5": md5(item["md5"])}
            if packs is not None:
                file["base_path"] = base_path
            files.append(file)
    if packs is not None and not tier_archive_found:
        raise ValueError(f"Official {tier} pack contains no {tier} archives")
    if available_tiers and tier not in available_tiers:
        raise ValueError(f"Official manifest offers {', '.join(sorted(available_tiers))}, not {tier}; no tier substitution")
    if not available_tiers and tier != "hd":
        raise ValueError("Legacy manifest has no selectable tiers; only the default hd is supported")
    if not any(item["path"].lower().endswith((".pak", ".utoc")) for item in files):
        raise ValueError("Launcher manifest contains no game archives")

    commits = json.loads(read_url(KEYS_REPOSITORY + "/commits?path=keys.json&per_page=100"))
    key_commit = next(
        (
            item["sha"]
            for item in commits
            if f"[{actual_version}]" in item["commit"]["message"]
        ),
        None,
    )
    plan = {
        "schema_version": 1,
        "job_type": "assets.download",
        "version": actual_version,
        "tier": tier,
        "index_url": INDEX_URL,
        "manifest_sha256": (manifest_hashes[0] if packs is None else
                            hashlib.sha256(json.dumps(manifest_hashes, separators=(",", ":")).encode()).hexdigest()),
        "cdns": cdns,
        "base_path": safe_path(configs[0]["baseUrl"].rstrip("/")),
        "keys_commit": key_commit,
        "files": sorted(files, key=lambda item: item["path"]),
    }
    if key_commit is None:
        # Only the caller's completed downloads can authorize an existing pin.
        # Recompute the entire identity against today's verified launcher manifest.
        for pin in completed_pins:
            commit = pin.get("keys_commit")
            if (
                pin.get("version") != actual_version
                or pin.get("tier") != tier
                or not isinstance(commit, str)
                or not re.fullmatch(r"[0-9a-f]{40}", commit)
            ):
                continue
            plan["keys_commit"] = commit
            if plan_id(plan) == pin.get("download_id"):
                plan["id"] = pin["download_id"]
                validate_plan(plan)
                logger.info("Reusing completed client download pin download_id=%s", plan["id"])
                return plan
        if completed_workspace is not None:
            wanted = {(item["path"], item["size"], item["md5"]) for item in files}
            for pin in completed_pins:
                try:
                    old_version, old_tier, old_id = pin["version"], pin["tier"], pin["download_id"]
                    if (old_tier != tier or not re.fullmatch(r"\d+\.\d+\.\d+", old_version)
                            or not re.fullmatch(r"[0-9a-f]{64}", old_id)):
                        continue
                    assets_dir = (completed_workspace / "assets").resolve()
                    root = (assets_dir / f"{old_version}-{old_tier}-{old_id[:16]}").resolve()
                    if not root.is_relative_to(assets_dir):
                        continue
                    previous = json.loads((root / "plan.json").read_text(encoding="utf-8"))
                    status = json.loads((root / "status.json").read_text(encoding="utf-8"))
                    validate_plan(previous)
                    if (previous["id"] != old_id or previous["version"] != old_version
                            or previous["tier"] != old_tier or previous["keys_commit"] != pin.get("keys_commit")
                            or status.get("state") != "downloaded" or status.get("id") != old_id):
                        continue
                    old = {(item["path"], item["size"], item["md5"]) for item in previous["files"]}
                    if old != wanted:
                        continue
                    game = (root / "game").resolve()
                    if not game.is_relative_to(root):
                        continue
                    def verified(item, game=game):
                        path = (game / item["path"]).resolve()
                        if not path.is_relative_to(game) or not path.is_file() or path.stat().st_size != item["size"]:
                            return False
                        with path.open("rb") as stream:
                            return hashlib.file_digest(stream, "md5").hexdigest() == item["md5"]
                    if not all(verified(item) for item in previous["files"]):
                        continue
                    plan["keys_commit"] = previous["keys_commit"]
                    plan["id"] = plan_id(plan)
                    validate_plan(plan)
                    logger.info("Reusing keys pin from validated completed client download_id=%s", old_id)
                    return plan
                except (OSError, ValueError, KeyError, TypeError):
                    continue
        raise ValueError(f"No version-labelled AES key update found for {actual_version}")
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
        if "base_path" in item:
            for cdn in plan["cdns"]:
                cdn_url(cdn, item["base_path"])
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


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def inspect_installed_clients(workspace: Path) -> list[dict[str, Any]]:
    assets_dir = workspace / "assets"
    if not assets_dir.is_dir():
        return []
    results = []
    for client_dir in sorted(assets_dir.iterdir(), reverse=True):
        if not client_dir.is_dir():
            continue
        plan_file = client_dir / "plan.json"
        status_file = client_dir / "status.json"
        if not plan_file.is_file():
            continue
        try:
            plan = json.loads(plan_file.read_text(encoding="utf-8"))
            status = json.loads(status_file.read_text(encoding="utf-8")) if status_file.is_file() else {}
            files = plan.get("files", [])
            results.append({
                "version": plan.get("version"),
                "tier": plan.get("tier"),
                "download_id": plan.get("id"),
                "keys_commit": plan.get("keys_commit"),
                "state": status.get("state", "unknown"),
                "key_count": status.get("key_count", 0),
                "file_count": len(files),
                "size_bytes": sum(f.get("size", 0) for f in files),
                "path": str(client_dir),
            })
        except Exception:
            continue
    return sorted(results, key=lambda item: tuple(int(part) for part in item["version"].split(".")), reverse=True)
