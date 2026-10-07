import pytest

from wuwa_story_worker import tooling


@pytest.mark.parametrize("env_name", tooling.TOOLS)
def test_bundled_tools_need_no_path_environment_variables(tmp_path, monkeypatch, env_name):
    executable = tmp_path / tooling.TOOLS[env_name][0]
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_bytes(b"tool")
    monkeypatch.setattr(tooling, "DEFAULT_TOOLS_DIR", str(tmp_path))
    monkeypatch.delenv("WUWA_TOOLS_DIR", raising=False)
    monkeypatch.delenv(env_name, raising=False)
    assert tooling.tool_path(env_name) == executable


def test_bundled_tool_resolution_does_not_copy_or_create_native_files(tmp_path, monkeypatch):
    executable = tmp_path / "fmodelcli/FModelCLI"
    executable.parent.mkdir()
    executable.write_bytes(b"tool")
    monkeypatch.setenv("WUWA_TOOLS_DIR", str(tmp_path))
    monkeypatch.setenv("WUWA_FMODEL_PATH", "")
    assert tooling.tool_path("WUWA_FMODEL_PATH") == executable
    assert set(tmp_path.rglob("*")) == {executable.parent, executable}


def test_explicit_tool_override_remains_supported(tmp_path, monkeypatch):
    executable = tmp_path / "FModelCLI.exe"
    executable.write_bytes(b"tool")
    monkeypatch.setenv("WUWA_FMODEL_PATH", str(executable))
    assert tooling.tool_path("WUWA_FMODEL_PATH") == executable


def test_missing_tool_reports_image_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("WUWA_TOOLS_DIR", str(tmp_path))
    monkeypatch.delenv("WUWA_FMODEL_PATH", raising=False)
    with pytest.raises(RuntimeError, match="Rebuild the worker image"):
        tooling.tool_path("WUWA_FMODEL_PATH")


def test_empty_tools_directory_uses_image_default(tmp_path, monkeypatch):
    executable = tmp_path / "fmodelcli/FModelCLI"
    executable.parent.mkdir()
    executable.write_bytes(b"tool")
    monkeypatch.setattr(tooling, "DEFAULT_TOOLS_DIR", str(tmp_path))
    monkeypatch.setenv("WUWA_TOOLS_DIR", "")
    monkeypatch.setenv("WUWA_FMODEL_PATH", "")
    assert tooling.tool_path("WUWA_FMODEL_PATH") == executable
