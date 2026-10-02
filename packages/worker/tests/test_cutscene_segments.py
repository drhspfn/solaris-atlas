import array
import io
import wave
from pathlib import Path

import pytest

from wuwa_story_worker import cutscene_segments as segments


def test_localized_rover_difference_is_not_diluted_by_the_background():
    settings = segments.ComparisonSettings(width=100, height=100)
    common = bytes(100 * 100 * 3)
    variant = bytes([80] * 30 * 3) + common[30 * 3 :]
    # Only 0.3% of the frame changes; whole-frame averaging would miss this.
    assert segments.changed_pixels(common, variant, settings) == 30
    compression_noise = bytes([2] * len(common))
    assert segments.changed_pixels(common, compression_noise, settings) == 0


def test_guarded_first_last_difference_and_shared_tail():
    settings = segments.ComparisonSettings(guard_frames=1, minimum_shared_seconds=0.1)
    assert segments.difference_window([False] * 10 + [True] * 5 + [False] * 10, 10, settings) == (
        9,
        16,
    )
    assert segments.difference_window([False] * 25, 10, settings) is None
    assert segments.difference_window([True] * 25, 10, settings) == (0, 25)


def test_streamed_comparison_records_small_early_changes(monkeypatch, tmp_path):
    settings = segments.ComparisonSettings(width=10, height=10, guard_frames=0)
    common = bytes(300)
    changed = bytes([80] * 30 * 3) + common[90:]
    frames = {"a": [common] * 6, "b": [common, common, changed, common, changed, common]}
    monkeypatch.setattr(segments, "probe", lambda *_: (10, False))

    class Process:
        def __init__(self, command, stdout, stderr):
            key = command[command.index("-i") + 1]
            self.stdout = io.BytesIO(b"".join(frames[key]))
            stderr.write("".join(f"pts_time:{i / 10}\n" for i in range(6)).encode())

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

    monkeypatch.setattr(segments.subprocess, "Popen", Process)
    report = segments.analyze(
        [tmp_path.__class__("a"), tmp_path.__class__("b")], tmp_path / "ffmpeg", tmp_path, settings
    )
    assert report["difference_window"] == [2, 5]
    assert report["visual_changed_pixels"] == [0, 0, 30, 0, 30, 0]


def test_unaligned_frame_rates_are_not_guessed(monkeypatch, tmp_path):
    rates = iter([(30, False), (24, False)])
    monkeypatch.setattr(segments, "probe", lambda *_: next(rates))
    with pytest.raises(ValueError, match="do not align"):
        segments.analyze(
            [tmp_path / "a", tmp_path / "b"],
            tmp_path / "ffmpeg",
            tmp_path,
            segments.ComparisonSettings(),
        )


def test_audio_only_difference_prevents_false_shared_video(monkeypatch, tmp_path):
    settings = segments.ComparisonSettings(width=10, height=10, guard_frames=0)
    monkeypatch.setattr(segments, "probe", lambda *_: (10, True))

    def decode_audio(command, **kwargs):
        source = command[command.index("-i") + 1]
        samples = array.array("h", [0] * 4800 * 2 * 6)
        if source == "b":
            samples[4800 * 2 * 2 : 4800 * 2 * 3] = array.array("h", [1000] * 4800 * 2)
        with wave.open(command[-1], "wb") as writer:
            writer.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
            writer.writeframes(samples.tobytes())

    class Process:
        def __init__(self, command, stdout, stderr):
            self.stdout = io.BytesIO(bytes(300 * 6))
            stderr.write("".join(f"pts_time:{i / 10}\n" for i in range(6)).encode())

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

    monkeypatch.setattr(segments.subprocess, "run", decode_audio)
    monkeypatch.setattr(segments.subprocess, "Popen", Process)
    report = segments.analyze([Path("a"), Path("b")], tmp_path / "ffmpeg", tmp_path, settings)
    assert report["visual_changed_pixels"] == [0] * 6
    assert report["difference_window"] == [2, 3]
