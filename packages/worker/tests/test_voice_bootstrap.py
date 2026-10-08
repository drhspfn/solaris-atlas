from unittest.mock import Mock

import pytest

from wuwa_story_worker import voice_bootstrap
from wuwa_story_worker.client_assets import plan_id


def test_game_configuration_is_discovered_without_user_crypto_file(tmp_path, monkeypatch):
    config = tmp_path / "Client/Config/Kuro/KuroPublicConfig.ini"
    config.parent.mkdir(parents=True)
    config.write_text("UrlPath=test")
    monkeypatch.setattr(voice_bootstrap, "read_url", Mock(return_value=b'[encryption]\nkey="fixture"\niv="fixture"'))
    discover = Mock(return_value={"files": [], "version": "3.7.0"})
    monkeypatch.setattr(voice_bootstrap, "discover_voice_plan", discover)
    plan = voice_bootstrap.discover_client_voices("3.7.0", tmp_path)
    discover.assert_called_once_with("3.7.0", config, {"key": "fixture", "iv": "fixture"})
    assert plan["id"] == plan_id(plan)
    assert plan["cipher_source"] == voice_bootstrap.CIPHER_SOURCE


def test_missing_game_configuration_is_not_silently_skipped(tmp_path):
    with pytest.raises(ValueError, match="exactly one"):
        voice_bootstrap.discover_client_voices("3.7.0", tmp_path)
