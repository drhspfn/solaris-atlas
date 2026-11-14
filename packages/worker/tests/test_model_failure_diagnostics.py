import subprocess

import pytest

from wuwa_story_worker import character_models as worker


async def test_exception_before_buffered_imports_is_kept_and_full_log_survives_scratch(tmp_path, monkeypatch):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    workspace = tmp_path / "client-assets"
    monkeypatch.setattr(worker, "asset_workspace", lambda: workspace)
    raw = (b'Traceback (most recent call last):\n  File "model_bundle.py", line 695\n'
           b'ValueError: missing optional bone\n' + b'[UEFORMAT] Importing clip\n' * 5000)

    async def fail(args, log, timeout):
        log.write_bytes(raw)
        raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(worker, "tool", fail)
    with pytest.raises(RuntimeError, match="missing optional bone") as caught:
        await worker.run_blender(["blender"], scratch, run_id=17)
    retained = list((workspace / "model-diagnostics" / "17").glob("*.blender.log"))
    assert len(retained) == 1 and retained[0].read_bytes() == raw
    assert str(retained[0]) in str(caught.value)
    assert len(str(caught.value).split("(last 8 KiB):\n", 1)[1].encode()) <= 8192


async def test_failed_log_backup_does_not_mask_blender_exception(tmp_path, monkeypatch):
    workspace = tmp_path / "not-a-directory"
    workspace.write_text("occupied")
    monkeypatch.setattr(worker, "asset_workspace", lambda: workspace)

    async def fail(args, log, timeout):
        log.write_text("ValueError: actual failure\n")
        raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(worker, "tool", fail)
    with pytest.raises(RuntimeError, match="actual failure"):
        await worker.run_blender(["blender"], tmp_path, run_id=17)
