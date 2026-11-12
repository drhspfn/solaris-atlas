import hashlib
import io
import json

import pytest

from wuwa_story_worker.voice_import import voice_identity
from wuwa_story_worker.voice_packages import package_files, resource_url


def test_base_download_uses_package_version_not_local_base_directory():
    manifest = {"Version": "3.7.6", "DiffPatch": {
        "BaseFiles": [{"Name": "pakchunk10.pak", "Size": "__kr_long__12", "Hash": "A" * 40}],
        "PatchFiles": [{"Name": "pakchunk10_P.pak", "Size": "8", "Hash": "B" * 40}]}}
    files = package_files(manifest, "Lang_en", "resources", "3.7.0")
    assert files[0]["path"] == "Lang_en/Base/pakchunk10.pak"
    assert files[0]["remote_path"] == "resources/Windows/3.7.0/pakchunk10.pak"
    assert files[1]["remote_path"] == "resources/Windows/3.7.6/pakchunk10_P.pak"


@pytest.mark.parametrize("name", ["en_vo_X_1.wem", "ja_vo_X_1.wem", "ko_vo_X_1.wem", "zh_vo_X_1.wem"])
def test_voice_identity_is_exact_and_language_specific(name):
    assert voice_identity(name) == (name[:2], "vo_X_1")


@pytest.mark.parametrize("name", ["../en_vo_X.wem", "vo_X.wem", "fr_vo_X.wem", "en_vo_X.wav"])
def test_voice_identity_rejects_ambiguous_or_unsafe_names(name):
    with pytest.raises(ValueError):
        voice_identity(name)


def test_voice_download_rejects_untrusted_cdn():
    with pytest.raises(ValueError):
        resource_url("https://example.com/prod/client/", "voice.pak")


def test_voice_download_accepts_official_aliyun_cdn():
    base = "https://cdn-aliyun-hw-mc.aki-game.net/prod/client/"
    assert resource_url(base, "voice.pak") == base + "voice.pak"
    with pytest.raises(ValueError):
        resource_url(base.replace(".aki-game.net", ".aki-game.net.example.com"), "voice.pak")


def test_explicit_rover_variants_resolve_missing_legacy_name(tmp_path):
    from wuwa_story_worker.voice_import import exported_voices
    for filename in ("en_vo_Character_JiYan_18_1_F.wem", "en_vo_Character_JiYan_18_1_M.WEM", "en_vo_Character_JiYan_18_10_F.wem"):
        (tmp_path / filename).write_bytes(b"audio")
    result = exported_voices(tmp_path, {"vo_Character_JiYan_18_1"})
    assert set(result) == {"vo_Character_JiYan_18_1"}
    assert set(result["vo_Character_JiYan_18_1"]) == {("en", "female"), ("en", "male")}


def test_exact_gender_named_reference_is_not_remapped(tmp_path):
    from wuwa_story_worker.voice_import import exported_voices
    (tmp_path / "en_vo_Line_F.wem").write_bytes(b"audio")
    assert set(exported_voices(tmp_path, {"vo_Line_F"})["vo_Line_F"]) == {("en", None)}


@pytest.mark.parametrize("corrupt", [False, True])
def test_download_shares_only_checksum_verified_archives_across_plans(tmp_path, monkeypatch, corrupt):
    from wuwa_story_worker import voice_packages as packages
    from wuwa_story_worker.client_assets import plan_id

    data = b"verified archive"
    item = {"path": "Lang_en/Base/voice.pak", "remote_path": "old/voice.pak",
            "size": len(data), "sha1": hashlib.sha1(data).hexdigest()}
    old = {"job_type": "voices.download", "version": "3.7.0", "files": [item],
           "cdns": ["https://cdn-aliyun-hw-mc.aki-game.net/prod/client/"]}
    old["id"] = plan_id(old)
    previous = tmp_path / ("3.7.0-" + old["id"][:16])
    source = previous / "game/Client/Saved/Resources/3.7.0" / item["path"]
    source.parent.mkdir(parents=True)
    source.write_bytes(b"x" * len(data) if corrupt else data)
    (previous / "plan.json").write_text(json.dumps(old))
    (previous / "status.json").write_text(json.dumps({"state": "downloaded", "id": old["id"]}))
    new = {**old, "files": [{**item, "remote_path": "new/voice.pak"}]}
    new["id"] = plan_id(new)
    target = tmp_path / ("3.7.0-" + new["id"][:16]) / "game/Client/Saved/Resources/3.7.0" / item["path"]
    target.parent.mkdir(parents=True)
    partial = target.with_suffix(".pak.part")
    partial.write_bytes(b"interrupted download")
    downloads = []

    def fetch(*args, **kwargs):
        downloads.append(args)
        return io.BytesIO(data)

    monkeypatch.setattr(packages, "urlopen", fetch)
    packages.download_voice_plan(new, tmp_path)
    assert target.read_bytes() == data
    assert not partial.exists()
    assert len(downloads) == int(corrupt)
    if not corrupt:
        assert target.samefile(source)
    assert source.read_bytes() == (b"x" * len(data) if corrupt else data)

@pytest.mark.parametrize("client_version,package_version,accepted", [
    ("3.7.0", "3.7.0", True),
    ("3.7.1", "3.7.0", True),
    ("3.7.0", "3.7.1", True),
    ("3.7.1", "3.8.0", True),
    ("3.7.1", "3.6.0", False),
    ("3.7.1", "4.7.0", False),
    ("3.7.1", "3.7.bad", False),
])
def test_voice_plan_separates_client_hotfix_from_authored_package_version(
    tmp_path, monkeypatch, client_version, package_version, accepted,
):
    from wuwa_story_worker import voice_packages as packages

    public = tmp_path / "KuroPublicConfig.ini"
    public.write_text("UrlPath=config\nInternalPrefix=https://aki-config-aws.aki-game.net\n")
    cdn = "https://cdn-aws-hw-mc.aki-game.net/prod/client/"
    manifest = {"Version": "3.7.6", "DiffPatch": {"BaseFiles": [
        {"Name": "voice.pak", "Size": "12", "Hash": "a" * 40},
    ], "PatchFiles": []}}
    body = json.dumps(manifest).encode()
    info = {"Version": "3.7.6", "IndexSha1": {"3.7.6": hashlib.sha1(body).hexdigest()}}
    aggregated = json.dumps({"role_lang_en": manifest}).encode()
    config = {"PackageVersion": package_version, "ResVersions": {
        **{lang: info for lang in packages.LANGUAGES},
        "ManifestAggregated": {**info, "IndexSha1": {"3.7.6": hashlib.sha1(aggregated).hexdigest()}},
    }}

    def fetch(url):
        if url.endswith("index.json"):
            return json.dumps({"default": {"CdnUrl": [{"url": cdn, "weight": 1}],
                                            "MixUri": "mix", "ResUri": "resources"}}).encode()
        if url.endswith("config.json"):
            return json.dumps(config).encode()
        return aggregated if url.endswith("ManifestAggregated.txt") else body

    monkeypatch.setattr(packages, "read_url", fetch)
    monkeypatch.setattr(packages, "decrypt_config", lambda value, crypto: json.loads(value))
    if not accepted:
        with pytest.raises(ValueError, match="Client CDN serves"):
            packages.discover_voice_plan(client_version, public, {})
        return
    plan = packages.discover_voice_plan(client_version, public, {})
    assert plan["version"] == client_version
    assert plan.get("package_version", client_version) == package_version
    assert len(plan["files"]) == 5
    assert all(f"/Windows/{package_version}/voice.pak" in item["remote_path"] for item in plan["files"])
    assert {item["path"].split("/")[0] for item in plan["files"]} == {
        "Lang_en", "Lang_ja", "Lang_ko", "Lang_zh", "RoleVoice",
    }
