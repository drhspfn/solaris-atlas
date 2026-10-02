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
