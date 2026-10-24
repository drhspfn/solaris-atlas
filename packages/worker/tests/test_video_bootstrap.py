import hashlib

import pytest

from wuwa_story_worker import voice_bootstrap


@pytest.mark.parametrize("available_id,expected_missing", [(157, [158]), (241, [157, 158])])
def test_official_video_plan_uses_native_cg_ids_and_reports_absent_mappings(tmp_path, monkeypatch, available_id, expected_missing):
    config = tmp_path / "KuroPublicConfig.ini"
    host = sorted(voice_bootstrap.CONFIG_HOSTS)[0]
    config.write_text(f"InternalPrefix=https://{host}\nUrlPath=client\n")
    item = {"PakName": "native.pak", "SigName": "native.sig", "PakSize": 12, "SigSize": 4,
            "PakHash": "a" * 40, "SigHash": "b" * 40}
    documents = {
        b"entry": {"default": {"CdnUrl": [{"url": "https://cdn-aws-hw-mc.aki-game.net/prod/client/", "weight": 1}],
                                "MixUri": "mix", "ResUri": "resources"}},
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
        if url.endswith("/VideoManifest_hd.json"):
            return b"manifest"
        raise AssertionError("Unexpected video bootstrap request")

    monkeypatch.setattr(voice_bootstrap, "read_url", read)
    monkeypatch.setattr(voice_bootstrap, "decrypt_config", lambda body, _: documents[body])
    diagnostics = {}
    plan = voice_bootstrap.discover_video_plan("3.7.0", tmp_path, "hd", {157, 158}, diagnostics=diagnostics)
    assert diagnostics["requested_cg_ids"] == [157, 158]
    assert diagnostics["missing_cg_ids"] == expected_missing
    assert [p["cg_id"] for p in diagnostics["video_packages"]] == ([157] if available_id == 157 else [])
    assert [p["path"] for p in plan["files"]] == (["Video/native.pak", "Video/native.sig"] if available_id == 157 else [])
    assert plan["id"] == voice_bootstrap.plan_id(plan)
    assert plan == voice_bootstrap.discover_video_plan("3.7.0", tmp_path, "hd", {157, 158})
