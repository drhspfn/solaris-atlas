import hashlib

import pytest

from wuwa_story_worker import voice_bootstrap


@pytest.mark.parametrize("client_version", ["3.7.0", "3.7.1", "3.6.0"])
@pytest.mark.parametrize("available_id,expected_missing", [(157, [158]), (241, [157, 158])])
def test_official_video_plan_uses_native_cg_ids_and_reports_absent_mappings(tmp_path, monkeypatch, available_id, expected_missing, client_version):
    config = tmp_path / "KuroPublicConfig.ini"
    host = sorted(voice_bootstrap.CONFIG_HOSTS)[0]
    config.write_text(f"InternalPrefix=https://{host}\nUrlPath=client\n")
    item = {"PakName": "native.pak", "SigName": "native.sig", "PakSize": 12, "SigSize": 4,
            "PakHash": "a" * 40, "SigHash": "b" * 40}
    documents = {
        b"entry": {"default": {"CdnUrl": [{"url": "https://cdn-aws-hw-mc.aki-game.net/prod/client/", "weight": 1}],
                                "MixUri": "mix", "ResUri": "resources"}},
        b"package": {"PackageVersion": "3.7.0"},
        b"config": {"IndexSha1": hashlib.sha1(b"manifest").hexdigest()},
        b"manifest": {"VideoInfos": {"PakMap": {f"{available_id}_0": item, "_157_0": item}}},
    }

    def read(url):
        if url == voice_bootstrap.CIPHER_SOURCE:
            return b"[encryption]\nkey = 'fixture'\n"
        if url.endswith("/index.json"):
            return b"entry"
        if url.endswith("/VideoConfig_hd.json"):
            return b"config"
        if url.endswith("/config.json"):
            return b"package"
        if url.endswith("/Windows/3.7.0/Video/VideoManifest_hd.json"):
            return b"manifest"
        raise AssertionError("Unexpected video bootstrap request")

    monkeypatch.setattr(voice_bootstrap, "read_url", read)
    monkeypatch.setattr(voice_bootstrap, "decrypt_config", lambda body, _: documents[body])
    if client_version == "3.6.0":
        with pytest.raises(ValueError, match="Video CDN serves"):
            voice_bootstrap.discover_video_plan(client_version, tmp_path, "hd", {157})
        return
    diagnostics = {}
    plan = voice_bootstrap.discover_video_plan(client_version, tmp_path, "hd", {157, 158}, diagnostics=diagnostics)
    assert diagnostics["requested_cg_ids"] == [157, 158]
    assert diagnostics["missing_cg_ids"] == expected_missing
    assert [p["cg_id"] for p in diagnostics["video_packages"]] == ([157] if available_id == 157 else [])
    assert [p["path"] for p in plan["files"]] == (["Video/native.pak", "Video/native.sig"] if available_id == 157 else [])
    assert plan["id"] == voice_bootstrap.plan_id(plan)
    assert plan == voice_bootstrap.discover_video_plan(client_version, tmp_path, "hd", {157, 158})

    assert plan["version"] == client_version
    assert plan.get("package_version", client_version) == "3.7.0"
    assert all("/Windows/3.7.0/Video/" in item["remote_path"] for item in plan["files"])


def test_official_video_plan_accepts_newer_supported_version(tmp_path, monkeypatch):
    config = tmp_path / "KuroPublicConfig.ini"
    host = sorted(voice_bootstrap.CONFIG_HOSTS)[0]
    config.write_text(f"InternalPrefix=https://{host}\nUrlPath=client\n")
    item = {"PakName": "native.pak", "SigName": "native.sig", "PakSize": 12, "SigSize": 4,
            "PakHash": "a" * 40, "SigHash": "b" * 40}
    documents = {
        b"entry": {"default": {"CdnUrl": [{"url": "https://cdn-aws-hw-mc.aki-game.net/prod/client/", "weight": 1}],
                                "MixUri": "mix", "ResUri": "resources"}},
        b"package": {"PackageVersion": "3.8.0"},
        b"config": {"IndexSha1": hashlib.sha1(b"manifest").hexdigest()},
        b"manifest": {"VideoInfos": {"PakMap": {"157_0": item}}},
    }

    def read(url):
        if url == voice_bootstrap.CIPHER_SOURCE:
            return b"[encryption]\nkey = 'fixture'\n"
        if url.endswith("/index.json"):
            return b"entry"
        if url.endswith("/VideoConfig_hd.json"):
            return b"config"
        if url.endswith("/config.json"):
            return b"package"
        if url.endswith("/Windows/3.8.0/Video/VideoManifest_hd.json"):
            return b"manifest"
        raise AssertionError(f"Unexpected video bootstrap request: {url}")

    monkeypatch.setattr(voice_bootstrap, "read_url", read)
    monkeypatch.setattr(voice_bootstrap, "decrypt_config", lambda body, _: documents[body])
    plan = voice_bootstrap.discover_video_plan("3.7.1", tmp_path, "hd", {157})
    assert plan["version"] == "3.7.1"
    assert plan["package_version"] == "3.8.0"
    assert len(plan["files"]) == 2
    assert all("/Windows/3.8.0/Video/" in item["remote_path"] for item in plan["files"])


def test_official_video_plan_resolves_cumulative_version_when_manifest_in_earlier_folder(tmp_path, monkeypatch):
    config = tmp_path / "KuroPublicConfig.ini"
    host = sorted(voice_bootstrap.CONFIG_HOSTS)[0]
    config.write_text(f"InternalPrefix=https://{host}\nUrlPath=client\n")
    item = {"PakName": "native.pak", "SigName": "native.sig", "PakSize": 12, "SigSize": 4,
            "PakHash": "a" * 40, "SigHash": "b" * 40}
    documents = {
        b"entry": {"default": {"CdnUrl": [{"url": "https://cdn-aws-hw-mc.aki-game.net/prod/client/", "weight": 1}],
                                "MixUri": "mix", "ResUri": "resources"}},
        b"package": {"PackageVersion": "3.8.0"},
        b"config": {"IndexSha1": hashlib.sha1(b"manifest_370").hexdigest()},
        b"manifest_370": {"VideoInfos": {"PakMap": {"157_0": item}}},
    }

    def read(url):
        if url == voice_bootstrap.CIPHER_SOURCE:
            return b"[encryption]\nkey = 'fixture'\n"
        if url.endswith("/index.json"):
            return b"entry"
        if url.endswith("/VideoConfig_hd.json"):
            return b"config"
        if url.endswith("/config.json"):
            return b"package"
        if url.endswith(("/Windows/3.8.0/Video/VideoManifest_hd.json", "/Windows/3.7.1/Video/VideoManifest_hd.json")):
            raise OSError("Not found on CDN")
        if url.endswith("/Windows/3.7.0/Video/VideoManifest_hd.json"):
            return b"manifest_370"
        raise AssertionError(f"Unexpected video bootstrap request: {url}")

    monkeypatch.setattr(voice_bootstrap, "read_url", read)
    monkeypatch.setattr(voice_bootstrap, "decrypt_config", lambda body, _: documents[body])
    plan = voice_bootstrap.discover_video_plan("3.7.1", tmp_path, "hd", {157})
    assert plan["version"] == "3.7.1"
    assert plan["package_version"] == "3.7.0"
    assert len(plan["files"]) == 2
    assert all("/Windows/3.7.0/Video/" in item["remote_path"] for item in plan["files"])


def test_extract_candidate_versions_discovers_versions_dynamically():
    package_config = {
        "PackageVersion": "4.2.0",
        "ResVersions": {
            "resource": {
                "Version": "4.1.5",
                "IndexSha1": {
                    "4.1.0": "hash1",
                    "4.1.3": "hash2",
                },
            },
            "launcher": {
                "Version": "4.1.9",
            },
        },
    }
    cands = voice_bootstrap.extract_candidate_versions(package_config, "4.1.2")
    assert cands == ["4.2.0", "4.1.2", "4.1.0", "4.1.5", "4.1.3", "4.1.9"]


