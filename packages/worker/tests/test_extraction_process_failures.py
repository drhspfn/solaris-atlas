import subprocess
import sys
from pathlib import Path

import pytest

from wuwa_story_worker import asset_export, broker


def extraction_paths(tmp_path):
    root, output, temporary = (tmp_path / name for name in ("client", "export", "attempt"))
    for path in (root, output, temporary):
        path.mkdir()
    log = tmp_path / "durable-export.log"
    return root, output, temporary, log


@pytest.mark.parametrize("returncode,transient", [(-9, True), (137, True), (1, False), (-15, False)])
def test_nonzero_exit_preserves_status_log_and_previous_export(tmp_path, monkeypatch, returncode, transient):
    root, output, temporary, log = extraction_paths(tmp_path)
    previous = output / "files"
    previous.mkdir()
    (previous / "valid.json").write_text("previous")
    original = None

    def killed(command, stdout, **kwargs):
        nonlocal original
        assert kwargs["check"] is True
        # Even a Done marker cannot make a failed process a successful export.
        (temporary / "files/partial.json").write_text("partial")
        stdout.write("[Done] Extracted 1 files\n")
        original = subprocess.CalledProcessError(returncode, command)
        raise original

    monkeypatch.setattr(asset_export.subprocess, "run", killed)
    with pytest.raises(subprocess.CalledProcessError) as caught:
        asset_export._extract_files(root, tmp_path / "extractor", "ConfigDB", {"files": []}, output, temporary, log)
    error = caught.value
    assert error.returncode == returncode
    assert error.cmd == original.cmd
    assert error.__cause__ is original
    assert error.log == log
    assert str(log) in str(error)
    assert log.read_text() == "[Done] Extracted 1 files\n"
    assert broker.transient_job_error(error) is transient
    assert (previous / "valid.json").read_text() == "previous"
    assert not (output / "manifest.json").exists()


def test_timeout_preserves_timeout_command_and_log(tmp_path, monkeypatch):
    root, output, temporary, log = extraction_paths(tmp_path)
    original = None

    def timeout(command, stdout, **kwargs):
        nonlocal original
        stdout.write("export started\n")
        original = subprocess.TimeoutExpired(command, kwargs["timeout"])
        raise original

    monkeypatch.setattr(asset_export.subprocess, "run", timeout)
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        asset_export._extract_files(root, tmp_path / "extractor", "ConfigDB", {"files": []}, output, temporary, log)
    error = caught.value
    assert error.timeout == 7200
    assert error.cmd == original.cmd
    assert error.__cause__ is original
    assert str(log) in str(error)
    assert log.read_text() == "export started\n"
    assert broker.transient_job_error(error)
    assert not (output / "files").exists()


@pytest.mark.parametrize("returncode", [1, 137])
def test_real_nonzero_process_is_not_reported_as_missing_done(tmp_path, returncode):
    root, output, temporary, log = extraction_paths(tmp_path)
    # Python stands in for the extractor; no game assets or external service needed.
    (root / "game").write_text(f"import sys\nprint('export started', flush=True)\nsys.exit({returncode})\n")
    with pytest.raises(subprocess.CalledProcessError) as caught:
        asset_export._extract_files(root, Path(sys.executable), "ConfigDB", {"files": []}, output, temporary, log)
    assert caught.value.returncode == returncode
    assert str(log) in str(caught.value)
    assert "export started" in log.read_text()
    assert not (output / "files").exists()
