import errno
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from wuwa_story_worker import broker, cutscene_frame_stems


@pytest.mark.asyncio
@pytest.mark.parametrize('error', [
    TimeoutError('storage timeout'),
    OSError(errno.ENOSPC, 'disk full'),
    OSError(errno.ENOMEM, 'memory pressure'),
    subprocess.TimeoutExpired('renderer', 120),
    subprocess.CalledProcessError(-9, 'renderer'),
    RuntimeError('dependency unavailable'),
    broker.JobDeferred('dependency queued'),
])
async def test_operational_failure_is_not_published_as_missing_audio(tmp_path, monkeypatch, error):
    event = SimpleNamespace(bank='event.bnk', audio_directory='audio', rover=None,
                            language='en', reference='/Game/Event.Event')
    video = SimpleNamespace(frame_audio=[event])
    missing = []
    monkeypatch.setattr(cutscene_frame_stems, 'render_role_stems', AsyncMock(side_effect=error))
    with pytest.raises(type(error)) as caught:
        await cutscene_frame_stems.prepare_frame_stems(video, tmp_path, tmp_path, tmp_path / 'decoder', set(), missing)
    assert caught.value is error
    assert missing == []


@pytest.mark.asyncio
async def test_unsupported_source_still_keeps_video_and_reports_missing_audio(tmp_path, monkeypatch):
    event = SimpleNamespace(bank='event.bnk', audio_directory='audio', rover=None,
                            language='en', reference='/Game/Event.Event')
    video = SimpleNamespace(frame_audio=[event])
    monkeypatch.setattr(cutscene_frame_stems, 'render_role_stems', AsyncMock(side_effect=ValueError('unknown native role')))
    missing = []
    assert await cutscene_frame_stems.prepare_frame_stems(
        video, tmp_path, tmp_path, tmp_path / 'decoder', set(), missing) == []
    assert missing == ['/Game/Event.Event: unknown native role']
