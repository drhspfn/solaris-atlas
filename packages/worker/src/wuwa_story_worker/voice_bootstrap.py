"""Discover voices using exported game configuration and pinned public cipher data."""

import hashlib
import re
import tomllib
from pathlib import Path
from urllib.parse import urlparse

from wuwa_story_worker.client_assets import plan_id, read_url, safe_path
from wuwa_story_worker.voice_packages import (
    CONFIG_HOSTS,
    decrypt_config,
    discover_voice_plan,
    resource_url,
)

# Public game-format constants, not an account credential.
CIPHER_SOURCE = (
    "https://raw.githubusercontent.com/thexeondev/Shorekeeper/"
    "b316e5f0ac1aea66943bc5843466506c410abeed/config-server/configserver.default.toml"
)


def discover_client_voices(version: str, exported_config: Path) -> dict:
    configs = sorted(exported_config.rglob("KuroPublicConfig.ini"))
    if len(configs) != 1:
        raise ValueError("Expected exactly one exported KuroPublicConfig.ini")
    crypto = tomllib.loads(read_url(CIPHER_SOURCE).decode("utf-8"))["encryption"]
    plan = discover_voice_plan(version, configs[0], crypto)
    plan["cipher_source"] = CIPHER_SOURCE
    plan["id"] = plan_id(plan)
    return plan


def discover_video_plan(version: str, exported_config: Path, tier: str, cg_ids: set[int]) -> dict:
    configs = sorted(exported_config.rglob("KuroPublicConfig.ini"))
    if len(configs) != 1 or tier not in {"hd", "sd", "uhd"} or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Invalid video bootstrap configuration")
    values = dict(line.split("=", 1) for line in configs[0].read_text(encoding="utf-8-sig").splitlines() if "=" in line)
    crypto = tomllib.loads(read_url(CIPHER_SOURCE).decode())["encryption"]
    entry = None
    for prefix in values["InternalPrefix"].split(";"):
        parsed = urlparse(prefix)
        if parsed.scheme != "https" or parsed.hostname not in CONFIG_HOSTS or parsed.username or parsed.port:
            continue
        try:
            entry = decrypt_config(read_url(prefix.rstrip("/") + "/" + safe_path(values["UrlPath"].strip()) + "/index.json"), crypto)["default"]
            break
        except (OSError, ValueError):
            continue
    if entry is None:
        raise ValueError("Official video configuration is unavailable")
    cdns = [item["url"] for item in sorted(entry["CdnUrl"], key=lambda item: int(item["weight"]), reverse=True)]
    for cdn in cdns:
        resource_url(cdn, "validate")
    def fetch(path):
        for cdn in cdns:
            try:
                return read_url(resource_url(cdn, path))
            except OSError:
                continue
        raise RuntimeError("Official video manifest is unavailable")
    mix = safe_path(entry["MixUri"])
    config = decrypt_config(fetch(f"{mix}/Windows/VideoConfig_{tier}.json"), crypto)
    body = fetch(f"{mix}/Windows/{version}/Video/VideoManifest_{tier}.json")
    if hashlib.sha1(body).hexdigest() != config["IndexSha1"].lower():
        raise ValueError("Video manifest checksum mismatch")
    manifest = decrypt_config(body, crypto)
    files = []
    for key, item in manifest["VideoInfos"]["PakMap"].items():
        if not re.fullmatch(r"\d+_\d+", key) or int(key.split("_")[0]) not in cg_ids:
            continue
        for field in ("Pak", "Sig"):
            name = safe_path(item[field + "Name"])
            if "/" in name or not name.endswith((".pak", ".sig")):
                raise ValueError("Invalid video archive name")
            files.append({"path": f"Video/{name}", "size": int(str(item[field + "Size"]).removeprefix("__kr_long__")),
                          "sha1": item[field + "Hash"].lower(),
                          "remote_path": f"{safe_path(entry['ResUri'])}/Windows/{version}/Video/{name}"})
    plan = {"schema_version": 1, "job_type": "voices.download", "version": version,
            "languages": [], "cdns": cdns, "files": files, "cipher_source": CIPHER_SOURCE,
            "sources": [{"sha256": hashlib.sha256(body).hexdigest()}]}
    plan["id"] = plan_id(plan)
    return plan
