"""Pinned official Windows story and character voice packages, in all four languages."""

import base64
import hashlib
import json
import logging
import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from wuwa_story_worker.client_assets import plan_id, read_url, safe_path, save_json, workspace_lock

LANGUAGES = ("en", "ja", "ko", "zh")
CDN_HOSTS = {
    f"cdn-{name}-hw-mc.aki-game.net" for name in ("aws", "akamai", "qcloud", "huoshan")}
CONFIG_HOSTS = {
    f"aki-config-{name}.aki-game.net" for name in ("aws", "akamai", "qcloud", "huoshan")}
logger = logging.getLogger(__name__)


def decrypt_config(body: bytes, crypto: dict) -> dict:
    # KuroLauncherLibrary uses base64 AES-256-CBC with PKCS7 padding.
    key, iv = (base64.b64decode(
        crypto[name], validate=True) for name in ("key", "iv"))
    if len(key) != 32 or len(iv) != 16:
        raise ValueError("Expected a 256-bit config key and 128-bit IV")
    decoder = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    raw = decoder.update(base64.b64decode(body.decode(
        "utf-8-sig"), validate=True)) + decoder.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    result = json.loads(unpadder.update(raw) + unpadder.finalize())
    if not isinstance(result, dict):
        raise ValueError("Expected a config object")
    return result


def resource_url(base: str, path: str) -> str:
    parsed = urlparse(base)
    if (parsed.scheme != "https" or parsed.hostname not in CDN_HOSTS or parsed.username
            or parsed.port or parsed.query or parsed.fragment or parsed.path != "/prod/client/"):
        raise ValueError("Expected an official client CDN")
    return base + safe_path(path)


def package_files(manifest: dict, group: str, uri: str, package_version: str) -> list[dict]:
    version = manifest["Version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Invalid voice resource version")
    result = []
    for field, directory in (("BaseFiles", "Base"), ("PatchFiles", version)):
        for item in manifest["DiffPatch"][field]:
            name = safe_path(item["Name"])
            digest = item["Hash"].lower()
            size = int(str(item["Size"]).removeprefix("__kr_long__"))
            if ("/" in name or not name.endswith((".pak", ".sig", ".utoc", ".ucas"))
                    or not re.fullmatch(r"[0-9a-f]{40}", digest) or size < 0):
                raise ValueError("Invalid voice archive entry")
            result.append({"path": safe_path(f"{group}/{directory}/{name}"), "size": size,
                           "sha1": digest, "remote_path": safe_path(f"{uri}/Windows/{package_version if directory == 'Base' else directory}/{name}")})
    return result


def discover_voice_plan(version: str, public_config: Path, crypto_path: Path) -> dict:
    values = dict(line.split("=", 1) for line in public_config.read_text(encoding="utf-8-sig").splitlines()
                  if "=" in line)
    uri = safe_path(values["UrlPath"].strip())
    crypto = json.loads(crypto_path.read_text(encoding="utf-8"))
    entry = None
    for prefix in values["InternalPrefix"].split(";"):
        if urlparse(prefix).hostname not in CONFIG_HOSTS or not prefix.startswith("https://"):
            continue
        try:
            entry = decrypt_config(read_url(prefix.rstrip(
                "/") + f"/{uri}/index.json"), crypto)["default"]
            break
        except (OSError, ValueError):
            continue
    if entry is None:
        raise RuntimeError(
            "No official config CDN returned a valid client entry")
    cdns = [item["url"] for item in sorted(
        entry["CdnUrl"], key=lambda item: int(item["weight"]), reverse=True)]
    for cdn in cdns:
        resource_url(cdn, uri)
    sources = []

    def fetch(path: str, sha1: str | None = None) -> dict:
        for cdn in cdns:
            try:
                body = read_url(resource_url(cdn, path))
                if sha1 and hashlib.sha1(body).hexdigest() != sha1.lower():
                    raise ValueError("Voice manifest checksum mismatch")
                decoded = decrypt_config(body, crypto)
                sources.append(
                    {"path": path, "sha256": hashlib.sha256(body).hexdigest()})
                return decoded
            except OSError:
                continue
        raise RuntimeError(f"Unable to fetch voice manifest: {path}")

    config = fetch(f"{safe_path(entry['MixUri'])}/Windows/config.json")
    if config["PackageVersion"] != version:
        raise ValueError(
            f"Client CDN serves {config['PackageVersion']}, requested {version}")
    uri = safe_path(entry["ResUri"])
    files = []
    for language in LANGUAGES:
        info = config["ResVersions"][language]
        manifest = fetch(f"{uri}/Windows/{info['Version']}/ManifestLang_{language}.txt",
                         info["IndexSha1"][info["Version"]])
        files.extend(package_files(manifest, f"Lang_{language}", uri, version))
    info = config["ResVersions"]["ManifestAggregated"]
    roles = fetch(f"{uri}/Windows/{info['Version']}/ManifestAggregated.txt",
                  info["IndexSha1"][info["Version"]])
    for group, manifest in roles.items():
        if group.startswith("role_lang_") and group.rsplit("_", 1)[-1] in LANGUAGES:
            files.extend(package_files(
                manifest, f"RoleVoice/{safe_path(group)}", uri, version))
    plan = {"schema_version": 1, "job_type": "voices.download", "version": version,
            "languages": list(LANGUAGES), "cdns": cdns, "sources": sources, "files": files}
    plan["id"] = plan_id(plan)
    return plan


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha1").hexdigest()


def download_voice_plan(plan: dict, workspace: Path, installed_game: Path | None = None,
                        concurrency: int = 4) -> Path:
    if plan.get("job_type") != "voices.download" or plan.get("id") != plan_id(plan):
        raise ValueError("Invalid voice download plan")
    if not re.fullmatch(r"\d+\.\d+\.\d+", plan["version"]) or not 1 <= concurrency <= 16:
        raise ValueError("Invalid version or concurrency")
    root = workspace / f"{plan['version']}-{plan['id'][:16]}"
    root.mkdir(parents=True, exist_ok=True)
    # Validate the entire plan before downloading anything.
    for item in plan["files"]:
        safe_path(item["path"])
        if not re.fullmatch(r"[0-9a-f]{40}", item["sha1"]) or type(item["size"]) is not int or item["size"] < 0:
            raise ValueError("Invalid voice archive identity")
        for cdn in plan["cdns"]:
            resource_url(cdn, item["remote_path"])

    def download(item: dict) -> None:
        target = root / "game/Client/Saved/Resources" / \
            plan["version"] / item["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.stat().st_size == item["size"] and digest(target) == item["sha1"]:
            return
        source = installed_game / "Client/Saved/Resources" / \
            plan["version"] / item["path"] if installed_game else None
        temporary = target.with_suffix(target.suffix + ".part")
        if source and source.is_file() and source.stat().st_size == item["size"] and digest(source) == item["sha1"]:
            shutil.copyfile(source, temporary)
        else:
            for cdn in plan["cdns"]:
                try:
                    with urlopen(Request(resource_url(cdn, item["remote_path"]),
                                         headers={"User-Agent": "solaris-atlas-worker/0.1"}), timeout=60) as response, temporary.open("wb") as output:
                        shutil.copyfileobj(response, output, 1024 * 1024)
                    if temporary.stat().st_size != item["size"] or digest(temporary) != item["sha1"]:
                        raise ValueError(
                            "Downloaded voice archive checksum mismatch")
                    break
                except (OSError, ValueError) as error:
                    logger.warning("voices.download_retry path=%s cdn=%s reason=%s",
                                   item["path"], urlparse(cdn).hostname, error)
                    continue
            else:
                raise RuntimeError(
                    f"Voice archive download failed: {item['path']}")
        if temporary.stat().st_size != item["size"] or digest(temporary) != item["sha1"]:
            raise ValueError("Copied voice archive checksum mismatch")
        temporary.replace(target)
        logger.info("voices.downloaded path=%s bytes=%s",
                    item["path"], item["size"])

    with workspace_lock(root):
        save_json(root / "plan.json", plan)
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            list(executor.map(download, plan["files"]))
        save_json(root / "status.json",
                  {"id": plan["id"], "state": "downloaded"})
    return root
