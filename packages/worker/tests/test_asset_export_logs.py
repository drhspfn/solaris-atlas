from pathlib import Path

import pytest

from wuwa_story_worker import asset_export


@pytest.mark.asyncio
async def test_failed_export_log_survives_workspace_cleanup(tmp_path, monkeypatch):
    base = tmp_path / "assets"
    root = base / "patch-workspaces" / "123"
    output = root / "exports" / "abc"
    output.mkdir(parents=True)
    monkeypatch.setattr(asset_export, "asset_workspace", lambda: base)

    def fail(root, executable, asset_filter, plan, output, temporary, log):
        log.write_text("[Fail] test", encoding="utf-8")
        raise RuntimeError(f"failed; inspect {log}")

    monkeypatch.setattr(asset_export, "_extract_files", fail)
    with pytest.raises(RuntimeError):
        await asset_export._finish_export(root, Path("tool"), "test", False, {}, "abc", output, "sha")
    logs = list((base / "export-logs" / "123" / "abc").glob("*.log"))
    assert len(logs) == 1
    assert logs[0].read_text(encoding="utf-8") == "[Fail] test"
