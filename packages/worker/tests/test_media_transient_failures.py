import errno
import subprocess
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from wuwa_story_worker import broker, cutscene_import, entity_media, story_spine
from wuwa_story_worker.asset_export import ExtractionProcessError


@pytest.mark.parametrize('code', [errno.EAGAIN, errno.EBUSY, errno.ENOMEM, errno.ENOSPC,
                                 errno.EDQUOT, errno.EMFILE, errno.ENFILE, errno.EIO])
def test_resource_exhaustion_is_retryable(code):
    assert broker.transient_job_error(OSError(code, 'temporary resource failure'))


def test_python_oom_is_retryable_without_retrying_missing_files_or_permissions():
    assert broker.transient_job_error(MemoryError('allocation failed'))
    assert not broker.transient_job_error(FileNotFoundError(errno.ENOENT, 'missing authored input'))
    assert not broker.transient_job_error(PermissionError(errno.EACCES, 'permissions'))


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    subprocess.TimeoutExpired("extractor", 7200),
    subprocess.CalledProcessError(-9, "extractor"),
    subprocess.CalledProcessError(137, "extractor"),
    ConnectionError("temporary storage failure"),
])
async def test_transient_spine_failure_reaches_broker_instead_of_partial(tmp_path, monkeypatch, error):
    if isinstance(error, subprocess.CalledProcessError):
        error = ExtractionProcessError(error, tmp_path / "export.log")
    monkeypatch.setattr(story_spine, "build_spine_bundle", AsyncMock(side_effect=error))
    target = {"kind": "spine", "path": "spine_background:7", "resource_id": 7}
    with pytest.raises(type(error)) as caught:
        await entity_media.build_entity_files(tmp_path, [target], tmp_path / "tool", tmp_path / "tool")
    assert caught.value is error
    assert broker.transient_job_error(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    ValueError("missing authored dependency"),
    RuntimeError("unsupported asset"),
    subprocess.CalledProcessError(1, "extractor"),
])
async def test_permanent_spine_failure_retains_valid_image_and_missing_report(tmp_path, monkeypatch, error):
    image = tmp_path / "static.png"
    Image.new("RGB", (2, 2)).save(image)
    monkeypatch.setattr(entity_media, "build_icons", AsyncMock(return_value={
        "/Game/Image": {"path": str(image), "raw_paths": []},
    }))
    monkeypatch.setattr(story_spine, "build_spine_bundle", AsyncMock(side_effect=error))
    targets = [{"kind": "image", "path": "/Game/Image"},
               {"kind": "spine", "path": "spine_background:7", "resource_id": 7}]
    files, missing = await entity_media.build_entity_files(tmp_path, targets, tmp_path / "tool", tmp_path / "tool")
    assert len(files) == 1 and files[0][0] == targets[0]
    assert missing == [targets[1]["path"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    subprocess.CalledProcessError(-9, ["decoder"], stderr=b"killed"),
    subprocess.CalledProcessError(137, ["decoder"], stderr=b"killed"),
    subprocess.TimeoutExpired(["decoder"], 120),
])
async def test_audio_video_tool_transient_exit_keeps_type_and_diagnostics(monkeypatch, error):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(cutscene_import.subprocess, "run", fail)
    with pytest.raises(type(error)) as caught:
        await cutscene_import.run_tool(["decoder"], timeout=120)
    assert caught.value is error
    assert caught.value.stderr == error.stderr
    assert broker.transient_job_error(caught.value)
