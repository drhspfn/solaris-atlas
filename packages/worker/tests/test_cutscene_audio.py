import wave

import pytest

from wuwa_story_worker.cutscene_audio import validate_stem


def test_stems_must_be_aligned_to_full_movie(tmp_path):
    path = tmp_path / "voice.wav"
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(48000)
        stream.writeframes(b"\0" * 48000 * 4)
    validate_stem(path, 1)
    with pytest.raises(ValueError, match="complete original"):
        validate_stem(path, 10)
