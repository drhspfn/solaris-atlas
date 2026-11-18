"""Optional offline validation against the exact user-supplied scene recording."""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

REFERENCE = json.loads((Path(__file__).parent / "fixtures/lily_scene_7458657_reference.json").read_text())


def test_reference_timeline_is_ordered_and_records_specific_visual_checks():
    checks = REFERENCE["checks"]
    assert [item["time"] for item in checks] == sorted(item["time"] for item in checks)
    assert all(0 <= item["time"] < REFERENCE["duration_seconds"] for item in checks)
    assert {77.0, 150.0, 152.0, 153.4, 169.0} <= {item["time"] for item in checks}


@pytest.mark.skipif(not os.getenv("SOLARIS_LILY_VIDEO") or not shutil.which("ffmpeg"),
                    reason="Set SOLARIS_LILY_VIDEO to the supplied recording and install ffmpeg")
def test_reference_frames_match_supplied_video(tmp_path):
    video = Path(os.environ["SOLARIS_LILY_VIDEO"])
    with video.open("rb") as stream:
        assert hashlib.file_digest(stream, "sha256").hexdigest() == REFERENCE["source_sha256"]
    for index, item in enumerate(REFERENCE["checks"]):
        if "dhash" not in item:
            continue
        frame = tmp_path / f"{index}.png"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(item["time"]),
                        "-i", str(video), "-frames:v", "1", "-y", str(frame)], check=True, timeout=30)
        with Image.open(frame) as image:
            thumbnail = image.convert("L").resize((9, 8))
            pixels = [thumbnail.getpixel((x, y)) for y in range(8) for x in range(9)]
        actual = sum((pixels[y * 9 + x] > pixels[y * 9 + x + 1]) << (y * 8 + x)
                     for y in range(8) for x in range(8))
        assert (actual ^ int(item["dhash"], 16)).bit_count() <= 5, item["visual"]
