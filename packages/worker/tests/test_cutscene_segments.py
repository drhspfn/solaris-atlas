import array
import io
import random
import shutil
import subprocess
import wave
from pathlib import Path
from types import SimpleNamespace

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


@pytest.mark.parametrize("tolerance", [0, 12, 255])
def test_pixel_mask_optimization_matches_max_channel_definition(tolerance):
    settings = segments.ComparisonSettings(width=40, height=30, pixel_tolerance=tolerance)
    rng = random.Random(79)
    left, right = rng.randbytes(3600), rng.randbytes(3600)
    expected = sum(max(abs(left[index + channel] - right[index + channel]) for channel in range(3))
                   > tolerance for index in range(0, len(left), 3))
    assert segments.changed_pixels(left, right, settings) == expected
    # Blue-only changes must survive the RGB-mask-to-luminance conversion.
    left = bytes(3600)
    right = bytes([0, 0, min(255, tolerance + 1)]) * 1200
    assert segments.changed_pixels(left, right, settings) == (1200 if tolerance < 255 else 0)


def test_identical_frame_fast_path_does_not_construct_images(monkeypatch):
    monkeypatch.setattr(segments.Image, "frombytes", lambda *_args: pytest.fail("identical bytes"))
    assert segments.changed_pixels(bytes(300), bytes(300), segments.ComparisonSettings(width=10, height=10)) == 0


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
            assert command[command.index("-threads") + 1] == "1"
            assert command[command.index("-filter_threads") + 1] == "1"
            assert command[command.index("-vf") + 1].startswith("scale=10:10:")
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


def test_identical_input_hashes_decode_once_without_redundant_audio_work(monkeypatch, tmp_path):
    monkeypatch.setattr(segments, "probe", lambda *_args: (10, True))
    monkeypatch.setattr(segments.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("identical audio"))
    decoded = []

    class Process:
        def __init__(self, command, stdout, stderr):
            decoded.append(command[command.index("-i") + 1])
            self.stdout = io.BytesIO(bytes(300 * 6))
            stderr.write("".join(f"pts_time:{index / 10}\n" for index in range(6)).encode())

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

    monkeypatch.setattr(segments.subprocess, "Popen", Process)
    report = segments.analyze([Path("a"), Path("b")], tmp_path / "ffmpeg", tmp_path,
                              segments.ComparisonSettings(width=10, height=10),
                              input_hashes=["same-full-file-hash", "same-full-file-hash"])
    assert len(decoded) == 1
    assert report["frames"] == 6
    assert report["difference_window"] is None
    assert report["visual_changed_pixels"] == report["audio_changed_samples"] == [0] * 6


def test_segment_export_uses_accurate_input_seek_and_bounded_threads(monkeypatch, tmp_path):
    paths = [tmp_path / "a.mp4", tmp_path / "b.mp4"]
    for path in paths:
        path.write_bytes(path.name.encode())
    monkeypatch.setattr(segments, "analyze", lambda *_args, **_kwargs: {
        "fps": 30, "frames": 900, "difference_window": [601, 750]})
    commands = []

    def run(command, **kwargs):
        if "-version" in command:
            return SimpleNamespace(stdout=b"ffmpeg test\n")
        if "-c:v" in command:
            assert command.index("-ss") < command.index("-i")
            assert "-accurate_seek" in command
            assert command[command.index("-preset") + 1] == "veryfast"
            assert command[command.index("-threads") + 1] == "1"
            assert command[command.index("-threads:v") + 1] == "1"
            Path(command[-1]).write_bytes(b"complete segment")
            commands.append(command)
            return SimpleNamespace(stdout=b"")
        assert command[command.index("-threads") + 1] == "1"
        duration = float(commands[-1][commands[-1].index("-t") + 1])
        return SimpleNamespace(stdout=f"frame={round(duration * 30)}\n".encode())

    monkeypatch.setattr(segments.subprocess, "run", run)
    report, exported = segments.export_segments(paths, tmp_path / "ffmpeg", tmp_path / "output")
    assert [part["role"] for part in exported] == ["intro", "branch-0", "branch-1", "outro"]
    assert [float(command[command.index("-ss") + 1]) for command in commands] == [0, 601 / 30, 601 / 30, 25]
    assert all(part["path"].exists() for part in exported)
    assert report["difference_window"] == [601, 750]


def test_wrong_frame_count_never_publishes_a_segment(monkeypatch, tmp_path):
    paths = [tmp_path / "a.mp4", tmp_path / "b.mp4"]
    for path in paths:
        path.write_bytes(path.name.encode())
    monkeypatch.setattr(segments, "analyze", lambda *_args, **_kwargs: {
        "fps": 30, "frames": 60, "difference_window": None})

    def run(command, **kwargs):
        if "-version" in command:
            return SimpleNamespace(stdout=b"ffmpeg test\n")
        if "-c:v" in command:
            Path(command[-1]).write_bytes(b"short segment")
        return SimpleNamespace(stdout=b"frame=59\n")

    monkeypatch.setattr(segments.subprocess, "run", run)
    with pytest.raises(ValueError, match="frame count differs"):
        segments.export_segments(paths, tmp_path / "ffmpeg", tmp_path / "output")
    assert not list((tmp_path / "output").rglob("shared.mp4"))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="native FFmpeg unavailable")
def test_native_non_keyframe_input_seek_matches_decoded_output_seek(monkeypatch, tmp_path):
    ffmpeg = Path(shutil.which("ffmpeg"))
    source = tmp_path / "source.mp4"
    subprocess.run([
        str(ffmpeg), "-v", "error", "-y", "-filter_threads", "1",
        "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=10:duration=6",
        "-c:v", "libx264", "-threads:v", "1", "-preset", "ultrafast", "-crf", "0",
        "-g", "20", "-keyint_min", "20", "-sc_threshold", "0", str(source),
    ], check=True, capture_output=True, timeout=30)
    monkeypatch.setattr(segments, "analyze", lambda *_args, **_kwargs: {
        "fps": 10, "frames": 60, "difference_window": [17, 43]})
    _, exported = segments.export_segments([source, source], ffmpeg, tmp_path / "segments")
    # The outro starts at frame 43, between source keyframes 40 and 60.
    actual = next(part["path"] for part in exported if part["role"] == "outro")
    reference = tmp_path / "reference.mp4"
    subprocess.run([
        str(ffmpeg), "-v", "error", "-y", "-threads", "1", "-filter_threads", "1",
        "-i", str(source), "-ss", "4.3", "-t", "1.7", "-map", "0:v:0", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-threads:v", "1", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(reference),
    ], check=True, capture_output=True, timeout=30)

    def frames(path):
        result = subprocess.run([
            str(ffmpeg), "-v", "error", "-threads", "1", "-i", str(path),
            "-map", "0:v:0", "-f", "framemd5", "-",
        ], check=True, capture_output=True, timeout=30)
        return [line for line in result.stdout.splitlines() if line and not line.startswith(b"#")]

    assert frames(actual) == frames(reference)
    assert len(frames(actual)) == 17


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="native FFmpeg unavailable")
def test_native_comparison_detects_one_frame_difference_without_sampling(tmp_path):
    ffmpeg = Path(shutil.which("ffmpeg"))
    paths = [tmp_path / "common.mkv", tmp_path / "variant.mkv"]
    for index, path in enumerate(paths):
        command = [str(ffmpeg), "-v", "error", "-y", "-filter_threads", "1",
                   "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=10:duration=3"]
        if index:
            command += ["-vf", "drawbox=x=10:y=10:w=32:h=32:color=white:t=fill:enable='eq(n,13)'"]
        subprocess.run([*command, "-c:v", "ffv1", "-threads:v", "1", str(path)],
                       check=True, capture_output=True, timeout=30)
    report = segments.analyze(paths, ffmpeg, tmp_path, segments.ComparisonSettings(
        width=160, height=90, guard_frames=0, minimum_shared_seconds=0))
    assert report["frames"] == 30
    assert report["difference_window"] == [13, 14]
    assert report["visual_changed_pixels"][13] >= 24
    assert sum(value > 0 for value in report["visual_changed_pixels"]) == 1
