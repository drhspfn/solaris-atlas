"""Detect localized variant differences and export frame-aligned playable segments."""

import array
import hashlib
import json
import re
import subprocess
import time
import wave
from dataclasses import asdict, dataclass
from pathlib import Path

from PIL import Image, ImageChops

from wuwa_story_worker.client_assets import save_json, workspace_lock


@dataclass(frozen=True)
class ComparisonSettings:
    width: int = 960
    height: int = 540
    blur_sigma: float = 1.2
    pixel_tolerance: int = 12
    minimum_changed_pixels: int = 24
    audio_tolerance: int = 32
    minimum_changed_samples: int = 8
    guard_frames: int = 3
    minimum_shared_seconds: float = 0.1
    timeout_seconds: int = 600
    algorithm: str = "localized-rgb-and-pcm-v1"


def changed_pixels(left: bytes, right: bytes, settings: ComparisonSettings) -> int:
    size = (settings.width, settings.height)
    images = [Image.frombytes("RGB", size, value) for value in (left, right)]
    red, green, blue = ImageChops.difference(*images).split()
    difference = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    return difference.point(
        [0 if value <= settings.pixel_tolerance else 255 for value in range(256)]
    ).histogram()[255]


def difference_window(
    scores: list[bool], fps: float, settings: ComparisonSettings
) -> tuple[int, int] | None:
    indexes = [index for index, different in enumerate(scores) if different]
    if not indexes:
        return None
    first = max(0, indexes[0] - settings.guard_frames)
    last = min(len(scores), indexes[-1] + 1 + settings.guard_frames)
    if first / fps < settings.minimum_shared_seconds:
        first = 0
    if (len(scores) - last) / fps < settings.minimum_shared_seconds:
        last = len(scores)
    return first, last


def probe(ffmpeg: Path, path: Path) -> tuple[float, bool]:
    result = subprocess.run(
        [str(ffmpeg), "-hide_banner", "-i", str(path)], capture_output=True, timeout=30
    )
    text = result.stderr.decode("utf-8", errors="replace")
    match = re.search(r"Video:.*?, (\d+(?:\.\d+)?) fps", text)
    if not match:
        raise ValueError("Cannot determine source frame rate")
    return float(match[1]), "Audio:" in text


def analyze(paths: list[Path], ffmpeg: Path, root: Path, settings: ComparisonSettings) -> dict:
    if not 2 <= len(paths) <= 8:
        raise ValueError("Variant comparison requires between two and eight videos")
    formats = [probe(ffmpeg, path) for path in paths]
    fps = formats[0][0]
    if fps <= 0 or any(rate != fps for rate, _ in formats):
        raise ValueError("Variant frame rates do not align; keep independent originals")
    processes, logs, audio = [], [], []
    scores, visual_counts, audio_counts = [], [], []
    frame_size = settings.width * settings.height * 3
    started = time.monotonic()
    try:
        for index, (path, (_, has_audio)) in enumerate(zip(paths, formats, strict=True)):
            if has_audio:
                wav_path = root / f"audio-{index}.wav"
                subprocess.run(
                    [
                        str(ffmpeg),
                        "-v",
                        "error",
                        "-y",
                        "-i",
                        str(path),
                        "-vn",
                        "-ac",
                        "2",
                        "-ar",
                        "48000",
                        "-c:a",
                        "pcm_s16le",
                        str(wav_path),
                    ],
                    capture_output=True,
                    check=True,
                    timeout=120,
                )
                audio.append(wave.open(str(wav_path), "rb"))
            else:
                audio.append(None)
            log = (root / f"frames-{index}.log").open("wb")
            logs.append(log)
            processes.append(
                subprocess.Popen(
                    [
                        str(ffmpeg),
                        "-hide_banner",
                        "-nostats",
                        "-i",
                        str(path),
                        "-vf",
                        f"scale={settings.width}:{settings.height}:flags=area,gblur=sigma={settings.blur_sigma},showinfo",
                        "-fps_mode",
                        "passthrough",
                        "-pix_fmt",
                        "rgb24",
                        "-f",
                        "rawvideo",
                        "pipe:1",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=log,
                )
            )
        frame = 0
        while True:
            if time.monotonic() - started > settings.timeout_seconds:
                raise TimeoutError("Cutscene comparison timed out")
            blobs = [process.stdout.read(frame_size) for process in processes]
            if not any(blobs):
                break
            if any(len(blob) != frame_size for blob in blobs):
                raise ValueError("Variant frame counts do not align; keep independent originals")
            visual = max(changed_pixels(blobs[0], other, settings) for other in blobs[1:])
            sample_frames = round((frame + 1) * 48000 / fps) - round(frame * 48000 / fps)
            samples = []
            for stream in audio:
                chunk = stream.readframes(sample_frames) if stream else b""
                chunk = chunk.ljust(sample_frames * 4, b"\0")
                samples.append(array.array("h", chunk))
            sound = max(
                sum(
                    abs(a - b) > settings.audio_tolerance
                    for a, b in zip(samples[0], other, strict=True)
                )
                for other in samples[1:]
            )
            visual_counts.append(visual)
            audio_counts.append(sound)
            scores.append(
                visual >= settings.minimum_changed_pixels
                or sound >= settings.minimum_changed_samples
            )
            frame += 1
        for process in processes:
            if process.wait(timeout=30) != 0:
                raise ValueError("Video decoder failed")
        for log in logs:
            log.flush()
        # Range timestamps are frame indexes only after constant-rate alignment is verified.
        for index in range(len(paths)):
            times = [
                float(value)
                for value in re.findall(
                    r"pts_time:([\d.e+-]+)",
                    (root / f"frames-{index}.log").read_text(encoding="utf-8", errors="replace"),
                )
            ]
            if len(times) != frame or any(
                abs(value - i / fps) > 0.002 for i, value in enumerate(times)
            ):
                raise ValueError("Variable frame timing cannot be segmented by frame indexes")
        if not frame:
            raise ValueError("Empty video")
        window = difference_window(scores, fps, settings)
        return {
            "fps": fps,
            "frames": frame,
            "difference_window": list(window) if window else None,
            "visual_changed_pixels": visual_counts,
            "audio_changed_samples": audio_counts,
        }
    finally:
        for process in processes:
            if process.stdout:
                process.stdout.close()
            if process.poll() is None:
                process.kill()
            process.wait()
        for log in logs:
            log.close()
        for stream in audio:
            if stream:
                stream.close()


def export_segments(
    paths: list[Path], ffmpeg: Path, output: Path, settings: ComparisonSettings | None = None
) -> tuple[dict, list[dict]]:
    settings = settings or ComparisonSettings()
    hashes = []
    for path in paths:
        with path.open("rb") as stream:
            hashes.append(hashlib.file_digest(stream, "sha256").hexdigest())
    tool = (
        subprocess.run([str(ffmpeg), "-version"], capture_output=True, check=True, timeout=30)
        .stdout.splitlines()[0]
        .decode()
    )
    identity = {"settings": asdict(settings), "inputs": hashes, "ffmpeg": tool}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    root = output / key
    with workspace_lock(root):
        report_path = root / "analysis.json"
        if report_path.exists():
            report = json.loads(report_path.read_text(encoding="utf-8"))
        else:
            report = {**identity, "id": key, **analyze(paths, ffmpeg, root, settings)}
            save_json(report_path, report)
        fps, count = report["fps"], report["frames"]
        window = report["difference_window"]
        specs = []
        if window is None:
            specs.append(("shared", 0, 0, count))
        else:
            first, last = window
            if first:
                specs.append(("intro", 0, 0, first))
            specs.extend((f"branch-{index}", index, first, last) for index in range(len(paths)))
            if last < count:
                specs.append(("outro", 0, last, count))
        segments = []
        for role, index, first, last in specs:
            target = root / f"{role}.mp4"
            if not target.exists():
                temporary = root / f"{role}.partial.mp4"
                subprocess.run(
                    [
                        str(ffmpeg),
                        "-v",
                        "error",
                        "-y",
                        "-i",
                        str(paths[index]),
                        "-ss",
                        str(first / fps),
                        "-t",
                        str((last - first) / fps),
                        "-map",
                        "0:v:0",
                        "-map",
                        "0:a?",
                        "-c:v",
                        "libx264",
                        "-preset",
                        "fast",
                        "-crf",
                        "18",
                        "-c:a",
                        "aac",
                        "-b:a",
                        "192k",
                        "-movflags",
                        "+faststart",
                        str(temporary),
                    ],
                    capture_output=True,
                    check=True,
                    timeout=settings.timeout_seconds,
                )
                # Verify both decode integrity and the exact exported frame count.
                decoded = subprocess.run(
                    [
                        str(ffmpeg),
                        "-v",
                        "error",
                        "-i",
                        str(temporary),
                        "-progress",
                        "pipe:1",
                        "-f",
                        "null",
                        "-",
                    ],
                    capture_output=True,
                    check=True,
                    timeout=settings.timeout_seconds,
                )
                counts = re.findall(rb"frame=\s*(\d+)", decoded.stdout)
                if not counts or int(counts[-1]) != last - first:
                    raise ValueError("Exported segment frame count differs from its source range")
                temporary.replace(target)
            segments.append(
                {
                    "id": f"segment-{key[:16]}-{role}",
                    "role": role,
                    "source_index": index,
                    "start_frame": first,
                    "end_frame": last,
                    "path": target,
                }
            )
        return report, segments
